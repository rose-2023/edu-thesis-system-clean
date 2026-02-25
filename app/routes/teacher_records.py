# app/routes/records.py
from flask import Blueprint, request, jsonify
from datetime import datetime, timezone
from app.db import db

records_bp = Blueprint("records", __name__)

def _utc_now():
    return datetime.now(timezone.utc)

@records_bp.get("/students")
def list_students():
    """
    GET /api/records/students?class_id=...&page=1&page_size=15
    你可以把 class_id 當作班級代碼
    """
    class_id = (request.args.get("class_id") or "").strip()
    page = int(request.args.get("page") or 1)
    page_size = int(request.args.get("page_size") or 15)
    page = max(page, 1)
    page_size = 15 if page_size <= 0 else min(page_size, 50)

    q = {}
    if class_id:
        q["class_id"] = class_id

    total = db.participants.count_documents(q) if "participants" in db.list_collection_names() else 0
    rows = []
    if total:
        cur = db.participants.find(q).sort([("student_id", 1)]).skip((page-1)*page_size).limit(page_size)
        for p in cur:
            rows.append({
                "participant_id": p.get("participant_id"),
                "student_id": p.get("student_id"),
                "name": p.get("name"),
                "class_id": p.get("class_id")
            })

    return jsonify({"ok": True, "total": total, "page": page, "page_size": page_size, "students": rows})

@records_bp.get("/learning_events")
def learning_events():
    """
    依學生查事件/流線
    GET /api/records/learning_events?participant_id=...&limit=50
    """
    pid = (request.args.get("participant_id") or "").strip()
    limit = int(request.args.get("limit") or 50)
    limit = min(max(limit, 1), 200)

    if not pid:
        return jsonify({"ok": False, "message": "missing participant_id"}), 400

    rows = []
    if "events" in db.list_collection_names():
        cur = db.events.find({"participant_id": pid}).sort([("created_at", -1)]).limit(limit)
        for e in cur:
            rows.append({
                "type": e.get("type"),
                "video_id": str(e.get("video_id")) if e.get("video_id") else None,
                "meta": e.get("meta", {}),
                "created_at": e.get("created_at")
            })
    return jsonify({"ok": True, "events": rows})
