from .parsons import create_task_for_video
from flask import Blueprint, request, jsonify
from datetime import datetime, timezone
from bson import ObjectId
from ..db import db

teacher_t5_bp = Blueprint("teacher_t5", __name__)


# =========================
# helpers
# =========================
def _utc_now():
    return datetime.now(timezone.utc)


def _oid(x: str):
    try:
        return ObjectId(x)
    except Exception:
        return None


def _status_zh(s: str):
    m = {
        "pending": "待審核",
        "published": "已發布",
        "rejected": "不發布",
        "draft": "待審核",
        "done": "待審核",
    }
    return m.get((s or "").strip().lower(), "待審核")


def _safe_iso(dt):
    try:
        return dt.isoformat() if dt else ""
    except Exception:
        return ""


# =========================
# A. units / videos / video_info
# =========================
@teacher_t5_bp.get("/units")
def units():
    """取得所有單元"""
    units_list = db.videos.distinct("unit", {"deleted": {"$ne": True}})
    units_list = [u for u in units_list if u]
    units_list.sort()

    return jsonify({
        "ok": True,
        "items": [{"id": u, "name": u} for u in units_list]
    })


@teacher_t5_bp.get("/videos")
def videos():
    """取得指定單元的影片"""
    unit_id = request.args.get("unit_id", "").strip()

    query = {"deleted": {"$ne": True}}
    if unit_id:
        query["unit"] = unit_id

    videos_list = list(db.videos.find(query).sort("created_at", -1))
    result = []
    for v in videos_list:
        result.append({
            "id": str(v["_id"]),
            "title": v.get("title", "未命名"),
            "unit": v.get("unit", ""),
            "enabled": v.get("enabled", True)
        })

    return jsonify({"ok": True, "items": result})


@teacher_t5_bp.get("/video_info")
def video_info():
    """取得影片詳細資訊"""
    video_id = (request.args.get("video_id", "") or "").strip()
    vid = _oid(video_id)
    if not vid:
        return jsonify({"ok": False, "error": "無效的影片ID"}), 404

    video = db.videos.find_one({"_id": vid})
    if not video:
        return jsonify({"ok": False, "error": "影片不存在"}), 404

    return jsonify({
        "ok": True,
        "enabled": video.get("enabled", True),
        "subtitle_uploaded": video.get("subtitle_uploaded", False),
        "subtitle_verified": video.get("subtitle_verified", False),
        "subtitle_versions": video.get("subtitle_versions", []),
        "source": video.get("source", "未知"),
        "duration": video.get("duration", "00:00:00")
    })


# =========================
# D. questions list (TeacherT5AgentLog.vue 需要)
# GET /questions?video_id=...&status=all&sort=newest
# =========================
@teacher_t5_bp.get("/questions")
def questions():
    video_id = (request.args.get("video_id", "") or "").strip()
    status = (request.args.get("status", "all") or "all").strip().lower()
    sort = (request.args.get("sort", "newest") or "newest").strip().lower()

    vid = _oid(video_id)
    if not vid:
        return jsonify({"ok": False, "error": "invalid video_id"}), 400

    q = {"video_id": vid}

    # 狀態過濾
    if status and status != "all":
        # 前端可能傳 pending / published / rejected
        q["status"] = status

    # 排序
    sort_dir = -1 if sort == "newest" else 1

    items = []
    for t in db.parsons_tasks.find(q).sort("created_at", sort_dir):
        enabled = bool(t.get("enabled", False))
        st = (t.get("status") or ("published" if enabled else "pending")).strip().lower()

        items.append({
            "task_id": str(t["_id"]),
            "version": t.get("version", "v1"),
            "status": st,
            "status_zh": _status_zh(st),
            "enabled": enabled,                  # 列表顯示用
            "student_visible": enabled,          # 預覽用（跟前端一致）
            "created_at": _safe_iso(t.get("created_at")),
            "segment_label": t.get("segment_label", "—"),
            "has_note": bool((t.get("review_note") or "").strip()),
        })

    return jsonify({"ok": True, "items": items})


# =========================
# Preview a single question (TeacherT5AgentLog.vue openPreview 需要)
# GET /question?task_id=...
# =========================
@teacher_t5_bp.get("/question")
def get_question():
    task_id = (request.args.get("task_id", "") or "").strip()
    tid = _oid(task_id)
    if not tid:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    t = db.parsons_tasks.find_one({"_id": tid})
    if not t:
        return jsonify({"ok": False, "error": "task not found"}), 404

    enabled = bool(t.get("enabled", False))
    st = (t.get("status") or ("published" if enabled else "pending")).strip().lower()

    # 兼容多種欄位命名（你之前有 mock / 也可能是 AI 代理寫入）
    question = t.get("question", {}) or {}
    prompt = question.get("prompt") or t.get("prompt") or ""

    # solution_blocks / distractor_blocks / solution_order
    solution_blocks = t.get("solution_blocks", []) or t.get("blocks", []) or []
    distractor_blocks = t.get("distractor_blocks", []) or t.get("distractors", []) or []
    solution_order = t.get("solution_order", []) or t.get("solution_ids", []) or []

    return jsonify({
        "ok": True,
        "task_id": str(t["_id"]),
        "version": t.get("version", "v1"),
        "status": st,
        "status_zh": _status_zh(st),
        "student_visible": enabled,
        "enabled": enabled,
        "created_at": _safe_iso(t.get("created_at")),
        "segment_label": t.get("segment_label", "—"),
        "subtitle_version": t.get("subtitle_version", None),

        # 前端要用的內容
        "question": {
            "prompt": prompt or "（未提供題目敘述）",
        },
        "solution_blocks": solution_blocks,
        "distractor_blocks": distractor_blocks,
        "solution_order": solution_order,

        # 老師審核
        "review_tags": t.get("review_tags", []) or [],
        "review_note": t.get("review_note", "") or "",
    })


# =========================
# Save review (tags/note + distractor keep)
# POST /question/review_save
# body: {task_id, review_tags, review_note, distractor_keep: {blockId: true/false}}
# =========================
@teacher_t5_bp.post("/question/review_save")
def review_save():
    body = request.get_json(silent=True) or {}
    tid = _oid((body.get("task_id") or "").strip())
    if not tid:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    t = db.parsons_tasks.find_one({"_id": tid})
    if not t:
        return jsonify({"ok": False, "error": "task not found"}), 404

    review_tags = body.get("review_tags") or []
    review_note = body.get("review_note") or ""
    distractor_keep = body.get("distractor_keep") or {}

    # 1) 存老師審核欄位（不改 schema：只是加/更新欄位）
    update_doc = {
        "review_tags": review_tags,
        "review_note": review_note,
        "updated_at": _utc_now(),
    }

    # 2) 套用干擾保留/移除（不改 schema：在 distractor_blocks 內加 enabled）
    #    - 你前端用 ✅/❌ 決定學生端要不要看到
    dblocks = t.get("distractor_blocks", []) or []
    if isinstance(dblocks, list) and dblocks:
        for b in dblocks:
            bid = str(b.get("id") or b.get("_id") or "")
            if not bid:
                continue
            if bid in distractor_keep:
                b["enabled"] = bool(distractor_keep.get(bid))
        update_doc["distractor_blocks"] = dblocks

    db.parsons_tasks.update_one({"_id": tid}, {"$set": update_doc})
    return jsonify({"ok": True})


# =========================
# Publish / Unpublish / Reject
# =========================
@teacher_t5_bp.post("/question/publish")
def publish():
    body = request.get_json(silent=True) or {}
    tid = _oid((body.get("task_id") or "").strip())
    if not tid:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    r = db.parsons_tasks.update_one({"_id": tid}, {"$set": {
        "enabled": True,
        "status": "published",
        "updated_at": _utc_now(),
    }})
    if r.matched_count == 0:
        return jsonify({"ok": False, "error": "task not found"}), 404
    return jsonify({"ok": True})


@teacher_t5_bp.post("/question/unpublish")
def unpublish():
    body = request.get_json(silent=True) or {}
    tid = _oid((body.get("task_id") or "").strip())
    if not tid:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    r = db.parsons_tasks.update_one({"_id": tid}, {"$set": {
        "enabled": False,
        "status": "pending",
        "updated_at": _utc_now(),
    }})
    if r.matched_count == 0:
        return jsonify({"ok": False, "error": "task not found"}), 404
    return jsonify({"ok": True})


@teacher_t5_bp.post("/question/reject")
def reject():
    body = request.get_json(silent=True) or {}
    tid = _oid((body.get("task_id") or "").strip())
    if not tid:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    review_tags = body.get("review_tags") or []
    review_note = body.get("review_note") or ""

    r = db.parsons_tasks.update_one({"_id": tid}, {"$set": {
        "enabled": False,
        "status": "rejected",
        "review_tags": review_tags,
        "review_note": review_note,
        "updated_at": _utc_now(),
    }})
    if r.matched_count == 0:
        return jsonify({"ok": False, "error": "task not found"}), 404
    return jsonify({"ok": True})


# =========================
# Regenerate (trigger new task)
# POST /regenerate
# =========================
@teacher_t5_bp.post("/regenerate")
def regenerate():
    """觸發重新生成題目：真正串接 AI 代理邏輯"""
    body = request.get_json(force=True) or {}
    video_id = body.get("video_id")
    level = body.get("level") or "L1"
    subtitle_version = body.get("subtitle_version")

    video_oid = _oid(str(video_id or ""))
    if not video_oid:
        return jsonify({"ok": False, "error": "無效的影片ID"}), 400

    video_doc = db.videos.find_one({"_id": video_oid})
    if not video_doc:
        return jsonify({"ok": False, "error": "影片不存在"}), 404

    try:
        # [修改] 呼叫 parsons.py 的核心邏輯，這會讀取字幕並送往 OpenAI
        doc, gen_source, gen_error, env = create_task_for_video(
            video_doc=video_doc, 
            video_id_str=str(video_id), 
            level=level
        )

        # [新增] 為了配合老師端的預覽介面，統一狀態欄位
        db.parsons_tasks.update_one({"_id": doc["_id"]}, {"$set": {
            "status": "pending",
            "enabled": False, # 預設不發布，待老師審核
            "version": "v1.AI",
            "updated_at": _utc_now()
        }})

        return jsonify({
            "ok": True,
            "task_id": str(doc["_id"]),
            "message": "AI 題目已生成為待審核版本"
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"AI 生成失敗: {str(e)}"}), 500


# =========================
# (Optional) generation_status / gen_logs / feedback_logs
# 保留你原本功能（若前端有用到）
# =========================
@teacher_t5_bp.get("/generation_status")
def generation_status():
    video_id = request.args.get("video_id", "")
    level = request.args.get("level", "L1")

    try:
        task = db.parsons_tasks.find_one(
            {"video_id": ObjectId(video_id), "level": level},
            sort=[("created_at", -1)]
        )
    except Exception:
        task = None

    if not task:
        return jsonify({
            "ok": True,
            "generated": False,
            "applied": False,
            "message": "還未生成"
        })

    return jsonify({
        "ok": True,
        "generated": True,
        "applied": bool(task.get("enabled", False)),
        "generated_at": task.get("created_at", "").isoformat() if task.get("created_at") else "",
        "segment_label": task.get("segment_label", "全片"),
        "version": task.get("version", "v1")
    })


@teacher_t5_bp.get("/gen_logs")
def gen_logs():
    video_id = request.args.get("video_id", "")
    level = request.args.get("level", "L1")

    try:
        tasks = list(db.parsons_tasks.find(
            {"video_id": ObjectId(video_id), "level": level}
        ).sort("created_at", -1))
    except Exception:
        tasks = []

    items = []
    for t in tasks:
        items.append({
            "version": t.get("version", "v1"),
            "time": t.get("created_at", "").isoformat() if t.get("created_at") else "",
            "by": "AI Agent",
            "segment": t.get("segment_label", "全片")
        })

    return jsonify({"ok": True, "items": items})


@teacher_t5_bp.get("/feedback_logs")
def feedback_logs():
    video_id = request.args.get("video_id", "")
    level = request.args.get("level", "L1")

    try:
        events = list(db.events.find({
            "video_id": ObjectId(video_id),
            "level": level,
            "type": {"$in": ["hint", "feedback"]}
        }).sort("created_at", -1))
    except Exception:
        events = []

    items = []
    for e in events:
        items.append({
            "participant_id": e.get("participant_id", "未知"),
            "time": e.get("created_at", "").isoformat() if e.get("created_at") else "",
            "event": e.get("type", ""),
            "detail": e.get("detail", "")
        })

    return jsonify({"ok": True, "items": items})
