import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

COLLECTION_NAME = "parsons_attempts_v2"
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
MONGO_DATABASE = os.environ.get("MONGO_DATABASE", "thesis_system")

DUPLICATE_FIELDS = (
    "duration_seconds",
    "submitted_at_utc",
    "created_at_utc",
    "updated_at_utc",
    "attempt_sequence_no",
    "submitted_indentation_by_block",
    "ai_hint_aggregation_mode",
    "ai_hint_concept_tags",
    "ai_hint_concept_scopes",
    "ai_hint_wrong_slots",
    "ai_hint_viewed_numbers",
    "ai_hint_last_viewed_no",
    "ai_hint_last_viewed_at",
    "ai_hint_last_viewed_at_taiwan",
    "ai_hint_clicked",
    "ai_hint_generated_before_submit",
    "ai_hint_viewed_before_submit",
    "wrong_slots",
    "error_concept",
    "needs_review",
)


def _exists_query():
    return {"$or": [{field: {"$exists": True}} for field in DUPLICATE_FIELDS]}


def _field_counts(collection):
    return {
        field: collection.count_documents({field: {"$exists": True}})
        for field in DUPLICATE_FIELDS
    }


def cleanup(collection, dry_run=False):
    before = _field_counts(collection)
    matched = collection.count_documents(_exists_query())
    modified = 0

    if matched and not dry_run:
        result = collection.update_many(
            _exists_query(),
            {"$unset": {field: "" for field in DUPLICATE_FIELDS}},
        )
        modified = result.modified_count

    after = _field_counts(collection) if not dry_run else before
    return {
        "collection": COLLECTION_NAME,
        "database": collection.database.name,
        "dry_run": dry_run,
        "matched_documents": matched,
        "modified_documents": modified,
        "fields": list(DUPLICATE_FIELDS),
        "before_counts": before,
        "after_counts": after,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mongo-uri", default=MONGO_URI)
    parser.add_argument("--database", default=MONGO_DATABASE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    client = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
    except ServerSelectionTimeoutError:
        print("MongoDB connection failed. Please confirm MongoDB is running.")
        return 1

    try:
        report = cleanup(client[args.database][COLLECTION_NAME], dry_run=args.dry_run)
    except PyMongoError as exc:
        print(f"{COLLECTION_NAME} duplicate-field cleanup failed: {exc}")
        return 1
    finally:
        client.close()

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
