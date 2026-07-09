from collections import Counter, defaultdict
from datetime import datetime, timezone

from bson import ObjectId
from flask import Blueprint, jsonify, request

from app.db import db


teacher_analysis_bp = Blueprint("teacher_analysis", __name__)

TEST_STUDENT_ID = "11461127"
VALID_ACTIVITY_TYPES = {"practice", "test"}
VALID_TEST_ROLES = {"pretest", "posttest"}
FORMAL_GROUP_TYPES = ("control", "experimental")
TEST_DATA_GROUP_FILTER = "test_data"


def _utc_min():
    return datetime.min.replace(tzinfo=timezone.utc)


def _optional_string(value):
    text = str(value or "").strip()
    return text or None


def _normalize_activity_type(value):
    text = str(value or "").strip().lower()
    return text if text in VALID_ACTIVITY_TYPES else "practice"


def _normalize_test_role(value):
    text = str(value or "").strip().lower()
    if text in {"pre", "pretest"}:
        return "pretest"
    if text in {"post", "posttest"}:
        return "posttest"
    return None


def _normalize_group_filter(value):
    text = str(value or "").strip().lower()
    if text in {"test", "test_data", "testing", "測試資料"}:
        return TEST_DATA_GROUP_FILTER
    if text in FORMAL_GROUP_TYPES:
        return text
    return None


def _apply_group_filter(query, class_name, group_filter, student_id=None):
    selected_student = _optional_string(student_id)
    if class_name:
        query["class_name"] = class_name

    if group_filter == TEST_DATA_GROUP_FILTER:
        query["$or"] = [
            {"is_test_data": True},
            {"student_id": TEST_STUDENT_ID},
            {"group_type": None},
            {"group_type": ""},
        ]
        if selected_student:
            query["student_id"] = selected_student
        return query

    if selected_student:
        query["student_id"] = selected_student

    if group_filter in FORMAL_GROUP_TYPES:
        query["is_test_data"] = {"$ne": True}
        query["group_type"] = group_filter
    return query


def _student_user_query(class_name=None, group_filter=None, student_id=None):
    query = {"role": "student"}
    selected_student = _optional_string(student_id)
    if class_name:
        query["class_name"] = class_name
    if selected_student:
        query["student_id"] = selected_student

    if group_filter == TEST_DATA_GROUP_FILTER:
        query["$or"] = [
            {"is_test_data": True},
            {"student_id": TEST_STUDENT_ID},
            {"group_type": None},
            {"group_type": ""},
        ]
        return query

    if group_filter in FORMAL_GROUP_TYPES:
        query["is_test_data"] = {"$ne": True}
        query["group_type"] = group_filter
    return query


def _profile_matches_filter(profile, class_name=None, group_filter=None, student_id=None):
    sid = str(profile.get("student_id") or "").strip()
    selected_student = _optional_string(student_id)
    if not sid:
        return False
    if selected_student and sid != selected_student:
        return False
    if class_name and profile.get("class_name") != class_name:
        return False

    is_test_data = profile.get("is_test_data") is True
    group_type = profile.get("group_type")
    if group_filter == TEST_DATA_GROUP_FILTER:
        return is_test_data
    if group_filter in FORMAL_GROUP_TYPES:
        return group_type == group_filter and not is_test_data
    return True


def _combined_student_profiles(class_name=None, group_filter=None, student_id=None):
    profiles = {}

    def ensure_profile(sid):
        sid = str(sid or "").strip()
        if not sid:
            return None
        return profiles.setdefault(sid, {
            "student_id": sid,
            "name": None,
            "class_name": None,
            "group_type": None,
            "is_test_data": False,
            "_explicit_test_data": False,
            "_user_missing_group_type": False,
        })

    def apply_source(doc, source):
        sid = str(doc.get("student_id") or "").strip()
        profile = ensure_profile(sid)
        if not profile:
            return
        if source == "users":
            profile["name"] = doc.get("name") or profile.get("name")
            profile["_explicit_test_data"] = doc.get("is_test_data") is True
            profile["_user_missing_group_type"] = _optional_string(doc.get("group_type")) is None
        if not profile.get("class_name") and doc.get("class_name"):
            profile["class_name"] = doc.get("class_name")
        if not profile.get("group_type") and doc.get("group_type"):
            profile["group_type"] = doc.get("group_type")
        if doc.get("is_test_data") is True:
            profile["_explicit_test_data"] = True

    user_projection = {
        "_id": 0,
        "student_id": 1,
        "name": 1,
        "class_name": 1,
        "group_type": 1,
        "is_test_data": 1,
        "role": 1,
    }
    for user in db.users.find({"role": "student"}, user_projection):
        apply_source(user, "users")

    shared_projection = {
        "_id": 0,
        "student_id": 1,
        "class_name": 1,
        "group_type": 1,
        "is_test_data": 1,
    }
    for attempt in db.parsons_attempts_v2.find({}, shared_projection):
        apply_source(attempt, "attempts")
    for log in db.learning_logs.find({}, shared_projection):
        apply_source(log, "logs")

    rows = []
    for profile in profiles.values():
        group_type = profile.get("group_type")
        profile["is_test_data"] = (
            profile.get("_explicit_test_data") is True
            or profile.get("_user_missing_group_type") is True
            or profile.get("student_id") == TEST_STUDENT_ID
            or group_type not in FORMAL_GROUP_TYPES
        )
        row = {key: value for key, value in profile.items() if not key.startswith("_")}
        if _profile_matches_filter(row, class_name, group_filter, student_id):
            rows.append(row)
    return sorted(rows, key=lambda row: str(row.get("student_id") or ""))


def _user_group_overview(class_name=None, group_filter=None):
    profiles = _combined_student_profiles(class_name, group_filter)
    control_count = sum(
        1 for profile in profiles
        if profile.get("group_type") == "control" and profile.get("is_test_data") is not True
    )
    experimental_count = sum(
        1 for profile in profiles
        if profile.get("group_type") == "experimental" and profile.get("is_test_data") is not True
    )
    test_count = sum(1 for profile in profiles if profile.get("is_test_data") is True)

    return {
        "experimental_count": experimental_count,
        "control_count": control_count,
        "test_account_count": test_count,
        "formal_student_count": experimental_count + control_count,
        "total_student_count": experimental_count + control_count + test_count,
    }


def _legacy_test_role(test_role):
    if test_role == "pretest":
        return "pre"
    if test_role == "posttest":
        return "post"
    return test_role


def _test_question_count(test_role):
    collection_name = "pre_parsons_questions" if test_role == "pretest" else "post_parsons_questions"
    count = db[collection_name].count_documents({"deleted": {"$ne": True}})
    if count:
        return count

    legacy_role = _legacy_test_role(test_role)
    task_ids = {
        str(value)
        for value in db.parsons_test_tasks.distinct("test_task_id", {"test_role": legacy_role})
        if value
    }
    source_ids = {
        str(value)
        for value in db.parsons_test_tasks.distinct("source_task_id", {"test_role": legacy_role})
        if value
    }
    return len(task_ids | source_ids)


def _student_test_task_counts(test_role, student_ids):
    ids = sorted({str(sid) for sid in student_ids if sid})
    if not ids:
        return {}

    counts = defaultdict(set)
    legacy_role = _legacy_test_role(test_role)
    legacy_cursor = db.parsons_test_attempts.find(
        {"student_id": {"$in": ids}, "test_role": legacy_role},
        {"_id": 0, "student_id": 1, "test_task_id": 1, "task_id": 1, "source_task_id": 1},
    )
    for attempt in legacy_cursor:
        sid = str(attempt.get("student_id") or "").strip()
        task_id = str(
            attempt.get("task_id")
            or attempt.get("test_task_id")
            or attempt.get("source_task_id")
            or ""
        ).strip()
        if sid and task_id:
            counts[sid].add(task_id)

    return {sid: len(tasks) for sid, tasks in counts.items()}


def _test_completion_overview(test_role, class_name, group_filter, student_id=None):
    if test_role not in VALID_TEST_ROLES:
        return None

    students = _combined_student_profiles(class_name, group_filter, student_id)
    student_ids = {
        str(row.get("student_id") or "").strip()
        for row in students
        if str(row.get("student_id") or "").strip()
    }
    expected_task_count = _test_question_count(test_role)
    task_counts = _student_test_task_counts(test_role, student_ids)
    required_count = max(expected_task_count, 1)
    completed_ids = {
        sid for sid, count in task_counts.items()
        if count >= required_count
    }
    total = len(student_ids)
    completed = len(completed_ids & student_ids)

    groups = []
    for group_type in FORMAL_GROUP_TYPES:
        group_ids = {
            str(row.get("student_id") or "").strip()
            for row in students
            if row.get("group_type") == group_type and row.get("is_test_data") is not True
        }
        group_ids = {sid for sid in group_ids if sid}
        group_completed = len(group_ids & completed_ids)
        groups.append({
            "group_type": group_type,
            "total_students": len(group_ids),
            "completed_students": group_completed,
            "pending_students": max(len(group_ids) - group_completed, 0),
            "completion_rate": _safe_rate(group_completed, len(group_ids)),
        })

    return {
        "test_role": test_role,
        "expected_task_count": expected_task_count,
        "total_students": total,
        "completed_students": completed,
        "pending_students": max(total - completed, 0),
        "completion_rate": _safe_rate(completed, total),
        "data_sources": ["parsons_test_attempts"],
        "groups": groups,
    }


def _student_practice_task_counts(student_ids):
    ids = sorted({str(sid) for sid in student_ids if sid})
    if not ids:
        return {}
    counts = defaultdict(set)
    cursor = db.parsons_attempts_v2.find(
        {
            "student_id": {"$in": ids},
            "activity_type": "practice",
            "test_role": None,
        },
        {"_id": 0, "student_id": 1, "task_id": 1},
    )
    for attempt in cursor:
        sid = str(attempt.get("student_id") or "").strip()
        task_id = str(attempt.get("task_id") or "").strip()
        if sid and task_id:
            counts[sid].add(task_id)
    return {sid: len(tasks) for sid, tasks in counts.items()}


def _student_last_activity_at(student_ids):
    ids = sorted({str(sid) for sid in student_ids if sid})
    if not ids:
        return {}
    latest = {}

    def touch(sid, value):
        sid = str(sid or "").strip()
        if not sid or not isinstance(value, datetime):
            return
        current = latest.get(sid)
        if current is None or value > current:
            latest[sid] = value

    for log in db.learning_logs.find(
        {"student_id": {"$in": ids}},
        {"_id": 0, "student_id": 1, "event_at": 1, "created_at": 1},
    ):
        touch(log.get("student_id"), log.get("event_at") or log.get("created_at"))

    for attempt in db.parsons_attempts_v2.find(
        {"student_id": {"$in": ids}},
        {"_id": 0, "student_id": 1, "submitted_at": 1, "created_at": 1},
    ):
        touch(attempt.get("student_id"), attempt.get("submitted_at") or attempt.get("created_at"))

    for attempt in db.parsons_test_attempts.find(
        {"student_id": {"$in": ids}},
        {"_id": 0, "student_id": 1, "submitted_at": 1, "created_at": 1},
    ):
        touch(attempt.get("student_id"), attempt.get("submitted_at") or attempt.get("created_at"))

    return latest


def _progress_status(count, required_count=None):
    count = int(count or 0)
    if required_count is None:
        return "in_progress" if count > 0 else "not_started"
    required = max(int(required_count or 0), 1)
    if count >= required:
        return "completed"
    if count > 0:
        return "in_progress"
    return "not_started"


def _student_progress_rows(class_name, group_filter, student_id=None):
    students = _combined_student_profiles(class_name, group_filter, student_id)
    student_ids = [row.get("student_id") for row in students if row.get("student_id")]
    pre_expected = _test_question_count("pretest")
    post_expected = _test_question_count("posttest")
    pre_counts = _student_test_task_counts("pretest", student_ids)
    post_counts = _student_test_task_counts("posttest", student_ids)
    practice_counts = _student_practice_task_counts(student_ids)
    latest = _student_last_activity_at(student_ids)

    rows = []
    for student in students:
        sid = str(student.get("student_id") or "").strip()
        if not sid:
            continue
        pre_count = pre_counts.get(sid, 0)
        post_count = post_counts.get(sid, 0)
        practice_count = practice_counts.get(sid, 0)
        rows.append(_json_safe({
            "student_id": sid,
            "name": student.get("name"),
            "class_name": student.get("class_name"),
            "group_type": student.get("group_type"),
            "is_test_data": student.get("is_test_data") is True,
            "pretest_status": _progress_status(pre_count, pre_expected),
            "pretest_completed_tasks": pre_count,
            "pretest_total_tasks": pre_expected,
            "learning_status": _progress_status(practice_count),
            "learning_task_count": practice_count,
            "posttest_status": _progress_status(post_count, post_expected),
            "posttest_completed_tasks": post_count,
            "posttest_total_tasks": post_expected,
            "last_activity_at": latest.get(sid),
        }))
    return rows


def _safe_float(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _valid_duration(value):
    seconds = _safe_float(value)
    if seconds is None or seconds < 0 or seconds > 3600:
        return None
    return seconds


def _safe_attempt_no(value):
    try:
        parsed = int(value)
        return parsed if parsed >= 1 else None
    except Exception:
        return None


def _safe_rate(numerator, denominator):
    return round(float(numerator) / float(denominator), 4) if denominator else 0


def _json_safe(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return value


def _sort_datetime(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return _utc_min()


def _attempt_sort_key(attempt):
    return (
        _safe_attempt_no(attempt.get("attempt_no")) or 0,
        _sort_datetime(attempt.get("submitted_at") or attempt.get("created_at")),
        str(attempt.get("_id") or ""),
    )


def _load_user_profiles(student_ids):
    ids = sorted({str(sid) for sid in student_ids if sid})
    if not ids:
        return {}

    profiles = {}
    cursor = db.users.find(
        {"student_id": {"$in": ids}},
        {
            "_id": 0,
            "student_id": 1,
            "name": 1,
            "class_name": 1,
            "group_type": 1,
            "is_test_data": 1,
            "role": 1,
        },
    )
    for user in cursor:
        sid = str(user.get("student_id") or "").strip()
        if sid:
            user["_user_missing_group_type"] = _optional_string(user.get("group_type")) is None
            profiles[sid] = user
    return profiles


def _first_concept(doc):
    for key in ("target_concept", "concept", "concept_tag"):
        value = _optional_string((doc or {}).get(key))
        if value:
            return value
    tags = (doc or {}).get("tags")
    if isinstance(tags, list):
        for tag in tags:
            value = _optional_string(tag)
            if value:
                return value
    return None


def _load_task_profiles(task_ids):
    ids = sorted({str(task_id) for task_id in task_ids if task_id})
    if not ids:
        return {}

    object_ids = [ObjectId(task_id) for task_id in ids if ObjectId.is_valid(task_id)]
    clauses = [{"task_id": {"$in": ids}}, {"id": {"$in": ids}}]
    if object_ids:
        clauses.append({"_id": {"$in": object_ids}})

    profiles = {}
    cursor = db.parsons_tasks.find(
        {"$or": clauses},
        {
            "_id": 1,
            "task_id": 1,
            "id": 1,
            "title": 1,
            "task_title": 1,
            "name": 1,
            "target_concept": 1,
            "concept": 1,
            "concept_tag": 1,
            "tags": 1,
        },
    )
    for task in cursor:
        keys = {
            str(task.get("_id") or ""),
            str(task.get("task_id") or ""),
            str(task.get("id") or ""),
        }
        title = (
            _optional_string(task.get("task_title"))
            or _optional_string(task.get("title"))
            or _optional_string(task.get("name"))
        )
        concept = _first_concept(task)
        value = {"task_title": title, "target_concept": concept}
        for key in keys:
            if key:
                profiles[key] = value
    return profiles


def _read_student_options(class_name, group_filter):
    return build_student_options(class_name, group_filter)


def _student_option_matches(row, class_name, group_filter):
    sid = str(row.get("student_id") or "").strip()
    if not sid:
        return False
    if class_name and row.get("class_name") != class_name:
        return False

    is_test_data = row.get("is_test_data") is True
    if group_filter == TEST_DATA_GROUP_FILTER:
        return is_test_data
    if group_filter in FORMAL_GROUP_TYPES:
        return row.get("group_type") == group_filter and not is_test_data
    return True


def _apply_source_profile(options, source_doc, source_name):
    sid = str(source_doc.get("student_id") or "").strip()
    if not sid:
        return
    option = options.setdefault(sid, {
        "student_id": sid,
        "class_name": None,
        "group_type": None,
        "is_test_data": False,
        "has_attempts": False,
        "has_logs": False,
        "_profile_source": None,
        "_user_missing_group_type": False,
    })

    if source_name == "attempts":
        option["has_attempts"] = True
    if source_name == "logs":
        option["has_logs"] = True

    if source_name == "users":
        option["_user_missing_group_type"] = _optional_string(source_doc.get("group_type")) is None

    if option.get("_profile_source") is not None:
        return
    option["class_name"] = source_doc.get("class_name")
    option["group_type"] = source_doc.get("group_type")
    option["is_test_data"] = source_doc.get("is_test_data") is True
    option["_profile_source"] = source_name


def build_student_options(class_name=None, group_filter=None):
    class_name = _optional_string(class_name)
    group_filter = _normalize_group_filter(group_filter)
    options = {}

    user_projection = {
        "_id": 0,
        "student_id": 1,
        "class_name": 1,
        "group_type": 1,
        "is_test_data": 1,
        "role": 1,
    }
    for user in db.users.find({"role": "student"}, user_projection):
        _apply_source_profile(options, user, "users")

    attempt_projection = {
        "_id": 0,
        "student_id": 1,
        "class_name": 1,
        "group_type": 1,
        "is_test_data": 1,
    }
    for attempt in db.parsons_attempts_v2.find({}, attempt_projection):
        _apply_source_profile(options, attempt, "attempts")

    log_projection = {
        "_id": 0,
        "student_id": 1,
        "class_name": 1,
        "group_type": 1,
        "is_test_data": 1,
    }
    for log in db.learning_logs.find({}, log_projection):
        _apply_source_profile(options, log, "logs")

    rows = []
    for option in options.values():
        row = {k: v for k, v in option.items() if not k.startswith("_")}
        group_type = row.get("group_type")
        row["is_test_data"] = (
            row.get("is_test_data") is True
            or option.get("_user_missing_group_type") is True
            or row.get("student_id") == TEST_STUDENT_ID
            or group_type not in FORMAL_GROUP_TYPES
        )
        if _student_option_matches(row, class_name, group_filter):
            rows.append(row)
    return sorted(rows, key=lambda row: str(row.get("student_id") or ""))


def _attempt_query(activity_type, test_role, class_name, group_filter, student_id):
    query = {"activity_type": activity_type}
    if activity_type == "test":
        query["test_role"] = test_role
    elif activity_type == "practice":
        query["test_role"] = None

    _apply_group_filter(query, class_name, group_filter, student_id)
    return query


def _read_attempts(activity_type, test_role, class_name, group_filter, student_id):
    if activity_type == "test":
        students = _combined_student_profiles(class_name, group_filter, student_id)
        student_ids = sorted(
            {
                str(row.get("student_id") or "").strip()
                for row in students
                if str(row.get("student_id") or "").strip()
            }
        )
        if not student_ids:
            return []

        query = {
            "student_id": {"$in": student_ids},
            "test_role": _legacy_test_role(test_role),
        }
        cursor = db.parsons_test_attempts.find(
            query,
            {
                "student_id": 1,
                "test_role": 1,
                "test_cycle_id": 1,
                "test_task_id": 1,
                "source_task_id": 1,
                "task_id": 1,
                "task_title": 1,
                "target_concept": 1,
                "attempt_no": 1,
                "is_correct": 1,
                "score": 1,
                "duration_sec": 1,
                "duration_seconds": 1,
                "submitted_at": 1,
                "submitted_at_utc": 1,
                "created_at": 1,
                "error_types": 1,
                "wrong_slots": 1,
                "wrong_indices": 1,
                "indent_errors": 1,
                "repeated_error": 1,
            },
        ).sort([("student_id", 1), ("task_id", 1), ("submitted_at", 1)])

        attempts = []
        for attempt in cursor:
            task_id = str(
                attempt.get("task_id")
                or attempt.get("test_task_id")
                or attempt.get("source_task_id")
                or ""
            ).strip()
            wrong_slots = attempt.get("wrong_slots")
            if not isinstance(wrong_slots, list):
                wrong_slots = attempt.get("wrong_indices") if isinstance(attempt.get("wrong_indices"), list) else []
            error_types = attempt.get("error_types")
            if not isinstance(error_types, list):
                error_types = []
                if wrong_slots:
                    error_types.append("sequence_error")
                if isinstance(attempt.get("indent_errors"), list) and attempt.get("indent_errors"):
                    error_types.append("indentation_error")
            attempts.append({
                "_id": attempt.get("_id"),
                "student_id": str(attempt.get("student_id") or "").strip(),
                "activity_type": "test",
                "test_role": test_role,
                "test_cycle_id": attempt.get("test_cycle_id"),
                "task_id": task_id,
                "task_title": attempt.get("task_title") or "",
                "target_concept": attempt.get("target_concept") or "unknown",
                "attempt_no": attempt.get("attempt_no") or 1,
                "is_correct": attempt.get("is_correct"),
                "score": attempt.get("score"),
                "duration_sec": attempt.get("duration_seconds") if attempt.get("duration_seconds") is not None else attempt.get("duration_sec"),
                "submitted_at": attempt.get("submitted_at_utc") or attempt.get("submitted_at"),
                "created_at": attempt.get("created_at"),
                "error_types": error_types,
                "wrong_slots": wrong_slots or [],
                "repeated_error": attempt.get("repeated_error") is True,
            })
        return attempts

    projection = {
        "student_id": 1,
        "class_name": 1,
        "group_type": 1,
        "is_test_data": 1,
        "activity_type": 1,
        "test_role": 1,
        "task_id": 1,
        "task_title": 1,
        "target_concept": 1,
        "attempt_no": 1,
        "is_correct": 1,
        "score": 1,
        "duration_sec": 1,
        "submitted_at": 1,
        "created_at": 1,
        "error_types": 1,
        "wrong_slots": 1,
        "repeated_error": 1,
    }
    return list(
        db.parsons_attempts_v2.find(
            _attempt_query(activity_type, test_role, class_name, group_filter, student_id),
            projection,
        ).sort([("student_id", 1), ("task_id", 1), ("attempt_no", 1), ("submitted_at", 1)])
    )


def _attempt_group_key(attempt):
    return (
        str(attempt.get("student_id") or ""),
        str(attempt.get("task_id") or ""),
        str(attempt.get("activity_type") or ""),
        str(attempt.get("test_role") or ""),
    )


def _enrich_attempts(attempts):
    student_profiles = _load_user_profiles(a.get("student_id") for a in attempts)
    task_profiles = _load_task_profiles(a.get("task_id") for a in attempts)
    enriched = []
    for attempt in attempts:
        sid = str(attempt.get("student_id") or "")
        task_id = str(attempt.get("task_id") or "")
        user = student_profiles.get(sid) or {}
        task = task_profiles.get(task_id) or {}
        row = dict(attempt)
        row["student_id"] = sid
        row["task_id"] = task_id
        row["class_name"] = (
            _optional_string(user.get("class_name"))
            or _optional_string(row.get("class_name"))
        )
        row["group_type"] = (
            _optional_string(user.get("group_type"))
            or _optional_string(row.get("group_type"))
        )
        row["is_test_data"] = (
            user.get("is_test_data") is True
            or user.get("_user_missing_group_type") is True
            or sid == TEST_STUDENT_ID
            or row.get("is_test_data") is True
            or row.get("group_type") not in FORMAL_GROUP_TYPES
        )
        row["task_title"] = (
            _optional_string(row.get("task_title"))
            or _optional_string(task.get("task_title"))
            or ""
        )
        row["target_concept"] = (
            _optional_string(row.get("target_concept"))
            or _optional_string(task.get("target_concept"))
            or "unknown"
        )
        row["duration_sec"] = _valid_duration(row.get("duration_sec"))
        enriched.append(row)
    return enriched

# 儀表板 KPI 計算
def _build_kpis(attempts):
    grouped = defaultdict(list)
    for attempt in attempts:
        grouped[_attempt_group_key(attempt)].append(attempt)

    first_attempts = []
    final_attempts = []
    for rows in grouped.values():
        ordered = sorted(rows, key=_attempt_sort_key)
        if ordered:
            first_attempts.append(ordered[0])
            final_attempts.append(ordered[-1])

    durations = [a["duration_sec"] for a in attempts if a.get("duration_sec") is not None]
    avg_attempts = (
        round(sum(len(rows) for rows in grouped.values()) / len(grouped), 2)
        if grouped else 0
    )
    return {
        "active_students": len({a.get("student_id") for a in attempts if a.get("student_id")}),
        "total_attempts": len(attempts),
        "first_try_correct_rate": _safe_rate(
            sum(1 for a in first_attempts if a.get("is_correct") is True),
            len(first_attempts),
        ),
        "final_correct_rate": _safe_rate(
            sum(1 for a in final_attempts if a.get("is_correct") is True),
            len(final_attempts),
        ),
        "avg_attempts_per_task": avg_attempts,
        "avg_duration_sec": round(sum(durations) / len(durations), 2) if durations else None,
    }

# 學生作答總覽
def _build_student_overview(attempts):
    grouped = defaultdict(list)
    for attempt in attempts:
        grouped[attempt.get("student_id")].append(attempt)

    rows = []
    for sid, student_attempts in grouped.items():
        by_task = defaultdict(list)
        for attempt in student_attempts:
            by_task[attempt.get("task_id")].append(attempt)
        final_attempts = [
            sorted(rows_for_task, key=_attempt_sort_key)[-1]
            for rows_for_task in by_task.values()
            if rows_for_task
        ]
        durations = [
            a.get("duration_sec")
            for a in student_attempts
            if a.get("duration_sec") is not None
        ]
        first = student_attempts[0] if student_attempts else {}
        task_count = len(by_task)
        rows.append({
            "student_id": sid,
            "class_name": first.get("class_name"),
            "group_type": first.get("group_type"),
            "is_test_data": first.get("is_test_data") is True,
            "task_count": task_count,
            "total_attempts": len(student_attempts),
            "correct_task_count": sum(1 for a in final_attempts if a.get("is_correct") is True),
            "avg_attempts_per_task": round(len(student_attempts) / task_count, 2) if task_count else 0,
            "avg_duration_sec": round(sum(durations) / len(durations), 2) if durations else None,
        })
    return sorted(rows, key=lambda row: str(row.get("student_id") or ""))


def _error_type_counts(attempts):
    counts = Counter()
    for attempt in attempts:
        for error_type in attempt.get("error_types") or []:
            error_type = _optional_string(error_type)
            if error_type:
                counts[error_type] += 1
    return [{"type": key, "count": count} for key, count in counts.most_common()]

# 儀表板題目錯誤分析
def _build_task_analysis(attempts):
    grouped = defaultdict(list)
    for attempt in attempts:
        grouped[attempt.get("task_id")].append(attempt)

    rows = []
    for task_id, task_attempts in grouped.items():
        first = task_attempts[0] if task_attempts else {}
        total = len(task_attempts)
        correct = sum(1 for a in task_attempts if a.get("is_correct") is True)
        wrong = sum(1 for a in task_attempts if a.get("is_correct") is False)
        rows.append({
            "task_id": task_id,
            "task_title": first.get("task_title") or "",
            "target_concept": first.get("target_concept") or "unknown",
            "total_attempts": total,
            "wrong_attempts": wrong,
            "correct_rate": _safe_rate(correct, total),
            "common_error_types": _error_type_counts(
                [a for a in task_attempts if a.get("is_correct") is False]
            ),
        })
    return sorted(rows, key=lambda row: (-row["wrong_attempts"], str(row["task_id"] or "")))

# 儀表板概念錯誤分析
def _build_concept_analysis(attempts):
    grouped = defaultdict(list)
    for attempt in attempts:
        concept = _optional_string(attempt.get("target_concept")) or "unknown"
        grouped[concept].append(attempt)

    rows = []
    for concept, concept_attempts in grouped.items():
        total = len(concept_attempts)
        correct = sum(1 for a in concept_attempts if a.get("is_correct") is True)
        wrong = sum(1 for a in concept_attempts if a.get("is_correct") is False)
        rows.append({
            "target_concept": concept,
            "total_attempts": total,
            "wrong_attempts": wrong,
            "correct_rate": _safe_rate(correct, total),
            "repeated_error_count": sum(1 for a in concept_attempts if a.get("repeated_error") is True),
        })
    return sorted(rows, key=lambda row: (-row["wrong_attempts"], row["target_concept"]))

# 學生個別操作歷程
def _read_student_logs(student_id, activity_type, test_role, limit, class_name, group_filter):
    sid = _optional_string(student_id)
    if not sid:
        return []

    query = {"activity_type": activity_type}
    if activity_type == "test":
        query["test_role"] = test_role
    elif activity_type == "practice":
        query["test_role"] = None
    _apply_group_filter(query, class_name, group_filter, sid)

    projection = {
        "_id": 0,
        "event_at": 1,
        "event_type": 1,
        "page": 1,
        "task_id": 1,
        "question_id": 1,
        "unit_id": 1,
        "question_type": 1,
        "user_id": 1,
        "from_video_id": 1,
        "from_video_title": 1,
        "watch_session_id": 1,
        "to_task_id": 1,
        "to_question_type": 1,
        "attempt_id": 1,
        "attempt_no": 1,
        "target_concept": 1,
        "review_type": 1,
        "hint_type": 1,
        "hint_no": 1,
        "max_hint_count": 1,
        "hint_content": 1,
        "hint_text": 1,
        "hint_click_no": 1,
        "trigger_method": 1,
        "button_name": 1,
        "close_method": 1,
        "return_method": 1,
        "error_type": 1,
        "wrong_slots": 1,
        "metadata": 1,
    }
    cursor = (
        db.learning_logs.find(query, projection)
        .sort([("event_at", -1), ("created_at", -1)])
        .limit(max(1, min(int(limit or 100), 500)))
    )
    rows = list(cursor)

    if activity_type == "practice":
        video_query = {}
        _apply_group_filter(video_query, class_name, group_filter, sid)
        video_projection = {
            "_id": 0,
            "event_at": 1,
            "event_type": 1,
            "student_id": 1,
            "class_name": 1,
            "group_type": 1,
            "is_test_data": 1,
            "video_id": 1,
            "video_title": 1,
            "unit_id": 1,
            "unit": 1,
            "watch_session_id": 1,
            "watch_seconds": 1,
            "watch_delta_sec": 1,
            "current_time_sec": 1,
            "video_duration_sec": 1,
            "reached_end": 1,
            "page": 1,
            "created_at": 1,
        }
        for log in db.video_rewatch_logs.find(
            video_query,
            video_projection,
        ).sort([("event_at", -1), ("created_at", -1)]).limit(max(1, min(int(limit or 100), 500))):
            rows.append({
                "event_at": log.get("event_at") or log.get("created_at"),
                "event_type": log.get("event_type"),
                "page": log.get("page") or "student_learning",
                "task_id": None,
                "unit_id": log.get("unit_id") or log.get("unit"),
                "question_type": "video",
                "from_video_id": log.get("video_id"),
                "from_video_title": log.get("video_title"),
                "watch_session_id": log.get("watch_session_id"),
                "metadata": {
                    "video_id": log.get("video_id"),
                    "video_title": log.get("video_title"),
                    "unit_id": log.get("unit_id") or log.get("unit"),
                    "watch_session_id": log.get("watch_session_id"),
                    "watch_seconds": log.get("watch_seconds"),
                    "watch_delta_sec": log.get("watch_delta_sec"),
                    "current_time_sec": log.get("current_time_sec"),
                    "video_duration_sec": log.get("video_duration_sec"),
                    "reached_end": log.get("reached_end"),
                },
            })

    def _student_log_sort_key(row):
        value = row.get("event_at") or row.get("created_at")
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        return _utc_min()

    rows = sorted(rows, key=_student_log_sort_key)
    max_rows = max(1, min(int(limit or 100), 500))
    if len(rows) > max_rows:
        rows = rows[-max_rows:]
    return [_json_safe(row) for row in rows]


def _student_attempt_rows(attempts, student_id):
    sid = _optional_string(student_id)
    if not sid:
        return []

    rows = []
    for attempt in sorted(attempts, key=_attempt_sort_key):
        if str(attempt.get("student_id") or "") != sid:
            continue
        rows.append(_json_safe({
            "submitted_at": attempt.get("submitted_at") or attempt.get("created_at"),
            "task_id": attempt.get("task_id"),
            "task_title": attempt.get("task_title"),
            "target_concept": attempt.get("target_concept"),
            "attempt_no": attempt.get("attempt_no"),
            "is_correct": attempt.get("is_correct"),
            "score": attempt.get("score"),
            "error_types": attempt.get("error_types") or [],
            "wrong_slots": attempt.get("wrong_slots") or [],
            "duration_sec": attempt.get("duration_sec"),
        }))
    return rows


def _build_analysis_payload(forced_activity_type=None, forced_test_role=None):
    class_name = _optional_string(request.args.get("class_name"))
    group_filter = _normalize_group_filter(request.args.get("group_type"))
    student_id = _optional_string(request.args.get("student_id"))
    activity_type = _normalize_activity_type(forced_activity_type or request.args.get("activity_type"))
    test_role = _normalize_test_role(forced_test_role or request.args.get("test_role"))
    if activity_type == "test" and test_role not in VALID_TEST_ROLES:
        test_role = "pretest"
    if activity_type == "practice":
        test_role = None

    attempts = _enrich_attempts(
        _read_attempts(activity_type, test_role, class_name, group_filter, student_id)
    )
    attempts = [
        attempt for attempt in attempts
        if _profile_matches_filter(attempt, class_name, group_filter, student_id)
    ]
    logs = _read_student_logs(
        student_id,
        activity_type,
        test_role,
        request.args.get("logs_limit") or 100,
        class_name,
        group_filter,
    )
    return {
        "ok": True,
        "filters": {
            "activity_type": activity_type,
            "test_role": test_role,
            "class_name": class_name,
            "group_type": group_filter,
            "student_id": student_id,
            "exclude_test_data": group_filter in FORMAL_GROUP_TYPES,
        },
        "kpis": _build_kpis(attempts),
        "student_options": _read_student_options(class_name, group_filter),
        "user_group_overview": _user_group_overview(class_name, group_filter),
        "test_completion_overview": _test_completion_overview(
            test_role,
            class_name,
            group_filter,
            student_id,
        ) if activity_type == "test" else None,
        "student_progress_rows": _student_progress_rows(class_name, group_filter, student_id),
        "student_overview": _build_student_overview(attempts),
        "task_error_analysis": _build_task_analysis(attempts),
        "concept_error_analysis": _build_concept_analysis(attempts),
        "student_attempts": _student_attempt_rows(attempts, student_id),
        "student_logs": logs,
    }


@teacher_analysis_bp.get("/parsons")
def parsons_analysis():
    return jsonify(_build_analysis_payload())


@teacher_analysis_bp.get("/practice")
def practice_analysis_compat():
    payload = _build_analysis_payload(forced_activity_type="practice")
    return jsonify({
        "ok": True,
        "students": [
            {
                "student_id": row["student_id"],
                "practice_attempts": row["total_attempts"],
                "avg_duration_sec": row["avg_duration_sec"],
                "total_review_yes": None,
            }
            for row in payload["student_overview"]
        ],
        "slot_ranking": [],
        "analysis": payload,
    })
