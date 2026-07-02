import secrets
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request
from pymongo.errors import PyMongoError
from werkzeug.security import check_password_hash

from ..db import db
from ..avatar_utils import resolve_avatar_src
from ..session_auth import require_active_session


auth_bp = Blueprint("auth", __name__)


def _utc_now():
    return datetime.now(timezone.utc)


@auth_bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    student_id = str(data.get("student_id") or data.get("studentId") or "").strip()
    password = str(data.get("password") or "")

    if not student_id or not password:
        return jsonify({"ok": False, "message": "請輸入帳號與密碼。"}), 400

    try:
        user = db.users.find_one({"student_id": student_id})
        stored_hash = (user or {}).get("password_hash")
        if not user or not stored_hash or not check_password_hash(stored_hash, password):
            return jsonify({"ok": False, "message": "帳號或密碼錯誤。"}), 401

        session_id = secrets.token_urlsafe(32)
        now = _utc_now()
        device_info = str(request.headers.get("User-Agent") or "").strip()[:500] or None
        active_ip = str(request.remote_addr or "").strip()[:100] or None
        db.users.update_one(
            {"_id": user["_id"]},
            {"$set": {
                "active_session_id": session_id,
                "active_login_at": now,
                "active_last_seen_at": now,
                "active_device_info": device_info,
                "active_ip": active_ip,
            }},
        )
    except PyMongoError:
        return jsonify({
            "ok": False,
            "error": "login_service_unavailable",
            "message": "登入服務暫時無法使用，請稍後再試。",
        }), 503

    uid = str(user["_id"])
    return jsonify({
        "ok": True,
        "token": session_id,
        "participant_id": uid,
        "name": user.get("name", ""),
        "class_name": user.get("class_name", ""),
        "role": user.get("role", "student"),
        "sexj": user.get("sexj"),
        "avatar_type": user.get("avatar_type"),
        "avatar_key": user.get("avatar_key"),
        "avatar_src": resolve_avatar_src(user),
        "avatar_style": user.get("avatar_style"),
        "avatar_license": user.get("avatar_license"),
    })


@auth_bp.post("/logout")
@require_active_session()
def logout():
    now = _utc_now()
    result = db.users.update_one(
        {
            "_id": g.current_user["_id"],
            "active_session_id": g.active_session_id,
        },
        {"$set": {
            "active_session_id": None,
            "active_last_seen_at": now,
        }},
    )
    return jsonify({"ok": True, "session_cleared": result.modified_count == 1})
