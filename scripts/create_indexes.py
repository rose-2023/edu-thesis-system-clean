import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import (
    ConnectionFailure,
    DuplicateKeyError,
    OperationFailure,
    PyMongoError,
    ServerSelectionTimeoutError,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
MONGO_DATABASE = os.environ.get("MONGO_DATABASE", "thesis_system")


INDEX_SPECS = {
    "users": [
        {
            "keys": [("student_id", ASCENDING)],
            "name": "uniq_users_student_id",
            "unique": True,
            "partialFilterExpression": {"student_id": {"$type": "string"}},
        },
        {"keys": [("role", ASCENDING)], "name": "role_1"},
        {"keys": [("class_name", ASCENDING)], "name": "class_name_1"},
        {"keys": [("group_type", ASCENDING)], "name": "group_type_1"},
        {"keys": [("is_test_data", ASCENDING)], "name": "is_test_data_1"},
        {"keys": [("active_session_id", ASCENDING)], "name": "active_session_id_1"},
        {
            "keys": [("class_name", ASCENDING), ("group_type", ASCENDING), ("is_test_data", ASCENDING)],
            "name": "class_group_test_1",
        },
    ],
    "randomization_slots": [
        {
            "keys": [("study_id", ASCENDING), ("position", ASCENDING)],
            "name": "uniq_randomization_slots_study_position",
            "unique": True,
        },
        {
            "keys": [("study_id", ASCENDING), ("student_id", ASCENDING)],
            "name": "uniq_randomization_slots_study_student",
            "unique": True,
            "partialFilterExpression": {"student_id": {"$type": "string"}},
        },
        {
            "keys": [
                ("study_id", ASCENDING),
                ("sequence_version", ASCENDING),
                ("status", ASCENDING),
                ("position", ASCENDING),
            ],
            "name": "randomization_slots_claim_order",
        },
    ],
    "parsons_attempts_v2": [
        {"keys": [("student_id", ASCENDING)], "name": "student_id_1"},
        {"keys": [("task_id", ASCENDING)], "name": "task_id_1"},
        {"keys": [("target_concept", ASCENDING)], "name": "target_concept_1"},
        {"keys": [("class_name", ASCENDING)], "name": "class_name_1"},
        {"keys": [("group_type", ASCENDING)], "name": "group_type_1"},
        {"keys": [("is_test_data", ASCENDING)], "name": "is_test_data_1"},
        {"keys": [("activity_type", ASCENDING)], "name": "activity_type_1"},
        {"keys": [("test_role", ASCENDING)], "name": "test_role_1"},
        {"keys": [("submitted_at", DESCENDING)], "name": "submitted_at_-1"},
        {
            "keys": [
                ("student_id", ASCENDING),
                ("task_id", ASCENDING),
                ("activity_type", ASCENDING),
                ("test_role", ASCENDING),
                ("attempt_no", ASCENDING),
            ],
            "name": "student_task_activity_role_attempt_1",
        },
        {
            "keys": [
                ("class_name", ASCENDING),
                ("group_type", ASCENDING),
                ("is_test_data", ASCENDING),
                ("activity_type", ASCENDING),
                ("test_role", ASCENDING),
            ],
            "name": "class_group_test_activity_role_1",
        },
        {
            "keys": [("task_id", ASCENDING), ("target_concept", ASCENDING), ("is_correct", ASCENDING)],
            "name": "task_concept_correct_1",
        },
    ],
    "parsons_test_attempts": [
        {"keys": [("student_id", ASCENDING)], "name": "student_id_1"},
        {"keys": [("test_cycle_id", ASCENDING)], "name": "test_cycle_id_1"},
        {"keys": [("test_role", ASCENDING)], "name": "test_role_1"},
        {"keys": [("task_id", ASCENDING)], "name": "task_id_1"},
        {"keys": [("submitted_at", DESCENDING)], "name": "submitted_at_-1"},
        {
            "keys": [
                ("student_id", ASCENDING),
                ("test_cycle_id", ASCENDING),
                ("test_role", ASCENDING),
                ("task_id", ASCENDING),
            ],
            "name": "uniq_student_cycle_role_task",
            "unique": True,
        },
    ],
    "parsons_hint_records": [
        {
            "keys": [("student_id", ASCENDING), ("task_id", ASCENDING)],
            "name": "student_task_unique",
            "unique": True,
        },
        {"keys": [("student_id", ASCENDING)], "name": "student_id_1"},
        {"keys": [("task_id", ASCENDING)], "name": "task_id_1"},
        {"keys": [("hint_id", ASCENDING)], "name": "hint_id_1"},
        {"keys": [("updated_at", DESCENDING)], "name": "updated_at_-1"},
    ],
    "parsons_ai_hint_state": [
        {
            "keys": [("student_id", ASCENDING), ("task_id", ASCENDING)],
            "name": "student_task_ai_hint_unique",
            "unique": True,
        },
        {
            "keys": [("group_type", ASCENDING), ("feedback_policy_version", ASCENDING)],
            "name": "group_policy_1",
        },
    ],
    "learning_logs": [
        {"keys": [("student_id", ASCENDING)], "name": "student_id_1"},
        {"keys": [("session_id", ASCENDING)], "name": "session_id_1"},
        {"keys": [("event_type", ASCENDING)], "name": "event_type_1"},
        {"keys": [("task_id", ASCENDING)], "name": "task_id_1"},
        {"keys": [("attempt_id", ASCENDING)], "name": "attempt_id_1"},
        {"keys": [("event_at", DESCENDING)], "name": "event_at_-1"},
        {"keys": [("is_test_data", ASCENDING)], "name": "is_test_data_1"},
        {
            "keys": [("student_id", ASCENDING), ("task_id", ASCENDING), ("event_at", ASCENDING)],
            "name": "student_task_event_at_1",
        },
        {
            "keys": [("student_id", ASCENDING), ("event_at", ASCENDING)],
            "name": "student_event_at_1",
        },
        {
            "keys": [
                ("class_name", ASCENDING),
                ("group_type", ASCENDING),
                ("is_test_data", ASCENDING),
                ("activity_type", ASCENDING),
                ("test_role", ASCENDING),
            ],
            "name": "class_group_test_activity_role_1",
        },
    ],
    "video_rewatch_logs": [
        {"keys": [("student_id", ASCENDING)], "name": "student_id_1"},
        {"keys": [("class_name", ASCENDING)], "name": "class_name_1"},
        {"keys": [("group_type", ASCENDING)], "name": "group_type_1"},
        {"keys": [("is_test_data", ASCENDING)], "name": "is_test_data_1"},
        {"keys": [("video_id", ASCENDING)], "name": "video_id_1"},
        {"keys": [("unit_id", ASCENDING)], "name": "unit_id_1"},
        {"keys": [("event_type", ASCENDING)], "name": "event_type_1"},
        {"keys": [("event_at", DESCENDING)], "name": "event_at_-1"},
        {
            "keys": [("student_id", ASCENDING), ("event_at", DESCENDING)],
            "name": "student_event_at_1",
        },
        {
            "keys": [("video_id", ASCENDING), ("event_at", DESCENDING)],
            "name": "video_event_at_1",
        },
        {
            "keys": [("watch_session_id", ASCENDING), ("event_at", ASCENDING)],
            "name": "watch_session_event_at_1",
        },
        {
            "keys": [("class_name", ASCENDING), ("group_type", ASCENDING), ("event_at", DESCENDING)],
            "name": "class_group_event_at_1",
        },
    ],
}


def _keys_as_list(index):
    return list((index.get("key") or {}).items())


def _find_compatible_index(collection, spec):
    expected_keys = list(spec["keys"])
    expected_unique = bool(spec.get("unique", False))
    for index in collection.list_indexes():
        if _keys_as_list(index) != expected_keys:
            continue
        if expected_unique and index.get("unique") is not True:
            continue
        if index.get("sparse", False) != bool(spec.get("sparse", False)):
            continue
        if index.get("partialFilterExpression") != spec.get("partialFilterExpression"):
            continue
        return index.get("name")
    return None


def _duplicate_keys(collection, keys, match=None):
    group_id = {field: f"${field}" for field, _direction in keys}
    pipeline = []
    if match:
        pipeline.append({"$match": match})
    pipeline.extend([
        {
            "$group": {
                "_id": group_id,
                "document_ids": {"$push": "$_id"},
                "count": {"$sum": 1},
            },
        },
        {"$match": {"count": {"$gt": 1}}},
        {"$sort": {"_id": 1}},
    ])
    return list(collection.aggregate(pipeline))


def _print_duplicate_keys(collection_name, spec, duplicates):
    print(f"\n{collection_name}.{spec['name']} unique index cannot be created because duplicates exist:")
    for duplicate in duplicates:
        key_values = duplicate.get("_id") or {}
        key_label = ", ".join(f"{field}={value}" for field, value in key_values.items())
        ids = ", ".join(str(item) for item in duplicate.get("document_ids", []))
        print(f"  {key_label}")
        print(f"  _id=[{ids}]")
    print("Please review these documents manually before creating the unique index.\n")


def _drop_incompatible_same_key_index(collection, spec):
    if not spec.get("unique"):
        return
    expected_keys = list(spec["keys"])
    for index in collection.list_indexes():
        name = index.get("name")
        if name == "_id_":
            continue
        named_index_incompatible = (
            name == spec["name"]
            and (
                _keys_as_list(index) != expected_keys
                or (spec.get("unique") and index.get("unique") is not True)
                or index.get("sparse", False) != bool(spec.get("sparse", False))
                or index.get("partialFilterExpression") != spec.get("partialFilterExpression")
            )
        )
        if named_index_incompatible:
            collection.drop_index(name)
            print(f"[replaced incompatible index] {collection.name}.{name}")
            return
        if _keys_as_list(index) == expected_keys and index.get("unique") is not True:
            collection.drop_index(name)
            print(f"[dropped old non-unique index] {collection.name}.{name}")
            return


def _create_collection_indexes(db, collection_name, specs):
    collection = db[collection_name]
    failures = []
    for spec in specs:
        existing_name = _find_compatible_index(collection, spec)
        if existing_name:
            print(f"[exists] {collection_name}.{existing_name}")
            continue

        if spec.get("unique"):
            duplicates = _duplicate_keys(
                collection,
                spec["keys"],
                spec.get("partialFilterExpression"),
            )
            if duplicates:
                _print_duplicate_keys(collection_name, spec, duplicates)
                failures.append(spec["name"])
                continue
            _drop_incompatible_same_key_index(collection, spec)

        options = {"name": spec["name"]}
        if spec.get("unique"):
            options["unique"] = True
        if spec.get("sparse"):
            options["sparse"] = True
        if spec.get("partialFilterExpression"):
            options["partialFilterExpression"] = spec["partialFilterExpression"]
        try:
            created_name = collection.create_index(spec["keys"], **options)
            print(f"[created] {collection_name}.{created_name}")
        except (DuplicateKeyError, OperationFailure) as exc:
            if spec.get("unique"):
                duplicates = _duplicate_keys(
                    collection,
                    spec["keys"],
                    spec.get("partialFilterExpression"),
                )
                if duplicates:
                    _print_duplicate_keys(collection_name, spec, duplicates)
                else:
                    print(f"[failed] {collection_name}.{spec['name']}: {exc}")
            else:
                print(f"[failed] {collection_name}.{spec['name']}: {exc}")
            failures.append(spec["name"])
        except PyMongoError as exc:
            print(f"[failed] {collection_name}.{spec['name']}: {exc}")
            failures.append(spec["name"])
    return failures


def _print_current_indexes(db, collection_name):
    print(f"\n[{collection_name}] current indexes:")
    for index in db[collection_name].list_indexes():
        keys = ", ".join(f"{field}:{direction}" for field, direction in _keys_as_list(index))
        unique = " unique=true" if index.get("unique") is True else ""
        print(f"  - {index.get('name')}: {keys}{unique}")


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

        failures = []
        for collection_name, specs in INDEX_SPECS.items():
            print(f"\nCreating/checking {collection_name} indexes...")
            failures.extend(
                f"{collection_name}.{name}"
                for name in _create_collection_indexes(db, collection_name, specs)
            )

        for collection_name in INDEX_SPECS:
            _print_current_indexes(db, collection_name)

        if failures:
            print("\nSome indexes were not created:")
            for failure in failures:
                print(f"  - {failure}")
            return 2

        print("\nAll requested indexes are ready.")
        return 0
    except (ServerSelectionTimeoutError, ConnectionFailure):
        print("MongoDB 連線失敗，請確認 MongoDB 是否已啟動並監聽 127.0.0.1:27017。")
        return 1
    except PyMongoError as exc:
        print(f"MongoDB index creation failed: {exc}")
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
