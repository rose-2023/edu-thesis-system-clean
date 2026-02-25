# app/routes/teacher_dashboard.py
from flask import Blueprint, request, jsonify
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from ..db import db


teacher_dashboard_bp = Blueprint("teacher_dashboard", __name__)

def _utc_now():
    return datetime.now(timezone.utc)

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
            "name": "老師",
            "role": "admin"
        }
        
        # 計算週期
        if range_param == "week":
            since = _utc_now() - timedelta(days=7)
        elif range_param == "month":
            since = _utc_now() - timedelta(days=30)
        else:
            since = _utc_now() - timedelta(days=7)
        
        # 影片統計
        total_videos = db.videos.count_documents({})
        enabled_videos = db.videos.count_documents({"enabled": True})
        subtitle_uploaded = db.videos.count_documents({"subtitle_uploaded": True})
        subtitle_verified = db.videos.count_documents({"subtitle_verified": True})
        
        # 練習統計
        total_tasks = db.parsons_tasks.count_documents({}) if "parsons_tasks" in db.list_collection_names() else 0
        enabled_tasks = db.parsons_tasks.count_documents({"enabled": True}) if total_tasks else 0
        
        # Overview 統計
        overview = {
            "weekly_sessions": 12,  # 這裡可以改成實際查詢
            "avg_accuracy": 78.5,
            "top_misconceptions": [
                "float_vs_int",
                "need_2dp",
                "loop_condition"
            ]
        }
        
        # 單元列表
        units_data = []
        units_list = db.videos.distinct("unit", {"deleted": {"$ne": True}})
        for unit in sorted([u for u in units_list if u]):
            videos_in_unit = db.videos.count_documents({"unit": unit})
            practices_in_unit = db.parsons_tasks.count_documents({"unit": unit}) if "parsons_tasks" in db.list_collection_names() else 0
            units_data.append({
                "unit": unit,
                "title": f"{unit} 單元",
                "videos_count": videos_in_unit,
                "practices_count": practices_in_unit
            })
        
        return jsonify({
            "ok": True,
            "teacher": teacher,
            "overview": overview,
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

