import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, PyMongoError, ServerSelectionTimeoutError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
MONGO_DATABASE = os.environ.get("MONGO_DATABASE", "thesis_system")

COLLECTIONS = ("pre_parsons_questions", "post_parsons_questions")
DATE_FIELDS = ("created_at", "updated_at")

MISSING_OR_STRING_DATE_QUERY = {
    "$or": [
        {field: {"$exists": False}}
        for field in DATE_FIELDS
    ]
    + [
        {field: None}
        for field in DATE_FIELDS
    ]
    + [
        {field: ""}
        for field in DATE_FIELDS
    ]
    + [
        {field: {"$type": "string"}}
        for field in DATE_FIELDS
    ]
}


def _utc_now():
    return datetime.now(timezone.utc)


def _parse_datetime_string(value):
    text = str(value or "").strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def _coerce_to_date(value, fallback):
    if isinstance(value, datetime):
        return value, "already_date"
    if isinstance(value, str):
        parsed = _parse_datetime_string(value)
        if parsed is not None:
            return parsed, "converted_string"
        return fallback, "generated_fallback"
    if value is None:
        return fallback, "generated_missing"
    return fallback, "generated_fallback"


def _normalize_collection(db, collection_name):
    collection = db[collection_name]
    docs = list(
        collection.find(
            MISSING_OR_STRING_DATE_QUERY,
            {"_id": 1, "created_at": 1, "updated_at": 1},
        )
    )

    stats = {
        "matched": len(docs),
        "modified": 0,
        "converted_string": 0,
        "generated_missing": 0,
        "generated_fallback": 0,
        "already_date": 0,
    }

    for doc in docs:
        now = _utc_now()
        update_fields = {}
        for field in DATE_FIELDS:
            value = doc.get(field)
            date_value, reason = _coerce_to_date(value, now)
            stats[reason] += 1
            if not isinstance(value, datetime):
                update_fields[field] = date_value

        if not update_fields:
            continue

        result = collection.update_one({"_id": doc["_id"]}, {"$set": update_fields})
        stats["modified"] += result.modified_count

    return stats


def main():
    client = MongoClient(
        MONGO_URI,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
    )

    try:
        client.admin.command("ping")
        db = client[MONGO_DATABASE]
        print(f"Connected to MongoDB database: {MONGO_DATABASE}")

        for collection_name in COLLECTIONS:
            stats = _normalize_collection(db, collection_name)
            print(f"\n{collection_name}")
            print(f"  matched documents: {stats['matched']}")
            print(f"  modified documents: {stats['modified']}")
            print(f"  converted string date fields: {stats['converted_string']}")
            print(f"  generated missing date fields: {stats['generated_missing']}")
            print(f"  generated fallback date fields: {stats['generated_fallback']}")

        print("\nDone. created_at and updated_at are stored as MongoDB Date values.")
        return 0
    except (ServerSelectionTimeoutError, ConnectionFailure):
        print(
            "MongoDB connection failed. Please confirm MongoDB is running on "
            "127.0.0.1:27017."
        )
        return 1
    except PyMongoError as exc:
        print(f"MongoDB update failed: {exc}")
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
