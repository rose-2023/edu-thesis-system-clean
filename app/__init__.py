import os

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

from .routes import register_blueprints
from .session_auth import active_session_guard


ROLE_PROTECTED_BLUEPRINTS = {
    "student": {"student"},
    "learning_logs": {"student"},
    "video_rewatch_logs": {"student"},
    "events": {"student"},
    "teacher_analysis": {"teacher", "admin"},
    "teacher_io": {"teacher", "admin"},
    "records": {"teacher", "admin"},
    "teacher_dashboard": {"teacher", "admin"},
    "teacher_t5": {"teacher", "admin"},
    "parsons_admin": {"teacher", "admin"},
    "admin": {"teacher", "admin"},
    "subtitle": {"teacher", "admin"},
}

ADMIN_UPLOAD_STUDENT_READ_ENDPOINTS = {
    "admin_upload.list_videos",
    "admin_upload.subtitle_content",
}


def create_app():
    app = Flask(__name__)
    app.config.update(DEBUG=False, TESTING=False)
    project_root = os.getcwd()
    configured_origins = [
        item.strip()
        for item in os.environ.get(
            "FRONTEND_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
        if item.strip()
    ]
    CORS(app, resources={r"/api/*": {"origins": configured_origins}})

    register_blueprints(app)

    @app.before_request
    def handle_api_preflight():
        if request.method == "OPTIONS" and request.path.startswith("/api/"):
            return ("", 204)
        return None

    @app.before_request
    def enforce_blueprint_session():
        if request.blueprint == "admin_upload":
            if request.method == "GET" and request.endpoint in ADMIN_UPLOAD_STUDENT_READ_ENDPOINTS:
                return active_session_guard({"student", "teacher", "admin"})
            return active_session_guard({"teacher", "admin"})
        allowed_roles = ROLE_PROTECTED_BLUEPRINTS.get(request.blueprint)
        if allowed_roles is None:
            return None
        return active_session_guard(allowed_roles)

    @app.route("/uploads/<path:filename>")
    def serve_uploads(filename):
        return send_from_directory(os.path.join(project_root, "uploads"), filename)

    @app.after_request
    def normalize_api_error(response):
        if not request.path.startswith("/api/") or response.status_code < 400:
            return response

        status = response.status_code
        if status >= 500:
            payload = {
                "ok": False,
                "error": "internal_server_error",
                "message": "系統暫時無法處理此請求，請稍後再試。",
            }
        else:
            payload = response.get_json(silent=True) if response.is_json else {}
            if not isinstance(payload, dict):
                payload = {}
            payload.pop("detail", None)
            payload.pop("traceback", None)
            payload.pop("stack", None)
            payload["ok"] = False
            if not isinstance(payload.get("error"), str) or not payload.get("error"):
                payload["error"] = f"http_{status}"
            if not isinstance(payload.get("message"), str) or not payload.get("message"):
                payload["message"] = "請求無法完成，請確認資料後再試。"

        normalized = jsonify(payload)
        normalized.status_code = status
        return normalized

    @app.errorhandler(Exception)
    def handle_unexpected_error(exc):
        if isinstance(exc, HTTPException):
            return exc
        app.logger.exception("Unhandled API error")
        return jsonify({
            "ok": False,
            "error": "internal_server_error",
            "message": "系統暫時無法處理此請求，請稍後再試。",
        }), 500

    return app
