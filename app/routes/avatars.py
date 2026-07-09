# 頭像檔案替換位置
from copy import deepcopy
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request

from app.db import db
from app.avatar_utils import resolve_avatar_src
from app.session_auth import require_active_session


avatars_bp = Blueprint("avatars", __name__)

# 頭像名稱與路徑存在users裡
CC0_LICENSE = "CC0 1.0"
FREE_COMMERCIAL_LICENSE = "Free for personal and commercial use"

IMAGE_AVATAR_GROUP_SPECS = [
    ("Bottts Neutral 機器人頭像", "bottts_neutral", "bottts-neutral", FREE_COMMERCIAL_LICENSE, "Bottts Neutral"),
    ("Bottts 機器人頭像", "bottts", "bottts", FREE_COMMERCIAL_LICENSE, "Bottts"),
    ("Initial Face", "initial_face", "initial-face", CC0_LICENSE, "Initial Face"),
    ("Lorelei", "lorelei", "lorelei", CC0_LICENSE, "Lorelei"),
    ("Lorelei Neutral", "lorelei_neutral", "lorelei-neutral", CC0_LICENSE, "Lorelei Neutral"),
    ("Notionists", "notionists", "notionists", CC0_LICENSE, "Notionists"),
    ("Notionists Neutral", "notionists_neutral", "notionists-neutral", CC0_LICENSE, "Notionists Neutral"),
    ("Avataaars", "avataaars", "avataaars", FREE_COMMERCIAL_LICENSE, "Avataaars"),
    ("Avataaars Neutral", "avataaars_neutral", "avataaars-neutral", FREE_COMMERCIAL_LICENSE, "Avataaars Neutral"),
]


def _image_avatar_group(group_label, key_prefix, style, license_name, label_prefix):
    return {
        "group_label": group_label,
        "avatars": [
            {
                "avatar_key": f"{key_prefix}_{number:02d}",
                "avatar_type": "image",
                "avatar_src": f"/static/avatars/{key_prefix}_{number:02d}.svg",
                "avatar_style": style,
                "avatar_license": license_name,
                "label": f"{label_prefix} {number}",
            }
            for number in range(1, 7)
        ],
    }


AVATAR_GROUPS = [
    {
        "group_label": "預設頭像",
        "avatars": [
            {
                "avatar_key": "default_male",
                "avatar_type": "emoji",
                "avatar_src": "👨",
                "avatar_style": "emoji",
                "avatar_license": None,
                "label": "男生預設",
            },
            {
                "avatar_key": "default_female",
                "avatar_type": "emoji",
                "avatar_src": "👩",
                "avatar_style": "emoji",
                "avatar_license": None,
                "label": "女生預設",
            },
        ],
    },
    *[_image_avatar_group(*spec) for spec in IMAGE_AVATAR_GROUP_SPECS],
]

AVATAR_ALLOWLIST = {
    avatar["avatar_key"]: avatar
    for group in AVATAR_GROUPS
    for avatar in group["avatars"]
}


@avatars_bp.get("/avatars")
def list_avatars():
    return jsonify({
        "ok": True,
        "groups": deepcopy(AVATAR_GROUPS),
    })


@avatars_bp.patch("/users/me/avatar")
@require_active_session({"student"})
def update_my_avatar():
    user = g.current_user

    data = request.get_json(silent=True) or {}
    if set(data) - {"avatar_key"}:
        return jsonify({"ok": False, "message": "avatar_key_only"}), 400

    avatar_key = str(data.get("avatar_key") or "").strip()
    avatar = AVATAR_ALLOWLIST.get(avatar_key)
    if not avatar:
        return jsonify({"ok": False, "message": "invalid_avatar_key"}), 400

    updated_at = datetime.now(timezone.utc)
    update = {
        "avatar_type": avatar["avatar_type"],
        "avatar_key": avatar["avatar_key"],
        "avatar_src": resolve_avatar_src(avatar),
        "avatar_style": avatar["avatar_style"],
        "avatar_license": avatar["avatar_license"],
        "avatar_updated_at": updated_at,
    }
    db.users.update_one(
        {"_id": user["_id"]},
        {
            "$set": update,
            "$unset": {"avatar_url": "", "avatar_value": ""},
        },
    )

    response_avatar = deepcopy(avatar)
    response_avatar["avatar_updated_at"] = updated_at.isoformat()
    return jsonify({"ok": True, "avatar": response_avatar})
