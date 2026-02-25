from flask import Blueprint, request, jsonify
from app.db import db

records_bp = Blueprint("records", __name__)

@records_bp.get("/students")
def students():
    class_id = (request.args.get("class_id") or "").strip()
    page = int(request.args.get("page") or 1)
    page_size = int(request.args.get("page_size") or 15)

    q = {}
    if class_id:
        q["class_id"] = class_id

    total = db.participants.count_documents(q)
    cursor = (
        db.participants.find(q, {"_id": 0})
        .sort("created_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    students = list(cursor)

    return jsonify({"ok": True, "students": students, "total": total})

@records_bp.get("/learning_events")
def learning_events():
    participant_id = (request.args.get("participant_id") or "").strip()
    limit = int(request.args.get("limit") or 20)

    if not participant_id:
        return jsonify({"ok": False, "message": "missing participant_id"}), 400

    cursor = (
        db.learning_events.find({"participant_id": participant_id}, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
    )
    events = list(cursor)
    return jsonify({"ok": True, "events": events})

@records_bp.get("/analytics")
def analytics():
    # 先給個保底版本，之後再做正式統計
    return jsonify({"ok": True, "completion_rate": 0, "avg_score": 0, "parsons_completed": 0})
