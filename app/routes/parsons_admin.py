from flask import Blueprint, request, jsonify
from bson import ObjectId
from datetime import datetime, timezone
from ..db import db

parsons_admin_bp = Blueprint("parsons_admin", __name__)

def _utc_now():
    return datetime.now(timezone.utc)

def _oid(x: str):
    try:
        return ObjectId(x)
    except Exception:
        return None

@parsons_admin_bp.get("/tasks")
def list_tasks():
    video_id = (request.args.get("video_id") or "").strip()
    q = {}
    if video_id:
        vid = _oid(video_id)
        if not vid:
            return jsonify({"ok": False, "message": "invalid video_id"}), 400
        q["$or"] = [{"video_id": vid}, {"video_id": str(vid)}]

    out = []
    for t in db.parsons_tasks.find(q).sort([("created_at", -1)]):
        out.append({
            "id": str(t["_id"]),
            "video_id": str(t.get("video_id")) if t.get("video_id") else None,
            "title": t.get("title", ""),
            "level": t.get("level", "L2"),
            "enabled": t.get("enabled", True),
            "created_at": t.get("created_at"),
        })
    return jsonify({"ok": True, "tasks": out})

@parsons_admin_bp.post("/set_enabled")
def set_enabled():
    data = request.get_json(silent=True) or {}
    tid = _oid((data.get("task_id") or "").strip())
    if not tid:
        return jsonify({"ok": False, "message": "invalid task_id"}), 400

    enabled = bool(data.get("enabled", True))
    db.parsons_tasks.update_one({"_id": tid}, {"$set": {"enabled": enabled, "updated_at": _utc_now()}})
    return jsonify({"ok": True})
