# 學生作答紀錄分析頁面.py檔
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


def _user_group_overview(class_name=None):
    base = {"role": "student"}
    if class_name:
        base["class_name"] = class_name

    control_count = db.users.count_documents({
        **base,
        "is_test_data": {"$ne": True},
        "group_type": "control",
    })
    experimental_count = db.users.count_documents({
        **base,
        "is_test_data": {"$ne": True},
        "group_type": "experimental",
    })
    test_ids = set()
    cursor = db.users.find(
        {
            **base,
            "$or": [
                {"is_test_data": True},
                {"student_id": TEST_STUDENT_ID},
                {"group_type": None},
                {"group_type": ""},
            ],
        },
        {"_id": 0, "student_id": 1},
    )
    for user in cursor:
        sid = str(user.get("student_id") or "").strip()
        if sid:
            test_ids.add(sid)

    return {
        "experimental_count": experimental_count,
        "control_count": control_count,
        "test_account_count": len(test_ids),
        "formal_student_count": experimental_count + control_count,
        "total_student_count": experimental_count + control_count + len(test_ids),
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


def _student_test_task_progress(test_role, student_ids):
    ids = sorted({str(sid) for sid in student_ids if sid})
    if not ids:
        return {}

    progress = defaultdict(lambda: {"task_ids": set(), "latest_at": _utc_min()})

    def add_attempt(attempt):
        sid = str(attempt.get("student_id") or "").strip()
        task_id = str(
            attempt.get("test_task_id")
            or attempt.get("task_id")
            or attempt.get("source_task_id")
            or ""
        ).strip()
        if not sid:
            return
        if task_id:
            progress[sid]["task_ids"].add(task_id)
        latest = max(
            _sort_datetime(attempt.get("submitted_at")),
            _sort_datetime(attempt.get("completed_at")),
            _sort_datetime(attempt.get("updated_at")),
            _sort_datetime(attempt.get("created_at")),
        )
        if latest > progress[sid]["latest_at"]:
            progress[sid]["latest_at"] = latest

    v2_cursor = db.parsons_attempts_v2.find(
        {
            "student_id": {"$in": ids},
            "activity_type": "test",
            "test_role": test_role,
        },
        {
            "_id": 0,
            "student_id": 1,
            "task_id": 1,
            "submitted_at": 1,
            "completed_at": 1,
            "updated_at": 1,
            "created_at": 1,
        },
    )
    for attempt in v2_cursor:
        add_attempt(attempt)

    legacy_role = _legacy_test_role(test_role)
    legacy_roles = [legacy_role]
    if test_role not in legacy_roles:
        legacy_roles.append(test_role)
    legacy_cursor = db.parsons_test_attempts.find(
        {"student_id": {"$in": ids}, "test_role": {"$in": legacy_roles}},
        {
            "_id": 0,
            "student_id": 1,
            "test_task_id": 1,
            "task_id": 1,
            "source_task_id": 1,
            "submitted_at": 1,
            "completed_at": 1,
            "updated_at": 1,
            "created_at": 1,
        },
    )
    for attempt in legacy_cursor:
        add_attempt(attempt)

    return {
        sid: {
            "task_count": len(row["task_ids"]),
            "latest_at": row["latest_at"] if row["latest_at"] != _utc_min() else None,
        }
        for sid, row in progress.items()
    }


def _student_test_task_counts(test_role, student_ids):
    progress = _student_test_task_progress(test_role, student_ids)
    return {sid: int(row.get("task_count") or 0) for sid, row in progress.items()}


def _test_completion_overview(test_role, class_name, group_filter, student_id=None):
    if test_role not in VALID_TEST_ROLES:
        return None

    students = list(db.users.find(
        _student_user_query(class_name, group_filter, student_id),
        {"_id": 0, "student_id": 1, "group_type": 1, "is_test_data": 1},
    ))
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

    test_group_ids = {
        str(row.get("student_id") or "").strip()
        for row in students
        if (
            row.get("is_test_data") is True
            or str(row.get("student_id") or "").strip() == TEST_STUDENT_ID
            or row.get("group_type") in (None, "")
        )
    }
    test_group_ids = {sid for sid in test_group_ids if sid}
    if test_group_ids:
        test_completed = len(test_group_ids & completed_ids)
        groups.append({
            "group_type": TEST_DATA_GROUP_FILTER,
            "total_students": len(test_group_ids),
            "completed_students": test_completed,
            "pending_students": max(len(test_group_ids) - test_completed, 0),
            "completion_rate": _safe_rate(test_completed, len(test_group_ids)),
        })

    return {
        "test_role": test_role,
        "expected_task_count": expected_task_count,
        "total_students": total,
        "completed_students": completed,
        "pending_students": max(total - completed, 0),
        "completion_rate": _safe_rate(completed, total),
        "data_sources": ["parsons_attempts_v2", "parsons_test_attempts"],
        "groups": groups,
    }


def _progress_status(completed_count, expected_count):
    completed_count = int(completed_count or 0)
    expected_count = int(expected_count or 0)
    if expected_count > 0 and completed_count >= expected_count:
        return "completed"
    if completed_count > 0:
        return "in_progress"
    return "not_started"


def _practice_status(completed_count, attempted_count):
    completed_count = int(completed_count or 0)
    attempted_count = int(attempted_count or 0)
    if attempted_count <= 0:
        return "not_started"
    if completed_count >= attempted_count:
        return "completed"
    return "in_progress"


def _attempt_is_correct(attempt):
    value = attempt.get("is_correct")
    if value is True:
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return False


def _attempt_is_wrong(attempt):
    value = attempt.get("is_correct")
    if value is False:
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 0
    if isinstance(value, str):
        return value.strip().lower() in {"false", "0", "no"}
    return False


def _practice_progress(student_ids):
    ids = sorted({str(sid) for sid in student_ids if sid})
    if not ids:
        return {}

    attempts_by_student_task = defaultdict(lambda: defaultdict(list))
    latest_by_student = {}
    cursor = db.parsons_attempts_v2.find(
        {
            "student_id": {"$in": ids},
            "activity_type": "practice",
        },
        {
            "_id": 1,
            "student_id": 1,
            "task_id": 1,
            "task_title": 1,
            "target_concept": 1,
            "attempt_no": 1,
            "is_correct": 1,
            "submitted_at": 1,
            "created_at": 1,
            "unit": 1,
            "unit_id": 1,
            "chapter": 1,
            "lesson": 1,
            "video_title": 1,
        },
    )
    for attempt in cursor:
        sid = str(attempt.get("student_id") or "").strip()
        if not sid:
            continue
        task_id = str(attempt.get("task_id") or "").strip()
        if task_id:
            attempts_by_student_task[sid][task_id].append(attempt)
        latest = _sort_datetime(attempt.get("submitted_at") or attempt.get("created_at"))
        current_latest = latest_by_student.get(sid)
        if current_latest is None or latest > current_latest["at"]:
            latest_by_student[sid] = {"at": latest, "attempt": attempt}

    task_ids = {
        task_id
        for tasks_by_id in attempts_by_student_task.values()
        for task_id in tasks_by_id.keys()
        if task_id
    }
    task_profiles = _load_task_profiles(task_ids)

    progress = {}
    for sid in ids:
        tasks_by_id = attempts_by_student_task.get(sid) or {}
        attempted_tasks = len(tasks_by_id)
        completed_tasks = 0
        attempt_count = 0
        for attempts in tasks_by_id.values():
            attempt_count += len(attempts)
            final_attempt = sorted(attempts, key=_attempt_sort_key)[-1] if attempts else {}
            if _attempt_is_correct(final_attempt):
                completed_tasks += 1

        latest_entry = latest_by_student.get(sid) or {}
        latest_attempt = latest_entry.get("attempt") or {}
        latest_task_id = str(latest_attempt.get("task_id") or "").strip()
        latest_profile = task_profiles.get(latest_task_id) or {}
        current_unit = (
            _optional_string(latest_attempt.get("unit"))
            or _optional_string(latest_attempt.get("unit_id"))
            or _optional_string(latest_attempt.get("chapter"))
            or _optional_string(latest_attempt.get("lesson"))
            or _optional_string(latest_attempt.get("video_title"))
            or _optional_string(latest_profile.get("unit"))
            or _optional_string(latest_profile.get("video_title"))
            or _optional_string(latest_profile.get("target_concept"))
            or ""
        )
        current_title = (
            _optional_string(latest_attempt.get("task_title"))
            or _optional_string(latest_profile.get("task_title"))
            or latest_task_id
        )
        latest_at = latest_entry.get("at")
        progress[sid] = {
            "attempted_tasks": attempted_tasks,
            "completed_tasks": completed_tasks,
            "attempt_count": attempt_count,
            "current_task_id": latest_task_id,
            "current_task_title": current_title,
            "current_unit": current_unit,
            "latest_at": latest_at if latest_at and latest_at != _utc_min() else None,
        }

    return progress


def _unit_sort_key(unit_label):
    text = str(unit_label or "").strip()
    prefix = "".join(ch for ch in text if not ch.isdigit()).lower()
    digits = "".join(ch for ch in text if ch.isdigit())
    number = int(digits) if digits else 999999
    return (prefix, number, text.lower())


def _task_unit_label(doc):
    return (
        _optional_string((doc or {}).get("unit"))
        or _optional_string((doc or {}).get("unit_id"))
        or _optional_string((doc or {}).get("chapter"))
        or _optional_string((doc or {}).get("lesson"))
        or _optional_string((doc or {}).get("video_title"))
        or _optional_string((doc or {}).get("target_concept"))
        or _optional_string((doc or {}).get("concept_tag"))
        or "未分類"
    )


def _task_title_label(doc, fallback_id=""):
    return (
        _optional_string((doc or {}).get("task_title"))
        or _optional_string((doc or {}).get("title"))
        or _optional_string((doc or {}).get("name"))
        or _optional_string(fallback_id)
        or "-"
    )


def _practice_task_catalog():
    projection = {
        "_id": 1,
        "task_id": 1,
        "id": 1,
        "title": 1,
        "task_title": 1,
        "name": 1,
        "unit": 1,
        "unit_id": 1,
        "chapter": 1,
        "lesson": 1,
        "video_title": 1,
        "target_concept": 1,
        "concept": 1,
        "concept_tag": 1,
        "tags": 1,
        "order": 1,
        "sort_order": 1,
        "created_at": 1,
        "video_id": 1,
        "video_id_str": 1,
    }
    catalog = {}
    aliases = {}
    visible_video_object_ids, visible_video_string_ids = _visible_practice_video_ids()
    task_query = {
        "deleted": {"$ne": True},
        "is_deleted": {"$ne": True},
        "$or": [
            {"enabled": True},
            {"status": "published"},
            {"review_status": "published"},
        ],
    }
    for task in db.parsons_tasks.find(task_query, projection):
        if not _task_belongs_to_visible_video(task, visible_video_object_ids, visible_video_string_ids):
            continue
        task_id = (
            _optional_string(task.get("task_id"))
            or _optional_string(task.get("id"))
            or str(task.get("_id") or "")
        )
        if not task_id:
            continue
        unit_label = _task_unit_label(task)
        alias_values = [
            str(key or "").strip()
            for key in (task.get("_id"), task.get("task_id"), task.get("id"))
            if str(key or "").strip()
        ]
        profile = {
            "task_id": task_id,
            "task_title": _task_title_label(task, task_id),
            "unit_key": unit_label,
            "unit_label": unit_label,
            "target_concept": _first_concept(task),
            "order": task.get("sort_order") if task.get("sort_order") is not None else task.get("order"),
            "created_at": task.get("created_at"),
            "aliases": list(dict.fromkeys(alias_values)),
        }
        catalog[task_id] = profile
        for key_text in alias_values:
            aliases[key_text] = profile
    return catalog, aliases


def _practice_attempt_projection():
    return {
        "_id": 1,
        "legacy_attempt_id": 1,
        "student_id": 1,
        "task_id": 1,
        "task_title": 1,
        "target_concept": 1,
        "task_attempt_session": 1,
        "attempt_no": 1,
        "is_correct": 1,
        "submitted_at": 1,
        "created_at": 1,
        "unit": 1,
        "unit_id": 1,
        "chapter": 1,
        "lesson": 1,
        "video_title": 1,
        "ai_hint_1_text": 1,
        "ai_hint_2_text": 1,
        "ai_hint_1_meta": 1,
        "ai_hint_2_meta": 1,
        "ai_hint_generation_count": 1,
        "ai_hint_view_count": 1,
        "ai_hint_generation_count_before_submit": 1,
        "ai_hint_view_count_before_submit": 1,
        "latest_ai_hint_no_before_submit": 1,
        "submitted_after_ai_hint": 1,
        "source_hint_id": 1,
    }


def _safe_int(value, default=0):
    try:
        parsed = int(value)
        return parsed
    except Exception:
        return default


def _int_list(value):
    if not isinstance(value, list):
        return []
    output = []
    for item in value:
        try:
            parsed = int(item)
        except Exception:
            continue
        if parsed >= 0:
            output.append(parsed)
    return sorted(set(output))


def _practice_round_no(attempt):
    return max(1, _safe_int((attempt or {}).get("task_attempt_session"), 1))


def _practice_task_profile_from_attempt(attempt):
    task_id = str((attempt or {}).get("task_id") or "").strip()
    unit_label = _task_unit_label(attempt)
    return {
        "task_id": task_id,
        "task_title": _task_title_label(attempt, task_id),
        "unit_key": unit_label,
        "unit_label": unit_label,
        "target_concept": _optional_string((attempt or {}).get("target_concept")),
        "order": None,
        "created_at": None,
        "aliases": [task_id] if task_id else [],
    }


PRACTICE_HINT_EVENT_TYPES = {
    "first_error_hint_shown",
    "view_hint",
    "review_open",
    "ai_hint_modal_open",
    "ai_hint_reopen",
    "ai_hint_view",
    "ai_hint_second_request",
    "second_hint_reminder_shown",
    "second_hint_reminder_clicked",
    "second_hint_reminder_ignored",
    "fixed_semantic_feedback_presented",
}


def _compact_hint_text(text, max_len=90):
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[:max_len].rstrip() + "..."


def _practice_hint_log_summary(student_ids):
    ids = sorted({str(sid) for sid in student_ids if sid})
    if not ids:
        return {}

    summaries = defaultdict(lambda: {"count": 0, "events": [], "texts": [], "latest_at": None})
    cursor = db.learning_logs.find(
        {
            "student_id": {"$in": ids},
            "activity_type": "practice",
            "event_type": {"$in": sorted(PRACTICE_HINT_EVENT_TYPES)},
        },
        {
            "_id": 1,
            "student_id": 1,
            "task_id": 1,
            "event_type": 1,
            "event_at": 1,
            "created_at": 1,
            "hint_content": 1,
            "hint_text": 1,
            "ai_hint_1_text": 1,
            "ai_hint_2_text": 1,
            "requested_hint_no": 1,
            "hint_no": 1,
            "metadata": 1,
        },
    )
    for log in cursor:
        metadata = log.get("metadata") if isinstance(log.get("metadata"), dict) else {}
        sid = str(log.get("student_id") or "").strip()
        task_id = str(
            log.get("task_id")
            or metadata.get("task_id")
            or metadata.get("question_id")
            or ""
        ).strip()
        if not sid or not task_id:
            continue
        key = (sid, task_id)
        summary = summaries[key]
        summary["count"] += 1
        event_at = _sort_datetime(log.get("event_at") or log.get("created_at"))
        if event_at and (summary["latest_at"] is None or event_at > summary["latest_at"]):
            summary["latest_at"] = event_at
        hint_text = (
            _optional_string(log.get("hint_content"))
            or _optional_string(log.get("hint_text"))
            or _optional_string(log.get("ai_hint_2_text"))
            or _optional_string(log.get("ai_hint_1_text"))
            or _optional_string(metadata.get("hint_content"))
            or _optional_string(metadata.get("hint_text"))
            or _optional_string(metadata.get("ai_hint_2_text"))
            or _optional_string(metadata.get("ai_hint_1_text"))
            or _optional_string(metadata.get("first_system_hint_text"))
        )
        if hint_text:
            summary["texts"].append(hint_text)
        summary["events"].append({
            "log_id": str(log.get("_id") or ""),
            "event_type": log.get("event_type"),
            "hint_no": log.get("requested_hint_no") or log.get("hint_no") or metadata.get("requested_hint_no") or metadata.get("hint_no"),
            "event_at": event_at,
            "hint_text": _compact_hint_text(hint_text, 120) if hint_text else "",
        })

    for summary in summaries.values():
        summary["events"] = sorted(
            summary["events"],
            key=lambda item: _sort_datetime(item.get("event_at")),
            reverse=True,
        )[:8]
        summary["texts"] = list(dict.fromkeys(summary["texts"]))[:4]
    return summaries


def _attempt_hint_summary(attempt, log_summary):
    attempt = attempt or {}
    log_summary = log_summary or {}
    ai_hint_1 = _optional_string(attempt.get("ai_hint_1_text"))
    ai_hint_2 = _optional_string(attempt.get("ai_hint_2_text"))
    view_count = max(
        _safe_int(attempt.get("ai_hint_view_count"), 0),
        _safe_int(attempt.get("ai_hint_view_count_before_submit"), 0),
    )
    generation_count = max(
        _safe_int(attempt.get("ai_hint_generation_count"), 0),
        _safe_int(attempt.get("ai_hint_generation_count_before_submit"), 0),
    )
    text_count = len([text for text in (ai_hint_1, ai_hint_2) if text])
    log_count = int(log_summary.get("count") or 0)
    total_count = max(log_count, view_count, text_count)
    text_parts = []
    if ai_hint_1:
        text_parts.append(f"第1則：{_compact_hint_text(ai_hint_1)}")
    if ai_hint_2:
        text_parts.append(f"第2則：{_compact_hint_text(ai_hint_2)}")
    for text in log_summary.get("texts") or []:
        if len(text_parts) >= 2:
            break
        compact = _compact_hint_text(text)
        if compact and all(compact not in part for part in text_parts):
            text_parts.append(compact)

    summary = {
        "attempt_id": str(attempt.get("_id") or ""),
        "legacy_attempt_id": str(attempt.get("legacy_attempt_id") or ""),
        "source_hint_id": attempt.get("source_hint_id"),
        "submitted_after_ai_hint": attempt.get("submitted_after_ai_hint") is True,
        "latest_ai_hint_no_before_submit": _safe_int(attempt.get("latest_ai_hint_no_before_submit"), 0),
        "ai_hint_view_count": view_count,
        "ai_hint_generation_count": generation_count,
        "log_hint_event_count": log_count,
        "ai_hint_1_text": ai_hint_1 or "",
        "ai_hint_2_text": ai_hint_2 or "",
        "ai_hint_1_meta": attempt.get("ai_hint_1_meta") if isinstance(attempt.get("ai_hint_1_meta"), dict) else {},
        "ai_hint_2_meta": attempt.get("ai_hint_2_meta") if isinstance(attempt.get("ai_hint_2_meta"), dict) else {},
        "hint_log_events": log_summary.get("events") or [],
    }
    return total_count, "；".join(text_parts) if text_parts else "-", summary


def _hint_log_summary_for_profile(hint_logs, student_id, profile):
    keys = [profile.get("task_id")] + list(profile.get("aliases") or [])
    for key in dict.fromkeys(str(item or "").strip() for item in keys if item):
        summary = hint_logs.get((student_id, key))
        if summary:
            return summary
    return None


def _practice_task_state(student, profile, attempts, hint_log_summary):
    attempts = sorted(attempts or [], key=_attempt_sort_key)
    if not attempts:
        return {
            "student_id": student.get("student_id"),
            "student_name": student.get("name") or "",
            "class_name": student.get("class_name") or "",
            "group_type": student.get("group_type"),
            "is_test_data": student.get("is_test_data") is True,
            "unit_key": profile.get("unit_key"),
            "unit_label": profile.get("unit_label"),
            "task_id": profile.get("task_id"),
            "task_title": profile.get("task_title"),
            "status": "not_started",
            "submission_count": 0,
            "round_no": None,
            "attempt_no": None,
            "round_attempt_count": 0,
            "result": "not_started",
            "is_correct": None,
            "last_submitted_at": None,
            "hint_total_count": 0,
            "hint_summary_text": "-",
            "hint_summary": {},
        }

    latest_round = max(_practice_round_no(attempt) for attempt in attempts)
    round_attempts = [
        attempt
        for attempt in attempts
        if _practice_round_no(attempt) == latest_round
    ]
    final_attempt = sorted(round_attempts or attempts, key=_attempt_sort_key)[-1]
    is_correct = _attempt_is_correct(final_attempt)
    status = "completed" if is_correct else "in_progress"
    task_id = str(profile.get("task_id") or final_attempt.get("task_id") or "").strip()
    hint_total, hint_text, hint_summary = _attempt_hint_summary(final_attempt, hint_log_summary)
    return {
        "student_id": student.get("student_id"),
        "student_name": student.get("name") or "",
        "class_name": student.get("class_name") or "",
        "group_type": student.get("group_type"),
        "is_test_data": student.get("is_test_data") is True,
        "unit_key": profile.get("unit_key"),
        "unit_label": profile.get("unit_label"),
        "task_id": task_id,
        "task_title": _optional_string(final_attempt.get("task_title")) or profile.get("task_title"),
        "status": status,
        "submission_count": len(attempts),
        "round_no": latest_round,
        "attempt_no": final_attempt.get("attempt_no"),
        "round_attempt_count": len(round_attempts or attempts),
        "result": "correct" if is_correct else "incorrect",
        "is_correct": is_correct,
        "last_submitted_at": final_attempt.get("submitted_at") or final_attempt.get("created_at"),
        "hint_total_count": hint_total,
        "hint_summary_text": hint_text,
        "hint_summary": hint_summary,
    }


def _task_profile_sort_key(profile):
    order_value = profile.get("order")
    order = _safe_int(order_value, 999999) if order_value is not None else 999999
    return (
        _unit_sort_key(profile.get("unit_label")),
        order,
        str(profile.get("task_id") or "").lower(),
        str(profile.get("task_title") or "").lower(),
    )


def _visible_practice_video_ids():
    query = {
        "deleted": {"$nin": [True, 1, "true", "True"]},
        "is_deleted": {"$nin": [True, 1, "true", "True"]},
        "active": {"$nin": [False, 0, "false", "False"]},
    }
    object_ids = set()
    string_ids = set()
    for video in db.videos.find(query, {"_id": 1}):
        vid = video.get("_id")
        if vid is None:
            continue
        object_ids.add(vid)
        string_ids.add(str(vid))
    return object_ids, string_ids


def _task_belongs_to_visible_video(task, visible_video_object_ids, visible_video_string_ids):
    raw_video_id = (task or {}).get("video_id")
    raw_video_id_str = _optional_string((task or {}).get("video_id_str"))
    if raw_video_id is None and not raw_video_id_str:
        return True

    candidates = []
    if raw_video_id is not None:
        candidates.append(raw_video_id)
        candidates.append(str(raw_video_id))
    if raw_video_id_str:
        candidates.append(raw_video_id_str)
        if ObjectId.is_valid(raw_video_id_str):
            try:
                candidates.append(ObjectId(raw_video_id_str))
            except Exception:
                pass

    for candidate in candidates:
        if candidate in visible_video_object_ids:
            return True
        if str(candidate) in visible_video_string_ids:
            return True
    return False


def _practice_task_expand_row(state):
    return {
        "student_id": state.get("student_id"),
        "unit_key": state.get("unit_key"),
        "unit_label": state.get("unit_label"),
        "task_id": state.get("task_id"),
        "task_title": state.get("task_title"),
        "status": state.get("status"),
        "submission_count": state.get("submission_count"),
        "last_submitted_at": state.get("last_submitted_at"),
    }


def _practice_unit_progress_payload(class_name, group_filter, student_id=None):
    students = list(db.users.find(
        _student_user_query(class_name, group_filter, student_id),
        {
            "_id": 0,
            "student_id": 1,
            "name": 1,
            "class_name": 1,
            "group_type": 1,
            "is_test_data": 1,
        },
    ))
    student_ids = [
        str(row.get("student_id") or "").strip()
        for row in students
        if str(row.get("student_id") or "").strip()
    ]
    if not student_ids:
        return {"columns": [], "rows": [], "latest_task_rows": []}

    catalog, aliases = _practice_task_catalog()
    attempts_by_student_task = defaultdict(lambda: defaultdict(list))
    cursor = db.parsons_attempts_v2.find(
        {"student_id": {"$in": student_ids}, "activity_type": "practice"},
        _practice_attempt_projection(),
    )
    for attempt in cursor:
        sid = str(attempt.get("student_id") or "").strip()
        raw_task_id = str(attempt.get("task_id") or "").strip()
        if not sid or not raw_task_id:
            continue
        profile = aliases.get(raw_task_id)
        if not profile:
            continue
        attempts_by_student_task[sid][profile["task_id"]].append(attempt)

    tasks_by_unit = defaultdict(list)
    for profile in sorted(catalog.values(), key=_task_profile_sort_key):
        tasks_by_unit[profile["unit_key"]].append(profile)

    unit_keys = sorted(tasks_by_unit.keys(), key=_unit_sort_key)
    columns = [
        {
            "unit_key": unit_key,
            "unit_label": unit_key,
            "task_total": len(tasks_by_unit.get(unit_key) or []),
        }
        for unit_key in unit_keys
    ]
    hint_logs = _practice_hint_log_summary(student_ids)

    rows = []
    latest_task_rows = []
    for student in students:
        sid = str(student.get("student_id") or "").strip()
        if not sid:
            continue
        normalized_student = {
            "student_id": sid,
            "name": student.get("name") or "",
            "class_name": student.get("class_name") or "",
            "group_type": student.get("group_type"),
            "is_test_data": (
                student.get("is_test_data") is True
                or sid == TEST_STUDENT_ID
                or student.get("group_type") in (None, "")
            ),
        }
        units = {}
        overall_completed = 0
        overall_total = 0
        latest_at = None
        for unit_key in unit_keys:
            task_rows = []
            completed = 0
            attempted = 0
            for profile in tasks_by_unit.get(unit_key) or []:
                task_id = profile.get("task_id")
                task_attempts = attempts_by_student_task.get(sid, {}).get(task_id, [])
                state = _practice_task_state(
                    normalized_student,
                    profile,
                    task_attempts,
                    _hint_log_summary_for_profile(hint_logs, sid, profile),
                )
                if state["status"] == "completed":
                    completed += 1
                if state["submission_count"] > 0:
                    attempted += 1
                if state.get("last_submitted_at"):
                    state_time = _sort_datetime(state.get("last_submitted_at"))
                    if latest_at is None or state_time > latest_at:
                        latest_at = state_time
                task_rows.append(_practice_task_expand_row(state))
                latest_task_rows.append({
                    **state,
                    "row_key": f"{sid}|{task_id}",
                })
            total = len(task_rows)
            overall_completed += completed
            overall_total += total
            if total > 0 and completed >= total:
                status = "completed"
            elif attempted > 0:
                status = "in_progress"
            else:
                status = "not_started"
            units[unit_key] = {
                "unit_key": unit_key,
                "unit_label": unit_key,
                "completed_tasks": completed,
                "attempted_tasks": attempted,
                "total_tasks": total,
                "status": status,
                "tasks": task_rows,
            }

        rows.append(_json_safe({
            **normalized_student,
            "units": units,
            "overall_completed_tasks": overall_completed,
            "overall_total_tasks": overall_total,
            "overall_status": (
                "completed"
                if overall_total > 0 and overall_completed >= overall_total
                else "in_progress"
                if any((cell.get("attempted_tasks") or 0) > 0 for cell in units.values())
                else "not_started"
            ),
            "latest_practice_at": latest_at,
        }))

    latest_task_rows = sorted(
        latest_task_rows,
        key=lambda row: (
            str(row.get("student_id") or ""),
            _unit_sort_key(row.get("unit_label")),
            str(row.get("task_id") or ""),
        ),
    )
    return {
        "columns": _json_safe(columns),
        "rows": sorted(rows, key=lambda row: str(row.get("student_id") or "")),
        "latest_task_rows": [_json_safe(row) for row in latest_task_rows],
    }


def _latest_activity_map(student_ids):
    ids = sorted({str(sid) for sid in student_ids if sid})
    latest_map = {}
    if not ids:
        return latest_map

    attempt_cursor = db.parsons_attempts_v2.find(
        {"student_id": {"$in": ids}},
        {"_id": 0, "student_id": 1, "submitted_at": 1, "created_at": 1},
    )
    for attempt in attempt_cursor:
        sid = str(attempt.get("student_id") or "").strip()
        latest = _sort_datetime(attempt.get("submitted_at") or attempt.get("created_at"))
        if sid and latest > latest_map.get(sid, _utc_min()):
            latest_map[sid] = latest

    log_cursor = db.learning_logs.find(
        {"student_id": {"$in": ids}},
        {"_id": 0, "student_id": 1, "event_at": 1, "created_at": 1, "started_at": 1, "end_at": 1},
    )
    for log in log_cursor:
        sid = str(log.get("student_id") or "").strip()
        latest = max(
            _sort_datetime(log.get("event_at")),
            _sort_datetime(log.get("created_at")),
            _sort_datetime(log.get("started_at")),
            _sort_datetime(log.get("end_at")),
        )
        if sid and latest > latest_map.get(sid, _utc_min()):
            latest_map[sid] = latest

    return latest_map


def _student_progress_rows(class_name, group_filter, student_id=None):
    students = list(db.users.find(
        _student_user_query(class_name, group_filter, student_id),
        {
            "_id": 0,
            "student_id": 1,
            "name": 1,
            "class_name": 1,
            "group_type": 1,
            "is_test_data": 1,
        },
    ))
    student_ids = [
        str(row.get("student_id") or "").strip()
        for row in students
        if str(row.get("student_id") or "").strip()
    ]

    pre_expected = _test_question_count("pretest")
    post_expected = _test_question_count("posttest")
    pre_progress = _student_test_task_progress("pretest", student_ids)
    post_progress = _student_test_task_progress("posttest", student_ids)
    practice_progress = _practice_progress(student_ids)
    latest_map = _latest_activity_map(student_ids)

    rows = []
    for student in students:
        sid = str(student.get("student_id") or "").strip()
        if not sid:
            continue
        is_test_data = (
            student.get("is_test_data") is True
            or sid == TEST_STUDENT_ID
            or student.get("group_type") in (None, "")
        )
        progress = practice_progress.get(sid) or {}
        pre_row = pre_progress.get(sid) or {}
        post_row = post_progress.get(sid) or {}
        pre_count = int(pre_row.get("task_count") or 0)
        post_count = int(post_row.get("task_count") or 0)
        practice_attempted = int(progress.get("attempted_tasks") or 0)
        practice_completed = int(progress.get("completed_tasks") or 0)
        practice_latest = progress.get("latest_at")
        latest = latest_map.get(sid) or practice_latest
        rows.append(_json_safe({
            "student_id": sid,
            "name": student.get("name") or "",
            "class_name": student.get("class_name") or "",
            "group_type": student.get("group_type"),
            "is_test_data": is_test_data,
            "pretest_completed_tasks": pre_count,
            "pretest_total_tasks": pre_expected,
            "pretest_status": _progress_status(pre_count, pre_expected),
            "latest_pretest_at": pre_row.get("latest_at"),
            "learning_task_count": practice_attempted,
            "learning_status": _practice_status(practice_completed, practice_attempted),
            "practice_attempted_tasks": practice_attempted,
            "practice_completed_tasks": practice_completed,
            "practice_attempt_count": int(progress.get("attempt_count") or 0),
            "practice_current_unit": progress.get("current_unit") or "",
            "practice_current_task_id": progress.get("current_task_id") or "",
            "practice_current_task_title": progress.get("current_task_title") or "",
            "latest_practice_at": practice_latest,
            "posttest_completed_tasks": post_count,
            "posttest_total_tasks": post_expected,
            "posttest_status": _progress_status(post_count, post_expected),
            "latest_posttest_at": post_row.get("latest_at"),
            "last_activity_at": latest if latest and latest != _utc_min() else None,
        }))

    return sorted(rows, key=lambda row: str(row.get("student_id") or ""))


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
    if isinstance(value, str):
        text = value.strip()
        if text:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    return parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
            except Exception:
                pass
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
            "unit": 1,
            "unit_id": 1,
            "chapter": 1,
            "lesson": 1,
            "video_title": 1,
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
        unit = (
            _optional_string(task.get("unit"))
            or _optional_string(task.get("unit_id"))
            or _optional_string(task.get("chapter"))
            or _optional_string(task.get("lesson"))
        )
        value = {
            "task_title": title,
            "target_concept": concept,
            "unit": unit,
            "video_title": _optional_string(task.get("video_title")),
        }
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
        return (
            is_test_data
            or sid == TEST_STUDENT_ID
            or row.get("group_type") in (None, "")
        )
    if group_filter in FORMAL_GROUP_TYPES:
        return (not is_test_data) and row.get("group_type") == group_filter
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
    })

    if source_name == "attempts":
        option["has_attempts"] = True
    if source_name == "logs":
        option["has_logs"] = True

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
        if _student_option_matches(row, class_name, group_filter):
            rows.append(row)
    return sorted(rows, key=lambda row: str(row.get("student_id") or ""))


def _attempt_query(activity_type, test_role, class_name, group_filter, student_id):
    query = {"activity_type": activity_type}
    if activity_type == "test":
        query["test_role"] = test_role
    elif activity_type == "practice":
        query["test_role"] = None

    student_ids = _student_ids_for_analysis_filter(class_name, group_filter, student_id)
    if student_ids is not None:
        query["student_id"] = {"$in": sorted(student_ids)}
    return query


def _student_ids_for_analysis_filter(class_name=None, group_filter=None, student_id=None):
    selected_student = _optional_string(student_id)
    if not class_name and not group_filter and not selected_student:
        return None
    rows = db.users.find(
        _student_user_query(class_name, group_filter, selected_student),
        {"_id": 0, "student_id": 1},
    )
    ids = {
        str(row.get("student_id") or "").strip()
        for row in rows
        if str(row.get("student_id") or "").strip()
    }
    if selected_student and not class_name and not group_filter:
        ids.add(selected_student)
    return ids


def _legacy_test_roles(test_role):
    legacy_role = _legacy_test_role(test_role)
    roles = [legacy_role]
    if test_role not in roles:
        roles.append(test_role)
    return roles


def _normalize_legacy_test_attempt(attempt, test_role):
    legacy_role = _legacy_test_role(test_role)
    normalized_role = _normalize_test_role(attempt.get("test_role")) or test_role
    task_id = str(
        attempt.get("task_id")
        or attempt.get("test_task_id")
        or attempt.get("source_task_id")
        or ""
    ).strip()
    incorrect_slots = _int_list(
        attempt.get("incorrect_slots")
        or attempt.get("wrong_indices_all")
        or attempt.get("wrong_indices")
        or attempt.get("wrong_slots")
        or []
    )
    sequence_slots = _int_list(
        attempt.get("sequence_slots")
        or attempt.get("wrong_indices")
        or attempt.get("wrong_slots")
        or []
    )
    indentation_slots = _int_list(attempt.get("indentation_slots") or attempt.get("indent_errors") or [])
    if not incorrect_slots:
        incorrect_slots = sorted(set(sequence_slots + indentation_slots))
    error_types = attempt.get("error_types") if isinstance(attempt.get("error_types"), list) else []
    if not error_types and incorrect_slots:
        if indentation_slots:
            error_types.append("indentation")
        if sequence_slots:
            error_types.append("order")
    return {
        "_id": attempt.get("_id"),
        "data_source": "parsons_test_attempts",
        "student_id": str(attempt.get("student_id") or "").strip(),
        "class_name": attempt.get("class_name"),
        "group_type": attempt.get("group_type"),
        "activity_type": "test",
        "test_role": normalized_role if normalized_role in VALID_TEST_ROLES else test_role,
        "legacy_test_role": legacy_role,
        "test_cycle_id": attempt.get("test_cycle_id"),
        "task_id": task_id,
        "task_title": attempt.get("task_title") or "",
        "target_concept": attempt.get("target_concept") or attempt.get("concept_tag") or attempt.get("concept") or "unknown",
        "attempt_no": attempt.get("attempt_no") or 1,
        "is_correct": attempt.get("is_correct"),
        "score": attempt.get("score"),
        "duration_sec": attempt.get("duration_sec") if attempt.get("duration_sec") is not None else attempt.get("duration_seconds"),
        "submitted_at": attempt.get("submitted_at") or attempt.get("completed_at"),
        "created_at": attempt.get("created_at"),
        "error_types": error_types,
        "incorrect_slots": incorrect_slots,
        "sequence_slots": sequence_slots,
        "indentation_slots": indentation_slots,
        "repeated_error": attempt.get("repeated_error") is True,
        "error_details": attempt.get("error_details") if isinstance(attempt.get("error_details"), list) else [],
        "repeated_error_types": attempt.get("repeated_error_types") if isinstance(attempt.get("repeated_error_types"), list) else [],
        "repeated_error_count": attempt.get("repeated_error_count") or 0,
        "repeated_error_basis": attempt.get("repeated_error_basis"),
        "repeated_error_rule_version": attempt.get("repeated_error_rule_version"),
    }


def _read_legacy_test_attempts(test_role, class_name, group_filter, student_id):
    if test_role not in VALID_TEST_ROLES:
        return []
    query = {"test_role": {"$in": _legacy_test_roles(test_role)}}
    student_ids = _student_ids_for_analysis_filter(class_name, group_filter, student_id)
    if student_ids is not None:
        query["student_id"] = {"$in": sorted(student_ids)}
    projection = {
        "student_id": 1,
        "class_name": 1,
        "group_type": 1,
        "test_cycle_id": 1,
        "test_role": 1,
        "test_task_id": 1,
        "task_id": 1,
        "source_task_id": 1,
        "task_title": 1,
        "target_concept": 1,
        "concept": 1,
        "concept_tag": 1,
        "attempt_no": 1,
        "is_correct": 1,
        "score": 1,
        "duration_sec": 1,
        "duration_seconds": 1,
        "submitted_at": 1,
        "completed_at": 1,
        "created_at": 1,
        "updated_at": 1,
        "error_types": 1,
        "incorrect_slots": 1,
        "wrong_indices": 1,
        "wrong_indices_all": 1,
        "wrong_slots": 1,
        "sequence_slots": 1,
        "indent_errors": 1,
        "indentation_slots": 1,
        "error_details": 1,
        "repeated_error": 1,
        "repeated_error_types": 1,
        "repeated_error_count": 1,
        "repeated_error_basis": 1,
        "repeated_error_rule_version": 1,
        "submitted_after_ai_hint": 1,
        "ai_hint_viewed_before_submit": 1,
        "ai_hint_view_count_before_submit": 1,
        "latest_ai_hint_no_before_submit": 1,
        "ai_hint_view_count": 1,
        "hint_view_count": 1,
    }
    return [
        _normalize_legacy_test_attempt(attempt, test_role)
        for attempt in db.parsons_test_attempts.find(query, projection)
    ]


def _read_attempts(activity_type, test_role, class_name, group_filter, student_id):
    projection = {
        "student_id": 1,
        "class_name": 1,
        "group_type": 1,
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
        "incorrect_slots": 1,
        "sequence_slots": 1,
        "indentation_slots": 1,
        "repeated_error": 1,
        "error_details": 1,
        "repeated_error_types": 1,
        "repeated_error_count": 1,
        "repeated_error_basis": 1,
        "repeated_error_rule_version": 1,
    }
    attempts = list(
        db.parsons_attempts_v2.find(
            _attempt_query(activity_type, test_role, class_name, group_filter, student_id),
            projection,
        ).sort([("student_id", 1), ("task_id", 1), ("attempt_no", 1), ("submitted_at", 1)])
    )
    for attempt in attempts:
        attempt.setdefault("data_source", "parsons_attempts_v2")

    if activity_type == "test":
        existing_keys = {
            (
                str(row.get("student_id") or ""),
                str(row.get("task_id") or ""),
                str(row.get("test_role") or ""),
            )
            for row in attempts
        }
        for legacy in _read_legacy_test_attempts(test_role, class_name, group_filter, student_id):
            key = (
                str(legacy.get("student_id") or ""),
                str(legacy.get("task_id") or ""),
                str(legacy.get("test_role") or ""),
            )
            if key not in existing_keys:
                attempts.append(legacy)
                existing_keys.add(key)
    return attempts


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
            or row.get("is_test_data") is True
            or sid == TEST_STUDENT_ID
            or row.get("group_type") in (None, "")
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
        "practice_attempt_count": len(attempts),
        "first_try_correct_rate": _safe_rate(
            sum(1 for a in first_attempts if _attempt_is_correct(a)),
            len(first_attempts),
        ),
        "first_try_denominator": len(first_attempts),
        "final_correct_rate": _safe_rate(
            sum(1 for a in final_attempts if _attempt_is_correct(a)),
            len(final_attempts),
        ),
        "final_correct_denominator": len(final_attempts),
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
            "task_count": task_count,
            "total_attempts": len(student_attempts),
            "correct_task_count": sum(1 for a in final_attempts if _attempt_is_correct(a)),
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


def _attempt_wrong_slots(attempt):
    return _int_list(
        attempt.get("incorrect_slots")
        or attempt.get("wrong_indices_all")
        or attempt.get("wrong_indices")
        or attempt.get("wrong_slots")
        or []
    )


def _wrong_slot_distribution(attempts):
    counts = Counter()
    wrong_attempt_count = 0
    for attempt in attempts:
        if not _attempt_is_wrong(attempt):
            continue
        slots = _attempt_wrong_slots(attempt)
        if slots:
            wrong_attempt_count += 1
        for slot in slots:
            counts[int(slot)] += 1
    return [
        {
            "slot_index": slot,
            "slot_label": f"第 {slot + 1} 格",
            "count": count,
            "wrong_attempt_denominator": wrong_attempt_count,
            "rate": _safe_rate(count, wrong_attempt_count),
        }
        for slot, count in counts.most_common()
    ]


def _attempt_has_hint_intervention(attempt):
    return bool(
        attempt.get("submitted_after_ai_hint") is True
        or attempt.get("ai_hint_viewed_before_submit") is True
        or _safe_int(attempt.get("ai_hint_view_count_before_submit"), 0) > 0
        or _safe_int(attempt.get("latest_ai_hint_no_before_submit"), 0) > 0
        or _safe_int(attempt.get("ai_hint_view_count"), 0) > 0
        or _safe_int(attempt.get("hint_view_count"), 0) > 0
    )


def _intervention_metrics(attempts):
    grouped = defaultdict(list)
    for attempt in attempts:
        grouped[_attempt_group_key(attempt)].append(attempt)

    wrong_attempt_count = 0
    hint_intervention_count = 0
    corrected_after_hint_count = 0
    no_hint_wrong_count = 0
    corrected_without_hint_count = 0

    for rows in grouped.values():
        ordered = sorted(rows, key=_attempt_sort_key)
        for index, attempt in enumerate(ordered):
            if not _attempt_is_wrong(attempt):
                continue
            wrong_attempt_count += 1
            future = ordered[index + 1 :]
            correction_index = next(
                (i for i, row in enumerate(future) if _attempt_is_correct(row)),
                None,
            )
            until_correction = future if correction_index is None else future[: correction_index + 1]
            corrected_later = correction_index is not None
            has_hint = _attempt_has_hint_intervention(attempt) or any(
                _attempt_has_hint_intervention(row) for row in until_correction
            )
            if has_hint:
                hint_intervention_count += 1
                if corrected_later:
                    corrected_after_hint_count += 1
            else:
                no_hint_wrong_count += 1
                if corrected_later:
                    corrected_without_hint_count += 1

    return {
        "wrong_attempt_count": wrong_attempt_count,
        "hint_intervention_count": hint_intervention_count,
        "hint_intervention_rate": _safe_rate(hint_intervention_count, wrong_attempt_count),
        "corrected_after_hint_count": corrected_after_hint_count,
        "corrected_after_hint_rate": _safe_rate(corrected_after_hint_count, hint_intervention_count),
        "no_hint_wrong_count": no_hint_wrong_count,
        "corrected_without_hint_count": corrected_without_hint_count,
        "corrected_without_hint_rate": _safe_rate(corrected_without_hint_count, no_hint_wrong_count),
    }


def _score_float(value):
    score = _safe_float(value)
    if score is None:
        return None
    return score


def _student_score_summary(attempts):
    by_student_task = defaultdict(list)
    for attempt in attempts:
        sid = str(attempt.get("student_id") or "").strip()
        task_id = str(attempt.get("task_id") or "").strip()
        if sid and task_id:
            by_student_task[(sid, task_id)].append(attempt)
    scores_by_student = defaultdict(list)
    latest_by_student = defaultdict(lambda: _utc_min())
    for (sid, _task_id), rows in by_student_task.items():
        latest = sorted(rows, key=_attempt_sort_key)[-1]
        score = _score_float(latest.get("score"))
        if score is not None:
            scores_by_student[sid].append(score)
        latest_at = _sort_datetime(latest.get("submitted_at") or latest.get("created_at"))
        if latest_at > latest_by_student[sid]:
            latest_by_student[sid] = latest_at
    return {
        sid: {
            "task_count": len(scores),
            "avg_score": round(sum(scores) / len(scores), 4) if scores else None,
            "latest_at": latest_by_student[sid] if latest_by_student[sid] != _utc_min() else None,
        }
        for sid, scores in scores_by_student.items()
    }


def _pre_post_score_gain(class_name, group_filter, student_id=None):
    pre = _student_score_summary(
        _enrich_attempts(_read_attempts("test", "pretest", class_name, group_filter, student_id))
    )
    post = _student_score_summary(
        _enrich_attempts(_read_attempts("test", "posttest", class_name, group_filter, student_id))
    )
    student_ids = sorted(set(pre.keys()) | set(post.keys()))
    rows = []
    gains = []
    for sid in student_ids:
        pre_score = pre.get(sid, {}).get("avg_score")
        post_score = post.get(sid, {}).get("avg_score")
        gain = None
        if pre_score is not None and post_score is not None:
            gain = round(post_score - pre_score, 4)
            gains.append(gain)
        rows.append({
            "student_id": sid,
            "pre_score": pre_score,
            "post_score": post_score,
            "score_gain": gain,
            "pre_task_count": pre.get(sid, {}).get("task_count", 0),
            "post_task_count": post.get(sid, {}).get("task_count", 0),
            "latest_pretest_at": pre.get(sid, {}).get("latest_at"),
            "latest_posttest_at": post.get(sid, {}).get("latest_at"),
        })
    return {
        "matched_student_count": len(gains),
        "avg_score_gain": round(sum(gains) / len(gains), 4) if gains else None,
        "rows": [_json_safe(row) for row in rows],
        "data_sources": ["parsons_attempts_v2", "parsons_test_attempts"],
    }

# 儀表板題目錯誤分析
def _build_task_analysis(attempts):
    grouped = defaultdict(list)
    for attempt in attempts:
        grouped[attempt.get("task_id")].append(attempt)

    rows = []
    for task_id, task_attempts in grouped.items():
        first = task_attempts[0] if task_attempts else {}
        total = len(task_attempts)
        correct = sum(1 for a in task_attempts if _attempt_is_correct(a))
        wrong = sum(1 for a in task_attempts if _attempt_is_wrong(a))
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
        correct = sum(1 for a in concept_attempts if _attempt_is_correct(a))
        wrong = sum(1 for a in concept_attempts if _attempt_is_wrong(a))
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
        "attempt_id": 1,
        "attempt_no": 1,
        "target_concept": 1,
        "metadata": 1,
    }
    cursor = (
        db.learning_logs.find(query, projection)
        .sort([("event_at", -1), ("created_at", -1)])
        .limit(max(1, min(int(limit or 100), 500)))
    )
    rows = list(cursor)
    rows.reverse()
    return [_json_safe(row) for row in rows]


def _video_rewatch_student_ids(class_name, group_filter, student_id=None):
    students = list(db.users.find(
        _student_user_query(class_name, group_filter, student_id),
        {
            "_id": 0,
            "student_id": 1,
            "name": 1,
            "class_name": 1,
            "group_type": 1,
            "is_test_data": 1,
        },
    ))
    profiles = {}
    for student in students:
        sid = str(student.get("student_id") or "").strip()
        if sid:
            profiles[sid] = student
    return set(profiles), profiles


def _video_event_time(log):
    return (
        log.get("event_at")
        or log.get("recorded_at")
        or log.get("watch_end_at")
        or log.get("created_at")
    )


def _video_watch_seconds_for_total(log):
    delta = _safe_float(log.get("watch_delta_sec"))
    if delta is not None:
        return delta
    seconds = _safe_float(log.get("watch_seconds"))
    return seconds if seconds is not None else 0


def _read_video_rewatch_logs(class_name, group_filter, student_id=None, limit=1000):
    student_ids, profiles = _video_rewatch_student_ids(class_name, group_filter, student_id)
    if not student_ids:
        return [], []

    query = {"student_id": {"$in": sorted(student_ids)}}
    try:
        limit = max(1, min(int(limit or 1000), 5000))
    except Exception:
        limit = 1000

    projection = {
        "_id": 1,
        "schema_version": 1,
        "student_id": 1,
        "class_name": 1,
        "group_type": 1,
        "is_test_data": 1,
        "event_type": 1,
        "video_id": 1,
        "video_title": 1,
        "unit_id": 1,
        "unit": 1,
        "watch_session_id": 1,
        "attempt_id": 1,
        "task_id": 1,
        "watch_seconds": 1,
        "watch_delta_sec": 1,
        "duration_minutes": 1,
        "current_time_sec": 1,
        "video_duration_sec": 1,
        "playback_rate": 1,
        "reached_end": 1,
        "completed_fully": 1,
        "watch_start_at": 1,
        "watch_end_at": 1,
        "segment_start_sec": 1,
        "segment_end_sec": 1,
        "seek_count": 1,
        "total_seek_distance": 1,
        "avg_seek_distance": 1,
        "is_frequent_seeker": 1,
        "page": 1,
        "source": 1,
        "event_at": 1,
        "recorded_at": 1,
        "created_at": 1,
    }
    logs = list(
        db.video_rewatch_logs.find(query, projection).sort(
            [("student_id", 1), ("event_at", -1), ("recorded_at", -1), ("created_at", -1)]
        ).limit(limit)
    )

    rows = []
    for log in logs:
        sid = str(log.get("student_id") or "").strip()
        profile = profiles.get(sid) or {}
        event_at = _video_event_time(log)
        watch_seconds = _safe_float(log.get("watch_seconds"))
        watch_delta_sec = _safe_float(log.get("watch_delta_sec"))
        rows.append(_json_safe({
            "log_id": str(log.get("_id") or ""),
            "student_id": sid,
            "student_name": profile.get("name"),
            "class_name": profile.get("class_name") or log.get("class_name"),
            "group_type": profile.get("group_type") or log.get("group_type"),
            "is_test_data": profile.get("is_test_data") is True or log.get("is_test_data") is True,
            "event_type": log.get("event_type") or "review_watch",
            "video_id": log.get("video_id"),
            "video_title": log.get("video_title"),
            "unit_id": log.get("unit_id") or log.get("unit"),
            "watch_session_id": log.get("watch_session_id"),
            "task_id": log.get("task_id"),
            "attempt_id": log.get("attempt_id"),
            "watch_seconds": watch_seconds,
            "watch_delta_sec": watch_delta_sec,
            "watch_seconds_for_total": _video_watch_seconds_for_total(log),
            "duration_minutes": _safe_float(log.get("duration_minutes")),
            "current_time_sec": _safe_float(log.get("current_time_sec")),
            "video_duration_sec": _safe_float(log.get("video_duration_sec")),
            "playback_rate": _safe_float(log.get("playback_rate")),
            "reached_end": log.get("reached_end") is True,
            "completed_fully": log.get("completed_fully") is True,
            "watch_start_at": log.get("watch_start_at"),
            "watch_end_at": log.get("watch_end_at"),
            "segment_start_sec": _safe_float(log.get("segment_start_sec")),
            "segment_end_sec": _safe_float(log.get("segment_end_sec")),
            "seek_count": log.get("seek_count"),
            "total_seek_distance": _safe_float(log.get("total_seek_distance")),
            "avg_seek_distance": _safe_float(log.get("avg_seek_distance")),
            "is_frequent_seeker": log.get("is_frequent_seeker") is True,
            "page": log.get("page"),
            "source": log.get("source"),
            "event_at": event_at,
        }))
    return rows, profiles


def _video_rewatch_summary(records, profiles):
    by_student = defaultdict(list)
    for record in records:
        by_student[record.get("student_id")].append(record)

    rows = []
    for sid, student_records in by_student.items():
        profile = profiles.get(sid) or {}
        videos = {
            str(record.get("video_id") or "")
            for record in student_records
            if record.get("video_id")
        }
        total_watch_seconds = sum(
            _safe_float(record.get("watch_seconds_for_total")) or 0
            for record in student_records
        )
        completed_count = sum(
            1
            for record in student_records
            if record.get("reached_end") is True or record.get("completed_fully") is True
        )
        latest = max(
            (_sort_datetime(record.get("event_at")) for record in student_records),
            default=_utc_min(),
        )
        rows.append(_json_safe({
            "student_id": sid,
            "student_name": profile.get("name"),
            "class_name": profile.get("class_name"),
            "group_type": profile.get("group_type"),
            "is_test_data": profile.get("is_test_data") is True,
            "record_count": len(student_records),
            "video_count": len(videos),
            "total_watch_seconds": round(total_watch_seconds, 3),
            "avg_watch_seconds": round(total_watch_seconds / len(student_records), 3) if student_records else 0,
            "completed_count": completed_count,
            "latest_event_at": latest if latest != _utc_min() else None,
        }))
    return sorted(rows, key=lambda row: (-row["record_count"], str(row.get("student_id") or "")))


def _student_attempt_rows(attempts, student_id):
    sid = _optional_string(student_id)
    if not sid:
        return []

    rows = []
    for attempt in sorted(attempts, key=_attempt_sort_key):
        if str(attempt.get("student_id") or "") != sid:
            continue
        incorrect_slots = (
            attempt.get("incorrect_slots")
            if isinstance(attempt.get("incorrect_slots"), list)
            else attempt.get("wrong_slots") or []
        )
        rows.append(_json_safe({
            "submitted_at": attempt.get("submitted_at") or attempt.get("created_at"),
            "data_source": attempt.get("data_source") or "unknown",
            "task_id": attempt.get("task_id"),
            "task_title": attempt.get("task_title"),
            "target_concept": attempt.get("target_concept"),
            "attempt_no": attempt.get("attempt_no"),
            "is_correct": attempt.get("is_correct"),
            "score": attempt.get("score"),
            "error_types": attempt.get("error_types") or [],
            "incorrect_slots": incorrect_slots,
            "sequence_slots": attempt.get("sequence_slots") or [],
            "indentation_slots": attempt.get("indentation_slots") or [],
            "error_details": attempt.get("error_details") or [],
            "duration_sec": attempt.get("duration_sec"),
            "repeated_error": attempt.get("repeated_error"),
            "repeated_error_types": attempt.get("repeated_error_types") or [],
            "repeated_error_count": attempt.get("repeated_error_count") or 0,
            "repeated_error_basis": attempt.get("repeated_error_basis"),
            "repeated_error_rule_version": attempt.get("repeated_error_rule_version"),
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
    logs = _read_student_logs(
        student_id,
        activity_type,
        test_role,
        request.args.get("logs_limit") or 100,
        class_name,
        group_filter,
    )
    test_completion_overviews = {
        "pretest": _test_completion_overview("pretest", class_name, group_filter, student_id),
        "posttest": _test_completion_overview("posttest", class_name, group_filter, student_id),
    }
    practice_unit_progress = (
        _practice_unit_progress_payload(class_name, group_filter, student_id)
        if activity_type == "practice"
        else {"columns": [], "rows": [], "latest_task_rows": []}
    )
    data_sources = sorted({
        str(attempt.get("data_source") or "unknown")
        for attempt in attempts
        if str(attempt.get("data_source") or "").strip()
    })
    wrong_slot_distribution = _wrong_slot_distribution(attempts)
    wrong_type_distribution = _error_type_counts(
        [attempt for attempt in attempts if _attempt_is_wrong(attempt)]
    )
    intervention_metrics = (
        _intervention_metrics(attempts)
        if activity_type == "practice"
        else {
            "wrong_attempt_count": 0,
            "hint_intervention_count": 0,
            "hint_intervention_rate": 0,
            "corrected_after_hint_count": 0,
            "corrected_after_hint_rate": 0,
            "no_hint_wrong_count": 0,
            "corrected_without_hint_count": 0,
            "corrected_without_hint_rate": 0,
        }
    )
    return {
        "ok": True,
        "data_sources": data_sources or ["parsons_attempts_v2"],
        "filters": {
            "activity_type": activity_type,
            "test_role": test_role,
            "class_name": class_name,
            "group_type": group_filter,
            "student_id": student_id,
            "exclude_test_data": group_filter in FORMAL_GROUP_TYPES,
        },
        "user_group_overview": _user_group_overview(class_name),
        "test_completion_overview": test_completion_overviews.get(test_role) if test_role else None,
        "test_completion_overviews": test_completion_overviews,
        "student_progress_rows": _student_progress_rows(class_name, group_filter, student_id),
        "practice_unit_columns": practice_unit_progress.get("columns") or [],
        "practice_unit_progress_rows": practice_unit_progress.get("rows") or [],
        "practice_task_latest_rows": practice_unit_progress.get("latest_task_rows") or [],
        "kpis": _build_kpis(attempts),
        "wrong_slot_distribution": wrong_slot_distribution,
        "wrong_type_distribution": wrong_type_distribution,
        "intervention_metrics": intervention_metrics,
        "pre_post_score_gain": _pre_post_score_gain(class_name, group_filter, student_id),
        "student_options": _read_student_options(class_name, group_filter),
        "student_overview": _build_student_overview(attempts),
        "task_error_analysis": _build_task_analysis(attempts),
        "concept_error_analysis": _build_concept_analysis(attempts),
        "student_attempts": _student_attempt_rows(attempts, student_id),
        "student_logs": logs,
    }


@teacher_analysis_bp.get("/parsons")
def parsons_analysis():
    return jsonify(_build_analysis_payload())


# 取得練習題分析資料 (舊版 API 相容)
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
        "slot_ranking": payload.get("wrong_slot_distribution") or [],
        "analysis": payload,
    })


@teacher_analysis_bp.get("/video-rewatch")
def video_rewatch_analysis():
    class_name = _optional_string(request.args.get("class_name"))
    group_filter = _normalize_group_filter(request.args.get("group_type"))
    student_id = _optional_string(request.args.get("student_id"))
    records, profiles = _read_video_rewatch_logs(
        class_name,
        group_filter,
        student_id,
        request.args.get("limit") or 1000,
    )
    summary = _video_rewatch_summary(records, profiles)
    return jsonify({
        "ok": True,
        "data_source": "video_rewatch_logs",
        "note": "影片觀看紀錄為輔助學習歷程資料，未納入 Parsons 主分析指標。",
        "filters": {
            "class_name": class_name,
            "group_type": group_filter,
            "student_id": student_id,
            "exclude_test_data": group_filter != TEST_DATA_GROUP_FILTER,
        },
        "summary": {
            "student_count": len(summary),
            "record_count": len(records),
            "total_watch_seconds": round(
                sum(_safe_float(row.get("total_watch_seconds")) or 0 for row in summary),
                3,
            ),
        },
        "student_summary": summary,
        "records": records,
    })
