from flask import Blueprint, request, jsonify
from datetime import datetime
from bson import ObjectId
from datetime import datetime, timezone
from ..db import db

quiz_bp = Blueprint("quiz", __name__)

PRE_TOTAL = 10  # 你的前測固定 10 題

def oid(s):
    try:
        return ObjectId(s)
    except Exception:
        return None

@quiz_bp.get("/next")
def next_question():
    session_id = request.args.get("session_id", "").strip()
    soid = oid(session_id)
    if not soid:
        return jsonify({"ok": False, "message": "session_id 格式錯誤"}), 400

    session = db.sessions.find_one({"_id": soid})
    if not session:
        return jsonify({"ok": False, "message": "找不到 session"}), 404

    # 已結束就不出題
    if session.get("end_time"):
        return jsonify({"ok": False, "code": "SESSION_ENDED", "message": "此測驗已結束"}), 400

    # 已作答題目
    answered = db.responses.find({"session_id": session_id}, {"question_id": 1, "_id": 0})
    answered_ids = {a["question_id"] for a in answered}

    # 取得可用題庫（前測先簡單：U1 mcq 全部可出，避免重複）
    # 你也可改成只出 active=True
    query = {"type": "mcq", "active": True}
    if answered_ids:
        query["_id"] = {"$nin": [ObjectId(qid) for qid in answered_ids if oid(qid)]}

    q = db.questions.find_one(query)
    if not q:
        # 沒題了 -> 結束 session
        db.sessions.update_one({"_id": soid}, {"$set": {"end_time": datetime.utcnow()}})
        return jsonify({"ok": False, "code": "NO_QUESTIONS", "message": "題庫不足或已完成"}), 200

    # 回傳題目（⚠️ 不要回傳 answer_key）
    return jsonify({
        "ok": True,
        "question": {
            "question_id": str(q["_id"]),
            "type": q.get("type", "mcq"),
            "unit": q.get("unit"),
            "difficulty": q.get("difficulty", 2),
            "stem": q.get("stem"),
            "options": q.get("options", [])
        }
    })


@quiz_bp.post("/submit")
def submit_answer():
    data = request.get_json() or {}
    session_id = (data.get("session_id") or "").strip()
    question_id = (data.get("question_id") or "").strip()
    answer = (data.get("answer") or "").strip()  # "A"/"B"/"C"/"D"
    time_spent = float(data.get("time_spent") or 0)
    hint_count = int(data.get("hint_count") or 0)

    soid = oid(session_id)
    qoid = oid(question_id)
    if not soid or not qoid:
        return jsonify({"ok": False, "message": "session_id 或 question_id 格式錯誤"}), 400

    session = db.sessions.find_one({"_id": soid})
    if not session:
        return jsonify({"ok": False, "message": "找不到 session"}), 404
    if session.get("end_time"):
        return jsonify({"ok": False, "message": "此測驗已結束"}), 400

    q = db.questions.find_one({"_id": qoid})
    if not q:
        return jsonify({"ok": False, "message": "找不到題目"}), 404

    correct = (answer == q.get("answer_key"))
    now = datetime.utcnow()

    # 防止同題重複送出（用 session_id + question_id 做唯一）
    existing = db.responses.find_one({"session_id": session_id, "question_id": question_id})
    if existing:
        return jsonify({"ok": False, "message": "此題已提交過"}), 400

    db.responses.insert_one({
        "session_id": session_id,
        "question_id": question_id,
        "answer": answer,
        "is_correct": bool(correct),
        "time_spent": time_spent,
        "hint_count": hint_count,
        "submit_time": now
    })

    # 更新 session 的統計（給適性化用）
    inc = {"correct_count": 1} if correct else {"wrong_count": 1}
    db.sessions.update_one({"_id": soid}, {"$inc": inc})

    # 若已達 10 題 -> 結束
    total = db.responses.count_documents({"session_id": session_id})
    ended = False
    if total >= PRE_TOTAL:
        db.sessions.update_one({"_id": soid}, {"$set": {"end_time": now}})
        ended = True

    return jsonify({
        "ok": True,
        "is_correct": bool(correct),
        "correct_answer": q.get("answer_key"),
        "total_answered": total,
        "ended": ended
    })

@quiz_bp.get("/status")
def quiz_status():
    """
    前端用：查詢目前 session 的進度與狀態
    回傳：
      - answered: 已作答題數
      - remaining: 剩餘題數
      - correct_count / wrong_count
      - ended: 是否已結束
      - start_time / end_time
    """
    session_id = request.args.get("session_id", "").strip()
    soid = oid(session_id)
    if not soid:
        return jsonify({"ok": False, "message": "session_id 格式錯誤"}), 400

    session = db.sessions.find_one({"_id": soid})
    if not session:
        return jsonify({"ok": False, "message": "找不到 session"}), 404

    answered = db.responses.count_documents({"session_id": session_id})
    ended = bool(session.get("end_time"))

    return jsonify({
        "ok": True,
        "session_id": session_id,
        "type": session.get("type"),
        "unit": session.get("unit"),
        "answered": answered,
        "remaining": max(PRE_TOTAL - answered, 0),
        "correct_count": int(session.get("correct_count") or 0),
        "wrong_count": int(session.get("wrong_count") or 0),
        "start_time": session.get("start_time"),
        "end_time": session.get("end_time"),
        "ended": ended
    })


@quiz_bp.post("/finish")
def finish_quiz():
    """
    保險用：強制結束某次測驗（例如管理者要提前結束）
    前端也可用於「作答完畢跳轉前」再保險結束一次（可選）
    """
    data = request.get_json() or {}
    session_id = (data.get("session_id") or "").strip()
    soid = oid(session_id)
    if not soid:
        return jsonify({"ok": False, "message": "session_id 格式錯誤"}), 400

    session = db.sessions.find_one({"_id": soid})
    if not session:
        return jsonify({"ok": False, "message": "找不到 session"}), 404

    now = datetime.utcnow()
    db.sessions.update_one({"_id": soid}, {"$set": {"end_time": now}})

    # 回傳最新狀態（方便前端直接導頁）
    answered = db.responses.count_documents({"session_id": session_id})

    return jsonify({
        "ok": True,
        "ended": True,
        "total_answered": answered,
        "end_time": now
    })