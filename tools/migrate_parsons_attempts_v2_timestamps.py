"""Normalize parsons_attempts_v2 timestamps to MongoDB BSON Date values in UTC."""

import argparse
import json
from datetime import datetime, timezone

from pymongo import MongoClient, UpdateOne


COLLECTION_NAME = "parsons_attempts_v2"
DISPLAY_TIMEZONE = "Asia/Taipei"
TIME_FIELDS = ("started_at", "submitted_at", "created_at", "updated_at")


def _as_utc_datetime(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 1_000_000_000_000:
            timestamp /= 1000.0
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    raise ValueError(f"unsupported timestamp type: {type(value).__name__}")


def migrate(collection, dry_run=False):
    operations = []
    invalid = []
    scanned = 0

    projection = {field: 1 for field in TIME_FIELDS}
    for document in collection.find({}, projection):
        scanned += 1
        updates = {"timezone": DISPLAY_TIMEZONE}
        for field in TIME_FIELDS:
            value = document.get(field)
            try:
                updates[field] = _as_utc_datetime(value)
            except (TypeError, ValueError, OverflowError) as exc:
                updates[field] = None
                invalid.append({
                    "_id": str(document.get("_id")),
                    "field": field,
                    "error": str(exc),
                })
        operations.append(UpdateOne({"_id": document["_id"]}, {"$set": updates}))

    modified = 0
    if operations and not dry_run:
        result = collection.bulk_write(operations, ordered=False)
        modified = result.modified_count

    type_counts = {}
    for field in TIME_FIELDS:
        type_counts[field] = {
            "date": collection.count_documents({field: {"$type": "date"}}),
            "null_or_missing": collection.count_documents({field: None}),
        }

    return {
        "collection": COLLECTION_NAME,
        "scanned": scanned,
        "modified": modified,
        "dry_run": dry_run,
        "timezone": DISPLAY_TIMEZONE,
        "timezone_marked": collection.count_documents({"timezone": DISPLAY_TIMEZONE}),
        "invalid_values": invalid,
        "type_counts": type_counts,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mongo-uri", default="mongodb://127.0.0.1:27017")
    parser.add_argument("--database", default="thesis_system")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    client = MongoClient(
        args.mongo_uri,
        tz_aware=True,
        tzinfo=timezone.utc,
        connectTimeoutMS=5000,
        socketTimeoutMS=5000,
        serverSelectionTimeoutMS=5000,
        timeoutMS=10000,
    )
    try:
        client.admin.command("ping")
        report = migrate(client[args.database][COLLECTION_NAME], dry_run=args.dry_run)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
