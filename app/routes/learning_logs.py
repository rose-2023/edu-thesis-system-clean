import uuid
from datetime import datetime, timezone

from bson import ObjectId
from flask import Blueprint, jsonify, request

from ..db import db
from ..session_auth import current_student_id


learning_logs_bp = Blueprint("learning_logs", __name__)

SCHEMA_VERSION = 1
TIMEZONE_NAME = "Asia/Taipei"
TEST_STUDENT_ID = "11461127"
MAX_HINT_COUNT = 2
ALLOWED_EVENT_TYPES = {
    "session_start",
    "session_end",
    "page_view",
    "task_open",
    "task_start",
    "answer_submit",
    "review_open",
    "review_close",
    "return_to_task",
    "idle_detected",
    "heartbeat",
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


def _attempt_context(attempt_id):
    normalized = _optional_string(attempt_id)
    if not normalized or not ObjectId.is_valid(normalized):
        return {}
    return db.parsons_attempts_v2.find_one(
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
            "wrong_slots": 1,
            "repeated_error": 1,
        },
    ) or {}


def _hint_event_metadata(event_type, student_id, session_id, task_id, metadata):
    normalized = dict(metadata or {})
    normalized["review_type"] = "ai_hint"
    normalized["max_hint_count"] = MAX_HINT_COUNT
    query = {
        "student_id": student_id,
        "session_id": session_id,
        "task_id": task_id,
        "event_type": "review_open",
        "metadata.review_type": "ai_hint",
        "metadata.hint_limit_reached": {"$ne": True},
    }

    if event_type == "review_open":
        opened_count = db.learning_logs.count_documents(query)
        normalized["hint_no"] = min(opened_count + 1, MAX_HINT_COUNT)
        if opened_count >= MAX_HINT_COUNT:
            normalized["hint_limit_reached"] = True
        else:
            normalized.pop("hint_limit_reached", None)
        return normalized

    latest_open = db.learning_logs.find_one(
        query,
        {"metadata.hint_no": 1},
        sort=[("event_at", -1), ("created_at", -1), ("_id", -1)],
    ) or {}
    latest_hint_no = (latest_open.get("metadata") or {}).get("hint_no")
    try:
        latest_hint_no = int(latest_hint_no)
    except Exception:
        latest_hint_no = None
    if latest_hint_no not in range(1, MAX_HINT_COUNT + 1):
        opened_count = db.learning_logs.count_documents(query)
        latest_hint_no = min(max(opened_count, 1), MAX_HINT_COUNT)
    normalized["hint_no"] = latest_hint_no
    normalized.pop("hint_limit_reached", None)
    return normalized


def write_learning_log(payload, enforced_student_id=None):
    data = payload if isinstance(payload, dict) else {}
    event_type = str(data.get("event_type") or "").strip()
    if event_type not in ALLOWED_EVENT_TYPES:
        raise ValueError("unsupported event_type")

    attempt_id = _optional_string(data.get("attempt_id"))
    attempt = _attempt_context(attempt_id)
    if event_type == "answer_submit" and not attempt:
        raise ValueError("answer_submit requires a valid parsons_attempts_v2 attempt_id")
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
            "wrong_slots": attempt.get("wrong_slots") or [],
            "repeated_error": attempt.get("repeated_error"),
        }

    now = _utc_now()
    session_id = _optional_string(data.get("session_id")) or str(uuid.uuid4())
    task_id = _optional_string(attempt.get("task_id") or data.get("task_id"))
    if event_type in {"review_open", "review_close"}:
        metadata = _hint_event_metadata(
            event_type,
            student_id,
            session_id,
            task_id,
            metadata,
        )
    document = {
        "schema_version": SCHEMA_VERSION,
        "log_id": str(uuid.uuid4()),
        "session_id": session_id,
        "student_id": student_id,
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
        "attempt_id": attempt_id,
        "attempt_no": _optional_attempt_no(
            attempt.get("attempt_no")
            if attempt.get("attempt_no") is not None
            else data.get("attempt_no")
        ),
        "target_concept": _optional_string(
            attempt.get("target_concept") or data.get("target_concept")
        ),
        "event_at": _parse_event_at(data.get("event_at")) or now,
        "timezone": TIMEZONE_NAME,
        "metadata": metadata,
        "created_at": now,
    }
    result = db.learning_logs.insert_one(document)
    document["_id"] = result.inserted_id
    return document


def write_learning_log_safely(payload):
    try:
        return write_learning_log(payload)
    except Exception as exc:
        print(f"[learning_logs] write failed: {exc}")
        return None


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
