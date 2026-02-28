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

    - v1.8 起：以 thesis_system.users 為主（role=student）
    - 若舊版仍使用 participants collection，會自動 fallback
    """
    class_name = (request.args.get("class_name") or request.args.get("class_id") or "").strip()  # [新增] class_name / class_id 相容
    page = int(request.args.get("page") or 1)
    page_size = int(request.args.get("page_size") or 15)
    page = max(page, 1)
    page_size = 15 if page_size <= 0 else min(page_size, 500)  # [新增] 允許一次抓更多供分析頁使用

    # [新增] 優先使用 users collection
    if "users" in db.list_collection_names():
        q = {"role": "student"}
        if class_name:
            q["class_name"] = class_name

        total = db.users.count_documents(q)
        rows = []
        if total:
            cur = db.users.find(q).sort([("class_name", 1), ("student_id", 1)]).skip((page - 1) * page_size).limit(page_size)
            for u in cur:
                rows.append({
                    "student_id": u.get("student_id"),
                    "name": u.get("name"),
                    "class_name": u.get("class_name"),
                    "role": u.get("role", "student"),
                })
        return jsonify({"ok": True, "total": total, "page": page, "page_size": page_size, "students": rows})

    # [保留] 舊版 participants fallback（不破壞既有功能）
    class_id = class_name
    q = {}
    if class_id:
        q["class_id"] = class_id

    total = db.participants.count_documents(q) if "participants" in db.list_collection_names() else 0
    rows = []
    if total:
        cur = db.participants.find(q).sort([("student_id", 1)]).skip((page - 1) * page_size).limit(page_size)
        for p in cur:
            rows.append({
                "participant_id": p.get("participant_id"),
                "student_id": p.get("student_id"),
                "name": p.get("name"),
                "class_id": p.get("class_id")
            })

    return jsonify({"ok": True, "total": total, "page": page, "page_size": page_size, "students": rows})


# =========================
# [新增] v1.8：學生帳號 CSV 匯入/匯出（thesis_system.users）
# =========================

@records_bp.post("/students/import_csv")
def import_students_csv():
    """
    POST /api/records/students/import_csv
    form-data:
      - file: CSV 檔
      - default_password: (optional) 若 CSV 無 password 欄位，統一用這個密碼
    CSV 欄位建議：
      student_id,name,class_name,password(optional)
    """
    if "users" not in db.list_collection_names():
        # [新增] 若尚未有 users collection，MongoDB 會在首次 insert 時自動建立
        pass

    f = request.files.get("file")
    default_password = (request.form.get("default_password") or "123456").strip()

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
        required = {"student_id", "name", "class_name"}
        missing = required - set([c.strip() for c in (reader.fieldnames or [])])
        if missing:
            return jsonify({"ok": False, "message": f"CSV 欄位缺少：{', '.join(sorted(missing))}"}), 400

        from werkzeug.security import generate_password_hash

        upserted = 0
        updated = 0
        errors = []

        for i, row in enumerate(reader, start=2):  # header=1
            sid = (row.get("student_id") or "").strip()
            name = (row.get("name") or "").strip()
            cname = (row.get("class_name") or "").strip()
            pw = (row.get("password") or "").strip() or default_password

            if not sid or not name or not cname:
                errors.append({"line": i, "student_id": sid, "message": "欄位空白（student_id/name/class_name）"})
                continue

            doc = {
                "student_id": sid,
                "name": name,
                "class_name": cname,
                "role": "student",
            }

            # [新增] 若已存在就不重設密碼（避免覆蓋既有帳密）
            existing = db.users.find_one({"student_id": sid})
            if existing:
                db.users.update_one({"_id": existing["_id"]}, {"$set": {**doc, "updated_at": _utc_now()}})
                updated += 1
            else:
                doc["password_hash"] = generate_password_hash(pw, method="scrypt")
                doc["created_at"] = _utc_now()
                doc["updated_at"] = _utc_now()
                db.users.insert_one(doc)
                upserted += 1

        return jsonify({"ok": True, "inserted": upserted, "updated": updated, "errors": errors})

    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@records_bp.get("/students/export_csv")
def export_students_csv():
    """
    GET /api/records/students/export_csv
    匯出 thesis_system.users(role=student) 成 CSV
    """
    import io, csv
    from flask import Response

    cur = db.users.find({"role": "student"}).sort([("class_name", 1), ("student_id", 1)])

    headers = ["student_id", "name", "class_name"]
    output = io.StringIO()
    w = csv.DictWriter(output, fieldnames=headers)
    w.writeheader()

    for u in cur:
        w.writerow({
            "student_id": u.get("student_id", ""),
            "name": u.get("name", ""),
            "class_name": u.get("class_name", ""),
        })

    csv_text = output.getvalue()
    output.close()

    filename = "students.csv"
    return Response(
        csv_text,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

@records_bp.get("/learning_events")
def learning_events():
    """
    依學生查事件/流線
    GET /api/records/learning_events?participant_id=...&limit=50
    """
    pid = (request.args.get("participant_id") or "").strip()
    limit = int(request.args.get("limit") or 50)
    limit = min(max(limit, 1), 200)

    if not pid:
        return jsonify({"ok": False, "message": "missing participant_id"}), 400

    rows = []
    if "events" in db.list_collection_names():
        cur = db.events.find({"participant_id": pid}).sort([("created_at", -1)]).limit(limit)
        for e in cur:
            rows.append({
                "type": e.get("type"),
                "video_id": str(e.get("video_id")) if e.get("video_id") else None,
                "meta": e.get("meta", {}),
                "created_at": e.get("created_at")
            })
    return jsonify({"ok": True, "events": rows})

# ========== V1.7: 回看統計分析 ==========

@records_bp.get("/rewatch_stats")
def rewatch_stats():
    """
    GET /api/records/rewatch_stats?student_id=...&task_id=...&video_id=...
    查詢指定學生/任務的回看統計
    """
    student_id = (request.args.get("student_id") or "").strip()
    task_id = (request.args.get("task_id") or "").strip()
    video_id = (request.args.get("video_id") or "").strip()
    
    if not student_id:
        return jsonify({"ok": False, "message": "missing student_id"}), 400
    
    # 構建查詢條件
    query = {"student_id": student_id}
    if task_id:
        query["task_id"] = task_id
    if video_id:
        query["video_id"] = video_id
    
    # 查詢回看日誌
    logs = list(db.video_rewatch_logs.find(query).sort("recorded_at", -1))
    
    # 統計數據
    total_rewatches = len(logs)
    total_watch_time = sum(log.get("watch_seconds", 0) for log in logs)
    rewatches_with_completion = sum(1 for log in logs if log.get("reached_end"))
    frequent_seekers = sum(1 for log in logs if log.get("is_frequent_seeker"))
    
    # 後續已回答的統計
    with_followup = sum(1 for log in logs if log.get("has_followup"))
    followup_correct = sum(1 for log in logs if log.get("followup_is_correct"))
    
    return jsonify({
        "ok": True,
        "student_id": student_id,
        "summary": {
            "total_rewatches": total_rewatches,
            "total_watch_minutes": round(total_watch_time / 60, 2),
            "completed_rewatches": rewatches_with_completion,
            "completion_rate": f"{(rewatches_with_completion/total_rewatches*100):.1f}%" if total_rewatches > 0 else "0%",
            "frequent_seek_sessions": frequent_seekers,
            "with_followup": with_followup,
            "followup_correct_count": followup_correct,
            "followup_accuracy": f"{(followup_correct/with_followup*100):.1f}%" if with_followup > 0 else "0%",
        },
        "logs": [
            {
                "attempt_id": log.get("attempt_id"),
                "video_id": log.get("video_id"),
                "watch_seconds": log.get("watch_seconds"),
                "watch_minutes": round(log.get("watch_seconds", 0) / 60, 2),
                "reached_end": log.get("reached_end"),
                "seek_count": log.get("seek_count"),
                "is_frequent_seeker": log.get("is_frequent_seeker"),
                "followup_is_correct": log.get("followup_is_correct"),
                "recorded_at": log.get("recorded_at")
            }
            for log in logs
        ]
    })

@records_bp.get("/class_rewatch_analytics")
def class_rewatch_analytics():
    """
    GET /api/records/class_rewatch_analytics?class_name=資工系A班
    查詢整個班級的回看行為分析
    """
    class_name = (request.args.get("class_name") or "").strip()
    
    if not class_name:
        return jsonify({"ok": False, "message": "missing class_name"}), 400
    
    # 獲取該班級所有學生
    students = list(db.users.find(
        {"class_name": class_name, "role": "student"},
        {"student_id": 1, "name": 1, "participant_id": 1, "rewatch_stats": 1}
    ))
    
    class_analytics = []
    
    for student in students:
        student_id = student.get("student_id")
        rewatch_stats = student.get("rewatch_stats", {})
        
        # 查詢該學生的回看日誌
        logs = list(db.video_rewatch_logs.find({"student_id": student_id}))
        
        # 計算統計
        total_rewatches = len(logs)
        never_rewatched = len(rewatch_stats.get("videos_never_rewatched", []))
        avg_watch_time = (sum(log.get("watch_seconds", 0) for log in logs) / total_rewatches 
                         if total_rewatches > 0 else 0)
        completed = sum(1 for log in logs if log.get("reached_end"))
        frequent_seekers = sum(1 for log in logs if log.get("is_frequent_seeker"))
        
        # 後續回答統計
        with_followup = sum(1 for log in logs if log.get("has_followup"))
        followup_correct = sum(1 for log in logs if log.get("followup_is_correct"))
        
        class_analytics.append({
            "student_id": student_id,
            "name": student.get("name"),
            "participant_id": student.get("participant_id"),
            "total_rewatches": total_rewatches,
            "never_rewatched_count": never_rewatched,
            "avg_watch_minutes": round(avg_watch_time / 60, 2),
            "completion_rate": f"{(completed/total_rewatches*100):.1f}%" if total_rewatches > 0 else "0%",
            "frequent_seek_sessions": frequent_seekers,
            "with_followup": with_followup,
            "followup_accuracy": f"{(followup_correct/with_followup*100):.1f}%" if with_followup > 0 else "0%",
        })
    
    return jsonify({
        "ok": True,
        "class_name": class_name,
        "total_students": len(students),
        "analytics": sorted(class_analytics, key=lambda x: x["total_rewatches"], reverse=True)
    })

@records_bp.get("/rewatch_behavior_summary")
def rewatch_behavior_summary():
    """
    GET /api/records/rewatch_behavior_summary?video_id=xxx
    V1.7 完整分析：回看行為分類
    - 哪個學生點了回看幾次
    - 哪個學生每次都不回看
    - 回看後是否答對
    - 回看時是否真的播放到結束
    - 是否頻繁 seek（亂拖）
    """
    video_id = (request.args.get("video_id") or "").strip()
    task_id = (request.args.get("task_id") or "").strip()
    
    query = {}
    if video_id:
        query["video_id"] = video_id
    if task_id:
        query["task_id"] = task_id
    
    logs = list(db.video_rewatch_logs.find(query))
    
    # 按學生分組
    student_behaviors = {}
    
    for log in logs:
        sid = log.get("student_id")
        if sid not in student_behaviors:
            student_behaviors[sid] = {
                "student_id": sid,
                "participant_id": log.get("participant_id"),
                "total_rewatches": 0,
                "completed_rewatches": 0,
                "frequent_seek_count": 0,
                "followup_sessions": 0,
                "followup_correct_count": 0,
                "logs": []
            }
        
        student_behaviors[sid]["total_rewatches"] += 1
        if log.get("reached_end"):
            student_behaviors[sid]["completed_rewatches"] += 1
        if log.get("is_frequent_seeker"):
            student_behaviors[sid]["frequent_seek_count"] += 1
        if log.get("has_followup"):
            student_behaviors[sid]["followup_sessions"] += 1
            if log.get("followup_is_correct"):
                student_behaviors[sid]["followup_correct_count"] += 1
        
        student_behaviors[sid]["logs"].append({
            "attempt_id": log.get("attempt_id"),
            "watch_seconds": log.get("watch_seconds"),
            "reached_end": log.get("reached_end"),
            "seek_count": log.get("seek_count"),
            "is_frequent_seeker": log.get("is_frequent_seeker"),
            "followup_is_correct": log.get("followup_is_correct"),
            "recorded_at": log.get("recorded_at")
        })
    
    # 分類學生
    categories = {
        "never_rewatched": [],           # 從未回看
        "always_complete": [],           # 每次都看完
        "frequent_seekers": [],          # 頻繁 seek
        "improved_after_review": [],     # 回看後改正
        "failed_after_review": []        # 回看仍未改正
    }
    
    for sid, behavior in student_behaviors.items():
        if behavior["total_rewatches"] == 0:
            categories["never_rewatched"].append(sid)
        elif behavior["completed_rewatches"] == behavior["total_rewatches"]:
            categories["always_complete"].append(sid)
        
        if behavior["frequent_seek_count"] > 0:
            categories["frequent_seekers"].append(sid)
        
        if behavior["followup_sessions"] > 0:
            accuracy = behavior["followup_correct_count"] / behavior["followup_sessions"]
            if accuracy > 0.5:
                categories["improved_after_review"].append(sid)
            else:
                categories["failed_after_review"].append(sid)
    
    return jsonify({
        "ok": True,
        "summary": {
            "total_unique_students": len(student_behaviors),
            "never_rewatched_count": len(categories["never_rewatched"]),
            "always_complete_count": len(categories["always_complete"]),
            "frequent_seekers_count": len(categories["frequent_seekers"]),
            "improved_after_review_count": len(categories["improved_after_review"]),
            "failed_after_review_count": len(categories["failed_after_review"]),
        },
        "categories": categories,
        "detailed_behaviors": {
            sid: {
                "total_rewatches": behavior["total_rewatches"],
                "completed_rewatches": behavior["completed_rewatches"],
                "completion_rate": f"{(behavior['completed_rewatches']/behavior['total_rewatches']*100):.1f}%" if behavior["total_rewatches"] > 0 else "0%",
                "frequent_seek_count": behavior["frequent_seek_count"],
                "followup_sessions": behavior["followup_sessions"],
                "followup_accuracy": f"{(behavior['followup_correct_count']/behavior['followup_sessions']*100):.1f}%" if behavior["followup_sessions"] > 0 else "0%",
                "is_never_rewatcher": behavior["total_rewatches"] == 0,
                "is_frequent_seeker": behavior["frequent_seek_count"] > 0,
                "improved_after_review": behavior["followup_correct_count"] > behavior["followup_sessions"] * 0.5 if behavior["followup_sessions"] > 0 else False,
            }
            for sid, behavior in student_behaviors.items()
        }
    })
