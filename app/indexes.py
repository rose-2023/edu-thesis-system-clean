from pymongo import ASCENDING, DESCENDING
from pymongo.errors import PyMongoError
from .db import db


CORE_INDEXES = {
    "users": [
        ([("student_id", ASCENDING)], "uniq_users_student_id", True),
        ([("class_name", ASCENDING)], "class_name_1", False),
        ([("group_type", ASCENDING)], "group_type_1", False),
        ([("is_test_data", ASCENDING)], "is_test_data_1", False),
        ([("active_session_id", ASCENDING)], "active_session_id_1", False),
    ],
    "randomization_slots": [
        (
            [("study_id", ASCENDING), ("position", ASCENDING)],
            "uniq_randomization_slots_study_position",
            True,
        ),
        (
            [("study_id", ASCENDING), ("student_id", ASCENDING)],
            "uniq_randomization_slots_study_student",
            True,
        ),
        (
            [
                ("study_id", ASCENDING),
                ("sequence_version", ASCENDING),
                ("status", ASCENDING),
                ("position", ASCENDING),
            ],
            "randomization_slots_claim_order",
            False,
        ),
    ],
    "parsons_attempts_v2": [
        ([("student_id", ASCENDING)], "student_id_1", False),
        ([("class_name", ASCENDING)], "class_name_1", False),
        ([("group_type", ASCENDING)], "group_type_1", False),
        ([("is_test_data", ASCENDING)], "is_test_data_1", False),
        ([("activity_type", ASCENDING)], "activity_type_1", False),
        ([("test_role", ASCENDING)], "test_role_1", False),
        ([("task_id", ASCENDING)], "task_id_1", False),
        ([("submitted_at", ASCENDING)], "submitted_at_1", False),
        (
            [
                ("student_id", ASCENDING),
                ("task_id", ASCENDING),
                ("activity_type", ASCENDING),
                ("test_role", ASCENDING),
                ("attempt_no", ASCENDING),
            ],
            "student_task_activity_role_attempt_1",
            False,
        ),
    ],
    "parsons_ai_hint_state": [
        (
            [("student_id", ASCENDING), ("task_id", ASCENDING)],
            "student_task_ai_hint_unique",
            True,
        ),
        ([("group_type", ASCENDING), ("feedback_policy_version", ASCENDING)], "group_policy_1", False),
    ],
    "learning_logs": [
        ([("student_id", ASCENDING)], "student_id_1", False),
        ([("class_name", ASCENDING)], "class_name_1", False),
        ([("group_type", ASCENDING)], "group_type_1", False),
        ([("is_test_data", ASCENDING)], "is_test_data_1", False),
        ([("session_id", ASCENDING)], "session_id_1", False),
        ([("event_type", ASCENDING)], "event_type_1", False),
        ([("task_id", ASCENDING)], "task_id_1", False),
        ([("attempt_id", ASCENDING)], "attempt_id_1", False),
        ([("event_at", ASCENDING)], "event_at_1", False),
    ],
    "hint_library": [
        ([("hint_key", ASCENDING), ("version", DESCENDING)], "hint_key_version_1", False),
        (
            [
                ("concept_tag", ASCENDING),
                ("error_type", ASCENDING),
                ("hint_level", ASCENDING),
                ("scope", ASCENDING),
                ("language", ASCENDING),
                ("is_active", ASCENDING),
            ],
            "concept_error_level_scope_lang_active_1",
            False,
        ),
        ([("task_scope", ASCENDING), ("task_family", ASCENDING), ("is_active", ASCENDING)], "task_scope_family_active_1", False),
        ([("updated_at", DESCENDING)], "updated_at_-1", False),
    ],
    "video_rewatch_logs": [
        ([("student_id", ASCENDING)], "student_id_1", False),
        ([("class_name", ASCENDING)], "class_name_1", False),
        ([("group_type", ASCENDING)], "group_type_1", False),
        ([("is_test_data", ASCENDING)], "is_test_data_1", False),
        ([("video_id", ASCENDING)], "video_id_1", False),
        ([("unit_id", ASCENDING)], "unit_id_1", False),
        ([("event_type", ASCENDING)], "event_type_1", False),
        ([("event_at", ASCENDING)], "event_at_1", False),
        ([("student_id", ASCENDING), ("event_at", ASCENDING)], "student_event_at_1", False),
        ([("video_id", ASCENDING), ("event_at", ASCENDING)], "video_event_at_1", False),
        ([("watch_session_id", ASCENDING), ("event_at", ASCENDING)], "watch_session_event_at_1", False),
    ],
}


def ensure_core_indexes(logger=None):
    """Create required indexes without deleting or rewriting existing data."""
    results = []
    for collection_name, specs in CORE_INDEXES.items():
        collection = db[collection_name]
        for keys, name, unique in specs:
            try:
                options = {"name": name, "unique": unique}
                if collection_name == "users" and name == "uniq_users_student_id":
                    options["partialFilterExpression"] = {
                        "student_id": {"$type": "string"},
                    }
                if (
                    collection_name == "randomization_slots"
                    and name == "uniq_randomization_slots_study_student"
                ):
                    options["partialFilterExpression"] = {
                        "student_id": {"$type": "string"},
                    }
                collection.create_index(keys, **options)
                results.append((collection_name, name, True))
            except PyMongoError as exc:
                results.append((collection_name, name, False))
                if logger:
                    logger.error(
                        "Unable to create MongoDB index %s.%s: %s",
                        collection_name,
                        name,
                        exc,
                    )
    return results
