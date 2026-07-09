from flask import Blueprint, request, jsonify, Response
from app.db import db
from app.avatar_utils import resolve_avatar_src
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash

records_bp = Blueprint("records", __name__)
_TEST_STUDENT_ID = "11461127"
_MAX_USER_CSV_BYTES = 2 * 1024 * 1024
_VALID_GROUP_TYPES = {"control", "experimental"}
_VALID_USER_ROLES = {"student", "teacher", "admin"}
_SEXJ_ALIASES = {
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


def _normalize_sexj(value):
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    return _SEXJ_ALIASES.get(normalized)

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
            "group_type": 1,
            "is_test_data": 1,
            "sexj": 1,
            "avatar_type": 1,
            "avatar_key": 1,
            "avatar_src": 1,
            "avatar_value": 1,
            "avatar_url": 1,
            "avatar_style": 1,
            "avatar_license": 1,
            "avatar_updated_at": 1,
            "created_at": 1,
        })
        .sort("created_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    students = list(cursor)
    for student in students:
        student["avatar_src"] = resolve_avatar_src(student)
        student.pop("avatar_url", None)
        student.pop("avatar_value", None)
    return jsonify({"ok": True, "students": students, "total": total})


@records_bp.post("/students/import_csv")
def import_students_csv():
    """
    匯入 students CSV
    欄位：student_id,name,class_name,role,group_type,is_test_data,sexj
    預設密碼固定為 student_id，資料庫只儲存 password_hash。
    """
    import io
    import csv

    if "file" not in request.files:
        return jsonify({"ok": False, "message": "missing file"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"ok": False, "message": "empty filename"}), 400
    if not str(f.filename).lower().endswith(".csv"):
        return jsonify({"ok": False, "message": "csv_file_required"}), 400

    raw = f.stream.read(_MAX_USER_CSV_BYTES + 1)
    if len(raw) > _MAX_USER_CSV_BYTES:
        return jsonify({"ok": False, "message": "csv_file_too_large"}), 413
    # Excel 常見 UTF-8 BOM
    text = raw.decode("utf-8-sig", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    headers = {str(name or "").strip() for name in (reader.fieldnames or [])}
    required = {"student_id", "name", "class_name"}
    if not required.issubset(headers):
        return jsonify({"ok": False, "message": "CSV 欄位需包含 student_id,name,class_name"}), 400

    upserts = 0
    invalid_rows = []
    for row_number, row in enumerate(reader, start=2):
        sid = (row.get("student_id") or "").strip()
        name = (row.get("name") or "").strip()
        class_name = (row.get("class_name") or "").strip()

        if not sid:
            invalid_rows.append({
                "row": row_number,
                "student_id": "",
                "reason": "missing_student_id",
            })
            continue

        existing = db.users.find_one({"student_id": sid})
        role = str(row.get("role") or "student").strip().lower() or "student"
        if role not in _VALID_USER_ROLES:
            invalid_rows.append({
                "row": row_number,
                "student_id": sid,
                "reason": "invalid_role",
            })
            continue
        sexj_raw = str(row.get("sexj") or "").strip()
        sexj = _normalize_sexj(sexj_raw)
        if sexj_raw and not sexj:
            continue
        group_type_input = str(row.get("group_type") or "").strip()
        if group_type_input:
            group_type = group_type_input.lower()
            if group_type not in _VALID_GROUP_TYPES:
                invalid_rows.append({
                    "row": row_number,
                    "student_id": sid,
                    "reason": "invalid_group_type",
                })
                continue
        elif existing and "group_type" in existing:
            group_type = existing.get("group_type")
        else:
            group_type = None

        is_test_data_raw = str(row.get("is_test_data") or "").strip()
        csv_is_test_data = _parse_optional_bool(is_test_data_raw)
        if is_test_data_raw and csv_is_test_data is None:
            invalid_rows.append({
                "row": row_number,
                "student_id": sid,
                "reason": "invalid_is_test_data",
            })
            continue
        if sid == _TEST_STUDENT_ID:
            is_test_data = True
        elif csv_is_test_data is not None:
            is_test_data = csv_is_test_data
        elif existing and isinstance(existing.get("is_test_data"), bool):
            is_test_data = existing.get("is_test_data")
        else:
            is_test_data = False
        if group_type is None and not is_test_data:
            invalid_rows.append({
                "row": row_number,
                "student_id": sid,
                "reason": "missing_group_type",
            })
            continue

        doc = {
            "student_id": sid,
            "name": name,
            "class_name": class_name,
            "role": role,
            "group_type": group_type,
            "is_test_data": is_test_data,
            "sexj": sexj,
            "updated_at": datetime.now(timezone.utc),
        }
        if existing:
            if not existing.get("password_hash"):
                doc["password_hash"] = generate_password_hash(sid)
            if "created_at" not in existing:
                doc["created_at"] = doc["updated_at"]
            if "last_login_at" not in existing:
                doc["last_login_at"] = None
            db.users.update_one({"_id": existing["_id"]}, {"$set": doc})
        else:
            doc["password_hash"] = generate_password_hash(sid)
            doc["created_at"] = doc["updated_at"]
            doc["last_login_at"] = None
            db.users.insert_one(doc)
        upserts += 1

    return jsonify({
        "ok": True,
        "upserts": upserts,
        "invalid_rows": invalid_rows,
    })


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

    rows = list(db.users.find(
        q,
        {"_id": 0, "student_id": 1, "name": 1, "class_name": 1, "sexj": 1},
    ).sort("student_id", 1))

    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["student_id", "name", "class_name", "sexj"])
    for r in rows:
        w.writerow([
            r.get("student_id", ""),
            r.get("name", ""),
            r.get("class_name", ""),
            r.get("sexj") or "",
        ])

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
    GET /api/records/corrected_success.csv?class_name=資工系A&student_id=11461127&task_id=...&rq3_only=1

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
                w.writerow(["student_id", "class_name", "name", "test_role", "is_correct", "score", "duration_sec", "wrong_indices", "submitted_at"])
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

    headers = ["student_id", "class_name", "name", "test_role", "is_correct", "score", "duration_sec", "wrong_indices", "submitted_at"]
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
            "test_role": a.get("test_role", ""),
            "is_correct": a.get("is_correct", ""),
            "score": a.get("score", ""),
            "duration_sec": a.get("duration_sec", ""),
            "wrong_indices": wrong_str,
            "submitted_at": a.get("submitted_at", ""),
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
