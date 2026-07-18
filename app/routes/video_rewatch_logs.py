"""影片觀看、時間軸拖曳與續播位置紀錄。"""

from datetime import datetime, timezone, timedelta
from math import isfinite

from bson import ObjectId
from flask import Blueprint, g, jsonify, request
from pymongo.errors import PyMongoError

from ..db import db
from ..session_auth import current_student_id


video_rewatch_logs_bp = Blueprint("video_rewatch_logs", __name__)


SCHEMA_VERSION = 3
TIMEZONE_NAME = "Asia/Taipei"
TAIPEI_TZ = timezone(timedelta(hours=8))

# video_progress 保留以相容舊版前端；新版前端已改為事件式紀錄，不會固定送出。
ALLOWED_EVENT_TYPES = {
    "video_click",
    "video_play",
    "video_pause",
    "video_progress",
    "video_seek",
    "video_ended",
    "video_leave",
    "video_rate_change",
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
    """處理不得為負數的有限數值，例如秒數、影片位置與播放速度。"""
    if value is None or value == "":
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not isfinite(number) or number < 0:
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

    query = [
        {"_id": video_id_text},
        {"video_id": video_id_text},
        {"video_id_str": video_id_text},
    ]

    if ObjectId.is_valid(video_id_text):
        oid = ObjectId(video_id_text)
        query.extend([{"_id": oid}, {"video_id": oid}])

    return db.videos.find_one(
        {"$or": query},
        {"unit": 1, "title": 1, "filename": 1, "path": 1},
    )


def _ensure_indexes():
    global _INDEX_READY

    if _INDEX_READY:
        return

    try:
        db.video_rewatch_logs.create_index(
            [("student_id", 1), ("event_at", -1)],
            name="student_event_at_1",
        )
        db.video_rewatch_logs.create_index(
            [("video_id", 1), ("event_at", -1)],
            name="video_event_at_1",
        )
        db.video_rewatch_logs.create_index(
            [("watch_session_id", 1), ("event_at", 1)],
            name="watch_session_event_at_1",
        )
        db.video_rewatch_logs.create_index(
            [("unit_id", 1), ("event_at", -1)],
            name="unit_event_at_1",
        )
        db.video_rewatch_logs.create_index(
            [("group_type", 1), ("event_at", -1)],
            name="group_event_at_1",
        )
        db.video_rewatch_logs.create_index(
            [("event_type", 1), ("event_at", -1)],
            name="event_type_event_at_1",
        )

        # 取得單一學生、單一影片最近續播位置所需的複合索引。
        db.video_rewatch_logs.create_index(
            [("student_id", 1), ("video_id", 1), ("event_at", -1)],
            name="student_video_event_at_1",
        )

        _INDEX_READY = True
    except PyMongoError:
        # 索引建立失敗不應阻止學習行為紀錄。
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

    unit_value = (
        _optional_string(data.get("unit_id"))
        or _optional_string(data.get("unit"))
        or video.get("unit")
    )

    # 新版前端會傳入 playback_rate 子物件；同時相容舊版的數字格式。
    playback_rate_payload = data.get("playback_rate")
    if isinstance(playback_rate_payload, dict):
        playback_rate_current = _optional_float(playback_rate_payload.get("current"))
        playback_rate_from = _optional_float(playback_rate_payload.get("from"))
        playback_rate_to = _optional_float(playback_rate_payload.get("to"))
    else:
        playback_rate_current = _optional_float(playback_rate_payload)
        playback_rate_from = _optional_float(data.get("playback_rate_from"))
        playback_rate_to = _optional_float(data.get("playback_rate_to"))

    playback_rate_direction = None
    if event_type == "video_rate_change":
        if (
            playback_rate_from is None
            or playback_rate_to is None
            or playback_rate_from <= 0
            or playback_rate_to <= 0
        ):
            return jsonify({
                "ok": False,
                "error": "invalid_playback_rate_data",
                "message": "video_rate_change 必須包含有效的播放倍速 from 與 to",
            }), 400

        # ratechange 發生後的 current 應等於 to；由後端統一校正。
        playback_rate_current = playback_rate_to
        if playback_rate_to > playback_rate_from:
            playback_rate_direction = "faster"
        elif playback_rate_to < playback_rate_from:
            playback_rate_direction = "slower"
        else:
            playback_rate_direction = "unchanged"
    else:
        # 只有倍速變更事件需要 from、to 與 direction。
        playback_rate_from = None
        playback_rate_to = None

    # 只有 video_seek 才處理時間軸拖曳欄位。
    seek_from_sec = None
    seek_to_sec = None
    seek_delta_sec = None
    seek_direction = None
    is_backward_seek = False

    if event_type == "video_seek":
        seek_from_sec = _optional_float(data.get("seek_from_sec"))
        seek_to_sec = _optional_float(data.get("seek_to_sec"))

        if seek_from_sec is None or seek_to_sec is None:
            return jsonify({
                "ok": False,
                "error": "invalid_seek_data",
                "message": "video_seek 必須包含有效的 seek_from_sec 與 seek_to_sec",
            }), 400

        # 不採信前端傳入的差值，由後端依兩個位置重新計算。
        seek_delta_sec = round(seek_to_sec - seek_from_sec, 3)

        # 至少移動 5 秒才視為明確的快轉或倒退，避免播放器微小誤差。
        if seek_delta_sec <= -5:
            seek_direction = "backward"
            is_backward_seek = True
        elif seek_delta_sec >= 5:
            seek_direction = "forward"
        else:
            seek_direction = "minor_adjustment"

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
        "video_title": (
            _optional_string(data.get("video_title"))
            or video.get("title")
            or video.get("filename")
        ),
        # 倒帶觀看紀錄不應該覆蓋完整影片的進度，避免學生刻意倒帶重看影片以獲得更高的續播位置。
        "seek_from_sec": seek_from_sec,
        "seek_to_sec": seek_to_sec,
        "seek_delta_sec": seek_delta_sec,
        "seek_direction": seek_direction,
        "is_backward_seek": is_backward_seek,
        "unit_id": unit_value,
        "unit": unit_value,
        "watch_seconds": watch_seconds,
        "watch_delta_sec": watch_delta_sec,
        "current_time_sec": _optional_float(data.get("current_time_sec")),
        "video_duration_sec": _optional_float(data.get("video_duration_sec")),
        "playback_rate": {
            "current": playback_rate_current,
            "from": playback_rate_from,
            "to": playback_rate_to,
            "direction": playback_rate_direction,
        },
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


@video_rewatch_logs_bp.get("/resume")
def get_video_resume_position():
    """讀取目前學生對指定影片最後一次有效的完整影片觀看位置。"""
    student_id = current_student_id()
    if not student_id:
        return jsonify({"ok": False, "message": "missing student session"}), 401

    video_id = _optional_string(request.args.get("video_id"))
    if not video_id:
        return jsonify({"ok": False, "message": "missing video_id"}), 400

    try:
        _ensure_indexes()
        latest = db.video_rewatch_logs.find_one(
            {
                "student_id": student_id,
                "video_id": video_id,
                # 回看錯誤片段的紀錄不可覆蓋完整教學影片的續播位置。
                # MongoDB 的 null 條件同時會匹配欄位不存在的舊資料。
                "segment_start_sec": None,
                # 排除剛點選影片所產生的 current_time_sec=0 紀錄。
                "event_type": {
                    "$in": [
                        "video_progress",
                        "video_pause",
                        "video_seek",
                        "video_rate_change",
                        "video_leave",
                        "video_ended",
                    ]
                },
                "current_time_sec": {"$gt": 0},
            },
            {
                "event_type": 1,
                "current_time_sec": 1,
                "video_duration_sec": 1,
                "reached_end": 1,
                "event_at": 1,
            },
            sort=[("event_at", -1)],
        )
    except PyMongoError:
        return jsonify({
            "ok": False,
            "error": "video_resume_read_failed",
            "message": "影片續播位置讀取失敗，請稍後再試。",
        }), 503

    if not latest:
        return jsonify({
            "ok": True,
            "video_id": video_id,
            "resume_sec": 0,
            "completed": False,
        })

    current_time = _optional_float(latest.get("current_time_sec")) or 0
    duration = _optional_float(latest.get("video_duration_sec"))
    completed = bool(
        latest.get("event_type") == "video_ended"
        or latest.get("reached_end") is True
        or (
            duration is not None
            and duration > 0
            and current_time >= duration - 3
        )
    )

    event_at = latest.get("event_at")
    return jsonify({
        "ok": True,
        "video_id": video_id,
        "resume_sec": 0 if completed else current_time,
        "completed": completed,
        "last_event_type": latest.get("event_type"),
        "last_event_at": event_at.isoformat() if isinstance(event_at, datetime) else None,
    })
