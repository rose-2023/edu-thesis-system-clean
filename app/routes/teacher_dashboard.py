# app/routes/teacher_dashboard.py
from flask import Blueprint, request, jsonify
from datetime import datetime, time, timezone, timedelta
from bson import ObjectId
from ..db import db
from ..unit_labels import unit_label_map

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - older Python fallback
    ZoneInfo = None


teacher_dashboard_bp = Blueprint("teacher_dashboard", __name__)
FORMAL_GROUP_TYPES = ("control", "experimental_1", "experimental_2")


def _taipei_timezone():
    if ZoneInfo:
        try:
            return ZoneInfo("Asia/Taipei")
        except Exception:
            pass
    return timezone(timedelta(hours=8))


TAIPEI_TZ = _taipei_timezone()

def _utc_now():
    return datetime.now(timezone.utc)


def _safe_iso(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else ""


def _date_input(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _date_range_from_request(range_param):
    start_date = _date_input(request.args.get("start_date"))
    end_date = _date_input(request.args.get("end_date"))
    today_taipei = _utc_now().astimezone(TAIPEI_TZ).date()

    if not start_date and not end_date:
        days = 30 if range_param == "month" else 7
        end_date = today_taipei
        start_date = today_taipei - timedelta(days=days - 1)
    elif start_date and not end_date:
        end_date = start_date
    elif end_date and not start_date:
        start_date = end_date

    if end_date < start_date:
        start_date, end_date = end_date, start_date

    start_local = datetime.combine(start_date, time.min, tzinfo=TAIPEI_TZ)
    end_local_exclusive = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=TAIPEI_TZ)
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "start_utc": start_local.astimezone(timezone.utc),
        "end_utc_exclusive": end_local_exclusive.astimezone(timezone.utc),
    }


def _formal_data_query():
    return {
        "is_test_data": {"$ne": True},
        "$or": [
            {"group_type": {"$in": list(FORMAL_GROUP_TYPES)}},
            {"analysis_group_type": {"$in": list(FORMAL_GROUP_TYPES)}},
        ],
    }


def _time_query(field, date_range):
    return {
        field: {
            "$gte": date_range["start_utc"],
            "$lt": date_range["end_utc_exclusive"],
        }
    }


def _learning_students_count(date_range):
    if "learning_logs" not in db.list_collection_names():
        return 0
    query = {
        **_formal_data_query(),
        **_time_query("event_at", date_range),
        "student_id": {"$nin": [None, ""]},
    }
    return len(db.learning_logs.distinct("student_id", query))


def _collection_exists(name):
    return name in db.list_collection_names()


def _count_not_deleted(collection_name, extra_query=None):
    if not _collection_exists(collection_name):
        return 0
    query = {"deleted": {"$ne": True}}
    if extra_query:
        query.update(extra_query)
    return db[collection_name].count_documents(query)


def _distinct_units():
    units = set()
    for collection_name in ("videos", "parsons_tasks", "units"):
        if not _collection_exists(collection_name):
            continue
        for value in db[collection_name].distinct("unit", {"deleted": {"$ne": True}}):
            text = str(value or "").strip()
            if text:
                units.add(text)
    return sorted(units)


def _legacy_test_task_count(test_role):
    if not _collection_exists("parsons_test_tasks"):
        return 0
    task_ids = {
        str(value)
        for value in db.parsons_test_tasks.distinct("test_task_id", {"test_role": test_role})
        if value
    }
    source_ids = {
        str(value)
        for value in db.parsons_test_tasks.distinct("source_task_id", {"test_role": test_role})
        if value
    }
    return len(task_ids | source_ids)


def _test_question_count(collection_name, legacy_role):
    count = _count_not_deleted(collection_name)
    return count if count else _legacy_test_task_count(legacy_role)


def _task_preview_for_unit(unit: str, limit: int = 5):
    if not _collection_exists("parsons_tasks"):
        return []

    query = {
        "unit": unit,
        "deleted": {"$ne": True},
    }
    cursor = db.parsons_tasks.find(query).sort([("created_at", -1)]).limit(max(1, int(limit or 5)))
    preview = []
    for t in cursor:
        source_type = (
            (t.get("source_type") or "").strip().lower()
            or (t.get("gen_source") or "").strip().lower()
            or ("ai" if bool(t.get("ai_generated")) else "fixed")
        )
        raw_status = (
            (t.get("status") or "").strip().lower()
            or (t.get("review_status") or "").strip().lower()
            or ("published" if bool(t.get("enabled", False)) else "pending")
        )
        preview.append({
            "task_id": str(t.get("_id")),
            "task_code": t.get("task_code", ""),
            "title": t.get("title") or t.get("question_text") or t.get("video_title") or "",
            "status": raw_status,
            "enabled": bool(t.get("enabled", False)),
            "source_type": source_type,
            "created_at": _safe_iso(t.get("created_at")),
        })
    return preview


@teacher_dashboard_bp.get("")
def dashboard():
    """
    GET /api/teacher_dashboard
    返回整個 Dashboard 的數據
    """
    range_param = request.args.get("range", "week")
    
    # 基本統計
    try:
        teacher = {
            "name": "老師/管理員",
            "role": "admin"
        }
        
        date_range = _date_range_from_request(range_param)
        
        # 影片統計
        total_videos = _count_not_deleted("videos")
        enabled_videos = _count_not_deleted("videos", {"enabled": True})
        subtitle_uploaded = _count_not_deleted("videos", {"subtitle_uploaded": True})
        subtitle_verified = _count_not_deleted("videos", {"subtitle_verified": True})
        
        # 練習統計
        total_tasks = _count_not_deleted("parsons_tasks")
        enabled_tasks = _count_not_deleted("parsons_tasks", {"enabled": True})
        pretest_question_count = _test_question_count("pre_parsons_questions", "pre")
        posttest_question_count = _test_question_count("post_parsons_questions", "post")
        units_list = _distinct_units()
        labels = unit_label_map(units_list)
        resource_counts = {
            "unit_count": len(units_list),
            "video_count": total_videos,
            "practice_task_count": total_tasks,
            "pretest_question_count": pretest_question_count,
            "posttest_question_count": posttest_question_count,
        }
        
        active_learners = _learning_students_count(date_range)

        # Overview 統計：learning_logs 用來計算期間學習人數。
        # 正確率與常見錯誤概念已移到 Parsons 學習分析頁。
        overview = {
            "weekly_sessions": active_learners,
            "active_learners": active_learners,
        }
        
        # 單元列表
        units_data = []
        for unit in units_list:
            videos_in_unit = _count_not_deleted("videos", {"unit": unit})
            practices_in_unit = _count_not_deleted("parsons_tasks", {"unit": unit})
            units_data.append({
                "unit": unit,
                "raw_name": unit,
                "name": labels.get(unit) or unit,
                "unit_label": labels.get(unit) or unit,
                "title": labels.get(unit) or f"{unit} 單元",
                "videos_count": videos_in_unit,
                "practices_count": practices_in_unit,
                "task_preview": _task_preview_for_unit(unit, limit=5),
            })
        
        return jsonify({
            "ok": True,
            "teacher": teacher,
            "overview": overview,
            "date_range": {
                "start_date": date_range["start_date"],
                "end_date": date_range["end_date"],
                "timezone": "Asia/Taipei",
            },
            "videos": {
                "total": total_videos,
                "enabled": enabled_videos,
                "subtitle_uploaded": subtitle_uploaded,
                "subtitle_verified": subtitle_verified
            },
            "parsons": {
                "total_tasks": total_tasks,
                "enabled_tasks": enabled_tasks,
                "pretest_question_count": pretest_question_count,
                "posttest_question_count": posttest_question_count,
            },
            "resource_counts": resource_counts,
            "units": units_data
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500

@teacher_dashboard_bp.get("/summary")
def summary():
    """
    GET /api/teacher_dashboard/summary
    回傳：影片狀態、字幕狀態、題庫狀態、最近事件/流線摘要
    """
    total_videos = db.videos.count_documents({})
    enabled_videos = db.videos.count_documents({"enabled": True})
    subtitle_uploaded = db.videos.count_documents({"subtitle_uploaded": True})
    subtitle_verified = db.videos.count_documents({"subtitle_verified": True})

    total_tasks = db.parsons_tasks.count_documents({}) if "parsons_tasks" in db.list_collection_names() else 0
    enabled_tasks = db.parsons_tasks.count_documents({"enabled": True}) if total_tasks else 0

    # 最近 7 天事件（若你有 events collection）
    since = _utc_now() - timedelta(days=7)
    recent_events = []
    if "events" in db.list_collection_names():
        cur = db.events.find({"created_at": {"$gte": since}}).sort([("created_at", -1)]).limit(20)
        for e in cur:
            recent_events.append({
                "type": e.get("type"),
                "video_id": str(e.get("video_id")) if e.get("video_id") else None,
                "participant_id": e.get("participant_id"),
                "meta": e.get("meta", {}),
                "created_at": e.get("created_at")
            })

    return jsonify({
        "ok": True,
        "videos": {
            "total": total_videos,
            "enabled": enabled_videos,
            "subtitle_uploaded": subtitle_uploaded,
            "subtitle_verified": subtitle_verified
        },
        "parsons": {
            "total_tasks": total_tasks,
            "enabled_tasks": enabled_tasks
        },
        "recent_events": recent_events
    })

@teacher_dashboard_bp.get("/agent_logs")
def agent_logs():
    rows = []
    if "teacher_agent_logs" in db.list_collection_names():
        cur = db.teacher_agent_logs.find({}).sort([("created_at", -1)]).limit(50)
        for r in cur:
            rows.append({
                "type": r.get("type"),
                "video_id": str(r.get("video_id")) if r.get("video_id") else None,
                "level": r.get("level"),
                "status": r.get("status"),
                "created_at": r.get("created_at"),
                "detail": r.get("detail", {})
            })
    return jsonify({"ok": True, "logs": rows})

@teacher_dashboard_bp.post("/units")
def create_unit():
    """
    POST /api/teacher_dashboard/units
    建立新單元
    Body: { "unit": "U1", "title": "單元名稱", "description": "描述" }
    """
    data = request.get_json(silent=True) or {}
    unit = (data.get("unit") or "").strip()
    title = (data.get("title") or "").strip()
    
    if not unit:
        return jsonify({"ok": False, "message": "單元代碼不可為空"}), 400
    if not title:
        return jsonify({"ok": False, "message": "單元名稱不可為空"}), 400
    
    # 檢查單元是否已存在
    existing = db.videos.find_one({"unit": unit})
    if existing:
        return jsonify({"ok": False, "message": "單元已存在"}), 400
    
    # 新增單元（通過在 videos 中插入一筆標記記錄）
    db.units.insert_one({
        "unit": unit,
        "title": title,
        "description": data.get("description") or "",
        "created_at": _utc_now(),
        "enabled": True
    })
    
    return jsonify({
        "ok": True,
        "message": "單元建立成功",
        "unit": unit,
        "title": title
    })

