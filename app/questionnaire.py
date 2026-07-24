
"""Shared schema and persistence helpers for the pretest questionnaire. 前測問卷的共用 schema 與資料存取輔助函式。"""

from copy import deepcopy
from datetime import datetime, timezone
import json
from collections.abc import Mapping

from pymongo import ASCENDING


QUESTIONNAIRE_COLLECTION = "student_questionnaire_responses"
QUESTIONNAIRE_FORM_VERSION = "pretest_questionnaire_v2"
LEGACY_QUESTIONNAIRE_FORM_VERSION = "pretest_questionnaire_v1"
QUESTIONNAIRE_FORM_VERSIONS = (
    QUESTIONNAIRE_FORM_VERSION,
    LEGACY_QUESTIONNAIRE_FORM_VERSION,
)
QUESTIONNAIRE_DATA_SOURCE = "student_questionnaire_responses"


def _choice(code, label):
    return {"code": code, "label": label}


DEPARTMENT_OPTIONS = [
    _choice("mechanical_computer_aided", "機械與電腦輔助工程系"),
    _choice("mechanical_design", "機械設計工程系"),
    _choice("power_mechanical", "動力機械工程系"),
    _choice("automation", "自動化工程系"),
    _choice("aeronautical", "飛機工程系"),
    _choice("vehicle", "車輛工程系"),
    _choice("materials_science", "材料科學與工程系"),
    _choice("electrical_engineering", "電機工程系"),
    _choice("electronic_engineering", "電子工程系"),
    _choice("computer_science", "資訊工程系"),
    _choice("optoelectronics", "光電工程系"),
    _choice("finance", "財務金融系"),
    _choice("business_administration", "企業管理系"),
    _choice("industrial_management", "工業管理系"),
    _choice("information_management", "資訊管理系"),
    _choice("applied_foreign_languages", "應用外語系"),
    _choice("multimedia_design", "多媒體設計系"),
    _choice("leisure_recreation", "休閒遊憩系"),
    _choice("biotechnology", "生物科技系"),
    _choice("agricultural_technology", "農業科技系"),
]


# Keep the original v1 schema immutable so historical, locked research
# responses are always shown with the wording and answer model students saw.
QUESTIONNAIRE_V1_PAGES = [
    {
        "id": "background",
        # "title": "目前就讀的科系",
        "questions": [
            {
                "id": "department",
                "label": "目前就讀的科系",
                "type": "single_choice",
                "required": True,
                "options": DEPARTMENT_OPTIONS,
            },
        ],
    },
    {
        "id": "programming_background",
        "title": "程式背景",
        "questions": [
            {
                "id": "programming_course_count",
                "label": "在參與本課程前，您曾修習多少門需要實作程式碼的課程？",
                "type": "single_choice",
                "required": True,
                "options": [
                    _choice("zero", "0門"),
                    _choice("one", "1門"),
                    _choice("two", "2門"),
                    _choice("three_or_more", "3門以上"),
                    _choice("unsure", "不確定"),
                ],
            },
            {
                "id": "programming_experience_duration",
                "label": "從您第一次開始接觸程式設計至今，大約經過多久？",
                "type": "single_choice",
                "required": True,
                "options": [
                    _choice("never", "從未接觸"),
                    _choice("under_6_months", "未滿6個月"),
                    _choice("six_to_under_twelve_months", "6個月至未滿1年"),
                    _choice("one_to_under_two_years", "1年至未滿2年"),
                    _choice("two_years_or_more", "2年以上"),
                ],
            },
            {
                "id": "programming_experience_rating",
                "label": "您如何評估自己目前整體的程式設計經驗？",
                "type": "rating",
                "required": True,
                "min": 1,
                "max": 5,
                "min_label": "",
                "max_label": "",
            },
            {
                "id": "python_proficiency_rating",
                "label": "在參加本次銜接課程以前，您對Python的接觸程度為何？",
                "type": "rating",
                "required": True,
                "min": 1,
                "max": 5,
                "min_label": "",
                "max_label": "",
            },
            {
                "id": "python_topics",
                "label": "在本次課程以前，您學過哪些Python內容？",
                "type": "single_choice",
                "required": True,
                "options": [
                    _choice("input_output", "輸入與輸出"),
                    _choice("numeric_operations", "數值運算"),
                    _choice("conditionals", "條件判斷"),
                    _choice("loops", "定數迴圈/不定數迴圈"),
                    _choice("lists", "串列"),
                    _choice("other", "其他"),
                    _choice("none", "都未學過"),
                ],
                "other_text_field": "python_topics_other",
            },
        ],
    },
    {
        "id": "parsons_python_experience",
        "title": "程式區塊操作與 Python 學習經驗",
        "questions": [
            {
                "id": "parsons_experience",
                "label": "您在本次課程以前是否接觸過「將打亂的程式碼或程式積木，拖曳並重新排列成正確順序」的題目 \n例如：使用 Scratch、App Inventor 2 或其他程式學習平台，拖曳及排列程式區塊。？",
                "type": "single_choice",
                "required": True,
                "options": [
                    _choice("never_used", "從未使用"),
                    _choice("seen_not_answered", "看過但未作答"),
                    _choice("answered_one_to_two", "曾作答1–2次"),
                    _choice("answered_three_or_more", "曾作答3次以上"),
                    _choice("unsure", "不確定"),
                ],
            },
            
        ],
    },
]


QUESTIONNAIRE_V2_PAGES = deepcopy(QUESTIONNAIRE_V1_PAGES)

for _page in QUESTIONNAIRE_V2_PAGES:
    for _question in _page["questions"]:
        if _question["id"] == "programming_experience_rating":
            _question["scale_labels"] = [
                _choice(1, "非常沒有經驗"),
                _choice(2, "沒有經驗"),
                _choice(3, "普通"),
                _choice(4, "有經驗"),
                _choice(5, "非常有經驗"),
            ]
        elif _question["id"] == "python_proficiency_rating":
            _question["scale_labels"] = [
                _choice(1, "完全沒有接觸"),
                _choice(2, "沒有接觸"),
                _choice(3, "普通"),
                _choice(4, "有接觸"),
                _choice(5, "能獨立撰寫完整的程式"),
            ]
        elif _question["id"] == "python_topics":
            _question.update({
                "type": "multiple_choice",
                "options": [
                    _choice("all_learned", "都學過"),
                    _choice("input_output", "輸入與輸出"),
                    _choice("numeric_operations", "數值運算"),
                    _choice("conditionals", "條件判斷"),
                    _choice("loops", "定數迴圈/不定數迴圈"),
                    _choice("lists", "串列"),
                    _choice("other", "其他"),
                    _choice("none", "都未學過"),
                ],
                "exclusive_option_codes": ["all_learned", "none"],
            })


QUESTIONNAIRE_SCHEMAS = {
    LEGACY_QUESTIONNAIRE_FORM_VERSION: QUESTIONNAIRE_V1_PAGES,
    QUESTIONNAIRE_FORM_VERSION: QUESTIONNAIRE_V2_PAGES,
}

# Retained for callers that need the active (new-submission) schema.
QUESTIONNAIRE_PAGES = QUESTIONNAIRE_SCHEMAS[QUESTIONNAIRE_FORM_VERSION]


def _pages_for_version(form_version):
    return QUESTIONNAIRE_SCHEMAS.get(
        str(form_version or "").strip(),
        QUESTIONNAIRE_SCHEMAS[LEGACY_QUESTIONNAIRE_FORM_VERSION],
    )


def _questions_for_version(form_version):
    return [
        question
        for page in _pages_for_version(form_version)
        for question in page["questions"]
    ]


def _question_by_id_for_version(form_version):
    return {
        question["id"]: question
        for question in _questions_for_version(form_version)
    }


QUESTION_BY_ID = _question_by_id_for_version(QUESTIONNAIRE_FORM_VERSION)

_INDEX_READY = False


def utc_now():
    return datetime.now(timezone.utc)


def questionnaire_form_payload():
    """Return a standalone payload so route handlers never mutate the schema."""
    return {
        "form_version": QUESTIONNAIRE_FORM_VERSION,
        "data_source": QUESTIONNAIRE_DATA_SOURCE,
        "pages": deepcopy(QUESTIONNAIRE_PAGES),
    }


def ensure_questionnaire_indexes(db):
    global _INDEX_READY
    if _INDEX_READY:
        return
    collection = db[QUESTIONNAIRE_COLLECTION]
    collection.create_index(
        [
            ("student_id", ASCENDING),
            ("test_cycle_id", ASCENDING),
            ("form_version", ASCENDING),
        ],
        unique=True,
        name="uniq_student_test_cycle_questionnaire_form",
    )
    collection.create_index([("student_id", ASCENDING)], name="questionnaire_student_id")
    _INDEX_READY = True


def get_questionnaire_response(db, student_id, test_cycle_id):
    sid = str(student_id or "").strip()
    cycle_id = str(test_cycle_id or "").strip()
    if not sid or not cycle_id:
        return None
    ensure_questionnaire_indexes(db)
    # A v2 response is preferred if both documents exist unexpectedly.  A
    # locked v1 response remains valid so students are never asked to repeat a
    # completed research instrument merely because the active form changed.
    for form_version in QUESTIONNAIRE_FORM_VERSIONS:
        response = db[QUESTIONNAIRE_COLLECTION].find_one(
            {
                "student_id": sid,
                "test_cycle_id": cycle_id,
                "form_version": form_version,
                "locked": True,
            }
        )
        if response:
            return response
    return None


def has_questionnaire_response(db, student_id, test_cycle_id):
    return get_questionnaire_response(db, student_id, test_cycle_id) is not None


def _choice_label(question, value):
    raw = str(value or "").strip()
    for option in question.get("options") or []:
        if option.get("code") == raw:
            return option.get("label") or raw
    return raw


def _multiple_choice_labels(question, values, answers):
    selected = values if isinstance(values, (list, tuple)) else [values]
    labels = []
    other_field = question.get("other_text_field")
    for value in selected:
        raw = str(value or "").strip()
        if not raw:
            continue
        label = _choice_label(question, raw)
        if raw == "other" and other_field:
            other_value = str(answers.get(other_field) or "").strip()
            if other_value:
                label = f"其他：{other_value}"
        labels.append(label)
    return "、".join(labels) if labels else "未填"


def _answer_map(raw_answers):
    """Read current dict answers and tolerate older imported answer formats.

    The current student endpoint always writes a dictionary.  This fallback is
    intentionally read-only: it lets teachers inspect historical responses
    that were saved as JSON text or as question/answer rows without rewriting
    any research data.
    """
    if isinstance(raw_answers, Mapping):
        return dict(raw_answers)

    if isinstance(raw_answers, str):
        try:
            decoded = json.loads(raw_answers)
        except (TypeError, ValueError):
            return {}
        return _answer_map(decoded)

    if not isinstance(raw_answers, list):
        return {}

    answer_map = {}
    ordered_question_ids = list(QUESTION_BY_ID)
    for index, item in enumerate(raw_answers):
        if isinstance(item, Mapping):
            question_id = str(
                item.get("question_id")
                or item.get("questionId")
                or item.get("id")
                or item.get("key")
                or ""
            ).strip()
            value = next(
                (
                    item[field]
                    for field in (
                        "answer_code",
                        "answer",
                        "value",
                        "selected_option",
                        "selectedAnswer",
                    )
                    if field in item
                ),
                None,
            )
        else:
            question_id = ordered_question_ids[index] if index < len(ordered_question_ids) else ""
            value = item
        if question_id:
            answer_map[question_id] = value
    return answer_map


def _json_safe_answer_code(value):
    """Keep teacher API responses JSON serializable for legacy answer values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe_answer_code(item) for item in value]
    return str(value)

# 抓取問卷答案，並將資料轉換成教師可讀的格式
def present_questionnaire_answers(response):
    """Map stable database codes to teacher-readable question and answer labels."""
    answers = _answer_map((response or {}).get("answers"))
    items = []
    form_version = str((response or {}).get("form_version") or "").strip()
    pages = _pages_for_version(form_version)
    for page in pages:
        for question in page["questions"]:
            question_id = question["id"]
            value = answers.get(question_id)
            if question["type"] == "single_choice":
                answer_label = _choice_label(question, value) if value is not None else "未填"
                other_field = question.get("other_text_field")
                if value == "other" and other_field:
                    other_value = str(answers.get(other_field) or "").strip()
                    answer_label = f"其他：{other_value}" if other_value else "其他"
            elif question["type"] == "multiple_choice":
                answer_label = _multiple_choice_labels(question, value, answers)
            elif question["type"] == "rating":
                answer_label = (
                    f"{value}（{question.get('min_label')}～{question.get('max_label')}）"
                    if value is not None else "未填"
                )
            else:
                answer_label = str(value or "未填")
            items.append(
                {
                    "page_id": page["id"],
                    "page_title": page.get("title") or "基本資料",
                    "question_id": question_id,
                    "question_label": question["label"],
                    "answer_code": _json_safe_answer_code(value),
                    "answer_label": answer_label,
                }
            )
    return items


def validate_questionnaire_answers(raw_answers, form_version=QUESTIONNAIRE_FORM_VERSION):
    if not isinstance(raw_answers, dict):
        return None, [{"field": "answers", "message": "answers must be an object"}]

    normalized = {}
    errors = []
    question_by_id = _question_by_id_for_version(form_version)
    for question_id, question in question_by_id.items():
        raw_value = raw_answers.get(question_id)
        if question["type"] == "single_choice":
            value = str(raw_value or "").strip()
            allowed_codes = {option["code"] for option in question.get("options") or []}
            if question.get("required") and not value:
                errors.append({"field": question_id, "message": "required"})
                continue
            if value not in allowed_codes:
                errors.append({"field": question_id, "message": "invalid option"})
                continue
            normalized[question_id] = value
            other_field = question.get("other_text_field")
            if other_field and value == "other":
                other_text = str(raw_answers.get(other_field) or "").strip()
                if not other_text:
                    errors.append({"field": other_field, "message": "required when other is selected"})
                else:
                    normalized[other_field] = other_text[:500]
        elif question["type"] == "multiple_choice":
            if not isinstance(raw_value, list):
                errors.append({"field": question_id, "message": "must be an array"})
                continue

            values = []
            invalid_value = False
            for item in raw_value:
                if not isinstance(item, str):
                    invalid_value = True
                    break
                value = item.strip()
                if not value:
                    invalid_value = True
                    break
                values.append(value)

            if question.get("required") and not values:
                errors.append({"field": question_id, "message": "required"})
                continue
            if invalid_value:
                errors.append({"field": question_id, "message": "invalid option"})
                continue
            if len(values) != len(set(values)):
                errors.append({"field": question_id, "message": "duplicate option"})
                continue

            allowed_codes = {option["code"] for option in question.get("options") or []}
            if any(value not in allowed_codes for value in values):
                errors.append({"field": question_id, "message": "invalid option"})
                continue

            exclusive_codes = set(question.get("exclusive_option_codes") or [])
            if exclusive_codes.intersection(values) and len(values) > 1:
                errors.append({"field": question_id, "message": "exclusive option"})
                continue

            normalized[question_id] = values
            other_field = question.get("other_text_field")
            if other_field and "other" in values:
                other_text = str(raw_answers.get(other_field) or "").strip()
                if not other_text:
                    errors.append({"field": other_field, "message": "required when other is selected"})
                else:
                    normalized[other_field] = other_text[:500]
        elif question["type"] == "rating":
            if isinstance(raw_value, bool):
                value = None
            else:
                try:
                    value = int(raw_value)
                except (TypeError, ValueError):
                    value = None
            if value is None:
                errors.append({"field": question_id, "message": "required"})
                continue
            if value < question["min"] or value > question["max"]:
                errors.append({"field": question_id, "message": "rating out of range"})
                continue
            normalized[question_id] = value

    return (normalized if not errors else None), errors
