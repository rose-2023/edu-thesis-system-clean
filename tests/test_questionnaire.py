import unittest

from app.questionnaire import (
    LEGACY_QUESTIONNAIRE_FORM_VERSION,
    QUESTIONNAIRE_COLLECTION,
    QUESTIONNAIRE_FORM_VERSION,
    get_questionnaire_response,
    present_questionnaire_answers,
    questionnaire_form_payload,
    validate_questionnaire_answers,
)


def _valid_v2_answers(python_topics=None, other_text=None):
    answers = {
        "department": "computer_science",
        "programming_course_count": "one",
        "programming_experience_duration": "under_6_months",
        "programming_experience_rating": 3,
        "python_proficiency_rating": 3,
        "parsons_experience": "never_used",
        "python_topics": python_topics if python_topics is not None else ["input_output"],
    }
    if other_text is not None:
        answers["python_topics_other"] = other_text
    return answers


class _FakeCollection:
    def __init__(self, documents):
        self.documents = documents

    def create_index(self, *_args, **_kwargs):
        return "test-index"

    def find_one(self, query):
        for document in self.documents:
            if all(document.get(key) == value for key, value in query.items()):
                return document
        return None


class _FakeDb:
    def __init__(self, documents):
        self.collection = _FakeCollection(documents)

    def __getitem__(self, name):
        if name != QUESTIONNAIRE_COLLECTION:
            raise KeyError(name)
        return self.collection


class QuestionnaireV2Tests(unittest.TestCase):
    def test_active_payload_contains_v2_scale_labels_and_multi_choice_topics(self):
        payload = questionnaire_form_payload()
        questions = {
            question["id"]: question
            for page in payload["pages"]
            for question in page["questions"]
        }

        self.assertEqual(payload["form_version"], QUESTIONNAIRE_FORM_VERSION)
        self.assertEqual(
            [item["label"] for item in questions["programming_experience_rating"]["scale_labels"]],
            ["非常沒有經驗", "沒有經驗", "普通", "有經驗", "非常有經驗"],
        )
        self.assertEqual(
            [item["label"] for item in questions["python_proficiency_rating"]["scale_labels"]],
            ["完全沒有接觸", "沒有接觸", "普通", "有接觸", "能獨立撰寫完整的程式"],
        )
        self.assertEqual(questions["python_topics"]["type"], "multiple_choice")
        self.assertEqual(
            [item["code"] for item in questions["python_topics"]["options"]],
            [
                "all_learned", "input_output", "numeric_operations", "conditionals",
                "loops", "lists", "other", "none",
            ],
        )

    def test_topics_accepts_multiple_values_and_required_other_text(self):
        normalized, errors = validate_questionnaire_answers(
            _valid_v2_answers(["input_output", "other"], "函式"),
        )

        self.assertEqual(errors, [])
        self.assertEqual(normalized["python_topics"], ["input_output", "other"])
        self.assertEqual(normalized["python_topics_other"], "函式")

    def test_topics_rejects_empty_unknown_duplicate_and_exclusive_combinations(self):
        invalid_topics = [
            [],
            ["unknown"],
            ["lists", "lists"],
            ["all_learned", "input_output"],
            ["none", "other"],
        ]
        for python_topics in invalid_topics:
            with self.subTest(python_topics=python_topics):
                normalized, errors = validate_questionnaire_answers(_valid_v2_answers(python_topics))
                self.assertIsNone(normalized)
                self.assertTrue(any(error["field"] == "python_topics" for error in errors))

    def test_topics_rejects_other_without_explanation_and_discards_unused_other_text(self):
        normalized, errors = validate_questionnaire_answers(_valid_v2_answers(["other"]))
        self.assertIsNone(normalized)
        self.assertIn(
            {"field": "python_topics_other", "message": "required when other is selected"},
            errors,
        )

        normalized, errors = validate_questionnaire_answers(
            _valid_v2_answers(["lists"], "不應儲存的草稿文字"),
        )
        self.assertEqual(errors, [])
        self.assertNotIn("python_topics_other", normalized)

    def test_v1_answers_keep_their_original_schema_when_presented(self):
        response = {
            "form_version": LEGACY_QUESTIONNAIRE_FORM_VERSION,
            "answers": {
                "programming_experience_rating": 5,
                "python_topics": "input_output",
            },
        }
        answers = {item["question_id"]: item for item in present_questionnaire_answers(response)}

        self.assertEqual(answers["python_topics"]["answer_code"], "input_output")
        self.assertEqual(answers["python_topics"]["answer_label"], "輸入與輸出")
        self.assertEqual(
            answers["programming_experience_rating"]["answer_label"],
            "5（非常沒有經驗～非常有經驗）",
        )

    def test_v2_multiple_choice_answer_is_presented_as_readable_labels_and_codes(self):
        response = {
            "form_version": QUESTIONNAIRE_FORM_VERSION,
            "answers": {
                "python_topics": ["input_output", "other"],
                "python_topics_other": "函式",
            },
        }
        answers = {item["question_id"]: item for item in present_questionnaire_answers(response)}

        self.assertEqual(answers["python_topics"]["answer_code"], ["input_output", "other"])
        self.assertEqual(answers["python_topics"]["answer_label"], "輸入與輸出、其他：函式")

    def test_locked_v1_response_is_still_a_completed_questionnaire(self):
        v1_response = {
            "student_id": "s001",
            "test_cycle_id": "cycle-a",
            "form_version": LEGACY_QUESTIONNAIRE_FORM_VERSION,
            "locked": True,
        }
        response = get_questionnaire_response(_FakeDb([v1_response]), "s001", "cycle-a")
        self.assertEqual(response, v1_response)


if __name__ == "__main__":
    unittest.main()
