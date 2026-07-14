import uuid
from datetime import datetime, timezone, timedelta

from bson import ObjectId
from flask import Blueprint, jsonify, request
from pymongo import ReturnDocument

from ..db import db
from ..session_auth import current_student_id


learning_logs_bp = Blueprint("learning_logs", __name__)

SCHEMA_VERSION = 1
TIMEZONE_NAME = "Asia/Taipei"
TAIPEI_TZ = timezone(timedelta(hours=8))
TEST_STUDENT_ID = "11461127"
MAX_HINT_COUNT = 2
HINT_METADATA_TOP_LEVEL_REMOVED_FIELD_KEYS = {
    "wrong_index",
    "concept",
    "concept_tag",
    "concept_scope",
}

HINT_METADATA_ALWAYS_REMOVED_FIELD_KEYS = {
    "subtitle_range",
    "subtitle_ranges",
    "subtitle_broad_range",
    "subtitle_narrow_range",
    "subtitle_range_available",
    "subtitle_scope",
}
HINT_METADATA_PATCH_KEYS = {
    "review_type",
    "hint_no",
    "hint_text",
    "hint_content",
    "hint_source",
    "hint_loaded",
    "hint_error",
    "hint_click_no",
    "max_hint_count",
    "hint_limit_reached",
    "hint_retry_count",
    "next_hint_no",
    "trigger_method",
    "button_name",
    "close_method",
    "return_method",
    "question_id",
    "unit_id",
    "question_type",
    "error_type",
    "wrong_slots",
    "hint_type",
    "ai_feedback_detail",
    "ai_diagnosis_summary",
    "concept_hint",
    "first_hint",
    "second_hint",
    "possible_causes",
    "reflection_questions",
    "impact",
    "guiding_question",
    "generated_at",
    "hint_id",
    "requested_hint_no",
    "hint_generation_count",
    "hint_view_count",
    "ai_hint_generation_count",
    "ai_hint_view_count",
    "first_system_hint_text",
    "ai_hint_1_text",
    "ai_hint_2_text",
    "repeated_error",
    "error_types",
}


def _clean_hint_metadata_fields(value, *, depth=0):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in HINT_METADATA_ALWAYS_REMOVED_FIELD_KEYS:
                continue
            if depth == 0 and key_text in HINT_METADATA_TOP_LEVEL_REMOVED_FIELD_KEYS:
                continue
            cleaned[key_text] = _clean_hint_metadata_fields(item, depth=depth + 1)
        return cleaned
    if isinstance(value, list):
        return [_clean_hint_metadata_fields(item, depth=depth + 1) for item in value]
    return value


HINT_TOP_LEVEL_DEFAULTS = {
    "hint_id": None,
    "question_id": None,
    "unit_id": None,
    "question_type": None,
    "review_type": None,
    "hint_type": None,
    "requested_hint_no": None,
    "hint_no": None,
    "hint_click_no": None,
    "max_hint_count": None,
    "hint_generation_count": None,
    "hint_view_count": None,
    "ai_hint_generation_count": None,
    "ai_hint_view_count": None,
    "hint_limit_reached": None,
    "hint_retry_count": None,
    "next_hint_no": None,
    "first_system_hint_text": None,
    "ai_hint_1_text": None,
    "ai_hint_2_text": None,
    "hint_text": None,
    "hint_content": None,
    "hint_source": None,
    "hint_loaded": None,
    "hint_error": None,
    "trigger_method": None,
    "button_name": None,
    "close_method": None,
    "return_method": None,
    "error_type": None,
    "error_types": [],
    "wrong_slots": [],
    "repeated_error": None,
    "ai_diagnosis_summary": None,
}
ALLOWED_EVENT_TYPES = {
    "session_start",
    "session_end",
    "page_view",
    "task_open",
    "task_start",
    "click_next_to_practice",
    "enter_parsons_task",
    "answer_submit",
    "view_hint",
    "hide_hint",
    "review_open",
    "review_close",
    "first_error_hint_shown",
    "ai_hint_modal_open",
    "review_code_from_hint",
    "ai_hint_modal_close",
    "return_to_fix_from_hint",
    "ai_hint_reopen",
    "ai_hint_view",
    "submit_after_hint",
    "ai_hint_second_request",
    "second_hint_reminder_shown",
    "second_hint_reminder_clicked",
    "second_hint_reminder_ignored",
    # C 策略固定中文語意提示：記錄系統已呈現固定提示，但不算 AI hint。
    "fixed_semantic_feedback_presented",
    "return_to_task",
    "idle_detected",
    "heartbeat",
}
HINT_EVENT_TYPES = {
    "view_hint",
    "hide_hint",
    "review_open",
    "review_close",
    "first_error_hint_shown",
    "ai_hint_modal_open",
    "review_code_from_hint",
    "ai_hint_modal_close",
    "return_to_fix_from_hint",
    "ai_hint_reopen",
    "ai_hint_view",
    "submit_after_hint",
    "ai_hint_second_request",
    "second_hint_reminder_shown",
    "second_hint_reminder_clicked",
    "second_hint_reminder_ignored",
}

_INDEXES_READY = False


def ensure_learning_log_indexes():
    global _INDEXES_READY
    if _INDEXES_READY:
        return
    for field in (
        "student_id",
        "session_id",
        "event_type",
        "task_id",
        "attempt_id",
        "event_at",
        "is_test_data",
    ):
        db.learning_logs.create_index([(field, 1)], name=f"{field}_1")
    _INDEXES_READY = True


def _utc_now():
    return datetime.now(timezone.utc)


def _taiwan_time_string(value):
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _parse_event_at(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 1_000_000_000_000:
            timestamp /= 1000.0
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except Exception:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _optional_string(value):
    normalized = str(value or "").strip()
    return normalized or None


def _normalize_activity_type(value):
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"practice", "test"} else None


def _normalize_test_role(value, activity_type):
    if activity_type != "test":
        return None
    normalized = str(value or "").strip().lower()
    if normalized in {"pre", "pretest"}:
        return "pretest"
    if normalized in {"post", "posttest"}:
        return "posttest"
    return None


def _optional_attempt_no(value):
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
        return parsed if parsed >= 1 else None
    except Exception:
        return None


def _optional_nonnegative_int(value):
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
        return parsed if parsed >= 0 else None
    except Exception:
        return None


def _int_list(value):
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        try:
            parsed = int(item)
        except Exception:
            continue
        if parsed >= 0:
            out.append(parsed)
    return sorted(set(out))


def _attempt_wrong_slots_for_log(attempt):
    doc = attempt if isinstance(attempt, dict) else {}
    if isinstance(doc.get("sequence_slots"), list):
        return _int_list(doc.get("sequence_slots"))
    if isinstance(doc.get("wrong_slots"), list):
        return _int_list(doc.get("wrong_slots"))
    return []


def _attempt_context(attempt_id):
    normalized = _optional_string(attempt_id)
    if not normalized or not ObjectId.is_valid(normalized):
        return {}
    attempt = db.parsons_attempts_v2.find_one(
        {"_id": ObjectId(normalized)},
        {
            "student_id": 1,
            "activity_type": 1,
            "test_role": 1,
            "task_id": 1,
            "attempt_no": 1,
            "target_concept": 1,
            "is_correct": 1,
            "score": 1,
            "error_count": 1,
            "error_types": 1,
            "incorrect_slots": 1,
            "sequence_slots": 1,
            "indentation_slots": 1,
            "wrong_slots": 1,
            "repeated_error": 1,
            "error_details": 1,
            "repeated_error_types": 1,
            "repeated_error_count": 1,
            "repeated_error_basis": 1,
            "repeated_error_rule_version": 1,
        },
    )
    if attempt:
        attempt["wrong_slots"] = _attempt_wrong_slots_for_log(attempt)
        return attempt

    test_attempt = db.parsons_test_attempts.find_one(
        {"_id": ObjectId(normalized)},
        {
            "student_id": 1,
            "test_role": 1,
            "test_task_id": 1,
            "task_id": 1,
            "source_task_id": 1,
            "attempt_no": 1,
            "target_concept": 1,
            "is_correct": 1,
            "score": 1,
            "error_count": 1,
            "error_types": 1,
            "wrong_slots": 1,
            "wrong_indices": 1,
            "indent_errors": 1,
            "repeated_error": 1,
            "error_details": 1,
            "repeated_error_types": 1,
            "repeated_error_count": 1,
            "repeated_error_basis": 1,
            "repeated_error_rule_version": 1,
        },
    )
    if not test_attempt:
        return {}

    wrong_slots = test_attempt.get("wrong_slots")
    if not isinstance(wrong_slots, list):
        wrong_slots = test_attempt.get("wrong_indices") if isinstance(test_attempt.get("wrong_indices"), list) else []
    error_types = test_attempt.get("error_types")
    if not isinstance(error_types, list):
        error_types = []
        if wrong_slots:
            error_types.append("sequence_error")
        if isinstance(test_attempt.get("indent_errors"), list) and test_attempt.get("indent_errors"):
            error_types.append("indentation_error")
    error_count = test_attempt.get("error_count")
    if not isinstance(error_count, int):
        error_count = len(set((wrong_slots or []) + (test_attempt.get("indent_errors") or [])))

    return {
        "student_id": test_attempt.get("student_id"),
        "activity_type": "test",
        "test_role": _normalize_test_role(test_attempt.get("test_role"), "test"),
        "task_id": test_attempt.get("task_id") or test_attempt.get("test_task_id") or test_attempt.get("source_task_id"),
        "attempt_no": test_attempt.get("attempt_no") or 1,
        "target_concept": test_attempt.get("target_concept"),
        "is_correct": test_attempt.get("is_correct"),
        "score": test_attempt.get("score"),
        "error_count": error_count,
        "error_types": error_types,
        "wrong_slots": wrong_slots or [],
        "repeated_error": test_attempt.get("repeated_error") if isinstance(test_attempt.get("repeated_error"), bool) else False,
        "error_details": test_attempt.get("error_details") if isinstance(test_attempt.get("error_details"), list) else [],
        "repeated_error_types": test_attempt.get("repeated_error_types") if isinstance(test_attempt.get("repeated_error_types"), list) else [],
        "repeated_error_count": test_attempt.get("repeated_error_count") if isinstance(test_attempt.get("repeated_error_count"), int) else 0,
        "repeated_error_basis": test_attempt.get("repeated_error_basis"),
        "repeated_error_rule_version": test_attempt.get("repeated_error_rule_version"),
    }


def _hint_event_metadata(event_type, student_id, session_id, task_id, attempt_id, metadata):
    normalized = _clean_hint_metadata_fields(dict(metadata or {}))
    normalized["review_type"] = "ai_hint"
    normalized["hint_type"] = normalized.get("hint_type") or "ai_hint"
    normalized["question_type"] = normalized.get("question_type") or "parsons"
    normalized["max_hint_count"] = MAX_HINT_COUNT
    if event_type not in {"view_hint", "hide_hint", "review_open", "review_close"}:
        return normalized
    query = {
        "student_id": student_id,
        "session_id": session_id,
        "task_id": task_id,
        "event_type": {"$in": ["view_hint", "review_open"]},
        "metadata.review_type": "ai_hint",
        "metadata.hint_limit_reached": {"$ne": True},
    }
    if attempt_id:
        query["attempt_id"] = attempt_id

    if event_type in {"view_hint", "review_open"}:
        opened_count = db.learning_logs.count_documents(query)
        hint_no = min(opened_count + 1, MAX_HINT_COUNT)
        normalized["hint_no"] = hint_no
        normalized["hint_click_no"] = hint_no
        if opened_count >= MAX_HINT_COUNT:
            normalized["hint_limit_reached"] = True
        else:
            normalized.pop("hint_limit_reached", None)
        return normalized

    latest_open = db.learning_logs.find_one(
        query,
        {"metadata.hint_no": 1, "metadata.hint_click_no": 1},
        sort=[("event_at", -1), ("created_at", -1), ("_id", -1)],
    ) or {}
    latest_hint_no = (latest_open.get("metadata") or {}).get("hint_click_no")
    if latest_hint_no is None:
        latest_hint_no = (latest_open.get("metadata") or {}).get("hint_no")
    try:
        latest_hint_no = int(latest_hint_no)
    except Exception:
        latest_hint_no = None
    if latest_hint_no not in range(1, MAX_HINT_COUNT + 1):
        opened_count = db.learning_logs.count_documents(query)
        latest_hint_no = min(max(opened_count, 1), MAX_HINT_COUNT)
    normalized["hint_no"] = latest_hint_no
    normalized["hint_click_no"] = latest_hint_no
    normalized.pop("hint_limit_reached", None)
    return normalized


def _hint_top_level_fields(metadata, task_id=None, include_task_fallback=True):
    hint_metadata = metadata if isinstance(metadata, dict) else {}
    return {
        **HINT_TOP_LEVEL_DEFAULTS,
        "hint_id": _optional_string(hint_metadata.get("hint_id")),
        "question_id": _optional_string(
            hint_metadata.get("question_id")
            or (task_id if include_task_fallback else None)
        ),
        "unit_id": _optional_string(hint_metadata.get("unit_id")),
        "question_type": _optional_string(hint_metadata.get("question_type") or "parsons"),
        "review_type": _optional_string(hint_metadata.get("review_type")),
        "hint_type": _optional_string(hint_metadata.get("hint_type") or hint_metadata.get("review_type")),
        "requested_hint_no": _optional_attempt_no(hint_metadata.get("requested_hint_no")),
        "hint_no": _optional_attempt_no(hint_metadata.get("hint_no")),
        "max_hint_count": _optional_attempt_no(hint_metadata.get("max_hint_count")),
        "hint_generation_count": _optional_nonnegative_int(
            hint_metadata.get("hint_generation_count")
            if hint_metadata.get("hint_generation_count") is not None
            else hint_metadata.get("ai_hint_generation_count")
        ),
        "hint_view_count": _optional_nonnegative_int(
            hint_metadata.get("hint_view_count")
            if hint_metadata.get("hint_view_count") is not None
            else hint_metadata.get("ai_hint_view_count")
        ),
        "ai_hint_generation_count": _optional_nonnegative_int(hint_metadata.get("ai_hint_generation_count")),
        "ai_hint_view_count": _optional_nonnegative_int(hint_metadata.get("ai_hint_view_count")),
        "hint_limit_reached": hint_metadata.get("hint_limit_reached") if isinstance(hint_metadata.get("hint_limit_reached"), bool) else None,
        "hint_retry_count": _optional_attempt_no(hint_metadata.get("hint_retry_count")),
        "next_hint_no": _optional_attempt_no(hint_metadata.get("next_hint_no")),
        "first_system_hint_text": _optional_string(hint_metadata.get("first_system_hint_text")),
        "ai_hint_1_text": _optional_string(hint_metadata.get("ai_hint_1_text")),
        "ai_hint_2_text": _optional_string(hint_metadata.get("ai_hint_2_text")),
        "hint_text": _optional_string(hint_metadata.get("hint_text") or hint_metadata.get("hint_content")),
        "hint_content": _optional_string(hint_metadata.get("hint_content") or hint_metadata.get("hint_text")),
        "hint_source": _optional_string(hint_metadata.get("hint_source")),
        "hint_loaded": hint_metadata.get("hint_loaded") if isinstance(hint_metadata.get("hint_loaded"), bool) else None,
        "hint_error": _optional_string(hint_metadata.get("hint_error")),
        "hint_click_no": _optional_attempt_no(
            hint_metadata.get("hint_click_no")
            if hint_metadata.get("hint_click_no") is not None
            else hint_metadata.get("hint_no")
        ),
        "trigger_method": _optional_string(hint_metadata.get("trigger_method")),
        "button_name": _optional_string(hint_metadata.get("button_name")),
        "close_method": _optional_string(hint_metadata.get("close_method")),
        "return_method": _optional_string(hint_metadata.get("return_method")),
        "error_type": _optional_string(hint_metadata.get("error_type")),
        "error_types": hint_metadata.get("error_types") if isinstance(hint_metadata.get("error_types"), list) else [],
        "wrong_slots": hint_metadata.get("wrong_slots") if isinstance(hint_metadata.get("wrong_slots"), list) else [],
        "repeated_error": hint_metadata.get("repeated_error") if isinstance(hint_metadata.get("repeated_error"), bool) else None,
        "ai_diagnosis_summary": _optional_string(hint_metadata.get("ai_diagnosis_summary")),
    }


def write_learning_log(payload, enforced_student_id=None):
    data = payload if isinstance(payload, dict) else {}
    event_type = str(data.get("event_type") or "").strip()
    if event_type not in ALLOWED_EVENT_TYPES:
        raise ValueError("unsupported event_type")

    attempt_id = _optional_string(data.get("attempt_id"))
    attempt = _attempt_context(attempt_id)
    if event_type == "answer_submit" and not attempt:
        raise ValueError("answer_submit requires a valid Parsons attempt_id")
    attempt_student_id = _optional_string(attempt.get("student_id"))
    session_student_id = _optional_string(enforced_student_id)
    if session_student_id and attempt_student_id and attempt_student_id != session_student_id:
        raise PermissionError("attempt does not belong to current student")
    student_id = session_student_id or attempt_student_id or _optional_string(data.get("student_id"))
    if not student_id:
        raise ValueError("missing student_id")

    user = db.users.find_one(
        {"student_id": student_id},
        {"class_name": 1, "group_type": 1, "is_test_data": 1, "role": 1},
    ) or {}
    activity_type = _normalize_activity_type(
        attempt.get("activity_type") or data.get("activity_type")
    )
    test_role = _normalize_test_role(
        attempt.get("test_role") or data.get("test_role"),
        activity_type,
    )
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = None
    if event_type == "answer_submit":
        metadata = {
            "is_correct": attempt.get("is_correct"),
            "score": attempt.get("score"),
            "error_count": attempt.get("error_count"),
            "error_types": attempt.get("error_types") or [],
            "wrong_slots": _attempt_wrong_slots_for_log(attempt),
            "repeated_error": attempt.get("repeated_error"),
            "error_details": attempt.get("error_details") if isinstance(attempt.get("error_details"), list) else [],
            "repeated_error_types": attempt.get("repeated_error_types") if isinstance(attempt.get("repeated_error_types"), list) else [],
            "repeated_error_count": attempt.get("repeated_error_count") if isinstance(attempt.get("repeated_error_count"), int) else 0,
            "repeated_error_basis": attempt.get("repeated_error_basis"),
            "repeated_error_rule_version": attempt.get("repeated_error_rule_version"),
        }

    now = _utc_now()
    session_id = _optional_string(data.get("session_id")) or str(uuid.uuid4())
    task_id = _optional_string(attempt.get("task_id") or data.get("task_id"))
    if event_type in HINT_EVENT_TYPES:
        metadata = _hint_event_metadata(
            event_type,
            student_id,
            session_id,
            task_id,
            attempt_id,
            metadata,
        )
    event_at = _parse_event_at(data.get("event_at")) or now
    document = {
        "schema_version": SCHEMA_VERSION,
        "log_id": str(uuid.uuid4()),
        "session_id": session_id,
        "student_id": student_id,
        "user_id": _optional_string(data.get("user_id") or user.get("_id")),
        "class_name": _optional_string(user.get("class_name") or data.get("class_name")),
        "group_type": _optional_string(user.get("group_type")),
        "is_test_data": bool(
            student_id == TEST_STUDENT_ID
            or user.get("is_test_data") is True
            or (
                user.get("role") == "student"
                and not _optional_string(user.get("group_type"))
            )
        ),
        "event_type": event_type,
        "page": _optional_string(data.get("page")),
        "activity_type": activity_type,
        "test_role": test_role,
        "task_id": task_id,
        "unit_id": _optional_string(data.get("unit_id")),
        "from_video_id": _optional_string(data.get("from_video_id")),
        "from_video_title": _optional_string(data.get("from_video_title")),
        "watch_session_id": _optional_string(data.get("watch_session_id")),
        "to_task_id": _optional_string(data.get("to_task_id")),
        "to_question_type": _optional_string(data.get("to_question_type")),
        "question_type": _optional_string(data.get("question_type")),
        "attempt_id": attempt_id,
        "attempt_no": _optional_attempt_no(
            attempt.get("attempt_no")
            if attempt.get("attempt_no") is not None
            else data.get("attempt_no")
        ),
        "target_concept": _optional_string(
            attempt.get("target_concept") or data.get("target_concept")
        ),
        "event_at": event_at,
        "event_at_utc": event_at,
        "event_at_taiwan": _taiwan_time_string(event_at),
        "timezone": TIMEZONE_NAME,
        "metadata": metadata,
        "created_at": now,
        "created_at_utc": now,
        "created_at_taiwan": _taiwan_time_string(now),
        **HINT_TOP_LEVEL_DEFAULTS,
    }
    if event_type in HINT_EVENT_TYPES:
        document.update(_hint_top_level_fields(metadata, task_id))
    result = db.learning_logs.insert_one(document)
    document["_id"] = result.inserted_id
    return document


def write_or_update_hint_learning_log(payload, match_window_sec=15):
    data = payload if isinstance(payload, dict) else {}
    event_type = str(data.get("event_type") or "").strip()
    if event_type not in {"view_hint", "review_open"}:
        return write_learning_log(data)

    student_id = _optional_string(data.get("student_id"))
    session_id = _optional_string(data.get("session_id"))
    task_id = _optional_string(data.get("task_id"))
    attempt_id = _optional_string(data.get("attempt_id"))
    event_at = _parse_event_at(data.get("event_at")) or _utc_now()
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}

    query = {
        "student_id": student_id,
        "session_id": session_id,
        "task_id": task_id,
        "event_type": {"$in": ["view_hint", "review_open"]},
        "event_at": {
            "$gte": event_at - timedelta(seconds=max(1, int(match_window_sec or 15))),
            "$lte": event_at + timedelta(seconds=max(1, int(match_window_sec or 15))),
        },
    }
    if attempt_id:
        query["attempt_id"] = attempt_id
    trigger_method = _optional_string(metadata.get("trigger_method"))
    if trigger_method:
        query["metadata.trigger_method"] = trigger_method

    existing = None
    if student_id and session_id and task_id:
        existing = db.learning_logs.find_one(
            query,
            sort=[("event_at", -1), ("created_at", -1), ("_id", -1)],
        )

    if not existing:
        return write_learning_log(data)

    merged_metadata = dict(existing.get("metadata") or {})
    for key, value in metadata.items():
        if value is not None and value != "":
            merged_metadata[key] = _limited_metadata_value(value)
    merged_metadata["metadata_updated_at"] = _utc_now().isoformat()
    top_fields = _hint_top_level_fields(merged_metadata, task_id)
    update = {
        "metadata": merged_metadata,
        **top_fields,
    }

    document = db.learning_logs.find_one_and_update(
        {"_id": existing["_id"]},
        {"$set": update},
        return_document=ReturnDocument.AFTER,
    )
    return document or existing


def write_learning_log_safely(payload):
    try:
        return write_learning_log(payload)
    except Exception as exc:
        print(f"[learning_logs] write failed: {exc}")
        return None


def write_or_update_hint_learning_log_safely(payload):
    try:
        return write_or_update_hint_learning_log(payload)
    except Exception as exc:
        print(f"[learning_logs] hint write failed: {exc}")
        return None


def _limited_metadata_value(value, depth=0):
    if depth > 3:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        return text[:1500]
    if isinstance(value, list):
        return [_limited_metadata_value(item, depth + 1) for item in value[:12]]
    if isinstance(value, dict):
        limited = {}
        for key, item in list(value.items())[:30]:
            normalized_key = str(key or "").strip()[:80]
            if not normalized_key:
                continue
            limited[normalized_key] = _limited_metadata_value(item, depth + 1)
        return limited
    return str(value)[:500]


def _hint_metadata_patch(metadata):
    if not isinstance(metadata, dict):
        return {}
    metadata = _clean_hint_metadata_fields(metadata)
    patch = {}
    for key in HINT_METADATA_PATCH_KEYS:
        if key in metadata:
            patch[key] = _limited_metadata_value(metadata.get(key))
    return patch


@learning_logs_bp.post("")
def create_learning_log():
    payload = request.get_json(silent=True) or {}
    payload["student_id"] = current_student_id()
    try:
        document = write_learning_log(payload, enforced_student_id=current_student_id())
    except PermissionError:
        return jsonify({
            "ok": False,
            "error": "attempt_access_denied",
            "message": "不得記錄其他學生的作答事件。",
        }), 403
    except ValueError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "message": "learning log write failed", "detail": str(exc)}), 500
    return jsonify({
        "ok": True,
        "log_id": document["log_id"],
        "session_id": document["session_id"],
        "event_at": document["event_at"].isoformat(),
        "metadata": document["metadata"],
    })


@learning_logs_bp.patch("/<log_id>/metadata")
def update_learning_log_metadata(log_id):
    student_id = current_student_id()
    if not student_id:
        return jsonify({"ok": False, "message": "missing student_id"}), 401

    payload = request.get_json(silent=True) or {}
    patch = _hint_metadata_patch(payload.get("metadata"))
    if not patch:
        return jsonify({"ok": False, "message": "no allowed metadata fields"}), 400

    now = _utc_now()
    update = {f"metadata.{key}": value for key, value in patch.items()}
    update["metadata.metadata_updated_at"] = now.isoformat()
    if "question_id" in patch:
        update["question_id"] = _optional_string(patch.get("question_id"))
    if "unit_id" in patch:
        update["unit_id"] = _optional_string(patch.get("unit_id"))
    if "question_type" in patch:
        update["question_type"] = _optional_string(patch.get("question_type"))
    if "hint_type" in patch:
        update["hint_type"] = _optional_string(patch.get("hint_type"))
    hint_content = patch.get("hint_content") or patch.get("hint_text")
    if hint_content is not None:
        update["hint_content"] = _optional_string(hint_content)
    hint_click_no = patch.get("hint_click_no")
    if hint_click_no is not None:
        update["hint_click_no"] = _optional_attempt_no(hint_click_no)
    if "error_type" in patch:
        update["error_type"] = _optional_string(patch.get("error_type"))
    if "wrong_slots" in patch:
        update["wrong_slots"] = patch.get("wrong_slots") if isinstance(patch.get("wrong_slots"), list) else []
    top_fields = _hint_top_level_fields(patch, None, include_task_fallback=False)
    for key in (
        "hint_id",
        "review_type",
        "requested_hint_no",
        "hint_no",
        "max_hint_count",
        "ai_hint_generation_count",
        "ai_hint_view_count",
        "hint_limit_reached",
        "hint_retry_count",
        "next_hint_no",
        "first_system_hint_text",
        "ai_hint_1_text",
        "ai_hint_2_text",
        "hint_text",
        "hint_source",
        "hint_loaded",
        "hint_error",
        "trigger_method",
        "button_name",
        "close_method",
        "return_method",
        "error_types",
        "repeated_error",
        "ai_diagnosis_summary",
    ):
        if key in patch:
            update[key] = top_fields.get(key)
    document = db.learning_logs.find_one_and_update(
        {
            "log_id": str(log_id or "").strip(),
            "student_id": student_id,
            "event_type": {"$in": list(HINT_EVENT_TYPES)},
        },
        {"$set": update},
        projection={"metadata": 1, "log_id": 1},
        return_document=ReturnDocument.AFTER,
    )
    if not document:
        return jsonify({"ok": False, "message": "learning log not found"}), 404
    return jsonify({
        "ok": True,
        "log_id": document.get("log_id"),
        "metadata": document.get("metadata") or {},
    })
