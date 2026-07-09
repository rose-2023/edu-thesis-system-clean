import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
MONGO_DATABASE = os.environ.get("MONGO_DATABASE", "thesis_system")

HINT_FIELD_DEFAULTS = {
    "question_id": None,
    "unit_id": None,
    "question_type": None,
    "review_type": None,
    "hint_type": None,
    "hint_no": None,
    "hint_click_no": None,
    "max_hint_count": None,
    "hint_limit_reached": None,
    "hint_retry_count": None,
    "next_hint_no": None,
    "hint_text": None,
    "hint_content": None,
    "hint_source": None,
    "hint_loaded": None,
    "hint_error": None,
    "trigger_method": None,
    "button_name": None,
    "close_method": None,
    "return_method": None,
    "error_type": None,
    "wrong_slots": [],
    "ai_diagnosis_summary": None,
}


def main():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
    except ServerSelectionTimeoutError:
        print("MongoDB connection failed. Please confirm MongoDB is running on 127.0.0.1:27017.")
        return 1

    db = client[MONGO_DATABASE]
    collection = db.learning_logs

    pipeline = [
        {
            "$set": {
                key: {"$ifNull": [f"${key}", value]}
                for key, value in HINT_FIELD_DEFAULTS.items()
            }
        }
    ]
    try:
        result = collection.update_many({}, pipeline)
    except PyMongoError as exc:
        print(f"learning_logs hint field backfill failed: {exc}")
        return 1

    print("learning_logs hint field backfill complete.")
    print(f"matched_count: {result.matched_count}")
    print(f"modified_count: {result.modified_count}")
    print("Backfilled fields:")
    for key in HINT_FIELD_DEFAULTS:
        print(f"- {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
