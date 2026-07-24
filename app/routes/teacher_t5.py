from .parsons_service import create_task_for_video
from flask import Blueprint, request, jsonify
from datetime import datetime, timezone
import re  # [新增] 用於 version 遞增解析
from bson import ObjectId
from ..db import db
from ..unit_labels import sort_units, unit_label_map, save_unit_label

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
    if isinstance(dt, str):
        return dt.strip()
    if isinstance(dt, ObjectId):
        try:
            return dt.generation_time.isoformat()
        except Exception:
            return ""
    try:
        return dt.isoformat() if dt else ""
    except Exception:
        return ""


def _doc_created_iso(doc):
    if not isinstance(doc, dict):
        return ""
    return _safe_iso(doc.get("created_at") or doc.get("created_at_utc") or doc.get("_id"))


def _doc_created_sort_value(doc):
    if not isinstance(doc, dict):
        return datetime.min.replace(tzinfo=timezone.utc)
    raw = doc.get("created_at") or doc.get("created_at_utc")
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if text:
            try:
                if text.endswith(("Z", "z")):
                    text = text[:-1] + "+00:00"
                return datetime.fromisoformat(text)
            except Exception:
                pass
    oid = doc.get("_id")
    if isinstance(oid, ObjectId):
        return oid.generation_time
    return datetime.min.replace(tzinfo=timezone.utc)


def _task_source_type(doc):
    if not isinstance(doc, dict):
        return "ai"
    return (
        (doc.get("source_type") or "").strip().lower()
        or (doc.get("gen_source") or "").strip().lower()
        or ("ai" if bool(doc.get("ai_generated")) else "fixed")
    )


def _backfill_created_at_if_missing(doc):
    if not isinstance(doc, dict) or doc.get("created_at"):
        return
    oid = doc.get("_id")
    if not isinstance(oid, ObjectId):
        return
    created_at = doc.get("created_at_utc") or oid.generation_time
    doc["created_at"] = created_at
    try:
        db.parsons_tasks.update_one(
            {
                "_id": oid,
                "$or": [
                    {"created_at": {"$exists": False}},
                    {"created_at": None},
                    {"created_at": ""},
                ],
            },
            {"$set": {"created_at": created_at}},
        )
    except Exception:
        pass


def _fixed_task_code(index):
    return f"FIXED-{max(1, int(index or 1)):02d}"


def _backfill_task_code_if_missing(doc, task_code):
    if not isinstance(doc, dict):
        return ""
    existing = str(doc.get("task_code") or "").strip()
    if existing:
        return existing
    clean_code = str(task_code or "").strip() or "FIXED-01"
    doc["task_code"] = clean_code
    oid = doc.get("_id")
    if isinstance(oid, ObjectId):
        try:
            db.parsons_tasks.update_one(
                {
                    "_id": oid,
                    "$or": [
                        {"task_code": {"$exists": False}},
                        {"task_code": None},
                        {"task_code": ""},
                    ],
                },
                {"$set": {"task_code": clean_code}},
            )
        except Exception:
            pass
    return clean_code


def _is_soft_deleted(video_doc: dict) -> bool:
    if not isinstance(video_doc, dict):
        return False
    v1 = video_doc.get("deleted")
    v2 = video_doc.get("is_deleted")
    true_like = {True, 1, "1", "true", "True", "TRUE"}
    return (v1 in true_like) or (v2 in true_like)


def _clean_text(value, max_len=8000, strip=False):
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if strip:
        text = text.strip()
    if len(text) > max_len:
        text = text[:max_len]
    return text


def _block_id(block, fallback):
    if isinstance(block, dict):
        raw = block.get("id", block.get("_id", fallback))
    else:
        raw = fallback
    raw = str(raw or fallback).strip()
    return raw or str(fallback)


_SEMANTIC_ZH_KEYS = ("meaning_zh", "semantic_zh", "semantic", "zh")


def _canonical_meaning_zh(block) -> str:
    """Return the canonical Chinese meaning with legacy-field fallback."""
    if not isinstance(block, dict):
        return ""
    for key in _SEMANTIC_ZH_KEYS:
        if key in block and block.get(key) is not None:
            return _clean_text(block.get(key), max_len=2000, strip=True)
    return ""


def _normalize_block_semantics(blocks):
    """Synchronize legacy aliases while treating meaning_zh as canonical."""
    if not isinstance(blocks, list):
        return blocks

    normalized = []
    for block in blocks:
        if not isinstance(block, dict):
            normalized.append(block)
            continue
        item = dict(block)
        meaning = _canonical_meaning_zh(item)
        item["meaning_zh"] = meaning
        item["semantic_zh"] = meaning
        item["semantic"] = meaning
        item["zh"] = meaning
        normalized.append(item)
    return normalized


def _merge_block_edits(existing_blocks, incoming_blocks):
    if not isinstance(incoming_blocks, list):
        return None

    existing_blocks = existing_blocks if isinstance(existing_blocks, list) else []
    existing_by_id = {}
    for idx, block in enumerate(existing_blocks):
        if not isinstance(block, dict):
            continue
        bid = _block_id(block, f"b{idx}")
        existing_by_id[bid] = dict(block)

    merged = []
    for idx, raw in enumerate(incoming_blocks):
        if not isinstance(raw, dict):
            continue
        bid = _block_id(raw, f"b{idx}")
        block = dict(existing_by_id.get(bid, {}))
        block["id"] = bid

        if any(k in raw for k in ("text", "code", "line")):
            block["text"] = _clean_text(
                raw.get("text", raw.get("code", raw.get("line", ""))),
                max_len=2000,
                strip=False,
            )

        if any(k in raw for k in ("meaning_zh", "semantic_zh", "semantic", "zh")):
            meaning = _clean_text(
                raw.get("meaning_zh", raw.get("semantic_zh", raw.get("semantic", raw.get("zh", "")))),
                max_len=2000,
                strip=True,
            )
            block["meaning_zh"] = meaning
            block["semantic_zh"] = meaning
            block["semantic"] = meaning
            block["zh"] = meaning

        if "indent" in raw:
            try:
                block["indent"] = max(0, int(raw.get("indent") or 0))
            except Exception:
                block["indent"] = 0

        if "enabled" in raw:
            block["enabled"] = bool(raw.get("enabled"))

        merged.append(block)

    return merged


def _normalize_solution_order(value):
    if isinstance(value, list):
        raw_items = value
    else:
        text = _clean_text(value, max_len=2000, strip=True)
        raw_items = re.split(r"\s*(?:->|=>|,|;|\||/|\n|\t|\s+)\s*", text)
    out = []
    seen = set()
    for item in raw_items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


# =========================
# A. units / videos / video_info
# =========================
@teacher_t5_bp.get("/units")
def units():
    """取得所有單元"""
    units_list = db.videos.distinct(
        "unit",
        {"deleted": {"$nin": [True, 1, "true", "True"]}, "is_deleted": {"$nin": [True, 1, "true", "True"]}},
    )
    labels = unit_label_map(units_list)
    units_list = sort_units([u for u in units_list if u], labels)

    return jsonify({
        "ok": True,
        "items": [
            {
                "id": u,
                "name": labels.get(u) or u,
                "raw_name": u,
                "unit_label": labels.get(u) or u,
            }
            for u in units_list
        ]
    })


@teacher_t5_bp.patch("/unit_label")
def update_unit_label():
    body = request.get_json(silent=True) or {}
    unit_id = (body.get("unit_id") or body.get("unit") or "").strip()
    label = (body.get("label") or body.get("name") or "").strip()
    if not unit_id:
        return jsonify({"ok": False, "error": "missing unit_id"}), 400
    if not label:
        return jsonify({"ok": False, "error": "missing label"}), 400
    try:
        saved = save_unit_label(unit_id, label, updated_by="teacher_t5")
        return jsonify({"ok": True, **saved})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _test_control_doc_id(test_role, test_cycle_id):
    role = str(test_role or "").strip().lower()
    if role in ("pre", "pretest"):
        return "pre", "pre_open", f"pre_open:{test_cycle_id}"
    return "post", "post_open", f"post_open:{test_cycle_id}"


_DEFAULT_TEST_CYCLE_ID = "2026_07_batch_01"


def _normalize_test_cycle_id(value):
    test_cycle_id = str(value or "").strip()
    return _DEFAULT_TEST_CYCLE_ID if not test_cycle_id or test_cycle_id == "default" else test_cycle_id


def _read_test_control(test_role, test_cycle_id):
    role, field, doc_id = _test_control_doc_id(test_role, test_cycle_id)
    doc = db.test_control.find_one({"_id": doc_id}) or {}
    if doc or test_cycle_id != _DEFAULT_TEST_CYCLE_ID:
        return doc
    return db.test_control.find_one({"_id": f"{field}:default"}) or {}


def _set_test_control(test_cycle_id, test_role, is_open):
    test_cycle_id = _normalize_test_cycle_id(test_cycle_id)
    role, field, doc_id = _test_control_doc_id(test_role, test_cycle_id)
    doc = db.test_control.find_one({"_id": doc_id}) or {}
    now = _utc_now()
    update = {
        "test_cycle_id": test_cycle_id,
        "test_role": role,
        field: bool(is_open),
        "updated_at": now,
    }
    if is_open:
        if not doc.get("open_at"):
            update["open_at"] = now
        update["close_at"] = None
    else:
        update["close_at"] = now
    db.test_control.update_one({"_id": doc_id}, {"$set": update}, upsert=True)
    return db.test_control.find_one({"_id": doc_id}) or {}


@teacher_t5_bp.get("/test_control")
def get_test_control():
    test_cycle_id = _normalize_test_cycle_id(request.args.get("test_cycle_id"))
    pre_doc = _read_test_control("pre", test_cycle_id)
    post_doc = _read_test_control("post", test_cycle_id)
    return jsonify({
        "ok": True,
        "test_cycle_id": test_cycle_id,
        "pre_open": bool(pre_doc.get("pre_open", False)),
        "post_open": bool(post_doc.get("post_open", False)),
        "pre_updated_at": _safe_iso(pre_doc.get("updated_at")),
        "post_updated_at": _safe_iso(post_doc.get("updated_at")),
    })


@teacher_t5_bp.post("/test_control")
def update_test_control():
    body = request.get_json(silent=True) or {}
    test_cycle_id = _normalize_test_cycle_id(body.get("test_cycle_id"))
    role = (body.get("test_role") or body.get("role") or "").strip().lower()

    if "pre_open" in body:
        role = "pre"
        is_open = bool(body.get("pre_open"))
    elif "post_open" in body:
        role = "post"
        is_open = bool(body.get("post_open"))
    else:
        is_open = bool(body.get("open"))

    if role not in ("pre", "pretest", "post", "posttest"):
        return jsonify({"ok": False, "error": "invalid test_role"}), 400

    _set_test_control(test_cycle_id, role, is_open)
    pre_doc = db.test_control.find_one({"_id": f"pre_open:{test_cycle_id}"}) or {}
    post_doc = db.test_control.find_one({"_id": f"post_open:{test_cycle_id}"}) or {}
    return jsonify({
        "ok": True,
        "test_cycle_id": test_cycle_id,
        "pre_open": bool(pre_doc.get("pre_open", False)),
        "post_open": bool(post_doc.get("post_open", False)),
    })


@teacher_t5_bp.get("/videos")
def videos():
    """取得指定單元的影片"""
    unit_id = request.args.get("unit_id", "").strip()

    query = {
        "deleted": {"$nin": [True, 1, "true", "True"]},
        "is_deleted": {"$nin": [True, 1, "true", "True"]},
    }
    if unit_id:
        query["unit"] = unit_id

    videos_list = list(db.videos.find(query).sort("created_at", -1))
    result = []
    for v in videos_list:
        if _is_soft_deleted(v):
            continue
        result.append({
            "id": str(v["_id"]),
            "title": v.get("title", "未命名"),
            "unit": v.get("unit", ""),
            "enabled": v.get("enabled", True),
            "deleted": bool(v.get("deleted", False)),
            "is_deleted": bool(v.get("is_deleted", False)),
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
    if _is_soft_deleted(video):
        return jsonify({"ok": False, "error": "影片已刪除"}), 404

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
    if not video_id:
        return jsonify({"ok": False, "error": "invalid video_id"}), 400

    # 相容手動匯入資料：video_id 可能是 ObjectId、字串，或寫在 video_id_str。
    if vid:
        q = {"$or": [{"video_id": vid}, {"video_id": video_id}, {"video_id_str": video_id}]}
    else:
        q = {"$or": [{"video_id": video_id}, {"video_id_str": video_id}]}

    # 軟刪除影片不應再出現在老師端題目列表（相容 deleted / is_deleted）。
    if vid:
        vdoc = db.videos.find_one({"_id": vid}, {"deleted": 1, "is_deleted": 1}) or {}
        if vdoc and (vdoc.get("deleted") is True or vdoc.get("is_deleted") is True):
            return jsonify({"ok": True, "items": []})

    # 排序
    sort_dir = -1 if sort == "newest" else 1

    items = []
    _video_deleted_cache = {}

    def _task_video_is_deleted(task_doc: dict) -> bool:
        raw_vid = task_doc.get("video_id")
        vid_oid = raw_vid if isinstance(raw_vid, ObjectId) else _oid(str(raw_vid or ""))
        if not vid_oid:
            vid_oid = _oid(str(task_doc.get("video_id_str") or ""))
        if not vid_oid:
            return False

        key = str(vid_oid)
        if key in _video_deleted_cache:
            return _video_deleted_cache[key]

        vdoc = db.videos.find_one({"_id": vid_oid}, {"deleted": 1, "is_deleted": 1}) or {}
        is_del = bool(vdoc and (vdoc.get("deleted") is True or vdoc.get("is_deleted") is True))
        _video_deleted_cache[key] = is_del
        return is_del

    task_docs = list(db.parsons_tasks.find(q))
    for t in task_docs:
        _backfill_created_at_if_missing(t)

    fixed_docs = [
        t for t in task_docs
        if _task_source_type(t) == "fixed" and not _task_video_is_deleted(t)
    ]
    fixed_docs.sort(key=lambda doc: (_doc_created_sort_value(doc), str(doc.get("_id") or "")))
    for idx, fixed_doc in enumerate(fixed_docs, start=1):
        _backfill_task_code_if_missing(fixed_doc, _fixed_task_code(idx))

    task_docs.sort(
        key=lambda doc: (_doc_created_sort_value(doc), str(doc.get("_id") or "")),
        reverse=(sort_dir == -1),
    )

    for t in task_docs:
        if _task_video_is_deleted(t):
            continue

        enabled = bool(t.get("enabled", False))

        # ===== 統一來源 =====
        source_type = _task_source_type(t)

        # ===== 統一狀態 =====
        raw_status = (
            (t.get("status") or "").strip().lower()
            or (t.get("review_status") or "").strip().lower()
        )

        # 固定題常見 draft / approved；AI 題常見 pending / published / rejected
        if not raw_status:
            raw_status = "published" if enabled else "pending"

        # 前端狀態過濾（統一用 status 比）
        # all / pending / published / rejected / approved / draft
        if status != "all" and raw_status != status:
            continue

        # ===== 題目代號 =====
        # 固定題優先 task_code，AI 題優先 version
        if source_type == "fixed":
            version = _backfill_task_code_if_missing(t, "FIXED-01")
        else:
            version = (t.get("version") or t.get("task_code") or "v1").strip()

        # ===== 顯示用中文狀態 =====
        status_zh_map = {
            "draft": "草稿",
            "pending": "待審核",
            "approved": "已審核",
            "published": "已發布",
            "rejected": "已退回",
        }
        status_zh = status_zh_map.get(raw_status, raw_status or "待審核")

        items.append({
            "task_id": str(t["_id"]),
            "version": version,                         # 前端目前 D 區用這個欄位顯示題目代號
            "task_code": version if source_type == "fixed" else t.get("task_code", ""),       # [新增] 給前端新表格用
            "title": t.get("title") or t.get("video_title") or "",
            "status": raw_status,
            "status_zh": status_zh,
            "enabled": enabled,
            "student_visible": enabled,
            "created_at": _doc_created_iso(t),
            "segment_label": t.get("segment_label", "—"),
            "has_note": bool((t.get("review_note") or "").strip()),
            "gen_source": source_type,                 # 前端目前用 q.gen_source 判斷 fixed / ai
            "source_type": source_type,                # [新增] 給新表格欄位直接用
            "review_status": (t.get("review_status") or "").strip().lower(),
            "parent_version": t.get("parent_version"),
            "parent_task_id": str(t.get("parent_task_id")) if t.get("parent_task_id") else "",
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
    _backfill_created_at_if_missing(t)
    if _task_source_type(t) == "fixed":
        _backfill_task_code_if_missing(t, "FIXED-01")

    enabled = bool(t.get("enabled", False))
    st = (t.get("status") or ("published" if enabled else "pending")).strip().lower()

    # 兼容多種欄位命名（你之前有 mock / 也可能是 AI 代理寫入）
    question = t.get("question", {}) or {}
    prompt = (question.get("prompt") or t.get("prompt") or t.get("question_text") or "")  # [新增] 支援 DB 的 question_text

    # solution_blocks / distractor_blocks / solution_order
    solution_blocks = _normalize_block_semantics(
        t.get("solution_blocks", []) or t.get("blocks", []) or []
    )
    distractor_blocks = _normalize_block_semantics(
        t.get("distractor_blocks", []) or t.get("distractors", []) or []
    )
    solution_order = t.get("solution_order", []) or t.get("solution_ids", []) or []

    solution_order = t.get("solution_order", []) or t.get("solution_ids", []) or []


    # =========================================================
    # [新增] 若 DB 沒有 solution_order，則用 solution_blocks 的順序自動補上（老師端顯示解答順序用）
    # =========================================================
    if (not solution_order) and isinstance(solution_blocks, list):
        _ids = []
        for _b in solution_blocks:
            if isinstance(_b, dict):
                _bid = _b.get("id") or _b.get("_id")
                if _bid is not None:
                    _ids.append(str(_bid))
        solution_order = _ids

    # =========================================================
    # [新增] 確保 template_slots 一定存在（老師端中文語意來源）
    # - 有些舊題/部分生成流程可能缺少 template_slots
    # - 這裡只做「回傳層」補齊，不寫回 DB，避免影響既有資料
    # =========================================================
    template_slots = t.get("template_slots", []) or []
    if (not isinstance(template_slots, list)) or len(template_slots) == 0:
        def _infer_label(code_line: str) -> str:
            s = (code_line or "").strip().lower()
            if "input(" in s:
                if "int(" in s:
                    return "讀入輸入並轉為整數"
                if "float(" in s:
                    return "讀入輸入並轉為浮點數"
                return "讀入使用者輸入"
            if s.startswith("for ") or " in range(" in s:
                return "迴圈處理/重複運算"
            if s.startswith("while ") or "while " in s:
                return "迴圈直到條件成立"
            if s.startswith("if ") or s.startswith("elif ") or s.startswith("else"):
                return "條件判斷分支"
            if "print(" in s:
                return "輸出結果"
            if any(op in s for op in ["+=", "-=", "*=", "/="]):
                return "更新累加/計算結果"
            if "=" in s:
                return "設定/更新變數"
            if "return " in s:
                return "回傳結果"
            return "完成此步驟"

        _ts = []
        if isinstance(solution_blocks, list) and len(solution_blocks) > 0:
            for idx, b in enumerate(solution_blocks):
                txt = b.get("text", "") if isinstance(b, dict) else ""
                _ts.append({"slot": str(idx), "label": _infer_label(txt)})
        template_slots = _ts

    # =========================================================
    # [新增] 把 template_slots 的 label 映射回 solution_blocks 的 semantic_zh（老師端顯示中文語意）
    # - 依照 solution_order 的順序：slot 0 -> 第 1 個 block ...
    # =========================================================
    try:
        slot_map = {}
        if isinstance(solution_order, list) and isinstance(template_slots, list):
            for idx, bid in enumerate(solution_order):
                if idx >= len(template_slots):
                    break
                s = template_slots[idx] if isinstance(template_slots[idx], dict) else {}
                label = (
                    (s.get("label") if isinstance(s, dict) else None)
                    or (s.get("meaning_zh") if isinstance(s, dict) else None)
                    or (s.get("semantic_zh") if isinstance(s, dict) else None)
                    or (s.get("zh") if isinstance(s, dict) else None)
                    or ""
                )
                if label:
                    slot_map[str(bid)] = label

        if slot_map and isinstance(solution_blocks, list):
            _new = []
            for b in solution_blocks:
                if not isinstance(b, dict):
                    _new.append(b)
                    continue
                bid = str(b.get("id") or b.get("_id") or "")
                if bid and bid in slot_map:
                    if not (b.get("semantic_zh") or b.get("semantic") or b.get("zh") or b.get("meaning_zh")):
                        meaning = _clean_text(slot_map[bid], max_len=2000, strip=True)
                        b["meaning_zh"] = meaning
                        b["semantic_zh"] = meaning
                        b["semantic"] = meaning
                        b["zh"] = meaning
                _new.append(b)
            solution_blocks = _new
    except Exception:
        pass
    return jsonify({
        "ok": True,
        "task_id": str(t["_id"]),
        "version": t.get("version", "v1"),
        "task_code": t.get("task_code", ""),
        "status": st,
        "status_zh": _status_zh(st),
        "student_visible": enabled,
        "enabled": enabled,
        "created_at": _doc_created_iso(t),
        "segment_label": t.get("segment_label", "—"),
        "subtitle_version": t.get("subtitle_version", None),

        # 前端要用的內容
        "question": {
            "prompt": prompt or "（未提供題目敘述）",
        },
        "solution_blocks": solution_blocks,
        "distractor_blocks": distractor_blocks,
        "solution_order": solution_order,

        # [新增] 可追溯/可控管
        "unit_type": t.get("unit_type", None),
        "constraints": t.get("constraints", None),
        "rule_check": t.get("rule_check", None),
        "source_subtitle": t.get("source_subtitle", None),
        "key_sentences": t.get("key_sentences", []) or [],
        "key_sentences_typed": t.get("key_sentences_typed", []) or [],
        "selector_meta": (
            t.get("selector_meta")
            or ((t.get("ai_debug") or {}).get("generation_debug") or {}).get("selector_quality_final")
            or {}
        ),
        "unified_policy_meta": (
            t.get("unified_policy_meta")
            or ((t.get("ai_debug") or {}).get("generation_debug") or {}).get("unified_policy_meta")
            or {}
        ),
        "function_profile": t.get("function_profile", {}) or {},
        "alignment_confidence": t.get("alignment_confidence", {}) or {},
        "subtitle_range": {
            "start_index": (t.get("source_subtitle") or {}).get("start_index"),
            "end_index": (t.get("source_subtitle") or {}).get("end_index"),
        },
        "subtitle_text_used": (t.get("source_subtitle") or {}).get("text_used", ""),
        "subtitle_time_range": {
            "start_ts": (t.get("source_subtitle") or {}).get("start_ts"),
            "end_ts": (t.get("source_subtitle") or {}).get("end_ts"),
        },

        "template_slots": template_slots,  # [新增] 方便老師端/除錯需要
        "hide_semantic_zh": bool(t.get("hide_semantic_zh", False)),
        # 老師審核
        "review_tags": t.get("review_tags", []) or [],
        "review_note": t.get("review_note", "") or "",
        # [新增] 概念章節相關欄位（讓前端重新開啟 modal 時能顯示已儲存內容），概念草稿與ai輔助區塊
        "concept_chapters_formal": t.get("concept_chapters_formal") or [],
        "concept_chapters_draft": t.get("concept_chapters_draft") or [],
        "teacher_concept_chapters": t.get("teacher_concept_chapters") or [],
        "concept_chapters_warnings": t.get("concept_chapters_warnings") or [],
        "concept_align_status": t.get("concept_align_status", "") or "draft",
        "teaching_range_start": t.get("teaching_range_start"),
        "teaching_range_end": t.get("teaching_range_end"),
        "code_start_ts": t.get("code_start_ts") or t.get("teacher_code_start_ts"),
        "block_chapter_map": t.get("block_chapter_map") or {},
        "teacher_segment_map": t.get("teacher_segment_map") or {},
        "ai_segment_map": t.get("ai_segment_map") or {},
        "slot_concept_map": t.get("slot_concept_map") or {},
        "chapter_recommendations": (
            (t.get("subtitle_health") or {}).get("chapter_recommendations") or []
        ),
    })


# =========================
# Save review (tags/note + distractor keep)
# POST /question/review_save
# body: {task_id, review_tags, review_note, distractor_keep, question_prompt, solution_blocks, distractor_blocks, solution_order}
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
    hide_semantic_zh = body.get("hide_semantic_zh", None)
    has_prompt_edit = "question_prompt" in body or "prompt" in body
    prompt_edit = body.get("question_prompt", body.get("prompt", None))
    solution_order_edit = body.get("solution_order", None)
    if solution_order_edit is None:
        solution_order_edit = body.get("solution_order_text", None)

    # 1) 存老師審核欄位（不改 schema：只是加/更新欄位）
    update_doc = {
        "review_tags": review_tags,
        "review_note": review_note,
        "updated_at": _utc_now(),
    }

    if has_prompt_edit:
        prompt = _clean_text(prompt_edit, max_len=4000, strip=True)
        question = dict(t.get("question") or {}) if isinstance(t.get("question"), dict) else {}
        question["prompt"] = prompt
        update_doc["question"] = question
        update_doc["prompt"] = prompt
        update_doc["question_text"] = prompt

    solution_blocks = _merge_block_edits(
        t.get("solution_blocks", []) or t.get("blocks", []) or [],
        body.get("solution_blocks"),
    )
    if solution_blocks is not None:
        solution_blocks = _normalize_block_semantics(solution_blocks)
        update_doc["solution_blocks"] = solution_blocks
        update_doc["blocks"] = solution_blocks

    if solution_order_edit is not None:
        solution_order = _normalize_solution_order(solution_order_edit)
        if solution_order:
            update_doc["solution_order"] = solution_order
            update_doc["solution_ids"] = solution_order

    # Apply teacher edits and visibility flags to distractor blocks.
    dblocks = _merge_block_edits(
        t.get("distractor_blocks", []) or t.get("distractors", []) or [],
        body.get("distractor_blocks"),
    )
    if dblocks is None:
        dblocks = t.get("distractor_blocks", []) or []
    dblocks = _normalize_block_semantics(dblocks)
    if isinstance(dblocks, list) and dblocks:
        for b in dblocks:
            bid = str(b.get("id") or b.get("_id") or "")
            if not bid:
                continue
            if bid in distractor_keep:
                b["enabled"] = bool(distractor_keep.get(bid))
        update_doc["distractor_blocks"] = dblocks
        update_doc["distractors"] = dblocks

    if solution_blocks is not None or isinstance(body.get("distractor_blocks"), list) or bool(distractor_keep):
        pool_solution_blocks = solution_blocks if solution_blocks is not None else (t.get("solution_blocks", []) or t.get("blocks", []) or [])
        pool_distractor_blocks = dblocks if isinstance(dblocks, list) else (t.get("distractor_blocks", []) or t.get("distractors", []) or [])
        update_doc["pool"] = list(pool_solution_blocks or []) + list(pool_distractor_blocks or [])

    # 3) 中文語意提示顯示設定（老師端/學生端共用）
    if hide_semantic_zh is not None:
        update_doc["hide_semantic_zh"] = bool(hide_semantic_zh)

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
    if _is_soft_deleted(video_doc):
        return jsonify({"ok": False, "error": "影片已刪除，無法重新生成"}), 400

    try:
        # [修改] 呼叫 parsons.py 的核心邏輯，這會讀取字幕並送往 OpenAI
        print(f"[teacher_t5.regenerate] video_id={video_id} unit={video_doc.get('unit')} level={level}")
        doc, gen_source, gen_error, env = create_task_for_video(
            video_doc=video_doc,
            video_id_str=str(video_id),
            level=level,
        )
        print(f"[teacher_t5.regenerate] gen_source={gen_source} gen_error={gen_error} task_id={doc.get('_id')}")

        # [新增] 為了配合老師端的預覽介面，統一狀態欄位
        # [修正] version 不要固定 v1.AI，改為同影片同 level 的遞增序號 v1/v2/v3...
        try:
            cur = db.parsons_tasks.find({
                "video_id": video_oid,
                "level": level,
            }, {"version": 1})
            max_v = 0
            for it in cur:
                v = str(it.get("version") or "")
                m = re.match(r"^v(\d+)", v)
                if m:
                    max_v = max(max_v, int(m.group(1)))
            next_version = f"v{max_v + 1}" if max_v > 0 else "v1"
        except Exception:
            next_version = "v1"
        db.parsons_tasks.update_one({"_id": doc["_id"]}, {"$set": {
            "status": "pending",
            "enabled": False,  # 預設不發布，待老師審核
            "version": next_version,
            "updated_at": _utc_now(),
        }})

        return jsonify({
            "ok": True,
            "task_id": str(doc["_id"]),
            "message": "AI 題目已生成為待審核版本",
            "gen_source": gen_source,
            "gen_error": gen_error,
            "env": env,
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
            "message": "還未生成",
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
