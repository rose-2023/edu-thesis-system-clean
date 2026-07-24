from flask import Blueprint, current_app, request, jsonify, Response
from app.db import db
from app.questionnaire import (
    QUESTIONNAIRE_COLLECTION,
    QUESTIONNAIRE_DATA_SOURCE,
    QUESTIONNAIRE_FORM_VERSION,
    QUESTIONNAIRE_FORM_VERSIONS,
    QUESTIONNAIRE_PAGES,
    ensure_questionnaire_indexes,
    get_questionnaire_response,
    present_questionnaire_answers,
)
from app.randomization import (
    RandomizationSlotsExhausted,
    assign_feedback_strategy_on_import,
)
from datetime import datetime, timedelta, timezone

records_bp = Blueprint("records", __name__)
_TEST_STUDENT_ID = "11461127"
_TAIPEI_TZ = timezone(timedelta(hours=8))
_DEFAULT_TEST_CYCLE_ID = "2026_07_batch_01"


def _format_taipei_datetime(value):
    if not isinstance(value, datetime):
        return str(value or "")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _parse_optional_bool(value):
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None

# =========================================================
# Students (thesis_system.users)
# =========================================================
@records_bp.get("/students")
def students():
    class_name = (request.args.get("class_name") or "").strip()
    page = int(request.args.get("page") or 1)
    page_size = int(request.args.get("page_size") or 15)

    q = {"role": "student"}
    if class_name:
        q["class_name"] = class_name

    total = db.users.count_documents(q)
    cursor = (
        db.users.find(q, {
            "_id": 0,
            "student_id": 1,
            "name": 1,
            "class_name": 1,
            "test_cycle_id": 1,
            "group_type": 1,
            "is_test_data": 1,
            "created_at": 1,
        })
        .sort("created_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    students = list(cursor)
    return jsonify({"ok": True, "students": students, "total": total})


@records_bp.post("/students/import_csv")
def import_students_csv():
    """
    匯入 students CSV
    欄位（最少）：student_id,name,class_name
    password 可選
    - 若 CSV 沒 password 且「預設密碼」也沒填：用 student_id 當密碼（避免空密碼）
    """
    import io
    import csv

    if "file" not in request.files:
        return jsonify({"ok": False, "message": "missing file"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"ok": False, "message": "empty filename"}), 400

    # [修改] 允許不填（不強制預設密碼）
    default_password = (request.form.get("default_password") or "").strip()  # [修改]

    raw = f.read()
    # Excel 常見 UTF-8 BOM
    text = raw.decode("utf-8-sig", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    required = {"student_id", "name", "class_name"}
    if not required.issubset(set([h.strip() for h in (reader.fieldnames or [])])):
        return jsonify({"ok": False, "message": "CSV 欄位需包含 student_id,name,class_name（password 可選）"}), 400

    upserts = 0
    assignment_summary = {
        "assigned": 0,
        "already_assigned": 0,
        "excluded_test_data": 0,
        "control": 0,
        "experimental_1": 0,
        "experimental_2": 0,
        "failed": [],
    }
    for row in reader:
        sid = (row.get("student_id") or "").strip()
        name = (row.get("name") or "").strip()
        class_name = (row.get("class_name") or "").strip()

        if not sid:
            continue

        pw = (row.get("password") or "").strip()  # [修改]
        if not pw:
            pw = default_password or sid  # [新增] CSV 無 password 且未填預設密碼時 → 用 student_id 當密碼

        existing = db.users.find_one({"student_id": sid, "role": "student"})
        # A CSV group_type never decides the research condition.  Existing
        # locked/manual assignments are retained on re-import; all other
        # formal students are assigned by the server immediately below.
        existing_locked = bool((existing or {}).get("assignment_locked") is True)
        existing_assigned = bool(
            existing
            and existing.get("assignment_status") == "assigned"
            and str(existing.get("group_type") or "").strip()
        )
        if existing_locked or existing_assigned:
            group_type = existing.get("group_type")
        else:
            group_type = None

        imported_test_cycle_id = str(row.get("test_cycle_id") or "").strip()
        if imported_test_cycle_id:
            test_cycle_id = imported_test_cycle_id
        elif existing:
            test_cycle_id = str(existing.get("test_cycle_id") or "").strip()
        else:
            test_cycle_id = ""

        csv_is_test_data = _parse_optional_bool(row.get("is_test_data"))
        if sid == _TEST_STUDENT_ID:
            is_test_data = True
        elif csv_is_test_data is not None:
            is_test_data = csv_is_test_data
        elif existing and isinstance(existing.get("is_test_data"), bool):
            is_test_data = existing.get("is_test_data")
        else:
            is_test_data = False

        doc = {
            "student_id": sid,
            "name": name,
            "class_name": class_name,
            "role": "student",
            "password": pw,
            "group_type": group_type,
            "is_test_data": is_test_data,
        }
        if not existing:
            doc.update({
                "assignment_status": "excluded_test_data" if is_test_data else "pending_randomization",
                "assignment_method": None,
                "assignment_locked": False,
            })
        elif not existing_locked and not existing_assigned:
            doc.update({
                "assignment_status": "excluded_test_data" if is_test_data else "pending_randomization",
                "assignment_method": None,
                "assignment_locked": False,
            })
        if test_cycle_id:
            doc["test_cycle_id"] = test_cycle_id

        db.users.update_one(
            {"student_id": sid, "role": "student"},
            {"$set": doc},
            upsert=True
        )
        upserts += 1

        if is_test_data:
            assignment_summary["excluded_test_data"] += 1
            continue

        try:
            assignment = assign_feedback_strategy_on_import(sid)
            if assignment.get("assigned") and not assignment.get("recovered"):
                assignment_summary["assigned"] += 1
                assigned_group_type = str(assignment.get("group_type") or "").strip().lower()
                if assigned_group_type in {"control", "experimental_1", "experimental_2"}:
                    assignment_summary[assigned_group_type] += 1
            else:
                assignment_summary["already_assigned"] += 1
        except RandomizationSlotsExhausted:
            assignment_summary["failed"].append({
                "student_id": sid,
                "reason": "randomization_slots_exhausted",
            })
        except Exception as exc:
            current_app.logger.exception(
                "Automatic randomization failed during student import: %s",
                sid,
            )
            assignment_summary["failed"].append({
                "student_id": sid,
                "reason": "randomization_assignment_failed",
                "detail": str(exc),
            })

    message = "學生已匯入，並已依全研究共用序列完成自動分派。"
    if assignment_summary["failed"]:
        message = "學生已匯入，但研究分派名額不足；請先新增 slots 後重新匯入同一份 CSV。"
    response = {
        "ok": not bool(assignment_summary["failed"]),
        "upserts": upserts,
        "assignment": assignment_summary,
        "message": message,
    }
    return jsonify(response), (409 if assignment_summary["failed"] else 200)


@records_bp.get("/students/export_csv")
def export_students_csv():
    """
    匯出 students CSV（Excel 不亂碼）
    """
    import io
    import csv

    class_name = (request.args.get("class_name") or "").strip()

    q = {"role": "student"}
    if class_name:
        q["class_name"] = class_name

    rows = list(db.users.find(q, {"_id": 0, "student_id": 1, "name": 1, "class_name": 1, "test_cycle_id": 1}).sort("student_id", 1))

    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["student_id", "name", "class_name", "test_cycle_id"])
    for r in rows:
        w.writerow([r.get("student_id", ""), r.get("name", ""), r.get("class_name", ""), r.get("test_cycle_id", "")])

    csv_text = output.getvalue()
    output.close()

    csv_text = "\ufeff" + csv_text  # [修改] 加 BOM，Excel 開啟不亂碼

    return Response(
        csv_text,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=students.csv; filename*=UTF-8''students.csv"
        },
    )


# =========================================================
# Pretest questionnaire (student_questionnaire_responses)
# =========================================================
def _positive_int(value, default, maximum):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, maximum))


_QUESTIONNAIRE_GROUP_FILTERS = {"control", "experimental_1", "experimental_2", "test_data"}


def _questionnaire_group_filter(value):
    group_type = str(value or "").strip().lower()
    if not group_type:
        return ""
    return group_type if group_type in _QUESTIONNAIRE_GROUP_FILTERS else None


def _questionnaire_student_query(group_filter=""):
    """Return questionnaire students filtered only by users.group_type."""
    query = {"role": "student"}
    if group_filter == "test_data":
        # This view defines missing/null allocation as test data, even when a
        # legacy user has not explicitly populated is_test_data yet.
        query["$or"] = [
            {"group_type": None},
            {"group_type": {"$exists": False}},
        ]
    elif group_filter:
        query["group_type"] = group_filter
    return query


@records_bp.get("/pretest-surveys")
def list_pretest_surveys():
    """List questionnaire status filtered only by randomized group."""
    group_filter = _questionnaire_group_filter(request.args.get("group_type"))
    if group_filter is None:
        return jsonify({"ok": False, "message": "invalid group_type"}), 400
    page = _positive_int(request.args.get("page"), 1, 100000)
    page_size = _positive_int(request.args.get("page_size"), 25, 200)

    student_query = _questionnaire_student_query(group_filter)

    students = list(
        db.users.find(
            student_query,
            {
                "_id": 0,
                "student_id": 1,
                "name": 1,
                "class_name": 1,
                "group_type": 1,
                "test_cycle_id": 1,
            },
        ).sort([("group_type", 1), ("class_name", 1), ("student_id", 1)])
    )
    student_ids = [str(row.get("student_id") or "").strip() for row in students]
    student_ids = [student_id for student_id in student_ids if student_id]

    ensure_questionnaire_indexes(db)
    response_map = {}
    if student_ids:
        response_cursor = db[QUESTIONNAIRE_COLLECTION].find(
            {
                "student_id": {"$in": student_ids},
                "form_version": {"$in": list(QUESTIONNAIRE_FORM_VERSIONS)},
                "locked": True,
            },
            {"student_id": 1, "test_cycle_id": 1, "form_version": 1, "submitted_at": 1},
        )
        for response in response_cursor:
            key = (str(response.get("student_id") or "").strip(), str(response.get("test_cycle_id") or "").strip())
            current = response_map.get(key)
            if not current or response.get("form_version") == QUESTIONNAIRE_FORM_VERSION:
                response_map[key] = response

    rows = []
    for student in students:
        student_id = str(student.get("student_id") or "").strip()
        test_cycle_id = str(student.get("test_cycle_id") or "").strip() or _DEFAULT_TEST_CYCLE_ID
        response = response_map.get((student_id, test_cycle_id))
        submitted_at = (response or {}).get("submitted_at")
        rows.append({
            "student_id": student_id,
            "name": student.get("name") or "",
            "class_name": student.get("class_name") or "",
            "group_type": student.get("group_type"),
            "test_cycle_id": test_cycle_id or None,
            "submitted": bool(response),
            "submission_status": "submitted" if response else "not_submitted",
            "form_version": (response or {}).get("form_version") or None,
            "submitted_at": submitted_at.isoformat() if hasattr(submitted_at, "isoformat") else submitted_at,
        })

    total = len(rows)
    submitted_count = sum(1 for row in rows if row["submitted"])
    start = (page - 1) * page_size
    return jsonify({
        "ok": True,
        "data_source": QUESTIONNAIRE_DATA_SOURCE,
        "group_type": group_filter or None,
        "page": page,
        "page_size": page_size,
        "total": total,
        "completion": {"submitted": submitted_count, "total": total},
        "students": rows[start:start + page_size],
    })


@records_bp.get("/pretest-surveys/<student_id>")
def get_pretest_survey_detail(student_id):
    sid = str(student_id or "").strip()
    student = db.users.find_one(
        {"student_id": sid, "role": "student"},
        {
            "_id": 0,
            "student_id": 1,
            "name": 1,
            "class_name": 1,
            "group_type": 1,
            "test_cycle_id": 1,
        },
    )
    if not student:
        return jsonify({"ok": False, "message": "student not found"}), 404

    test_cycle_id = str(student.get("test_cycle_id") or "").strip() or _DEFAULT_TEST_CYCLE_ID
    response = get_questionnaire_response(db, sid, test_cycle_id)
    submitted_at = (response or {}).get("submitted_at")
    return jsonify({
        "ok": True,
        "data_source": QUESTIONNAIRE_DATA_SOURCE,
        "student": {
            "student_id": sid,
            "name": student.get("name") or "",
            "class_name": student.get("class_name") or "",
            "group_type": student.get("group_type"),
            "test_cycle_id": test_cycle_id or None,
        },
        "submitted": bool(response),
        "form_version": (response or {}).get("form_version") or None,
        "submitted_at": submitted_at.isoformat() if hasattr(submitted_at, "isoformat") else submitted_at,
        "answers": present_questionnaire_answers(response) if response else [],
    })


@records_bp.get("/pretest-surveys/export_csv")
def export_pretest_surveys_csv():
    """Export the current pretest questionnaire dataset for Excel and research use."""
    import csv
    import io

    group_filter = _questionnaire_group_filter(request.args.get("group_type"))
    if group_filter is None:
        return jsonify({"ok": False, "message": "invalid group_type"}), 400
    student_query = _questionnaire_student_query(group_filter)

    students = list(
        db.users.find(
            student_query,
            {
                "_id": 0,
                "student_id": 1,
                "name": 1,
                "class_name": 1,
                "group_type": 1,
                "test_cycle_id": 1,
            },
        ).sort([("group_type", 1), ("class_name", 1), ("student_id", 1)])
    )
    student_ids = [str(student.get("student_id") or "").strip() for student in students]
    student_ids = [student_id for student_id in student_ids if student_id]

    ensure_questionnaire_indexes(db)
    response_map = {}
    if student_ids:
        response_cursor = db[QUESTIONNAIRE_COLLECTION].find(
            {
                "student_id": {"$in": student_ids},
                "form_version": {"$in": list(QUESTIONNAIRE_FORM_VERSIONS)},
                "locked": True,
            },
            {"student_id": 1, "test_cycle_id": 1, "form_version": 1, "submitted_at": 1, "answers": 1},
        )
        for response in response_cursor:
            key = (
                str(response.get("student_id") or "").strip(),
                str(response.get("test_cycle_id") or "").strip(),
            )
            current = response_map.get(key)
            if not current or response.get("form_version") == QUESTIONNAIRE_FORM_VERSION:
                response_map[key] = response

    questions = [
        question
        for page in QUESTIONNAIRE_PAGES
        for question in page["questions"]
    ]
    headers = [
        "群組",
        "group_type",
        "班級",
        "學號",
        "姓名",
        "測驗批次",
        "問卷狀態",
        "提交時間（台北時間）",
        "問卷版本",
        "資料來源",
    ]
    for index, question in enumerate(questions, start=1):
        headers.extend([
            f"Q{index}：{question['label']}",
            f"Q{index}：答案代碼",
        ])

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for student in students:
        student_id = str(student.get("student_id") or "").strip()
        test_cycle_id = str(student.get("test_cycle_id") or "").strip() or _DEFAULT_TEST_CYCLE_ID
        response = response_map.get((student_id, test_cycle_id))
        answer_items = {
            item["question_id"]: item
            for item in present_questionnaire_answers(response)
        } if response else {}
        submitted_at = (response or {}).get("submitted_at")
        row = {
            "群組": (
                "控制組" if student.get("group_type") == "control"
                else "AI 增強型結構化回饋組" if student.get("group_type") == "experimental_1"
                else "結構化錯誤回饋組" if student.get("group_type") == "experimental_2"
                else "測試資料"
            ),
            "group_type": student.get("group_type") or "",
            "班級": student.get("class_name") or "",
            "學號": student_id,
            "姓名": student.get("name") or "",
            "測驗批次": test_cycle_id,
            "問卷狀態": "已提交" if response else "未提交",
            "提交時間（台北時間）": _format_taipei_datetime(submitted_at) if response else "",
            "問卷版本": (response or {}).get("form_version") or "",
            "資料來源": QUESTIONNAIRE_DATA_SOURCE if response else "",
        }
        for index, question in enumerate(questions, start=1):
            answer = answer_items.get(question["id"], {})
            row[f"Q{index}：{question['label']}"] = answer.get("answer_label", "")
            answer_code = answer.get("answer_code", "")
            row[f"Q{index}：答案代碼"] = "|".join(answer_code) if isinstance(answer_code, list) else answer_code
        writer.writerow(row)

    csv_text = "\ufeff" + output.getvalue()
    output.close()
    return Response(
        csv_text,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=pretest_questionnaires.csv; filename*=UTF-8''pretest_questionnaires.csv"
        },
    )


# =========================================================
# Learning events (保留)
# =========================================================
@records_bp.get("/learning_events")
def learning_events():
    participant_id = (request.args.get("participant_id") or "").strip()
    limit = int(request.args.get("limit") or 20)

    if not participant_id:
        return jsonify({"ok": False, "message": "missing participant_id"}), 400

    cursor = (
        db.learning_events.find({"participant_id": participant_id}, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
    )
    events = list(cursor)
    return jsonify({"ok": True, "events": events})


@records_bp.get("/analytics")
def analytics():
    return jsonify({"ok": True, "completion_rate": 0, "avg_score": 0, "parsons_completed": 0})


def _safe_dt(v):
    if isinstance(v, datetime):
        return v
    if isinstance(v, str) and v.strip():
        s = v.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


@records_bp.get("/corrected_success.csv")
def export_corrected_success_csv():
    """
    GET /api/records/corrected_success.csv?class_name=資管系A&student_id=11461127&task_id=...&rq3_only=1

    以 (student_id, task_id) 分組，依 created_at 排序：
    - 若先出現 is_correct=false，後續又出現 is_correct=true，則 corrected_success=1
    - 其餘為 corrected_success=0
    - rq3_eligible=1 代表此組至少曾出現錯誤（可納入「錯後修正」分析）
    """
    import io
    import csv

    class_name = (request.args.get("class_name") or "").strip()
    student_id_arg = (request.args.get("student_id") or "").strip()
    task_id_arg = (request.args.get("task_id") or "").strip()
    rq3_only = str(request.args.get("rq3_only") or "").strip().lower() in ("1", "true", "yes")

    q = {}
    if student_id_arg:
        q["student_id"] = student_id_arg
    if task_id_arg:
        q["task_id"] = task_id_arg

    user_map = {}
    student_ids_in_class = None
    if "users" in db.list_collection_names():
        uq = {"role": "student"}
        if class_name:
            uq["class_name"] = class_name
        for u in db.users.find(uq, {"_id": 0, "student_id": 1, "name": 1, "class_name": 1}):
            sid = str(u.get("student_id") or "").strip()
            if sid:
                user_map[sid] = {
                    "name": u.get("name") or "",
                    "class_name": u.get("class_name") or "",
                }
        if class_name:
            student_ids_in_class = set(user_map.keys())

    if class_name:
        if student_ids_in_class is None:
            student_ids_in_class = set()
        if student_id_arg and student_id_arg not in student_ids_in_class:
            q["student_id"] = "__NO_MATCH__"
        elif not student_id_arg:
            q["student_id"] = {"$in": list(student_ids_in_class) or ["__NO_MATCH__"]}

    projection = {
        "_id": 1,
        "student_id": 1,
        "task_id": 1,
        "is_correct": 1,
        "created_at": 1,
    }
    attempts = list(db.parsons_attempts.find(q, projection))

    groups = {}
    for a in attempts:
        sid = str(a.get("student_id") or "").strip()
        tid = str(a.get("task_id") or "").strip()
        if not sid or sid.lower() == "unknown" or not tid:
            continue
        key = (sid, tid)
        groups.setdefault(key, []).append(a)

    rows = []
    for (sid, tid), items in groups.items():
        items_sorted = sorted(
            items,
            key=lambda x: (_safe_dt(x.get("created_at")), str(x.get("_id") or ""))
        )

        seen_wrong = False
        corrected_success = 0
        first_wrong_at = ""
        first_correct_at = ""
        first_is_correct = None

        for idx, it in enumerate(items_sorted):
            is_ok = bool(it.get("is_correct", False))
            c_at = it.get("created_at")
            c_at_s = c_at.isoformat() if isinstance(c_at, datetime) else str(c_at or "")

            if idx == 0:
                first_is_correct = 1 if is_ok else 0

            if not is_ok and not first_wrong_at:
                first_wrong_at = c_at_s
                seen_wrong = True
                continue

            if is_ok and not first_correct_at:
                first_correct_at = c_at_s

            if is_ok and seen_wrong:
                corrected_success = 1
                break

        rq3_eligible = 1 if first_is_correct == 0 else 0
        if rq3_only and rq3_eligible == 0:
            continue

        rows.append({
            "student_id": sid,
            "name": (user_map.get(sid) or {}).get("name", ""),
            "class_name": (user_map.get(sid) or {}).get("class_name", ""),
            "task_id": tid,
            "attempt_count": len(items_sorted),
            "first_is_correct": first_is_correct if first_is_correct is not None else "",
            "first_wrong_at": first_wrong_at,
            "first_correct_at": first_correct_at,
            "rq3_eligible": rq3_eligible,
            "corrected_success": corrected_success,
        })

    rows.sort(key=lambda r: (r.get("student_id", ""), r.get("task_id", "")))

    headers = [
        "student_id",
        "name",
        "class_name",
        "task_id",
        "attempt_count",
        "first_is_correct",
        "first_wrong_at",
        "first_correct_at",
        "rq3_eligible",
        "corrected_success",
    ]

    output = io.StringIO()
    w = csv.DictWriter(output, fieldnames=headers)
    w.writeheader()
    for r in rows:
        w.writerow(r)

    csv_text = "\ufeff" + output.getvalue()
    output.close()

    return Response(
        csv_text,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=corrected_success.csv; filename*=UTF-8''corrected_success.csv"
        },
    )


# =========================================================
# ✅ Parsons test attempts export (Excel 不亂碼 + 會補齊學生姓名/班級)
# =========================================================
@records_bp.get("/test_attempts.csv")
def export_parsons_test_attempts_csv():
    """
    GET /api/records/test_attempts.csv?test_cycle_id=default&class_name=資工系A
    匯出 parsons_test_attempts 成 CSV（Excel 不亂碼、含學生姓名/班級）
    """
    import io, csv

    test_cycle_id = (request.args.get("test_cycle_id") or "").strip()
    class_name = (request.args.get("class_name") or "").strip()

    q = {}
    if test_cycle_id:
        q["test_cycle_id"] = test_cycle_id

    # [新增] class_name 篩選：以 users 反查 student_id，再用 $in 篩選 attempts（因 attempts 可能沒存班級）
    user_map = {}
    if "users" in db.list_collection_names():
        uq = {"role": "student"}
        if class_name:
            uq["class_name"] = class_name
        for u in db.users.find(uq, {"_id": 0, "student_id": 1, "name": 1, "class_name": 1}):
            sid = str(u.get("student_id") or "")
            if sid:
                user_map[sid] = {"name": u.get("name", ""), "class_name": u.get("class_name", "")}

        if class_name:
            ids = list(user_map.keys())
            if not ids:
                output = io.StringIO()
                w = csv.writer(output)
                w.writerow(["student_id", "class_name", "name", "test_cycle_id", "assessment_version", "test_role", "question_text", "concept_tag", "selected_answer", "selected_answer_text", "correct_answer", "is_correct", "score", "max_score", "min_score", "duration_seconds", "wrong_indices", "started_at", "submitted_at", "created_at", "updated_at"])
                csv_text = "\ufeff" + output.getvalue()
                output.close()
                return Response(
                    csv_text,
                    mimetype="text/csv; charset=utf-8",
                    headers={
                        "Content-Disposition": "attachment; filename=test_attempts.csv; filename*=UTF-8''test_attempts.csv"
                    },
                )
            q["student_id"] = {"$in": ids}

    cur = db.parsons_test_attempts.find(q).sort([("submitted_at", 1), ("student_id", 1)])

    headers = ["student_id", "class_name", "name", "test_cycle_id", "assessment_version", "test_role", "question_text", "concept_tag", "selected_answer", "selected_answer_text", "correct_answer", "is_correct", "score", "max_score", "min_score", "duration_seconds", "wrong_indices", "started_at", "submitted_at", "created_at", "updated_at"]
    output = io.StringIO()
    w = csv.DictWriter(output, fieldnames=headers)
    w.writeheader()

    for a in cur:
        sid = str(a.get("student_id") or "")
        um = user_map.get(sid, {})
        wrong = a.get("wrong_indices", [])
        if isinstance(wrong, list):
            wrong_str = ",".join([str(x) for x in wrong])
        else:
            wrong_str = str(wrong or "")

        w.writerow({
            "student_id": sid,
            "class_name": a.get("class_name") or um.get("class_name", ""),
            "name": a.get("name") or um.get("name", ""),
            "test_cycle_id": a.get("test_cycle_id", ""),
            "assessment_version": a.get("assessment_version", ""),
            "test_role": a.get("test_role", ""),
            "question_text": a.get("question_text") or a.get("task_title", ""),
            "concept_tag": a.get("concept_tag") or a.get("target_concept", ""),
            "selected_answer": a.get("selected_answer") or a.get("answer", ""),
            "selected_answer_text": a.get("selected_answer_text") or a.get("answer_text", ""),
            "correct_answer": a.get("correct_answer") or a.get("expected_answer", ""),
            "is_correct": a.get("is_correct", ""),
            "score": a.get("score", ""),
            "max_score": a.get("max_score", ""),
            "min_score": a.get("min_score", ""),
            "duration_seconds": a.get("duration_seconds") if a.get("duration_seconds") is not None else a.get("duration_sec", ""),
            "wrong_indices": wrong_str,
            "started_at": _format_taipei_datetime(a.get("started_at") or a.get("started_at_utc")),
            "submitted_at": _format_taipei_datetime(a.get("submitted_at") or a.get("submitted_at_utc")),
            "created_at": _format_taipei_datetime(a.get("created_at") or a.get("created_at_utc")),
            "updated_at": _format_taipei_datetime(a.get("updated_at") or a.get("updated_at_utc")),
        })

    csv_text = output.getvalue()
    output.close()

    csv_text = "\ufeff" + csv_text  # [修改] BOM + utf-8，Excel 開啟不亂碼

    return Response(
        csv_text,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=test_attempts.csv; filename*=UTF-8''test_attempts.csv"
        },
    )
