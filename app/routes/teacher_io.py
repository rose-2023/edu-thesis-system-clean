import csv
import io
import json
import re
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, jsonify, request
from werkzeug.security import generate_password_hash

from app.db import db
from app.routes.teacher_analysis import (
    TEST_STUDENT_ID,
    VALID_TEST_ROLES,
    _apply_group_filter,
    _attempt_sort_key,
    build_student_options,
    _json_safe,
    _normalize_activity_type,
    _normalize_group_filter,
    _normalize_test_role,
    _optional_string,
    _read_video_rewatch_logs,
    _safe_rate,
    _valid_duration,
)


teacher_io_bp = Blueprint("teacher_io", __name__)

TAIPEI_TZ = timezone(timedelta(hours=8))
VALID_GROUP_TYPES = {"experimental", "control"}
VALID_USER_ROLES = {"student", "teacher", "admin"}
MAX_USER_CSV_BYTES = 2 * 1024 * 1024
SENSITIVE_CSV_FIELDS = frozenset({
    "password",
    "password_hash",
    "active_session_id",
    "active_ip",
    "session_id",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "cookie",
})
SEXJ_ALIASES = {
    "boy": "boy",
    "m": "boy",
    "male": "boy",
    "男": "boy",
    "男性": "boy",
    "girl": "girl",
    "f": "girl",
    "female": "girl",
    "女": "girl",
    "女性": "girl",
    "o": "other",
    "other": "other",
    "其他": "other",
}


def _parse_bool(value, default=False):
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _normalize_sexj(value):
    text = str(value or "").strip().lower()
    if not text:
        return None
    return SEXJ_ALIASES.get(text)


def _exclude_test_data():
    parsed = _parse_bool(request.args.get("exclude_test_data"), default=True)
    return True if parsed is None else parsed


def _format_csv_datetime(value):
    if not isinstance(value, datetime):
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _normalize_csv_field_name(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _is_sensitive_csv_field(value):
    return _normalize_csv_field_name(value) in SENSITIVE_CSV_FIELDS


def _sanitize_csv_value(value):
    if isinstance(value, dict):
        return {
            key: _sanitize_csv_value(item)
            for key, item in value.items()
            if not _is_sensitive_csv_field(key)
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_csv_value(item) for item in value]
    if isinstance(value, str) and re.match(
        r"^\s*(?:bearer\s+|authorization\s*[:=]|cookie\s*[:=])",
        value,
        flags=re.IGNORECASE,
    ):
        return "[redacted]"
    return value


def _csv_text(headers, rows):
    safe_headers = [header for header in headers if not _is_sensitive_csv_field(header)]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=safe_headers, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({
            key: _sanitize_csv_value(row.get(key, ""))
            for key in safe_headers
        })
    csv_text = "\ufeff" + output.getvalue()
    output.close()
    return csv_text


def _csv_response(headers, rows, filename):
    csv_text = _csv_text(headers, rows)
    return Response(
        csv_text,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename={filename}; filename*=UTF-8''{filename}"
            ),
            "Cache-Control": "no-store, private, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


def _zip_csv_response(files, filename):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for csv_filename, headers, rows in files:
            archive.writestr(csv_filename, _csv_text(headers, rows).encode("utf-8"))
    return Response(
        output.getvalue(),
        mimetype="application/zip",
        headers={
            "Content-Disposition": (
                f"attachment; filename={filename}; filename*=UTF-8''{filename}"
            ),
            "Cache-Control": "no-store, private, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


def _user_profiles(student_ids):
    ids = sorted({str(sid) for sid in student_ids if sid})
    if not ids:
        return {}
    profiles = {}
    for user in db.users.find(
        {"student_id": {"$in": ids}},
        {"_id": 0, "student_id": 1, "class_name": 1, "group_type": 1, "is_test_data": 1},
    ):
        sid = str(user.get("student_id") or "").strip()
        if sid:
            profiles[sid] = user
    return profiles


def _attempt_export_query(include_student=True):
    activity_type = _normalize_activity_type(request.args.get("activity_type"))
    test_role = _normalize_test_role(request.args.get("test_role"))
    if activity_type == "test" and test_role not in VALID_TEST_ROLES:
        test_role = "pretest"
    if activity_type == "practice":
        test_role = None

    class_name = _optional_string(request.args.get("class_name"))
    group_filter = _normalize_group_filter(request.args.get("group_type"))
    student_id = _optional_string(request.args.get("student_id")) if include_student else None

    query = {"activity_type": activity_type}
    if activity_type == "test":
        query["test_role"] = test_role
    else:
        query["test_role"] = None
    _apply_group_filter(query, class_name, group_filter, student_id)
    return query


def _load_attempts_for_summary():
    projection = {
        "student_id": 1,
        "class_name": 1,
        "group_type": 1,
        "task_id": 1,
        "activity_type": 1,
        "test_role": 1,
        "attempt_no": 1,
        "is_correct": 1,
        "duration_sec": 1,
        "submitted_at": 1,
        "created_at": 1,
    }
    attempts = list(
        db.parsons_attempts_v2.find(_attempt_export_query(), projection).sort(
            [("student_id", 1), ("task_id", 1), ("attempt_no", 1), ("submitted_at", 1)]
        )
    )
    profiles = _user_profiles(a.get("student_id") for a in attempts)
    for attempt in attempts:
        sid = str(attempt.get("student_id") or "")
        profile = profiles.get(sid) or {}
        attempt["student_id"] = sid
        attempt["class_name"] = (
            _optional_string(profile.get("class_name"))
            or _optional_string(attempt.get("class_name"))
        )
        attempt["group_type"] = (
            _optional_string(profile.get("group_type"))
            or _optional_string(attempt.get("group_type"))
        )
        attempt["duration_sec"] = _valid_duration(attempt.get("duration_sec"))
    return attempts


def _student_summary_rows():
    attempts = _load_attempts_for_summary()
    by_student = defaultdict(list)
    for attempt in attempts:
        by_student[attempt.get("student_id")].append(attempt)

    rows = []
    for sid, student_attempts in by_student.items():
        by_task = defaultdict(list)
        for attempt in student_attempts:
            by_task[str(attempt.get("task_id") or "")].append(attempt)

        first_attempts = []
        final_attempts = []
        for task_attempts in by_task.values():
            ordered = sorted(task_attempts, key=_attempt_sort_key)
            if ordered:
                first_attempts.append(ordered[0])
                final_attempts.append(ordered[-1])

        durations = [
            attempt.get("duration_sec")
            for attempt in student_attempts
            if attempt.get("duration_sec") is not None
        ]
        first = student_attempts[0] if student_attempts else {}
        task_count = len(by_task)
        rows.append({
            "student_id": sid,
            "class_name": first.get("class_name") or "",
            "group_type": first.get("group_type") or "",
            "task_count": task_count,
            "total_attempts": len(student_attempts),
            "correct_task_count": sum(1 for a in final_attempts if a.get("is_correct") is True),
            "first_try_correct_rate": _safe_rate(
                sum(1 for a in first_attempts if a.get("is_correct") is True),
                task_count,
            ),
            "final_correct_rate": _safe_rate(
                sum(1 for a in final_attempts if a.get("is_correct") is True),
                task_count,
            ),
            "avg_attempts_per_task": (
                round(len(student_attempts) / task_count, 2) if task_count else 0
            ),
            "avg_duration_sec": round(sum(durations) / len(durations), 2) if durations else "",
        })
    return sorted(rows, key=lambda row: str(row.get("student_id") or ""))


def _learning_log_query(include_student=True):
    class_name = _optional_string(request.args.get("class_name"))
    group_filter = _normalize_group_filter(request.args.get("group_type"))
    student_id = _optional_string(request.args.get("student_id")) if include_student else None
    activity_type = _optional_string(request.args.get("activity_type"))
    test_role = _normalize_test_role(request.args.get("test_role"))
    task_id = _optional_string(request.args.get("task_id"))
    event_type = _optional_string(request.args.get("event_type"))

    query = {}
    _apply_group_filter(query, class_name, group_filter, student_id)
    if activity_type:
        query["activity_type"] = activity_type
    if activity_type == "test":
        query["test_role"] = test_role or "pretest"
    elif activity_type == "practice":
        query["test_role"] = None
    elif test_role:
        query["test_role"] = test_role
    if task_id:
        query["task_id"] = task_id
    if event_type:
        query["event_type"] = event_type
    return query


def _json_csv_cell(value):
    if value is None:
        return ""
    return json.dumps(
        _json_safe(_sanitize_csv_value(value)),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _attempt_record_rows(include_student=True):
    headers = [
        "attempt_id",
        "student_id",
        "class_name",
        "group_type",
        "is_test_data",
        "activity_type",
        "test_role",
        "test_cycle_id",
        "task_id",
        "task_title",
        "target_concept",
        "attempt_no",
        "is_correct",
        "score",
        "submitted_order",
        "submitted_indentation",
        "correct_answer",
        "error_count",
        "error_types",
        "incorrect_slots",
        "sequence_slots",
        "indentation_slots",
        "error_details",
        "repeated_error",
        "repeated_error_types",
        "repeated_error_count",
        "repeated_error_basis",
        "repeated_error_rule_version",
        "started_at",
        "submitted_at",
        "elapsed_sec_raw",
        "duration_sec",
        "duration_outlier",
        "duration_outlier_reason",
        "review_reason",
        "timezone",
        "created_at",
        "updated_at",
    ]
    projection = {key: 1 for key in headers if key != "attempt_id"}
    projection["_id"] = 1
    cursor = db.parsons_attempts_v2.find(
        _attempt_export_query(include_student=include_student),
        projection,
    ).sort([("student_id", 1), ("task_id", 1), ("attempt_no", 1), ("submitted_at", 1)])

    rows = []
    json_fields = {
        "submitted_order",
        "submitted_indentation",
        "correct_answer",
        "error_types",
        "incorrect_slots",
        "sequence_slots",
        "indentation_slots",
        "error_details",
        "repeated_error_types",
        "review_reason",
    }
    datetime_fields = {"started_at", "submitted_at", "created_at", "updated_at"}
    for attempt in cursor:
        row = {key: attempt.get(key, "") for key in headers}
        row["attempt_id"] = str(attempt.get("_id") or "")
        for key in json_fields:
            row[key] = _json_csv_cell(attempt.get(key))
        for key in datetime_fields:
            row[key] = _format_csv_datetime(attempt.get(key))
        rows.append(row)
    return headers, rows


def _learning_log_rows(include_student=True):
    headers = [
        "event_at",
        "student_id",
        "class_name",
        "group_type",
        "event_type",
        "page",
        "activity_type",
        "test_role",
        "task_id",
        "attempt_id",
        "attempt_no",
        "target_concept",
        "metadata",
    ]
    projection = {key: 1 for key in headers if key != "metadata"}
    projection["metadata"] = 1
    projection["_id"] = 0
    cursor = db.learning_logs.find(
        _learning_log_query(include_student=include_student),
        projection,
    ).sort([("event_at", 1), ("created_at", 1)])

    rows = []
    for log in cursor:
        row = {key: log.get(key, "") for key in headers}
        row["event_at"] = _format_csv_datetime(log.get("event_at"))
        row["metadata"] = _json_csv_cell(log.get("metadata") or {})
        rows.append(row)
    return headers, rows


def _video_rewatch_log_rows(include_student=True):
    headers = [
        "event_at",
        "log_id",
        "student_id",
        "student_name",
        "class_name",
        "group_type",
        "is_test_data",
        "event_type",
        "video_id",
        "video_title",
        "unit_id",
        "watch_session_id",
        "task_id",
        "attempt_id",
        "watch_seconds",
        "watch_delta_sec",
        "watch_seconds_for_total",
        "duration_minutes",
        "current_time_sec",
        "video_duration_sec",
        "playback_rate",
        "reached_end",
        "completed_fully",
        "watch_start_at",
        "watch_end_at",
        "segment_start_sec",
        "segment_end_sec",
        "seek_count",
        "total_seek_distance",
        "avg_seek_distance",
        "is_frequent_seeker",
        "page",
        "source",
    ]
    class_name = _optional_string(request.args.get("class_name"))
    group_filter = _normalize_group_filter(request.args.get("group_type"))
    student_id = _optional_string(request.args.get("student_id")) if include_student else None
    records, _profiles = _read_video_rewatch_logs(
        class_name,
        group_filter,
        student_id,
        request.args.get("limit") or 5000,
    )

    rows = []
    datetime_fields = {"event_at", "watch_start_at", "watch_end_at"}
    for record in records:
        row = {key: record.get(key, "") for key in headers}
        for key in datetime_fields:
            value = record.get(key)
            if isinstance(value, str):
                row[key] = value
            else:
                row[key] = _format_csv_datetime(value)
        rows.append(row)
    return headers, rows


@teacher_io_bp.get("/analytics/student-options")
def analytics_student_options():
    class_name = _optional_string(request.args.get("class_name"))
    group_filter_raw = request.args.get("group_filter")
    if str(group_filter_raw or "").strip().lower() == "all":
        group_filter = None
    else:
        group_filter = _normalize_group_filter(group_filter_raw)
    return jsonify(build_student_options(class_name, group_filter))


@teacher_io_bp.post("/import/users-csv")
def import_users_csv():
    if "file" not in request.files:
        return jsonify({"ok": False, "message": "missing file"}), 400

    upload = request.files["file"]
    filename = str(upload.filename or "").strip()
    if not filename.lower().endswith(".csv"):
        return jsonify({"ok": False, "message": "csv_file_required"}), 400
    raw = upload.stream.read(MAX_USER_CSV_BYTES + 1)
    if len(raw) > MAX_USER_CSV_BYTES:
        return jsonify({"ok": False, "message": "csv_file_too_large"}), 413
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    headers = {str(name or "").strip() for name in (reader.fieldnames or [])}
    if "student_id" not in headers:
        return jsonify({"ok": False, "message": "missing student_id header"}), 400

    inserted_count = 0
    updated_count = 0
    skipped_count = 0
    invalid_rows = []

    for row_number, row in enumerate(reader, start=2):
        student_id = _optional_string(row.get("student_id"))
        if not student_id:
            skipped_count += 1
            invalid_rows.append({
                "row": row_number,
                "student_id": "",
                "reason": "missing_student_id",
            })
            continue

        group_type = _optional_string(row.get("group_type"))
        if group_type:
            group_type = group_type.lower()
        if group_type and group_type not in VALID_GROUP_TYPES:
            skipped_count += 1
            invalid_rows.append({
                "row": row_number,
                "student_id": student_id,
                "reason": "invalid_group_type",
            })
            continue

        is_test_data = _parse_bool(row.get("is_test_data"), default=False)
        if is_test_data is None:
            skipped_count += 1
            invalid_rows.append({
                "row": row_number,
                "student_id": student_id,
                "reason": "invalid_is_test_data",
            })
            continue
        if student_id == TEST_STUDENT_ID:
            is_test_data = True
        if group_type is None and not is_test_data:
            skipped_count += 1
            invalid_rows.append({
                "row": row_number,
                "student_id": student_id,
                "reason": "missing_group_type",
            })
            continue

        role = (_optional_string(row.get("role")) or "student").lower()
        if role not in VALID_USER_ROLES:
            skipped_count += 1
            invalid_rows.append({
                "row": row_number,
                "student_id": student_id,
                "reason": "invalid_role",
            })
            continue

        sexj_raw = _optional_string(row.get("sexj"))
        sexj = _normalize_sexj(sexj_raw)
        if sexj_raw and not sexj:
            skipped_count += 1
            invalid_rows.append({
                "row": row_number,
                "student_id": student_id,
                "reason": "invalid_sexj",
            })
            continue
        try:
            existing = db.users.find_one({"student_id": student_id})
            now = datetime.now(timezone.utc)
            update_doc = {
                "student_id": student_id,
                "name": _optional_string(row.get("name")),
                "class_name": _optional_string(row.get("class_name")),
                "role": role,
                "group_type": group_type,
                "is_test_data": bool(is_test_data),
                "sexj": sexj,
                "updated_at": now,
            }

            if existing:
                if not existing.get("password_hash"):
                    update_doc["password_hash"] = generate_password_hash(student_id)
                if "created_at" not in existing:
                    update_doc["created_at"] = now
                if "last_login_at" not in existing:
                    update_doc["last_login_at"] = None
                db.users.update_one({"_id": existing["_id"]}, {"$set": update_doc})
                updated_count += 1
            else:
                update_doc["password_hash"] = generate_password_hash(student_id)
                update_doc["created_at"] = now
                update_doc["last_login_at"] = None
                db.users.insert_one(update_doc)
                inserted_count += 1
        except Exception:
            skipped_count += 1
            invalid_rows.append({
                "row": row_number,
                "student_id": student_id,
                "reason": "row_write_failed",
            })

    return jsonify({
        "ok": True,
        "inserted_count": inserted_count,
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "invalid_rows": invalid_rows,
    })


@teacher_io_bp.get("/export/student-summary.csv")
def export_student_summary_csv():
    headers = [
        "student_id",
        "class_name",
        "group_type",
        "task_count",
        "total_attempts",
        "correct_task_count",
        "first_try_correct_rate",
        "final_correct_rate",
        "avg_attempts_per_task",
        "avg_duration_sec",
    ]
    return _csv_response(headers, _student_summary_rows(), "parsons_student_summary.csv")


@teacher_io_bp.get("/export/group-learning-data.zip")
def export_group_learning_data_zip():
    attempt_headers, attempt_rows = _attempt_record_rows(include_student=False)
    log_headers, log_rows = _learning_log_rows(include_student=False)
    video_headers, video_rows = _video_rewatch_log_rows(include_student=False)
    return _zip_csv_response(
        [
            ("parsons_attempt_records.csv", attempt_headers, attempt_rows),
            ("learning_logs.csv", log_headers, log_rows),
            ("video_rewatch_logs.csv", video_headers, video_rows),
        ],
        "parsons_group_learning_data.zip",
    )


@teacher_io_bp.get("/export/learning-logs.csv")
def export_learning_logs_csv():
    headers, rows = _learning_log_rows()
    return _csv_response(headers, rows, "parsons_learning_logs.csv")


@teacher_io_bp.get("/export/video-rewatch-logs.csv")
def export_video_rewatch_logs_csv():
    headers, rows = _video_rewatch_log_rows()
    return _csv_response(headers, rows, "video_rewatch_logs.csv")
