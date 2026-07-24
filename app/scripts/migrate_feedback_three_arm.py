"""Map the two-arm feedback study to the versioned three-arm study.

Run without arguments for a dry-run summary. Use --apply only after reviewing
the counts. Historical documents retain their original group_type snapshot.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient


LEGACY_POLICY = "feedback_v1_two_arm"
CURRENT_POLICY = "feedback_v2_three_arm"
GROUP_MAP = {"experimental": "experimental_1", "control": "experimental_2"}
HISTORICAL_COLLECTIONS = (
    "parsons_attempts",
    "parsons_attempts_v2",
    "parsons_review_logs",
    "parsons_hint_records",
    "learning_logs",
)

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def now_utc():
    return datetime.now(timezone.utc)


def migrated_group(group_type):
    return GROUP_MAP.get(str(group_type or "").strip().lower())


def migrate_users(database, apply):
    scanned = changed = 0
    for user in database.users.find({"group_type": {"$in": list(GROUP_MAP)}}):
        scanned += 1
        canonical = migrated_group(user.get("group_type"))
        if not canonical:
            continue
        changed += 1
        if apply:
            database.users.update_one(
                {"_id": user["_id"]},
                {"$set": {
                    "legacy_group_type": user.get("group_type"),
                    "group_type": canonical,
                    "analysis_group_type": canonical,
                    "feedback_strategy": "B" if canonical == "experimental_1" else "C",
                    "updated_at": now_utc(),
                }},
            )
    return scanned, changed


def migrate_history(collection, apply):
    scanned = changed = 0
    for doc in collection.find({"group_type": {"$in": list(GROUP_MAP)}}):
        scanned += 1
        canonical = migrated_group(doc.get("group_type"))
        needs_update = (
            doc.get("analysis_group_type") != canonical
            or not doc.get("feedback_policy_version")
        )
        if not needs_update:
            continue
        changed += 1
        if apply:
            collection.update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "analysis_group_type": canonical,
                    "feedback_policy_version": doc.get("feedback_policy_version") or LEGACY_POLICY,
                    "group_mapping_version": CURRENT_POLICY,
                    "group_mapping_migrated_at": now_utc(),
                }},
            )
    return scanned, changed


def main():
    parser = argparse.ArgumentParser(description="Preview or apply feedback-study group migration")
    parser.add_argument("--apply", action="store_true", help="Write the migration after a dry run")
    args = parser.parse_args()
    client = MongoClient(os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017"))
    database = client[os.environ.get("MONGO_DATABASE", "thesis_system")]
    mode = "applied" if args.apply else "dry-run"

    scanned, changed = migrate_users(database, args.apply)
    print(f"{mode}: users scanned={scanned} changed={changed}")
    for name in HISTORICAL_COLLECTIONS:
        scanned, changed = migrate_history(database[name], args.apply)
        print(f"{mode}: {name} scanned={scanned} changed={changed}")


if __name__ == "__main__":
    main()
