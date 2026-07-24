"""Normalize pre/post choice-question records to the canonical field names.

Run without arguments to preview the changes. Use --apply only after reviewing
the summary. The script updates only choice items in the test question
collections and corresponding `parsons_test_attempts` records.
"""

import argparse
import os
from datetime import datetime, timezone

from pymongo import MongoClient


CHOICE_TYPES = ["choice", "choices", "mcq", "multiple_choice", "single_choice"]
CHOICE_TEST_MAX_SCORE = 4
CHOICE_TEST_MIN_SCORE = 0
ATTEMPT_LEGACY_FIELDS = {
    "answer": "",
    "answer_text": "",
    "expected_answer": "",
    "duration_sec": "",
    "started_at_utc": "",
    "submitted_at_utc": "",
    "submitted_at_taiwan": "",
    "created_at_utc": "",
    "created_at_taiwan": "",
    "updated_at_utc": "",
    "updated_at_taiwan": "",
    "timezone": "",
    "target_concept": "",
    "error_concept": "",
}
QUESTION_LEGACY_FIELDS = {
    "stem": "",
    "question": "",
    "answer_key": "",
    "answer": "",
    "correct_option": "",
    "target_concept": "",
    "concept": "",
}
TIMESTAMP_FIELDS = {"started_at", "submitted_at", "created_at", "updated_at"}
ATTEMPT_CANONICAL_ALIASES = {
    "selected_answer": ("answer",),
    "selected_answer_text": ("answer_text",),
    "correct_answer": ("expected_answer",),
    "duration_seconds": ("duration_sec",),
    "started_at": ("started_at_utc",),
    "submitted_at": ("submitted_at_utc",),
    "created_at": ("created_at_utc",),
    "updated_at": ("updated_at_utc",),
    "concept_tag": ("target_concept", "error_concept"),
}
QUESTION_CANONICAL_ALIASES = {
    "question_text": ("stem", "question"),
    "correct_answer": ("answer_key", "answer", "correct_option"),
    "concept_tag": ("target_concept", "concept"),
}


def first_present(doc, *keys):
    for key in keys:
        value = doc.get(key)
        if value is not None and value != "":
            return value
    return None


def utc_datetime(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 1_000_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    return None


def normalized_choice_value(value, options):
    if value is None:
        return ""
    text = str(value).strip()
    if text.isdigit() and options:
        index = int(text)
        if 0 <= index < len(options):
            option = options[index]
        elif 1 <= index <= len(options):
            option = options[index - 1]
        else:
            option = None
        if isinstance(option, dict):
            return str(option.get("key") or option.get("label") or option.get("value") or text).strip().upper()
    return text.upper()


def normalized_answer_key(doc):
    value = first_present(doc, "correct_answer", "answer_key", "answer", "correct_option")
    options = doc.get("options") if isinstance(doc.get("options"), list) else []
    return normalized_choice_value(value, options)


def choice_test_data_source(test_role):
    role = str(test_role or "").strip().lower()
    if role in {"pre", "pretest"}:
        return "pretest_choice_questions"
    if role in {"post", "posttest"}:
        return "posttest_choice_questions"
    return "test_choice_questions"


def choice_test_score(doc):
    if doc.get("is_correct") is True:
        return CHOICE_TEST_MAX_SCORE
    if doc.get("is_correct") is False:
        return CHOICE_TEST_MIN_SCORE
    try:
        return CHOICE_TEST_MAX_SCORE if float(doc.get("score")) > 0 else CHOICE_TEST_MIN_SCORE
    except (TypeError, ValueError):
        return CHOICE_TEST_MIN_SCORE


def build_question_update(doc):
    question_text = first_present(doc, "question_text", "stem", "instruction", "question", "title")
    concept_tag = first_present(doc, "concept_tag", "target_concept", "concept", "unit")
    return {
        "question_text": str(question_text or "").strip(),
        "concept_tag": str(concept_tag or "").strip() or None,
        "correct_answer": normalized_answer_key(doc),
    }


def build_attempt_update(doc):
    question_text = first_present(doc, "question_text", "task_title")
    concept_tag = first_present(doc, "concept_tag", "target_concept", "error_concept")
    return {
        "question_text": str(question_text or "").strip(),
        "concept_tag": str(concept_tag or "").strip() or None,
        "selected_answer": str(first_present(doc, "selected_answer", "answer") or "").strip().upper(),
        "selected_answer_text": str(first_present(doc, "selected_answer_text", "answer_text") or "").strip(),
        "correct_answer": str(first_present(doc, "correct_answer", "expected_answer") or "").strip().upper(),
        "score": choice_test_score(doc),
        "max_score": CHOICE_TEST_MAX_SCORE,
        "min_score": CHOICE_TEST_MIN_SCORE,
        "data_source": choice_test_data_source(doc.get("test_role")),
        "duration_seconds": first_present(doc, "duration_seconds", "duration_sec"),
        "started_at": utc_datetime(first_present(doc, "started_at", "started_at_utc")),
        "submitted_at": utc_datetime(first_present(doc, "submitted_at", "submitted_at_utc")),
        "created_at": utc_datetime(first_present(doc, "created_at", "created_at_utc")),
        "updated_at": utc_datetime(first_present(doc, "updated_at", "updated_at_utc", "created_at", "created_at_utc")),
    }


def migrate_collection(collection, query, build_update, unset_fields, canonical_aliases, apply):
    scanned = changed = conflicts = 0
    for doc in collection.find(query):
        scanned += 1
        update_fields = build_update(doc)

        def values_match(key, current, proposed):
            if key in TIMESTAMP_FIELDS:
                return utc_datetime(current) == utc_datetime(proposed)
            if key == "duration_seconds":
                try:
                    return float(current) == float(proposed)
                except (TypeError, ValueError):
                    return current == proposed
            if key == "correct_answer":
                options = doc.get("options") if isinstance(doc.get("options"), list) else []
                return normalized_choice_value(current, options) == normalized_choice_value(proposed, options)
            if key in {"selected_answer", "selected_answer_text", "question_text", "concept_tag"}:
                return str(current or "").strip().upper() == str(proposed or "").strip().upper()
            return current == proposed

        conflicting_fields = [
            key for key in update_fields
            if key in doc
            and doc.get(key) not in (None, "")
            and update_fields[key] not in (None, "")
            and key not in {"score", "max_score", "min_score", "data_source"}
            and not values_match(key, doc.get(key), update_fields[key])
        ]
        if conflicting_fields:
            conflicts += 1
            print(f"Conflict: {collection.name}/{doc.get('_id')} fields={','.join(conflicting_fields)}")
            continue
        alias_conflicts = []
        for canonical, aliases in canonical_aliases.items():
            proposed = update_fields.get(canonical)
            for alias in aliases:
                if alias in doc and doc.get(alias) not in (None, "") and not values_match(canonical, doc.get(alias), proposed):
                    alias_conflicts.append(f"{canonical}:{alias}")
        if alias_conflicts:
            conflicts += 1
            print(f"Conflict: {collection.name}/{doc.get('_id')} fields={','.join(alias_conflicts)}")
            continue
        needs_set = any(
            not values_match(key, doc.get(key), value)
            for key, value in update_fields.items()
        )
        fields_to_unset = {key: value for key, value in unset_fields.items() if key in doc}
        if not needs_set and not fields_to_unset:
            continue
        changed += 1
        if apply:
            update_fields["updated_at"] = datetime.now(timezone.utc)
            update = {"$set": update_fields}
            if fields_to_unset:
                update["$unset"] = fields_to_unset
            collection.update_one({"_id": doc["_id"]}, update)
    return scanned, changed, conflicts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write the normalized records to MongoDB.")
    args = parser.parse_args()

    client = MongoClient(os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017"))
    db = client[os.environ.get("MONGO_DATABASE", "thesis_system")]

    question_query = {"$or": [
        {"question_type": {"$in": CHOICE_TYPES}},
        {"type": {"$in": CHOICE_TYPES}},
    ]}
    attempt_query = {
        "test_role": {"$in": ["pre", "pretest", "post", "posttest"]},
        "$or": [
            {"question_type": {"$in": CHOICE_TYPES}},
            {"selected_answer": {"$exists": True}},
            {"choice_options": {"$exists": True}},
        ],
    }

    question_results = []
    for collection_name in ("pre_parsons_questions", "post_parsons_questions"):
        question_results.append((
            collection_name,
            migrate_collection(
                db[collection_name],
                question_query,
                build_question_update,
                QUESTION_LEGACY_FIELDS,
                QUESTION_CANONICAL_ALIASES,
                args.apply,
            ),
        ))
    attempt_result = migrate_collection(
        db.parsons_test_attempts,
        attempt_query,
        build_attempt_update,
        ATTEMPT_LEGACY_FIELDS,
        ATTEMPT_CANONICAL_ALIASES,
        args.apply,
    )
    mode = "applied" if args.apply else "preview"
    for collection_name, result in question_results:
        print(f"{mode}: {collection_name} scanned={result[0]} changed={result[1]} conflicts={result[2]}")
    print(f"{mode}: parsons_test_attempts scanned={attempt_result[0]} changed={attempt_result[1]} conflicts={attempt_result[2]}")


if __name__ == "__main__":
    main()
