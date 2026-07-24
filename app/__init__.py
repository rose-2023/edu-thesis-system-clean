import logging
import os
import sys

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

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

SERVER_ERROR_MESSAGE = "Backend error. Check the server terminal for details."
REQUEST_ERROR_MESSAGE = "Request failed. Check the API route, request data, or login session."


def configure_console_logging(app):
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if app.logger.handlers:
        for handler in app.logger.handlers:
            handler.setFormatter(formatter)
            handler.setLevel(level)
    else:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(level)
        app.logger.addHandler(console_handler)

    app.logger.setLevel(level)
    logging.getLogger("waitress").setLevel(level)
    logging.getLogger("werkzeug").setLevel(level)


def create_app():
    # Route modules import optional integrations (for example OpenAI).  Keep
    # them out of the package import path so maintenance scripts can import
    # lightweight modules such as ``app.db`` without needing those settings.
    from .routes import register_blueprints

    app = Flask(__name__)
    app.config.update(DEBUG=False, TESTING=False)
    configure_console_logging(app)

    project_root = os.getcwd()
    configured_origins = [
        item.strip()
        for item in os.environ.get(
            "FRONTEND_ORIGINS",
            ",".join(
                [
                    "http://localhost:5173",
                    "http://127.0.0.1:5173",
                    "http://localhost:8080",
                    "http://127.0.0.1:8080",
                ]
            ),
        ).split(",")
        if item.strip()
    ]
    CORS(app, resources={r"/api/*": {"origins": configured_origins}})
    app.logger.info("Allowed frontend origins: %s", ", ".join(configured_origins))

    register_blueprints(app)

    @app.get("/")
    def backend_status():
        return jsonify(
            {
                "ok": True,
                "service": "edu-thesis backend",
                "message": "Backend is running. Open the frontend on Vite port 8080.",
                "api_prefix": "/api",
            }
        )

    @app.get("/favicon.ico")
    def favicon():
        return ("", 204)

    @app.before_request
    def handle_api_preflight():
        if request.method == "OPTIONS" and request.path.startswith("/api/"):
            return ("", 204)
        return None
        
    PUBLIC_ENDPOINTS = {
        "auth.login",
        "admin_upload.serve_uploads",
    }
    ADMIN_UPLOAD_STUDENT_READ_ENDPOINTS = {
    "admin_upload.list_videos",
    "admin_upload.subtitle_content",
    }
    @app.before_request
    def enforce_blueprint_session():
        app.logger.info(
            "AUTH CHECK path=%s endpoint=%s blueprint=%s",
            request.path,
            request.endpoint,
            request.blueprint,
        )
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None

        if request.blueprint == "admin_upload":
            if (
                request.method == "GET"
                and request.endpoint in ADMIN_UPLOAD_STUDENT_READ_ENDPOINTS
            ):
                return active_session_guard(
                    {"student", "teacher", "admin"}
                )

            return active_session_guard(
                {"teacher", "admin"}
            )

        allowed_roles = ROLE_PROTECTED_BLUEPRINTS.get(
            request.blueprint
        )

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
                "message": SERVER_ERROR_MESSAGE,
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
                payload["message"] = REQUEST_ERROR_MESSAGE

        normalized = jsonify(payload)
        normalized.status_code = status
        return normalized

    @app.after_request
    def log_error_response(response):
        if response.status_code >= 400:
            query_string = request.query_string.decode("utf-8", errors="replace")
            query_suffix = f"?{query_string}" if query_string else ""
            app.logger.warning(
                "HTTP %s %s %s%s endpoint=%s remote=%s origin=%s",
                response.status_code,
                request.method,
                request.path,
                query_suffix,
                request.endpoint or "-",
                request.remote_addr or "-",
                request.headers.get("Origin", "-"),
            )
        return response

    @app.errorhandler(Exception)
    def handle_unexpected_error(exc):
        if isinstance(exc, HTTPException):
            return exc
        app.logger.exception(
            "Unhandled API error: %s %s endpoint=%s remote=%s",
            request.method,
            request.path,
            request.endpoint or "-",
            request.remote_addr or "-",
        )
        return jsonify({
            "ok": False,
            "error": "internal_server_error",
            "message": SERVER_ERROR_MESSAGE,
        }), 500

    return app
