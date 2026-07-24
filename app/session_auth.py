# 預防同個帳號在不同裝置同時登入，造成 session 被覆蓋的問題。 資安防護
from datetime import datetime, timezone
from functools import wraps

from flask import after_this_request, current_app, g, jsonify, request
from pymongo.errors import PyMongoError

from .db import db


SESSION_EXPIRED_PAYLOAD = {
    "ok": False,
    "error": "session_expired_due_to_new_login",
    "message": "此帳號已在其他裝置登入，請重新登入。",
}


def _utc_now():
    return datetime.now(timezone.utc)


def _bearer_token():
    authorization = str(request.headers.get("Authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return str(request.headers.get("X-User-Token") or "").strip()


def _role_set(roles):
    if roles is None:
        return None
    if isinstance(roles, str):
        return {roles}
    return {str(role) for role in roles}


def current_student_id():
    return str((getattr(g, "current_user", None) or {}).get("student_id") or "").strip()


def current_participant_id():
    return str((getattr(g, "current_user", None) or {}).get("_id") or "").strip()


def _student_identity_matches(user):
    student_id = str(user.get("student_id") or "").strip()
    user_id = str(user.get("_id") or "").strip()
    if not student_id:
        return False

    sources = [request.args, request.form]
    if request.is_json:
        sources.append(request.get_json(silent=True) or {})

    for source in sources:
        claimed_student_id = str(source.get("student_id") or "").strip()
        if claimed_student_id and claimed_student_id != student_id:
            return False

        participant_id = str(source.get("participant_id") or "").strip()
        if participant_id and participant_id not in {student_id, user_id}:
            return False
    return True


def active_session_guard(roles=None):
    """Validate the bearer token against the user's one active login session."""
    if request.method == "OPTIONS":
        return None

    token = _bearer_token()
    if not token:
        return jsonify(SESSION_EXPIRED_PAYLOAD), 401

    try:
        user = db.users.find_one({"active_session_id": token})
    except PyMongoError:
        current_app.logger.exception("Active session lookup failed")
        return jsonify({
            "ok": False,
            "error": "authentication_service_unavailable",
            "message": "登入驗證服務暫時無法使用，請稍後再試。",
        }), 503

    if not user:
        return jsonify(SESSION_EXPIRED_PAYLOAD), 401

    allowed_roles = _role_set(roles)
    role = str(user.get("role") or "student")
    if allowed_roles is not None and role not in allowed_roles:
        return jsonify({
            "ok": False,
            "error": "forbidden",
            "message": "您沒有權限使用此功能。",
        }), 403

    if role == "student" and not _student_identity_matches(user):
        return jsonify({
            "ok": False,
            "error": "student_identity_mismatch",
            "message": "不得存取其他學生的資料。",
        }), 403

    g.current_user = user
    g.active_session_id = token

    @after_this_request
    def update_last_seen(response):
        if response.status_code < 400:
            try:
                db.users.update_one(
                    {"_id": user["_id"], "active_session_id": token},
                    {"$set": {"active_last_seen_at": _utc_now()}},
                )
            except PyMongoError:
                current_app.logger.exception("Active session last-seen update failed")
        return response

    return None


def require_active_session(roles=None):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            denied = active_session_guard(roles)
            if denied is not None:
                return denied
            return view(*args, **kwargs)

        return wrapped

    return decorator
