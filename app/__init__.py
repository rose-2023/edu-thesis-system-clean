from flask import Flask, send_from_directory
from flask_cors import CORS
from .routes import register_blueprints
import os

def create_app():
    app = Flask(__name__)
    PROJECT_ROOT = os.getcwd()
    CORS(app, resources={r"/api/*": {"origins": ["http://localhost:5173", "http://127.0.0.1:5173"]}})

    register_blueprints(app)  # ✅ 統一在 routes/__init__.py 註冊所有 blueprint

    # ===== 提供上傳檔案的靜態路徑 =====
    @app.route("/uploads/<path:filename>")
    def serve_uploads(filename):
        return send_from_directory(os.path.join(PROJECT_ROOT, "uploads"), filename)

    return app
