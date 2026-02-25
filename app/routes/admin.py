from flask import Blueprint
from ..db import db

admin_bp = Blueprint("admin", __name__)

@admin_bp.get("/videos")
def list_videos():
    return {"ok": True, "videos": []}
