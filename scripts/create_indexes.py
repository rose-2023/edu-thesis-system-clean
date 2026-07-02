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
            "name": "users_student_id_unique",
            "unique": True,
        },
        {"keys": [("role", ASCENDING)], "name": "role_1"},
        {"keys": [("class_name", ASCENDING)], "name": "class_name_1"},
        {"keys": [("group_type", ASCENDING)], "name": "group_type_1"},
        {"keys": [("is_test_data", ASCENDING)], "name": "is_test_data_1"},
        {"keys": [("active_session_id", ASCENDING)], "name": "active_session_id_1"},
        {
            "keys": [
                ("class_name", ASCENDING),
                ("group_type", ASCENDING),
                ("is_test_data", ASCENDING),
            ],
            "name": "class_group_test_1",
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
            "keys": [
                ("task_id", ASCENDING),
                ("target_concept", ASCENDING),
                ("is_correct", ASCENDING),
            ],
            "name": "task_concept_correct_1",
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
            "keys": [
                ("student_id", ASCENDING),
                ("task_id", ASCENDING),
                ("event_at", ASCENDING),
            ],
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
        if expected_unique and index.get("partialFilterExpression"):
            continue
        return index.get("name")
    return None


def _duplicate_student_ids(collection):
    pipeline = [
        {
            "$group": {
                "_id": "$student_id",
                "document_ids": {"$push": "$_id"},
                "count": {"$sum": 1},
            },
        },
        {"$match": {"count": {"$gt": 1}}},
        {"$sort": {"_id": 1}},
    ]
    return list(collection.aggregate(pipeline))


def _print_duplicates(duplicates):
    print("\nusers.student_id unique index 無法建立，發現重複資料：")
    for duplicate in duplicates:
        student_id = duplicate.get("_id")
        label = "<null 或缺少 student_id>" if student_id is None else str(student_id)
        ids = ", ".join(str(item) for item in duplicate.get("document_ids", []))
        print(f"  student_id={label}")
        print(f"  _id=[{ids}]")
    print("未刪除或合併任何資料，請人工確認後再重新執行腳本。\n")


def _create_collection_indexes(db, collection_name, specs):
    collection = db[collection_name]
    failures = []
    for spec in specs:
        existing_name = _find_compatible_index(collection, spec)
        if existing_name:
            print(f"[已存在] {collection_name}.{existing_name}")
            continue

        if collection_name == "users" and spec.get("unique"):
            duplicates = _duplicate_student_ids(collection)
            if duplicates:
                _print_duplicates(duplicates)
                failures.append(spec["name"])
                continue

        options = {"name": spec["name"]}
        if spec.get("unique"):
            options["unique"] = True
        try:
            created_name = collection.create_index(spec["keys"], **options)
            print(f"[已建立] {collection_name}.{created_name}")
        except (DuplicateKeyError, OperationFailure) as exc:
            if collection_name == "users" and spec.get("unique"):
                duplicates = _duplicate_student_ids(collection)
                if duplicates:
                    _print_duplicates(duplicates)
                else:
                    print(f"[建立失敗] {collection_name}.{spec['name']}: {exc}")
            else:
                print(f"[建立失敗] {collection_name}.{spec['name']}: {exc}")
            failures.append(spec["name"])
        except PyMongoError as exc:
            print(f"[建立失敗] {collection_name}.{spec['name']}: {exc}")
            failures.append(spec["name"])
    return failures


def _print_current_indexes(db, collection_name):
    print(f"\n[{collection_name}] 目前 indexes：")
    for index in db[collection_name].list_indexes():
        keys = ", ".join(
            f"{field}:{direction}" for field, direction in _keys_as_list(index)
        )
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
        print(f"已連線 MongoDB database：{MONGO_DATABASE}")

        failures = []
        for collection_name, specs in INDEX_SPECS.items():
            print(f"\n建立或確認 {collection_name} indexes...")
            failures.extend(
                f"{collection_name}.{name}"
                for name in _create_collection_indexes(db, collection_name, specs)
            )

        for collection_name in INDEX_SPECS:
            _print_current_indexes(db, collection_name)

        if failures:
            print("\n部分 indexes 未完成：")
            for failure in failures:
                print(f"  - {failure}")
            return 2

        print("\n所有指定 indexes 均已建立或確認存在。")
        return 0
    except (ServerSelectionTimeoutError, ConnectionFailure):
        print("MongoDB 連線失敗，請確認 MongoDB 是否已啟動並監聽 127.0.0.1:27017。")
        return 1
    except PyMongoError as exc:
        print(f"MongoDB index 建立失敗：{exc}")
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
