from datetime import datetime, timezone, timedelta

from bson import ObjectId
from flask import Blueprint, g, jsonify, request
from pymongo.errors import PyMongoError

from ..db import db
from ..session_auth import current_student_id


video_rewatch_logs_bp = Blueprint("video_rewatch_logs", __name__)

SCHEMA_VERSION = 1
TIMEZONE_NAME = "Asia/Taipei"
TAIPEI_TZ = timezone(timedelta(hours=8))
ALLOWED_EVENT_TYPES = {
    "video_click",
    "video_play",
    "video_pause",
    "video_progress",
    "video_ended",
    "video_leave",
}

_INDEX_READY = False


def _utc_now():
    return datetime.now(timezone.utc)


def _taiwan_time_string(value):
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _optional_string(value):
    text = str(value or "").strip()
    return text or None


def _optional_float(value):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number < 0:
        return None
    return round(number, 3)


def _parse_datetime(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _video_lookup(video_id):
    video_id_text = _optional_string(video_id)
    if not video_id_text:
        return None

    query = [{"_id": video_id_text}, {"video_id": video_id_text}, {"video_id_str": video_id_text}]
    if ObjectId.is_valid(video_id_text):
        oid = ObjectId(video_id_text)
        query.extend([{"_id": oid}, {"video_id": oid}])
    return db.videos.find_one({"$or": query}, {"unit": 1, "title": 1, "filename": 1, "path": 1})


def _ensure_indexes():
    global _INDEX_READY
    if _INDEX_READY:
        return
    try:
        db.video_rewatch_logs.create_index([("student_id", 1), ("event_at", -1)], name="student_event_at_1")
        db.video_rewatch_logs.create_index([("video_id", 1), ("event_at", -1)], name="video_event_at_1")
        db.video_rewatch_logs.create_index([("watch_session_id", 1), ("event_at", 1)], name="watch_session_event_at_1")
        db.video_rewatch_logs.create_index([("unit_id", 1), ("event_at", -1)], name="unit_event_at_1")
        db.video_rewatch_logs.create_index([("group_type", 1), ("event_at", -1)], name="group_event_at_1")
        db.video_rewatch_logs.create_index([("event_type", 1), ("event_at", -1)], name="event_type_event_at_1")
        _INDEX_READY = True
    except PyMongoError:
        # Index creation should not block learning behavior logging.
        return


@video_rewatch_logs_bp.post("")
def create_video_rewatch_log():
    student_id = current_student_id()
    if not student_id:
        return jsonify({"ok": False, "message": "missing student session"}), 401

    data = request.get_json(silent=True) or {}
    event_type = str(data.get("event_type") or "").strip()
    if event_type not in ALLOWED_EVENT_TYPES:
        return jsonify({
            "ok": False,
            "error": "invalid_event_type",
            "message": "video_rewatch_logs event_type is not allowed",
        }), 400

    video_id = _optional_string(data.get("video_id"))
    if not video_id:
        return jsonify({"ok": False, "message": "missing video_id"}), 400

    user = getattr(g, "current_user", None) or db.users.find_one(
        {"student_id": student_id},
        {"class_name": 1, "group_type": 1, "is_test_data": 1, "role": 1},
    ) or {}

    try:
        video = _video_lookup(video_id) or {}
    except PyMongoError:
        video = {}

    now = _utc_now()
    event_at = _parse_datetime(data.get("event_at")) or now
    watch_seconds = _optional_float(data.get("watch_seconds"))
    watch_delta_sec = _optional_float(data.get("watch_delta_sec"))

    unit_value = _optional_string(data.get("unit_id")) or _optional_string(data.get("unit")) or video.get("unit")

    doc = {
        "schema_version": SCHEMA_VERSION,
        "student_id": student_id,
        "class_name": user.get("class_name") or None,
        "group_type": user.get("group_type") or None,
        "is_test_data": bool(
            user.get("is_test_data") is True
            or (
                user.get("role") == "student"
                and not _optional_string(user.get("group_type"))
            )
        ),
        "event_type": event_type,
        "video_id": video_id,
        "watch_session_id": _optional_string(data.get("watch_session_id")),
        "video_title": _optional_string(data.get("video_title")) or video.get("title") or video.get("filename"),
        "unit_id": unit_value,
        "unit": unit_value,
        "watch_seconds": watch_seconds,
        "watch_delta_sec": watch_delta_sec,
        "current_time_sec": _optional_float(data.get("current_time_sec")),
        "video_duration_sec": _optional_float(data.get("video_duration_sec")),
        "playback_rate": _optional_float(data.get("playback_rate")),
        "reached_end": bool(data.get("reached_end") is True),
        "watch_start_at": _parse_datetime(data.get("watch_start_at")),
        "watch_end_at": _parse_datetime(data.get("watch_end_at")) or event_at,
        "page": _optional_string(data.get("page")) or "student_learning",
        "source": _optional_string(data.get("source")) or "StudentLearning.vue",
        "route_path": _optional_string(data.get("route_path")),
        "attempt_id": _optional_string(data.get("attempt_id")),
        "task_id": _optional_string(data.get("task_id")),
        "segment_start_sec": _optional_float(data.get("start_sec")),
        "segment_end_sec": _optional_float(data.get("end_sec")),
        "event_at": event_at,
        "event_at_utc": event_at,
        "event_at_taiwan": _taiwan_time_string(event_at),
        "timezone": TIMEZONE_NAME,
        "created_at": now,
        "created_at_utc": now,
        "created_at_taiwan": _taiwan_time_string(now),
        "updated_at": now,
        "updated_at_utc": now,
        "updated_at_taiwan": _taiwan_time_string(now),
    }

    try:
        _ensure_indexes()
        result = db.video_rewatch_logs.insert_one(doc)
    except PyMongoError:
        return jsonify({
            "ok": False,
            "error": "video_rewatch_log_write_failed",
            "message": "影片觀看紀錄寫入失敗，請稍後再試。",
        }), 503

    return jsonify({
        "ok": True,
        "log_id": str(result.inserted_id),
        "event_type": event_type,
        "student_id": student_id,
        "group_type": doc["group_type"],
        "video_id": video_id,
    })
