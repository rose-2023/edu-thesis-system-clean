# app/routes/records.py
from flask import Blueprint, request, jsonify
from datetime import datetime, timezone
from app.db import db
from bson import ObjectId

records_bp = Blueprint("records", __name__)

def _utc_now():
    return datetime.now(timezone.utc)

@records_bp.get("/students")
def list_students():
    """
    GET /api/records/students?class_name=...&page=1&page_size=15
    """
    class_name = (request.args.get("class_name") or "").strip()
    page = int(request.args.get("page") or 1)
    page_size = int(request.args.get("page_size") or 15)

    q = {"role": "student"}
    if class_name:
        q["class_name"] = class_name

    total = db.users.count_documents(q)
    cursor = (
        db.users.find(q, {"_id": 0, "password_hash": 0})
        .sort("created_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    students = list(cursor)
    return jsonify({"ok": True, "students": students, "total": total})

@records_bp.post("/students/import_csv")
def import_students_csv():
    """
    POST /api/records/students/import_csv
    form-data:
      - file: CSV 檔
      - default_password: (optional) 若 CSV 無 password 欄位，統一用這個密碼（可不傳）
      - default_class_name: (optional) 若 CSV 無 class_name 或為空白，使用這個班級（可不傳）
    CSV 欄位建議（最少 student_id 即可；其他可選）：
      student_id,name(optional),class_name(optional),password(optional)
    """
    if "users" not in db.list_collection_names():
        # [新增] 若尚未有 users collection，MongoDB 會在首次 insert 時自動建立
        pass

    f = request.files.get("file")
    default_password = (request.form.get("default_password") or "").strip()  # [新增]
    default_class_name = (request.form.get("default_class_name") or "").strip()  # [新增]

    if not f:
        return jsonify({"ok": False, "message": "missing file"}), 400

    try:
        raw = f.read()
        # 嘗試 utf-8-sig（Excel 常見）
        try:
            text = raw.decode("utf-8-sig")
        except Exception:
            text = raw.decode("utf-8")

        import csv, io
        reader = csv.DictReader(io.StringIO(text))

        # [新增] 最少需要 student_id；class_name / name / password 允許缺
        fieldnames = set([c.strip() for c in (reader.fieldnames or [])])
        if "student_id" not in fieldnames:
            return jsonify({"ok": False, "message": "CSV 欄位缺少：student_id"}), 400

        from werkzeug.security import generate_password_hash

        inserted = 0
        updated = 0
        errors = []

        for i, row in enumerate(reader, start=2):  # header=1
            sid = (row.get("student_id") or "").strip()
            name = (row.get("name") or "").strip()
            cname = (row.get("class_name") or "").strip() or default_class_name  # [新增]
            pw = (row.get("password") or "").strip()

            if not sid:
                errors.append({"line": i, "student_id": sid, "message": "欄位空白（student_id）"})
                continue

            # [新增] 班級可缺：若仍為空，給保底
            if not cname:
                cname = "資工系A"

            # [新增] 密碼可缺：優先 CSV password，其次 default_password，再其次 student_id
            if not pw:
                pw = default_password or sid

            doc = {
                "student_id": sid,
                "name": name or sid,  # [新增] name 可缺，保底用學號
                "class_name": cname,
                "role": "student",
            }

            # 若已存在就不重設密碼（避免覆蓋既有帳密）
            existing = db.users.find_one({"student_id": sid})
            if existing:
                db.users.update_one({"_id": existing["_id"]}, {"$set": {**doc, "updated_at": _utc_now()}})
                updated += 1
            else:
                doc["password_hash"] = generate_password_hash(pw, method="scrypt")
                doc["created_at"] = _utc_now()
                doc["updated_at"] = _utc_now()
                db.users.insert_one(doc)
                inserted += 1

        return jsonify({"ok": True, "inserted": inserted, "updated": updated, "errors": errors})

    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500

@records_bp.get("/students/export_csv")
def export_students_csv():
    """
    GET /api/records/students/export_csv?class_name=...
    """
    class_name = (request.args.get("class_name") or "").strip()
    q = {"role": "student"}
    if class_name:
        q["class_name"] = class_name

    import csv, io
    from flask import Response

    rows = list(db.users.find(q, {"_id": 0, "password_hash": 0}).sort("created_at", 1))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["student_id", "name", "class_name"])
    for r in rows:
        writer.writerow([r.get("student_id", ""), r.get("name", ""), r.get("class_name", "")])

    csv_text = output.getvalue()
    output.close()

    return Response(
        csv_text,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=students.csv"},
    )

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

@records_bp.get("/rewatch_stats")
def rewatch_stats():
    # 依你專案既有邏輯（保留）
    return jsonify({"ok": True, "items": []})

@records_bp.get("/class_rewatch_analytics")
def class_rewatch_analytics():
    # 依你專案既有邏輯（保留）
    return jsonify({"ok": True, "items": []})

@records_bp.get("/rewatch_behavior_summary")
def rewatch_behavior_summary():
    # 依你專案既有邏輯（保留）
    return jsonify({"ok": True, "items": []})


# =========================
# [新增] Export parsons_test_attempts as CSV
# GET /api/records/test_attempts.csv?class_name=資工系A&test_role=post&test_cycle_id=default
# test_cycle_id 可留空（留空=不過濾）
# =========================
from flask import Response  # [新增]
import csv  # [新增]
import io  # [新增]

@records_bp.get("/test_attempts.csv")  # [新增]
def export_parsons_test_attempts_csv():  # [新增]
    test_cycle_id = (request.args.get("test_cycle_id") or "").strip()  # [新增] 可空
    class_name = (request.args.get("class_name") or "").strip()        # [新增] 可空
    test_role = (request.args.get("test_role") or "").strip().lower()  # [新增] 可空

    q = {}  # [新增]
    if test_cycle_id:  # [新增]
        q["test_cycle_id"] = test_cycle_id  # [新增]
    if class_name:  # [新增]
        q["class_name"] = class_name  # [新增]
    if test_role in ("pre", "post"):  # [新增]
        q["test_role"] = test_role  # [新增]

    rows = list(db.parsons_test_attempts.find(q).sort("submitted_at", 1))  # [新增]

    output = io.StringIO()  # [新增]
    writer = csv.writer(output)  # [新增]

    writer.writerow([  # [新增]
        "student_id", "class_name", "name", "test_role",
        "is_correct", "score", "duration_sec",
        "wrong_indices", "submitted_at",
        "test_cycle_id", "test_task_id", "source_task_id"
    ])

    for r in rows:  # [新增]
        writer.writerow([  # [新增]
            r.get("student_id", ""),
            r.get("class_name", ""),
            r.get("name", ""),
            r.get("test_role", ""),
            r.get("is_correct", ""),
            r.get("score", ""),
            r.get("duration_sec", ""),
            r.get("wrong_indices", ""),
            r.get("submitted_at", ""),
            r.get("test_cycle_id", ""),
            r.get("test_task_id", ""),
            r.get("source_task_id", ""),
        ])

    csv_text = output.getvalue()  # [新增]
    output.close()  # [新增]

    csv_text = "\ufeff" + csv_text        # [新增] ✅ Excel 需要 BOM 才不會中文亂碼

    return Response(
        csv_text,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=parsons_test_attempts.csv"}
    )