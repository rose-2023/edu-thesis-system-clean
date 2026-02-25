from flask import Blueprint, request, jsonify
from datetime import datetime, timezone
from ..db import db
import os
import re
from openai import OpenAI
from bson import ObjectId

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
student_bp = Blueprint("student", __name__)
PRE_TOTAL = 10


@student_bp.get("/entry")
def entry():
    participant_id = request.args.get("participant_id", "").strip()
    if not participant_id:
        return jsonify({"ok": False, "message": "缺少 participant_id"}), 400

    # 已完成前測
    done = db.sessions.find_one({
        "participant_id": participant_id,
        "type": "pre",
        "end_time": {"$ne": None}
    })
    if done:
        return jsonify({"ok": True, "next": "home"})

    # 找未結束的 pre session，沒有就建一筆
    s = db.sessions.find_one({
        "participant_id": participant_id,
        "type": "pre",
        "end_time": None
    })

    if not s:
        r = db.sessions.insert_one({
            "participant_id": participant_id,
            "type": "pre",
            "start_time": datetime.now(timezone.utc),
            "end_time": None,
            "current_index": 0,
            "total": PRE_TOTAL
        })
        sid = str(r.inserted_id)
    else:
        sid = str(s["_id"])

    return jsonify({"ok": True, "next": "pre", "session_id": sid})


# 紀錄學習開始/結束時間
def now_utc():
    return datetime.now(timezone.utc)


# =========================
# 1) 進入學習頁：建立 log
# POST /api/student/learning/start
# body: { participant_id, unit }
# =========================
@student_bp.post("/learning/start")
def learning_start():
    data = request.get_json() or {}
    participant_id = (data.get("participant_id") or "").strip()
    unit = (data.get("unit") or "").strip()

    if not participant_id or not unit:
        return jsonify({"ok": False, "message": "缺少 participant_id 或 unit"}), 400

    r = db.learning_logs.insert_one({
        "participant_id": participant_id,
        "unit": unit,
        "start_at": now_utc(),
        "end_at": None,
        "duration_sec": None,
        "regen_clicks": 0,
        "understood": None
    })

    return jsonify({"ok": True, "log_id": str(r.inserted_id)})


# =========================
# 2) 離開學習頁：結束 log
# POST /api/student/learning/end
# body: { log_id, duration_sec, regen_clicks, understood }
# =========================
@student_bp.post("/learning/end")
def learning_end():
    data = request.get_json() or {}
    log_id = (data.get("log_id") or "").strip()
    duration_sec = data.get("duration_sec", None)
    regen_clicks = data.get("regen_clicks", None)
    understood = data.get("understood", None)

    if not log_id:
        return jsonify({"ok": False, "message": "缺少 log_id"}), 400

    try:
        oid = ObjectId(log_id)
    except Exception:
        return jsonify({"ok": False, "message": "log_id 格式錯誤"}), 400

    update = {"end_at": now_utc()}
    if duration_sec is not None:
        update["duration_sec"] = duration_sec
    if regen_clicks is not None:
        update["regen_clicks"] = regen_clicks
    if understood is not None:
        update["understood"] = understood

    db.learning_logs.update_one({"_id": oid}, {"$set": update})

    return jsonify({"ok": True})


# =========================
# 3) 取得單元學習頁資料（影片 + bullets）
# GET /api/student/unit/<unit>/learning
# =========================
@student_bp.get("/unit/<unit>/learning")
def unit_learning(unit):
    unit = (unit or "").strip()
    if not unit:
        return jsonify({"ok": False, "message": "unit 不可為空"}), 400

    # 影片：從老師端 videos 找該 unit 的「啟用中」最新一筆
    v = db.videos.find_one({"unit": unit, "deleted": False, "active": True}, sort=[("created_at", -1)])
    if not v:
        return jsonify({"ok": True, "video": None, "bullets": []})

    video = {
        "unit": v.get("unit"),
        "title": v.get("title"),
        "video_url": "/" + (v.get("path") or "").lstrip("/"),
        "subtitle_path": "/" + (v.get("subtitle_path") or "").lstrip("/"),
    }

    # bullets：從 unit_bullets 讀快取
    bdoc = db.unit_bullets.find_one({"unit": unit})
    bullets = (bdoc or {}).get("bullets") or []

    return jsonify({"ok": True, "video": video, "bullets": bullets})


# =========================
# 4) 我不懂：重新生成 bullets 並存回 DB（安全版：先用字幕規則摘要/必要時可換 AI）
# POST /api/student/unit/<unit>/bullets/regenerate
# =========================
@student_bp.post("/unit/<unit>/bullets/regenerate")
def bullets_regenerate(unit):
    unit = (unit or "").strip()
    if not unit:
        return jsonify({"ok": False, "message": "unit 不可為空"}), 400

    v = db.videos.find_one({"unit": unit, "deleted": False, "active": True}, sort=[("created_at", -1)])
    if not v:
        return jsonify({"ok": False, "message": f"{unit} 尚未有啟用影片"}), 404

    subtitle_path = (v.get("subtitle_path") or "").strip()
    if not subtitle_path:
        return jsonify({"ok": False, "message": "尚未上傳字幕，無法生成 bullets"}), 400

    # 讀字幕檔（srt）→ 抓文字行 → 做簡單去重/摘要
    full = os.path.join(os.getcwd(), subtitle_path.replace("/", os.sep))
    try:
        with open(full, "r", encoding="utf-8-sig", errors="ignore") as f:
            raw = f.read()
    except Exception as e:
        return jsonify({"ok": False, "message": f"讀字幕失敗: {str(e)}"}), 500

    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]

    # 過濾掉序號與時間軸行
    text_lines = []
    for ln in lines:
        if re.fullmatch(r"\d+", ln):
            continue
        if "-->" in ln:
            continue
        text_lines.append(ln)

    # 簡單去重：保留前 N 句不重複
    bullets = []
    seen = set()
    for ln in text_lines:
        key = re.sub(r"\s+", " ", ln)
        if key in seen:
            continue
        seen.add(key)
        bullets.append(key)
        if len(bullets) >= 8:
            break

    # 存回 unit_bullets
    db.unit_bullets.update_one(
        {"unit": unit},
        {"$set": {"unit": unit, "bullets": bullets, "updated_at": now_utc()}},
        upsert=True
    )

    return jsonify({"ok": True, "bullets": bullets})
