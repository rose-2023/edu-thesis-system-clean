import os
import re
from urllib.parse import urlparse

from dotenv import load_dotenv
from pymongo import MongoClient


def _relative_avatar_src(value, avatar_type=None):
    text = str(value or "").strip()
    if not text:
        return None
    if str(avatar_type or "").strip() == "emoji" or text in {"👨", "👩"}:
        return text
    if text.startswith(("http://", "https://")):
        text = urlparse(text).path
    if re.fullmatch(r"/static/avatars/[a-z0-9_-]+\.svg", text, flags=re.IGNORECASE):
        return text
    return None


def _resolve_avatar_src(document):
    for field in ("avatar_src", "avatar_url", "avatar_value"):
        resolved = _relative_avatar_src(
            document.get(field),
            document.get("avatar_type"),
        )
        if resolved is not None:
            return resolved
    return None


def main():
    load_dotenv()
    client = MongoClient(
        os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017"),
        serverSelectionTimeoutMS=5000,
    )
    db = client[os.environ.get("MONGO_DATABASE", "thesis_system")]
    matched = 0
    migrated = 0
    query = {
        "$or": [
            {"avatar_src": {"$exists": True}},
            {"avatar_url": {"$exists": True}},
            {"avatar_value": {"$exists": True}},
        ],
    }
    projection = {
        "avatar_type": 1,
        "avatar_src": 1,
        "avatar_url": 1,
        "avatar_value": 1,
    }

    for user in db.users.find(query, projection):
        matched += 1
        avatar_src = _resolve_avatar_src(user)
        update = {"$unset": {"avatar_url": "", "avatar_value": ""}}
        if avatar_src is not None:
            update["$set"] = {"avatar_src": avatar_src}
        else:
            update["$unset"]["avatar_src"] = ""
        result = db.users.update_one({"_id": user["_id"]}, update)
        migrated += int(result.modified_count)

    field_counts = {
        "avatar_src": db.users.count_documents({"avatar_src": {"$exists": True}}),
        "avatar_url": db.users.count_documents({"avatar_url": {"$exists": True}}),
        "avatar_value": db.users.count_documents({"avatar_value": {"$exists": True}}),
        "absolute_avatar_src": db.users.count_documents({
            "avatar_src": {"$regex": r"^https?://"},
        }),
    }
    client.close()
    print(f"matched={matched} migrated={migrated}")
    print(field_counts)


if __name__ == "__main__":
    main()
