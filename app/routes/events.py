# app/routes/events.py
from flask import Blueprint, request, jsonify
from bson import ObjectId
from datetime import datetime, timezone
from app.db import db

events_bp = Blueprint("events", __name__)

def _utc_now():
    return datetime.now(timezone.utc)

def _oid(x: str):
    try:
        return ObjectId(x)
    except Exception:
        return None

@events_bp.post("/log")
def log_event():
    """
    JSON:
      type: "jump_to_segment" | "submit_parsons" | "view_answer" ...
      participant_id
      video_id(optional)
      meta(optional)
    """
    data = request.get_json(silent=True) or {}
    etype = (data.get("type") or "").strip()
    participant_id = (data.get("participant_id") or "").strip()
    video_id = (data.get("video_id") or "").strip()
    meta = data.get("meta") or {}

    if not etype:
        return jsonify({"ok": False, "message": "missing type"}), 400

    vid = _oid(video_id) if video_id else None

    db.events.insert_one({
        "type": etype,
        "participant_id": participant_id or None,
        "video_id": vid,
        "meta": meta,
        "created_at": _utc_now()
    })
    return jsonify({"ok": True})
