import re
from urllib.parse import urlparse


DEFAULT_AVATAR_KEY = "default_person"
DEFAULT_AVATAR_SRC = "/static/avatars/person_edit.svg"
DEFAULT_AVATAR_STYLE = "neutral-person"


def _relative_avatar_src(value, avatar_type=None):
    text = str(value or "").strip()
    if not text:
        return None
    if str(avatar_type or "").strip() == "emoji" or text in {"👨", "👩"}:
        return DEFAULT_AVATAR_SRC

    if text.startswith(("http://", "https://")):
        text = urlparse(text).path
    if re.fullmatch(r"/static/avatars/[a-z0-9_-]+\.svg", text, flags=re.IGNORECASE):
        return text
    return None


def resolve_avatar_src(document):
    """Read the canonical field first, then support legacy user documents."""
    data = document if isinstance(document, dict) else {}
    avatar_type = data.get("avatar_type")
    for field in ("avatar_src", "avatar_url", "avatar_value"):
        resolved = _relative_avatar_src(data.get(field), avatar_type)
        if resolved is not None:
            return resolved
    return DEFAULT_AVATAR_SRC
