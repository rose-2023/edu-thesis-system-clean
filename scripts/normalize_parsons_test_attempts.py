import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId
from bson.errors import InvalidId
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
MONGO_DATABASE = os.environ.get("MONGO_DATABASE", "thesis_system")


def maybe_object_id(value):
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError):
        return None


def clean_text(value):
    if value is None:
        return ""
    return str(value)


def normalize_id(value):
    if value is None:
        return ""
    return str(value).strip()


def question_collection_name(test_role):
    role = normalize_id(test_role).lower()
    if role == "pre":
        return "pre_parsons_questions"
    if role == "post":
        return "post_parsons_questions"
    return None


def canonical_task_id(doc):
    for key in ("task_id", "question_id", "test_task_id", "source_task_id"):
        value = normalize_id((doc or {}).get(key))
        if value:
            return value
    if (doc or {}).get("_id") is not None:
        return str(doc.get("_id"))
    return ""


def find_question(db, test_role, task_id):
    collection_name = question_collection_name(test_role)
    task_ref = normalize_id(task_id)
    if not collection_name or not task_ref:
        return None

    candidates = [
        {"task_id": task_ref},
        {"question_id": task_ref},
        {"test_task_id": task_ref},
        {"source_task_id": task_ref},
    ]
    oid = maybe_object_id(task_ref)
    if oid is not None:
        candidates.append({"_id": oid})

    return db[collection_name].find_one({"$or": candidates})


def build_question_context(question):
    question = question if isinstance(question, dict) else {}
    blocks = question.get("blocks") if isinstance(question.get("blocks"), list) else []
    solution = question.get("solution") if isinstance(question.get("solution"), list) else []
    blocks_by_id = {}

    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        block_id = normalize_id(block.get("block_id") or block.get("id") or f"b{index + 1}")
        if not block_id:
            continue
        try:
            indent = int(block.get("indent_level") or block.get("indent") or 0)
        except (TypeError, ValueError):
            indent = 0
        blocks_by_id[block_id] = {
            "id": block_id,
            "line": clean_text(block.get("code") if block.get("code") is not None else block.get("text")),
            "indentation": indent,
        }

    expected_order = []
    expected_lines = []
    expected_indentation = []
    for item in solution:
        if isinstance(item, dict):
            block_id = normalize_id(item.get("block_id") or item.get("id"))
            try:
                indent = int(item.get("indent_level") or item.get("indent") or 0)
            except (TypeError, ValueError):
                indent = blocks_by_id.get(block_id, {}).get("indentation", 0)
        else:
            block_id = normalize_id(item)
            indent = blocks_by_id.get(block_id, {}).get("indentation", 0)
        if not block_id:
            continue
        expected_order.append(block_id)
        expected_lines.append(blocks_by_id.get(block_id, {}).get("line", ""))
        expected_indentation.append(indent)

    if not expected_order and blocks_by_id:
        for block_id, block in blocks_by_id.items():
            expected_order.append(block_id)
            expected_lines.append(block.get("line", ""))
            expected_indentation.append(block.get("indentation", 0))

    return {
        "blocks_by_id": blocks_by_id,
        "task_title": question.get("title") or question.get("task_title") or question.get("question_text"),
        "target_concept": question.get("target_concept") or question.get("concept_tag") or question.get("concept"),
        "correct_answer": {
            "order": expected_order,
            "lines": expected_lines,
            "indentation": expected_indentation,
        },
        "expected_order": expected_order,
        "expected_lines": expected_lines,
        "expected_indentation": expected_indentation,
    }


def infer_indentation_from_lines(lines):
    if not isinstance(lines, list):
        return []
    out = []
    for line in lines:
        if line is None:
            out.append(None)
            continue
        text = str(line).replace("\t", "    ")
        out.append(len(text) - len(text.lstrip(" ")))
    return out


def indentation_by_block(order, indentation):
    result = {}
    values = indentation if isinstance(indentation, list) else []
    for index, block_id in enumerate(order or []):
        key = normalize_id(block_id)
        if not key:
            continue
        result[key] = values[index] if index < len(values) else None
    return result


def normalize_list(value):
    if isinstance(value, list):
        return value
    return []


def build_update(attempt, question_context):
    task_id = canonical_task_id(attempt)
    answer_ids = normalize_list(attempt.get("answer_ids") or attempt.get("answer_block_ids"))
    submitted_order = [normalize_id(block_id) for block_id in answer_ids if normalize_id(block_id)]

    answer_lines = normalize_list(attempt.get("answer_lines"))
    if not answer_lines and question_context:
        blocks_by_id = question_context.get("blocks_by_id") or {}
        answer_lines = [blocks_by_id.get(block_id, {}).get("line", "") for block_id in submitted_order]

    if isinstance(attempt.get("submitted_indentation"), list):
        submitted_indentation = attempt.get("submitted_indentation")
    elif answer_lines:
        submitted_indentation = infer_indentation_from_lines(answer_lines)
    else:
        submitted_indentation = [None for _ in submitted_order]

    expected_order = normalize_list((question_context or {}).get("expected_order"))
    expected_lines = normalize_list((question_context or {}).get("expected_lines"))
    expected_indentation = normalize_list((question_context or {}).get("expected_indentation"))

    existing_wrong_indices = normalize_list(attempt.get("wrong_indices"))
    sequence_slots = []
    for index, block_id in enumerate(submitted_order):
        if index >= len(expected_order) or normalize_id(block_id) != normalize_id(expected_order[index]):
            sequence_slots.append(index)
    if existing_wrong_indices:
        sequence_slots = sorted(set(sequence_slots + existing_wrong_indices))

    indent_errors = normalize_list(attempt.get("indent_errors"))
    if not indent_errors and expected_indentation and submitted_indentation:
        for index, expected in enumerate(expected_indentation):
            if index >= len(submitted_indentation):
                continue
            actual = submitted_indentation[index]
            if actual is not None and actual != expected:
                indent_errors.append(index)

    is_correct = bool(attempt.get("is_correct"))
    if is_correct:
        sequence_slots = []
        indent_errors = []

    error_types = []
    if sequence_slots:
        error_types.append("sequence_error")
    if indent_errors:
        error_types.append("indentation_error")

    submitted_blocks = []
    for index, block_id in enumerate(submitted_order):
        actual_indent = submitted_indentation[index] if index < len(submitted_indentation) else None
        expected_block_id = expected_order[index] if index < len(expected_order) else None
        expected_indent = expected_indentation[index] if index < len(expected_indentation) else None
        submitted_blocks.append({
            "index": index,
            "block_id": block_id,
            "line": answer_lines[index] if index < len(answer_lines) else None,
            "indentation": actual_indent,
            "expected_block_id": expected_block_id,
            "expected_line": expected_lines[index] if index < len(expected_lines) else None,
            "expected_indentation": expected_indent,
            "is_sequence_correct": normalize_id(block_id) == normalize_id(expected_block_id),
            "is_indentation_correct": (
                actual_indent is not None
                and expected_indent is not None
                and actual_indent == expected_indent
            ),
        })

    total_slots = max(len(expected_order), len(submitted_order), 1)
    update_set = {
        "task_id": task_id,
        "answer_lines": answer_lines,
        "answer_block_ids": submitted_order,
        "submitted_order": submitted_order,
        "submitted_indentation": submitted_indentation,
        "submitted_indentation_by_block": indentation_by_block(submitted_order, submitted_indentation),
        "submitted_blocks": submitted_blocks,
        "correct_answer": (question_context or {}).get("correct_answer"),
        "expected_order": expected_order,
        "expected_lines": expected_lines,
        "expected_indentation": expected_indentation,
        "wrong_indices": sequence_slots,
        "wrong_indices_all": sequence_slots,
        "id_mismatch_indices": sequence_slots,
        "indent_errors": indent_errors,
        "wrong_slots": sequence_slots,
        "error_count": len(set(sequence_slots + indent_errors)),
        "error_types": error_types,
        "error_concept": attempt.get("target_concept") or (question_context or {}).get("target_concept") or "unknown",
        "extra_wrong_count": max(0, len(submitted_order) - len(expected_order)),
        "total_slots": total_slots,
        "updated_at": datetime.now(timezone.utc),
    }

    if not attempt.get("task_title") and (question_context or {}).get("task_title"):
        update_set["task_title"] = question_context.get("task_title")
    if not attempt.get("target_concept") and (question_context or {}).get("target_concept"):
        update_set["target_concept"] = question_context.get("target_concept")

    return {
        "$set": update_set,
        "$unset": {
            "test_task_id": "",
            "source_task_id": "",
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="Normalize parsons_test_attempts to use task_id and full answer fields."
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing to MongoDB.")
    args = parser.parse_args()

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
    except ServerSelectionTimeoutError:
        print("MongoDB connection failed. Please confirm MongoDB is running on 127.0.0.1:27017.")
        return 1

    db = client[MONGO_DATABASE]
    query = {
        "$or": [
            {"test_task_id": {"$exists": True}},
            {"source_task_id": {"$exists": True}},
            {"submitted_order": {"$exists": False}},
            {"submitted_blocks": {"$exists": False}},
            {"correct_answer": {"$exists": False}},
            {"error_types": {"$exists": False}},
            {"wrong_slots": {"$exists": False}},
        ]
    }

    matched = 0
    updated = 0
    missing_question = 0

    try:
        cursor = db.parsons_test_attempts.find(query)
        for attempt in cursor:
            matched += 1
            task_id = canonical_task_id(attempt)
            question = find_question(db, attempt.get("test_role"), task_id)
            if not question:
                missing_question += 1
            context = build_question_context(question or {})
            update_doc = build_update(attempt, context)
            if args.dry_run:
                print(f"[dry-run] would update _id={attempt.get('_id')} task_id={task_id}")
                continue
            result = db.parsons_test_attempts.update_one({"_id": attempt["_id"]}, update_doc)
            updated += result.modified_count
    except PyMongoError as exc:
        print(f"MongoDB update failed: {exc}")
        return 1

    print(f"matched: {matched}")
    print(f"updated: {updated if not args.dry_run else 0}")
    print(f"missing_question_context: {missing_question}")
    if args.dry_run:
        print("dry-run only; no documents were changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
