import os
import re
_re = re  # [新增] 統一使用 _re，避免未定義
import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Tuple, Optional

from flask import Blueprint, request, jsonify
from bson import ObjectId
from bson.errors import InvalidId

from ..db import db

from . import parsons_ai  # [新增] 統一管理 OpenAI 呼叫（方案1）
from .parsons_service import (
    now_utc,
    maybe_oid,
    normalize_video_id,
    log_event,
    read_subtitle_text,
    env_snapshot,
    ai_enabled,
    safe_json_loads,
    parse_srt_segments,
    compact_segments_for_prompt,
    extract_context_around,
    pick_latest_subtitle_path,
    simple_fallback_generate,
    resolve_unit_constraints,
    ai_generate_condition_from_subtitle,
    ai_generate_io_from_subtitle,
    ai_generate_parsons_from_subtitle,
    create_task_for_video,
)

parsons_bp = Blueprint("parsons", __name__)

# =========================
# UTC time helper
# =========================
def _utc_now():
    """
    Return current UTC datetime.
    Used by AI feedback / subtitle alignment / regenerate timestamps.
    """
    return datetime.now(timezone.utc)

# =========================
# ✅ V1.8 Test (Pre/Post) Utils
# =========================
_TEST_INDEX_READY = False

def ensure_test_indexes():
    """Create unique index for test attempts (one per student per cycle per role)."""
    global _TEST_INDEX_READY
    if _TEST_INDEX_READY:
        return
    try:
        db.parsons_test_attempts.create_index(
            [("student_id", 1), ("test_cycle_id", 1), ("test_role", 1)],
            unique=True,
            name="uniq_student_cycle_role",
        )
    except Exception as e:
        import traceback
        print("\n========== AI HINT AND SEGMENT ERROR ==========")
        print("error =", repr(e))
        traceback.print_exc()
        print("==============================================\n")


# =========================================
# [新增] 取得題目正確 block 順序
# =========================================
def _get_expected_ids_from_task(task_doc: dict) -> list:
    """
    從 task_doc 取得正確 block id 順序
    """
    try:
        parsed = t5doc_to_parsons_task(task_doc)

        slots = parsed.get("template_slots") or []

        expected_ids = []

        for s in slots:
            eid = s.get("expected_id")
            if eid:
                expected_ids.append(str(eid))

        return expected_ids

    except Exception as e:
        print("ERROR _get_expected_ids_from_task:", e)
        return []



def get_default_test_cycle_id() -> str:
    """預設測驗批次。v1.8 統一使用 test_control，不再依賴 parsons_test_cycles。"""
    return "default"

def is_posttest_open(test_cycle_id: str) -> bool:
    """是否開放後測（統一讀 test_control）。"""
    test_cycle_id = (test_cycle_id or "default").strip() or "default"
    doc = db.test_control.find_one({"_id": f"post_open:{test_cycle_id}"}) or {}
    return bool(doc.get("post_open", False))


# ========================
# [新增] 將資料庫的 task doc 轉成前端 Parsons.vue 期待的格式
def t5doc_to_parsons_task(doc: dict) -> dict:
    """
    將 parsons_tasks / parsons_test_tasks 來源任務（doc）轉成前端 Parsons.vue 期待的格式。
    - 支援 template_slots 可能是 dict list 或 str list（舊資料/手動匯入）。
    """
    question_text = doc.get("question_text") or doc.get("question") or ""
    solution_blocks = doc.get("solution_blocks") or []
    distractor_blocks = doc.get("distractor_blocks") or []
    template_slots = doc.get("template_slots") or []

    # --- Normalize blocks (防呆：若舊資料是 string list) ---
    def _norm_blocks(blocks):
        out = []
        for i, b in enumerate(blocks or []):
            if isinstance(b, dict):
                bid = b.get("id") or b.get("_id") or f"b{i+1}"
                out.append({
                    "id": str(bid),
                    "text": b.get("text") if b.get("text") is not None else b.get("code", ""),
                    "type": b.get("type") or "solution",
                })
            else:
                out.append({"id": f"b{i+1}", "text": str(b), "type": "solution"})
        return out

    solution_blocks = _norm_blocks(solution_blocks)
    distractor_blocks = _norm_blocks(distractor_blocks)

    # [新增] 若干擾區塊沒有 enabled 欄位，預設視為保留（True），避免學生端看不到干擾題
    try:
        _dbs = []
        for _b in (distractor_blocks or []):
            if isinstance(_b, dict) and ('enabled' not in _b):
                _b['enabled'] = True
            _dbs.append(_b)
        distractor_blocks = _dbs
    except Exception:
        pass


    # --- Normalize template_slots ---
    # 期望是 list[dict]：{slot, label, expected_id}
    if template_slots and not isinstance(template_slots[0], dict):
        # 例如：["s1","s2"...] 或 [0,1,2...] → 轉成 dict list
        _tmp = []
        for i, s in enumerate(template_slots):
            _tmp.append({
                "slot": str(s),
                "label": f"第{i+1}格",
            })
        template_slots = _tmp

    # 若 template_slots 為空，依 solution_blocks 長度自動生成
    if not template_slots:
        template_slots = [{"slot": f"s{i+1}", "label": f"第{i+1}格"} for i in range(len(solution_blocks))]

    # 補 expected_id：若資料本身沒有，就按照 solution_blocks 順序對齊
    for i in range(min(len(template_slots), len(solution_blocks))):
        if not template_slots[i].get("expected_id"):
            template_slots[i]["expected_id"] = solution_blocks[i]["id"]

    pool = doc.get("pool")
    if not pool:
        pool = solution_blocks + distractor_blocks

    # 中文語意：測驗不需要，但若有也一起帶給前端（前端可選擇不顯示）
    ai_slot_hints = doc.get("ai_slot_hints") or doc.get("slot_hints") or {}

    return {
        "task_id": str(doc.get("_id", "")),
        "question_text": question_text,
        "solution_blocks": solution_blocks,
        "distractor_blocks": distractor_blocks,
        "pool": pool,
        "template_slots": template_slots,
        "ai_slot_hints": ai_slot_hints,
        "status": doc.get("status") or doc.get("review_status") or "",
        "enabled": bool(doc.get("enabled", True)),
    }


# =========================
# Fallback generator
# =========================


# =========================
# 固定題：老師手動建立 / 更新
# POST /fixed_task/save
# =========================
@parsons_bp.post("/fixed_task/save")
def save_fixed_task():
    """
    老師在前端填寫題目後儲存。
    同一支影片只保留一筆 gen_source=fixed 的題目（upsert）。
    """
    data = request.get_json(silent=True) or {}
    video_id = (data.get("video_id") or "").strip()
    if not video_id:
        return jsonify({"ok": False, "message": "missing video_id"}), 400

    question_text = (data.get("question_text") or "").strip()
    if not question_text:
        return jsonify({"ok": False, "message": "question_text is required"}), 400

    solution_lines = data.get("solution_lines") or []
    distractor_lines = data.get("distractor_lines") or []

    if not isinstance(solution_lines, list) or len(solution_lines) < 2:
        return jsonify({"ok": False, "message": "solution_lines must have at least 2 items"}), 400

    # 轉成 blocks 格式（與 AI 題一致，學生端 parsons.vue 直接可用）
    def _to_blocks(lines, prefix=""):
        blocks = []
        for i, ln in enumerate(lines):
            text = str(ln).rstrip()
            if not text:
                continue
            # 計算縮排空格數（不是等級，就是空格數）
            indent_spaces = len(text) - len(text.lstrip())
            # 移除縮排，只保存程式文本
            text_no_indent = text.lstrip()
            blocks.append({
                "id": f"{prefix}{i}",
                "text": text_no_indent,  # 純文字，無縮排
                "indent": indent_spaces,  # 縮排空格數（用於與學生答案比較）
                "semantic_zh": "",
            })
        return blocks

    solution_blocks = _to_blocks(solution_lines, prefix="s")
    distractor_blocks = _to_blocks(distractor_lines, prefix="d")
    pool = solution_blocks + distractor_blocks

    # template_slots：用 solution_blocks 的 text 做中文標籤（空白，老師可以事後補）
    template_slots = [
        {"slot": str(i), "label": b["text"]}
        for i, b in enumerate(solution_blocks)
    ]

    vid_oid = maybe_oid(video_id)
    unit = (data.get("unit") or "").strip()
    level = (data.get("level") or "L1").strip()
    # [新增] 先整理字幕範圍，避免 start_ts / end_ts 未定義
    source_subtitle = data.get("source_subtitle") or {}
    raw_subtitle_range = data.get("subtitle_range") or {}

    subtitle_range = {
        "start": source_subtitle.get("start_ts", raw_subtitle_range.get("start")),
        "end": source_subtitle.get("end_ts", raw_subtitle_range.get("end")),
    }
    now = now_utc()
    doc_set = {
        "video_id_str": video_id,
        "gen_source": "fixed",
        "question_text": question_text,
        "solution_blocks": solution_blocks,
        "distractor_blocks": distractor_blocks,
        "pool": pool,
        "template_slots": template_slots,
        "unit": unit,
        "level": level,
        "unit_type": data.get("unit_type") or "loop",
        "enabled": False,
        "status": "pending",
        "review_status": "draft",
        "updated_at": now,
        "ai_generated": False, # 固定題預設不是 AI 生成
        "ai_segment_map": {}, # AI 推估的 slot -> segment（start/end/evidence）
        "ai_slot_hints": {}, # AI 推估的 slot -> 中文提示
        "ai_segments_compact": "", # AI 生成時用的字幕精簡版（冗長，先不存）
        "subtitle_range": subtitle_range,
        "version": "fixed",
    }
    print("data keys =", list(data.keys()))
    print("source_subtitle =", data.get("source_subtitle"))
    print("subtitle_range =", data.get("subtitle_range"))

    if vid_oid:
        doc_set["video_id"] = vid_oid

    # upsert：同影片只保留一筆 fixed
    q = {"gen_source": "fixed"}
    if vid_oid:
        q["video_id"] = vid_oid
    else:
        q["video_id_str"] = video_id

    existing = db.parsons_tasks.find_one(q)
    if existing:
        db.parsons_tasks.update_one({"_id": existing["_id"]}, {"$set": doc_set})
        task_id = str(existing["_id"])
    else:
        doc_set["created_at"] = now
        result = db.parsons_tasks.insert_one(doc_set)
        task_id = str(result.inserted_id)

    return jsonify({"ok": True, "task_id": task_id})

# ========================
# GET /fixed_task/get 供前端載入已儲存的固定題
@parsons_bp.get("/fixed_task/get")
def get_fixed_task():
    """取得這支影片的固定題（供前端載入已儲存內容）"""
    video_id = (request.args.get("video_id") or "").strip()
    if not video_id:
        return jsonify({"ok": False, "message": "missing video_id"}), 400

    vid_oid = maybe_oid(video_id)
    q = {"gen_source": "fixed"}
    if vid_oid:
        q["$or"] = [{"video_id": vid_oid}, {"video_id_str": video_id}]
    else:
        q["video_id_str"] = video_id

    task = db.parsons_tasks.find_one(q)
    if not task:
        return jsonify({"ok": True, "found": False})

    sol_lines = [b.get("text", "") for b in (task.get("solution_blocks") or [])]
    dis_lines = [b.get("text", "") for b in (task.get("distractor_blocks") or [])]

    return jsonify({
        "ok": True,
        "found": True,
        "task_id": str(task["_id"]),
        "question_text": task.get("question_text") or "",
        "solution_lines": sol_lines,
        "distractor_lines": dis_lines,
        "enabled": bool(task.get("enabled", False)),
        "status": task.get("status") or "pending",
    })

# ========================


# ========================
# [新增] POST /fixed_task/align_subtitle  手動觸發固定題字幕對齊
# 直接從 prompt_source.subtitle_preview 或 ai_segments_compact 取字幕，不需要找檔案路徑
# ========================
@parsons_bp.post("/fixed_task/align_subtitle")
def align_fixed_task_subtitle():
    data = request.get_json(silent=True) or {}
    task_id_str = (data.get("task_id") or "").strip()
    video_id = (data.get("video_id") or "").strip()

    task = None
    if task_id_str:
        try:
            task = db.parsons_tasks.find_one({"_id": ObjectId(task_id_str)})
        except Exception:
            pass
    if not task and video_id:
        vid_oid = maybe_oid(video_id)
        q = {"gen_source": "fixed"}
        if vid_oid:
            q["$or"] = [{"video_id": vid_oid}, {"video_id_str": video_id}]
        else:
            q["video_id_str"] = video_id
        task = db.parsons_tasks.find_one(q)

    if not task:
        return jsonify({"ok": False, "message": "找不到固定題（gen_source=fixed）"}), 404

    real_task_id = str(task["_id"])

    # ① 取字幕文字：優先 subtitle_preview，其次 ai_segments_compact，其次 subtitle_text_used
    prompt_source = task.get("prompt_source") or {}
    subtitle_raw = (
        prompt_source.get("subtitle_preview")
        or prompt_source.get("subtitle_text")
        or task.get("subtitle_text_used")
        or task.get("ai_segments_compact")
        or ""
    ).strip()

    # ② 如果還是空的，嘗試從影片的 subtitle_preview 欄位取
    if not subtitle_raw:
        vid_oid = task.get("video_id")
        vid_str = task.get("video_id_str") or (str(vid_oid) if vid_oid else "")
        video_doc = {}
        if vid_oid:
            try:
                video_doc = db.videos.find_one({"_id": vid_oid}) or {}
            except Exception:
                pass
        subtitle_raw = (
            video_doc.get("subtitle_preview")
            or video_doc.get("subtitle_text")
            or ""
        ).strip()

    if not subtitle_raw:
        return jsonify({
            "ok": False,
            "message": "找不到字幕內容，請確認此固定題的影片有字幕資料",
            "task_id": real_task_id,
            "prompt_source_keys": list(prompt_source.keys()),
        }), 404

    # ③ 解析字幕成時間段
    # 判斷是 SRT 格式還是已經是 [start-end] text 格式
    if "-->" in subtitle_raw:
        segs = parse_srt_segments(subtitle_raw)
        segs_compact = compact_segments_for_prompt(segs, max_chars=12000)
    else:
        # 已經是 compact 格式，直接用
        segs_compact = subtitle_raw[:12000]
        segs = []

    if not segs_compact:
        return jsonify({"ok": False, "message": "字幕解析結果為空"}), 400

    solution_blocks = task.get("solution_blocks") or []
    if not solution_blocks:
        return jsonify({"ok": False, "message": "此固定題沒有 solution_blocks"}), 400

    blocks_lines_list = []
    for i, b in enumerate(solution_blocks):
        blocks_lines_list.append("slot \"%d\" (id=%s): %s" % (i, b.get("id", ""), b.get("text", "")))
    blocks_text = "\n".join(blocks_lines_list)

    if not ai_enabled():
        return jsonify({"ok": False, "message": "AI_ENABLED=false"}), 503

    model = (os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()

    system_prompt = (
        "你是字幕對齊助教。為每個程式區塊，從字幕中找出老師「邊輸入程式碼、邊口頭講解」該段程式的片段。"
        "判斷標準：1.字幕出現程式關鍵字（變數名/函式名） 2.字幕有「輸入」「打」「寫」「這行」「進到同步編輯器」等操作詞 3.概念解釋句。"
        "start/end 必須是字幕實際時間戳，不可捏造。找不到則省略。只輸出純 JSON。"
    )

    user_prompt = "\n".join([
        "請為以下每個程式區塊，找出老師邊輸入程式碼邊講解的字幕片段。",
        "",
        "【程式區塊】",
        blocks_text,
        "",
        "【字幕（格式：[start-end] text）】",
        segs_compact,
        "",
        "輸出格式（slot key 用 \"0\",\"1\"... 字串）：",
        "{\"ai_segment_map\":{\"0\":{\"start\":120.0,\"end\":150.0,\"evidence\":\"字幕句\"},\"1\":{\"start\":155.0,\"end\":180.0,\"evidence\":\"字幕句\"}},\"subtitle_range\":{\"start\":120.0,\"end\":180.0}}",
    ])

    try:
        ai_resp = parsons_ai.call_openai_json(
            model=model,
            system=system_prompt,
            user=user_prompt
        ) or {}
    except Exception as e:
        return jsonify({"ok": False, "message": f"AI 呼叫失敗: {e}"}), 500

    update = {}
    seg_map = ai_resp.get("ai_segment_map")
    if isinstance(seg_map, dict):
        valid_map = {}
        for k, v in seg_map.items():
            try:
                s = float(v.get("start"))
                e = float(v.get("end"))
                if e > s:
                    valid_map[k] = {"start": s, "end": e, "evidence": str(v.get("evidence", ""))}
            except Exception:
                pass
        if valid_map:
            update["ai_segment_map"] = valid_map

    sub_range = ai_resp.get("subtitle_range")
    if isinstance(sub_range, dict):
        try:
            rs = float(sub_range.get("start"))
            re2 = float(sub_range.get("end"))
            if re2 > rs:
                update["subtitle_range"] = {"start": rs, "end": re2}
        except Exception:
            pass

    update["ai_segments_compact"] = segs_compact
    db.parsons_tasks.update_one({"_id": task["_id"]}, {"$set": update})

    return jsonify({
        "ok": True,
        "task_id": real_task_id,
        "subtitle_chars": len(segs_compact),
        "slots_aligned": list(update.get("ai_segment_map", {}).keys()),
        "ai_segment_map": update.get("ai_segment_map", {}),
        "message": "對齊完成，共 %d 個 slot" % len(update.get("ai_segment_map", {})),
    })


# ========================
# [新增] GET /fixed_task/debug
# ========================
@parsons_bp.get("/fixed_task/debug")
def debug_fixed_task():
    video_id = (request.args.get("video_id") or "").strip()
    task_id_str = (request.args.get("task_id") or "").strip()
    task = None
    if task_id_str:
        try:
            task = db.parsons_tasks.find_one({"_id": ObjectId(task_id_str)})
        except Exception:
            pass
    if not task and video_id:
        vid_oid = maybe_oid(video_id)
        q = {"gen_source": "fixed"}
        if vid_oid:
            q["$or"] = [{"video_id": vid_oid}, {"video_id_str": video_id}]
        else:
            q["video_id_str"] = video_id
        task = db.parsons_tasks.find_one(q)
    if not task:
        return jsonify({"ok": False, "message": "找不到固定題（gen_source=fixed）"})
    prompt_source = task.get("prompt_source") or {}
    return jsonify({
        "ok": True,
        "task_id": str(task["_id"]),
        "prompt_source_keys": list(prompt_source.keys()),
        "has_subtitle_preview": bool(prompt_source.get("subtitle_preview")),
        "subtitle_preview_len": len(prompt_source.get("subtitle_preview") or ""),
        "ai_segments_compact_len": len(task.get("ai_segments_compact") or ""),
        "subtitle_range": task.get("subtitle_range") or {},
        "ai_segment_map": task.get("ai_segment_map") or {},
        "solution_blocks": [b.get("text") for b in (task.get("solution_blocks") or [])],
    })

# GET /task 供學生端載入題目
@parsons_bp.get("/task")
def get_task():
    video_id = request.args.get("video_id", "").strip()
    level = request.args.get("level", "L2").strip()

    if not video_id:
        return jsonify({"ok": False, "message": "missing video_id"}), 400

    try:
        vid_oid = ObjectId(video_id)
    except Exception:
        vid_oid = None

    q = {"level": level, "enabled": True}
    if vid_oid:
        q["video_id"] = {"$in": [video_id, vid_oid]}
    else:
        q["video_id"] = video_id

    task = db.parsons_tasks.find_one(q, sort=[("created_at", -1)])
    if not task:
        return jsonify({"ok": True, "noTask": True, "message": "此影片尚未發布題目"})

    parsed = t5doc_to_parsons_task(task)

    return jsonify({
        "ok": True,
        "noTask": False,
        "task_id": str(task.get("_id")),
        "video_id": normalize_video_id(task.get("video_id")),
        "level": task.get("level"),
        "question_text": parsed.get("question_text", ""),
        "pool": parsed.get("pool", []),
        "template_slots": parsed.get("template_slots", []),
        "solution_blocks": parsed.get("solution_blocks", []),
        "distractor_blocks": parsed.get("distractor_blocks", []),
        "ai_feedback": parsed.get("ai_feedback", {}),
        "version": task.get("version", "v1.AI"),
    })


# =========================
# (A) POST /publish  老師端：發布題目（同影片同 level 只允許一題 enabled）
# =========================
@parsons_bp.post("/publish")
def publish_task():
    data = request.get_json(silent=True) or {}
    task_id = (data.get("task_id") or "").strip()

    if not task_id:
        return jsonify({"ok": False, "message": "missing task_id"}), 400

    try:
        oid = ObjectId(task_id)
    except Exception:
        return jsonify({"ok": False, "message": "invalid task_id"}), 400

    task = db.parsons_tasks.find_one({"_id": oid})
    if not task:
        return jsonify({"ok": False, "message": "task not found"}), 404

    task_video_id = task.get("video_id")
    task_level = task.get("level")

    db.parsons_tasks.update_many(
        {"video_id": task_video_id, "level": task_level, "_id": {"$ne": oid}},
        {"$set": {"enabled": False, "review_status": "draft"}}
    )
    if isinstance(task_video_id, str):
        v_oid = maybe_oid(task_video_id)
        if v_oid:
            db.parsons_tasks.update_many(
                {"video_id": v_oid, "level": task_level, "_id": {"$ne": oid}},
                {"$set": {"enabled": False, "review_status": "draft"}}
            )

    r = db.parsons_tasks.update_one(
        {"_id": oid},
        {"$set": {
            "enabled": True,
            "review_status": "published",
            "published_at": now_utc()
        }}
    )

    return jsonify({"ok": True, "matched": r.matched_count, "modified": r.modified_count}), 200


# =========================
# AI: build hint + jump segment for wrong slot
# =========================
def ai_hint_and_segment_for_wrong(task: dict, slot_key: str, expected_text: str, actual_text: str, level: str, slot_label: str) -> Tuple[str, Optional[float], Optional[float], str]:
    """
    [新增] V1.5：錯誤時的 AI 回饋與回看時間軸
    回傳 (hint, start, end, subtitle_context)
    - 優先使用 task.ai_segment_map / task.ai_slot_hints（生成時已產出，穩定、可重現）
    - 若缺少，才用 OpenAI 依字幕時間戳即時推估，並回寫到 task（不改 schema，只新增欄位內容）
    """
    hint = ""
    start = None
    end = None
    subtitle_context = ""

    seg_map = (task.get("ai_segment_map") or {}) if isinstance(task.get("ai_segment_map"), dict) else {}
    slot_hints = (task.get("ai_slot_hints") or {}) if isinstance(task.get("ai_slot_hints"), dict) else {}

    seg = seg_map.get(str(slot_key)) or None
    if isinstance(seg, dict):
        try:
            s = float(seg.get("start"))
            e = float(seg.get("end"))
            if e > s:
                start, end = s, e
        except Exception:
            start, end = None, None

    hint = (slot_hints.get(str(slot_key)) or "").strip()

    try:
        sub_path = ((task.get("prompt_source") or {}).get("subtitle_path") or "").strip()
        if sub_path:
            sub_text = read_subtitle_text(sub_path)
            segs = parse_srt_segments(sub_text)
            if start is not None and end is not None:
                subtitle_context = extract_context_around(segs, start, end, window=5)
            else:
                subtitle_context = compact_segments_for_prompt(segs[:18], max_chars=3000)
    except Exception:
        subtitle_context = ""

    if ai_enabled() and (not hint or start is None or end is None):
        try:
            model = (os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()
            segs_compact = (task.get("ai_segments_compact") or "").strip()
            if not segs_compact:
                sub_path = ((task.get("prompt_source") or {}).get("subtitle_path") or "").strip()
                segs_compact = compact_segments_for_prompt(parse_srt_segments(read_subtitle_text(sub_path)), max_chars=12000)

            prompt = f"""
你是一位 Python 程式設計助教。學生在 Parsons 題目中把某一格放錯了。
請你做兩件事：
1) 給「繁體中文」提示（1~2句，針對該格錯誤）
2) 從字幕時間戳中找出老師「邊輸入程式碼、邊口頭講解」該程式區塊的片段（start/end 必須是字幕裡實際存在的時間戳，不可捏造）

請輸出「純 JSON」，不要多餘文字：
{{
  "hint": "繁體中文提示",
  "start": 120.0,
  "end": 150.0,
  "evidence": "引用字幕關鍵句（可短）"
}}

資訊：
- 難度 level: {level}
- 錯誤格：{slot_label}
- 正確應該是（expected）：{expected_text}
- 學生放的是（actual）：{actual_text if actual_text else "（空白）"}

字幕（含時間戳）如下（格式：[start-end] text）：
{segs_compact}
""".strip()

            # [新增] OpenAI 呼叫改由 parsons_ai 統一管理（不改既有 prompt/解析）
            data = parsons_ai.call_openai_json(
                model=model,
                system="你是一位 Python 程式設計助教，協助分析 Parsons 錯誤並找出老師講解程式碼的字幕時間戳。只輸出 JSON。",
                user=prompt
            ) or {}

            ai_hint = (data.get("hint") or "").strip()
            ai_s = data.get("start", None)
            ai_e = data.get("end", None)

            ai_start = float(ai_s) if ai_s is not None else None
            ai_end = float(ai_e) if ai_e is not None else None

            if ai_hint:
                hint = ai_hint
            if ai_start is not None and ai_end is not None and ai_end > ai_start:
                start, end = ai_start, ai_end

            try:
                update = {}
                if hint:
                    update[f"ai_slot_hints.{str(slot_key)}"] = hint
                if start is not None and end is not None:
                    update[f"ai_segment_map.{str(slot_key)}"] = {
                        "start": float(start),
                        "end": float(end),
                        "evidence": (data.get("evidence") or "").strip(),
                    }
                if update:
                    db.parsons_tasks.update_one({"_id": task.get("_id")}, {"$set": update})
            except Exception:
                pass

            try:
                sub_path = ((task.get("prompt_source") or {}).get("subtitle_path") or "").strip()
                if sub_path:
                    sub_text = read_subtitle_text(sub_path)
                    segs = parse_srt_segments(sub_text)
                    if start is not None and end is not None:
                        subtitle_context = extract_context_around(segs, start, end, window=5)
            except Exception:
                pass

        except Exception:
            pass

    return hint, start, end, subtitle_context


# =========================
# (B) POST /submit 送出作答
# =========================

# =========================
# ✅ V1.8 Test (Pre/Post) APIs
#  - collections:
#    - parsons_test_tasks: {test_cycle_id, test_role, test_task_id, source_task_id}
#    - parsons_test_cycles: {test_cycle_id, post_open, open_at, close_at}
#    - parsons_test_attempts: {student_id, test_cycle_id, test_role, test_task_id, is_correct, score, duration_sec, wrong_indices, submitted_at}
# =========================

@parsons_bp.get("/test/status")
def test_status():
    ensure_test_indexes()
    student_id = (request.args.get("student_id") or "").strip()
    test_cycle_id = (request.args.get("test_cycle_id") or get_default_test_cycle_id()).strip() or get_default_test_cycle_id()

    if not student_id:
        return jsonify({"ok": False, "message": "missing student_id"}), 400

    pre_done = bool(db.parsons_test_attempts.find_one({"student_id": student_id, "test_cycle_id": test_cycle_id, "test_role": "pre"}))
    post_done = bool(db.parsons_test_attempts.find_one({"student_id": student_id, "test_cycle_id": test_cycle_id, "test_role": "post"}))

    return jsonify({
        "ok": True,
        "student_id": student_id,
        "test_cycle_id": test_cycle_id,
        "pre_open": is_pretest_open(test_cycle_id),
        "pre_done": pre_done,
        "post_done": post_done,
        "post_open": is_posttest_open(test_cycle_id),
    })

def is_pretest_open(test_cycle_id: str) -> bool:
    test_cycle_id = (test_cycle_id or "default").strip() or "default"
    doc = db.test_control.find_one({"_id": f"pre_open:{test_cycle_id}"})
    if not doc:
        return True  # ✅ 預設開放前測
    return bool(doc.get("pre_open", True))


@parsons_bp.get("/test/task")
def get_test_task():
    '''
    回傳一題 Parsons 測驗題（前測/後測各一題，先跑通流程）
    query:
      - student_id
      - test_role: pre/post
      - test_cycle_id
    '''
    # 從 query string 讀參數，避免 NameError
    test_role = (request.args.get("test_role") or "").strip().lower()
    test_cycle_id = (request.args.get("test_cycle_id") or "").strip()

    if test_role not in ("pre", "post"):
        return jsonify({"ok": False, "message": "invalid test_role"}), 400
    if not test_cycle_id:
        return jsonify({"ok": False, "message": "missing test_cycle_id"}), 400

    tt = db.parsons_test_tasks.find_one({"test_cycle_id": test_cycle_id, "test_role": test_role})
    if not tt:
        return jsonify({"ok": False, "message": "test task not configured"}), 404

    # 取得原始題目
    raw_source = tt.get("source_task_id")
    source_task_id = str(raw_source).strip() if raw_source else ""
    try:
        task_doc = db.parsons_tasks.find_one({"_id": ObjectId(source_task_id)})
    except:
        task_doc = None

    if not task_doc:
        return jsonify({"ok": False, "message": "source task not found"}), 404

    # 使用現有的 normalize 函式
    parsed = t5doc_to_parsons_task(task_doc)

    return jsonify({
        "ok": True,
        "test_task_id": str(tt.get("_id")),
         # ✅【新增】一定要回傳 parsons_tasks 的 _id，給前端 submit 用
        "source_task_id": str(task_doc.get("_id")),  # <= 最重要
        "task_id": str(task_doc.get("_id")),         # <= 可選：給前端相容用（你後端 submit 也吃 task_id）
        "question_text": task_doc.get("question_text") or "",
        "template_slots": parsed.get("template_slots") or [],
        "pool": parsed.get("pool") or [],
        "total": 1, # 目前設計為一人一題
        "current_index": 1
    })


@parsons_bp.post("/test/submit")
def submit_test_answer():
    '''
    payload:
      - student_id
      - test_cycle_id
      - test_role: pre/post
      - test_task_id
      - source_task_id / task_id
      - answer_ids: [block_id,...]
      - duration_sec
    '''
    ensure_test_indexes()
    data = request.get_json(silent=True) or {}

    student_id = (data.get("student_id") or "").strip()
    participant_id = (data.get("participant_id") or "").strip()
    test_cycle_id = (data.get("test_cycle_id") or get_default_test_cycle_id()).strip() or get_default_test_cycle_id()
    test_role = (data.get("test_role") or "").strip().lower()
    test_task_id = (data.get("test_task_id") or "").strip()
    source_task_id = (data.get("source_task_id") or data.get("task_id") or "").strip()
    answer_ids = data.get("answer_ids") or []
    answer_lines = data.get("answer_lines") or []
    duration_sec = int(data.get("duration_sec") or 0)

    if not student_id:
        return jsonify({"ok": False, "message": "missing student_id"}), 400
    if test_role not in ("pre", "post"):
        return jsonify({"ok": False, "message": "invalid test_role"}), 400

    if test_role == "post" and not is_posttest_open(test_cycle_id):
        return jsonify({"ok": False, "message": "posttest not open"}), 403

    # 取得題目
    try:
        task = db.parsons_tasks.find_one({"_id": ObjectId(source_task_id)})
    except Exception:
        task = None
    if not task:
        return jsonify({"ok": False, "message": "task not found"}), 404

    # 若前端只帶 participant_id，嘗試回查 student_id，避免完成紀錄失聯。
    if (not student_id) and participant_id:
        try:
            if ObjectId.is_valid(participant_id):
                u = db.users.find_one({"_id": ObjectId(participant_id)})
                if u and u.get("student_id"):
                    student_id = str(u.get("student_id")).strip()
        except Exception:
            pass

    parsed = t5doc_to_parsons_task(task)
    expected_ids = [str(s.get("expected_id")) for s in (parsed.get("template_slots") or [])]

    def _infer_indents_from_structure(blocks: list) -> list:
        out = []
        level = 0
        for b in blocks or []:
            raw = str((b or {}).get("text") or "")
            s = raw.strip()
            low = s.lower()
            if low.startswith(("elif ", "else:", "except", "finally:")):
                level = max(0, level - 1)
            out.append(level * 4)
            if s.endswith(":"):
                level += 1
        return out

    expected_blocks = parsed.get("solution_blocks") or []
    expected_indent_list = []
    for b in expected_blocks:
        b = b or {}
        raw_text = str(b.get("text") or "")
        if "indent" in b:
            expected_indent_list.append(int(b.get("indent", 0) or 0))
        else:
            expected_indent_list.append(len(raw_text) - len(raw_text.lstrip(" ")))
    if expected_indent_list and all(x == 0 for x in expected_indent_list):
        expected_indent_list = _infer_indents_from_structure(expected_blocks)

    aligned = list(answer_ids)
    if len(aligned) < len(expected_ids):
        aligned = aligned + [None] * (len(expected_ids) - len(aligned))

    # [新增] 允許「文字相同但 block_id 不同」視為同一行（避免干擾題與解答文字相同導致誤判）
    def _norm_line_for_compare(s: str) -> str:
        try:
            s = (s or "").replace("	", "    ")
            # 比對時忽略左右空白（縮排由另外的 indentation 機制處理）
            return s.strip()
        except Exception:
            return (s or "").strip()

    pool_by_id_for_compare = {str(b.get("id")): b for b in (parsed.get("pool") or [])}

    wrong_indices = []
    for i in range(len(expected_ids)):
        aid = str(aligned[i]) if aligned[i] is not None else ""
        eid = str(expected_ids[i])

        if aid == eid:
            continue

        a_text = str(pool_by_id_for_compare.get(aid, {}).get("text", "") or "")
        e_text = str(pool_by_id_for_compare.get(eid, {}).get("text", "") or "")

        # 若文字完全相同（忽略左右空白），視為該格正確
        if _norm_line_for_compare(a_text) and _norm_line_for_compare(a_text) == _norm_line_for_compare(e_text):
            continue

        wrong_indices.append(i)

    # 縮排檢查：有 answer_lines 時一併判定。
    indent_errors = []
    for i in range(min(len(expected_ids), len(answer_lines), len(expected_indent_list))):
        expected_indent = int(expected_indent_list[i] or 0)

        user_line = str(answer_lines[i] or "")
        user_indent = len(user_line) - len(user_line.lstrip(" "))

        if user_indent != expected_indent:
            indent_errors.append(i)
            if i not in wrong_indices:
                wrong_indices.append(i)

    extra_wrong = max(0, len(answer_ids) - len(expected_ids))
    is_correct = (len(wrong_indices) == 0 and extra_wrong == 0)

    total_slots = max(1, len(expected_ids))
    score = (total_slots - len(wrong_indices)) / total_slots

    attempt_doc = {
        "student_id": student_id,
        "test_cycle_id": test_cycle_id,
        "test_role": test_role,
        "test_task_id": test_task_id or str(task.get("_id")),
        "source_task_id": str(task.get("_id")),
        "answer_ids": answer_ids,
        "is_correct": is_correct,
        "score": score,
        "duration_sec": duration_sec,
        "wrong_indices": wrong_indices,
        "indent_errors": indent_errors,
        "submitted_at": now_utc(),
    }

    try:
        ins = db.parsons_test_attempts.insert_one(attempt_doc)
        attempt_id = str(ins.inserted_id)
        return jsonify({
            "ok": True,
            "already_submitted": False,
            "attempt_id": attempt_id,
            "is_correct": is_correct,
            "score": score,
            "wrong_indices": wrong_indices,
            "indent_errors": indent_errors,
        })
    except Exception:
        # duplicate key => already submitted
        return jsonify({
            "ok": True,
            "already_submitted": True,
            "is_correct": is_correct,
            "score": score,
            "wrong_indices": wrong_indices,
            "indent_errors": indent_errors,
        })


@parsons_bp.post("/test/cycle/toggle")
def toggle_test_cycle():
    """
    老師端控制後測開放/關閉（v1.8 統一使用 test_control）
    支援：
      1) JSON body: { test_cycle_id, post_open } 或 { test_cycle_id, open }
      2) Query string: ?test_cycle_id=default&post_open=true
      3) 若未提供 post_open/open，則直接「反轉」目前狀態
    """
    try:
        data = request.get_json(silent=True) or {}

        # test_cycle_id：body 優先，其次 query，最後 default
        test_cycle_id = (
            data.get("test_cycle_id")
            or request.args.get("test_cycle_id")
            or get_default_test_cycle_id()
            or "default"
        )
        test_cycle_id = str(test_cycle_id).strip() or "default"

        # 允許 post_open / open 兩種欄位
        raw_open = data.get("post_open", None)
        if raw_open is None:
            raw_open = data.get("open", None)
        if raw_open is None:
            raw_open = request.args.get("post_open", None)
        if raw_open is None:
            raw_open = request.args.get("open", None)

        # 目前狀態
        doc = db.test_control.find_one({"_id": f"post_open:{test_cycle_id}"}) or {}
        cur_open = bool(doc.get("post_open", False))

        # 若沒提供 open 值 → toggle
        if raw_open is None:
            post_open = (not cur_open)
        else:
            # 將字串/布林都轉成 bool
            if isinstance(raw_open, bool):
                post_open = raw_open
            else:
                s = str(raw_open).strip().lower()
                post_open = s in ("1", "true", "t", "yes", "y", "open", "on")

        now = now_utc()

        update = {
            "test_cycle_id": test_cycle_id,
            "post_open": bool(post_open),
            "updated_at": now,
        }

        # 開啟：補 open_at（僅當目前沒有 open_at 時）
        if post_open:
            if not doc.get("open_at"):
                update["open_at"] = now
            update["close_at"] = None
        else:
            update["close_at"] = now

        db.test_control.update_one(
            {"_id": f"post_open:{test_cycle_id}"},
            {"$set": update},
            upsert=True
        )

        # 回傳最新狀態
        new_doc = db.test_control.find_one({"_id": f"post_open:{test_cycle_id}"}) or {}
        return jsonify({
            "ok": True,
            "test_cycle_id": test_cycle_id,
            "post_open": bool(new_doc.get("post_open", False)),
            "open_at": new_doc.get("open_at"),
            "close_at": new_doc.get("close_at"),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# =========================
# v1.8 後測開關（統一只用 test_control）
# 新增：GET /test/cycle/get
# =========================
@parsons_bp.get("/test/cycle/get")
def get_test_cycle_control():
    test_cycle_id = (request.args.get("test_cycle_id") or "default").strip()

    doc_id = f"post_open:{test_cycle_id}"
    doc = db.test_control.find_one({"_id": doc_id}) or {}

    # 統一輸出給前端判斷顯示/隱藏「後測區塊」
    return jsonify({
        "ok": True,
        "test_cycle_id": test_cycle_id,
        "post_open": bool(doc.get("post_open", False)),
        "open_at": doc.get("open_at"),
        "close_at": doc.get("close_at"),
        "updated_at": doc.get("updated_at"),
        "_id": doc_id,
    })    

@parsons_bp.get("/test/export_csv")
def export_test_csv():
    ensure_test_indexes()
    test_cycle_id = (request.args.get("test_cycle_id") or get_default_test_cycle_id()).strip() or get_default_test_cycle_id()

    cur = db.parsons_test_attempts.find({"test_cycle_id": test_cycle_id}).sort("submitted_at", 1)

    headers = [
        "student_id",
        "test_cycle_id",
        "test_role",
        "test_task_id",
        "is_correct",
        "score",
        "duration_sec",
        "wrong_indices",
        "submitted_at",
    ]

    import io, csv
    output = io.StringIO()
    w = csv.DictWriter(output, fieldnames=headers)
    w.writeheader()

    for d in cur:
        row = {
            "student_id": d.get("student_id", ""),
            "test_cycle_id": d.get("test_cycle_id", ""),
            "test_role": d.get("test_role", ""),
            "test_task_id": d.get("test_task_id", ""),
            "is_correct": d.get("is_correct", False),
            "score": d.get("score", ""),
            "duration_sec": d.get("duration_sec", ""),
            "wrong_indices": json.dumps(d.get("wrong_indices", []), ensure_ascii=False),
            "submitted_at": (d.get("submitted_at").isoformat() if isinstance(d.get("submitted_at"), datetime) else str(d.get("submitted_at") or "")),
        }
        w.writerow(row)

    csv_text = output.getvalue()
    output.close()

    from flask import Response
    filename = f"parsons_test_attempts_{test_cycle_id}.csv"
    return Response(
        csv_text,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@parsons_bp.post("/submit")
def submit_answer():
    data = request.get_json(silent=True) or {}

    task_id = (data.get("task_id") or "").strip()
    answer_ids = data.get("answer_ids") or []
    answer_lines = data.get("answer_lines") or []
    student_id = (data.get("student_id") or "").strip()
    participant_id = (data.get("participant_id") or "").strip()

    if not task_id:
        return jsonify({"ok": False, "message": "missing task_id"}), 400

    try:
        task = db.parsons_tasks.find_one({"_id": ObjectId(task_id)})
    except Exception:
        return jsonify({"ok": False, "message": "invalid task_id"}), 400

    if not task:
        return jsonify({"ok": False, "message": "task not found"}), 404

    # 若前端只帶 participant_id，嘗試回查 student_id，避免完成紀錄失聯。
    if (not student_id) and participant_id:
        try:
            if ObjectId.is_valid(participant_id):
                u = db.users.find_one({"_id": ObjectId(participant_id)})
                if u and u.get("student_id"):
                    student_id = str(u.get("student_id")).strip()
        except Exception:
            pass

    parsed = t5doc_to_parsons_task(task)

    # 取得難度/等級資訊（優先 body 的 level，其次為 task 本身的設定）
    level = (data.get("level") or task.get("level") or "").strip()

    expected_ids = [
        str(s.get("expected_id"))
        for s in (parsed.get("template_slots") or [])
    ]

    # ===== DEBUG 區塊 1 =====
    print("\n========== DEBUG SUBMIT START ==========")
    print("task_id =", task_id)
    print("answer_ids =", answer_ids)
    print("expected_ids =", expected_ids)
    print("answer_lines =", answer_lines)
    print("=========================================\n")

    # 先初始化（最終正確性以後段「模板槽位比對 + 縮排比對」為準）
    wrong_indices = []
    indent_errors = []
    is_correct = False

    # [新增] V1.3：Submit 端 fallback 解析 SRT（把 [行-行] 轉成秒數）
    def _parse_srt_time_to_seconds(t: str) -> float:
        # 格式：HH:MM:SS,mmm
        try:
            t = (t or "").strip()
            hh, mm, rest = t.split(":")
            ss, ms = rest.split(",")
            return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0
        except Exception:
            return 0.0

    def _read_srt_segments(abs_or_rel_path: str):
        """
        回傳 list[dict]: [{start:float, end:float, text:str}, ...]
        若讀不到就回 []
        """
        try:
            import os
            import re

            p = (abs_or_rel_path or "").strip()
            if not p:
                return []

            # 允許 DB 存 uploads/... 的相對路徑
            if not os.path.isabs(p):
                # 專案根目錄下的 uploads
                # 你原本 DB 例子：uploads/subtitles/xxx.srt
                root = os.getcwd()
                p = os.path.join(root, p)

            if not os.path.exists(p):
                return []

            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read()

            # 以空行分段
            blocks = re.split(r"\n\s*\n", raw.strip(), flags=re.M)
            out = []
            for b in blocks:
                lines = [x.strip("\ufeff").strip() for x in b.splitlines() if x.strip()]
                if len(lines) < 2:
                    continue
                # lines[0] 可能是序號
                time_line = lines[1] if "-->" in lines[1] else (lines[0] if "-->" in lines[0] else "")
                if "-->" not in time_line:
                    continue
                a, b2 = [x.strip() for x in time_line.split("-->")[:2]]
                start = _parse_srt_time_to_seconds(a)
                end = _parse_srt_time_to_seconds(b2)
                text = "\n".join(lines[2:]) if "-->" in lines[1] else "\n".join(lines[1:])
                out.append({"start": float(start), "end": float(end), "text": text})
            return out
        except Exception:
            return []

    def _pick_segment_from_compact(compact_text: str, slot_idx: int):
        """
        compact 格式像：
        [0-5] ...
        [5-11] ...
        回傳 (line_start:int, line_end:int) 或 (None,None)
        """
        try:
            import re
            pairs = re.findall(r"\[(\d+)\s*-\s*(\d+)\]", compact_text or "")
            if not pairs:
                return (None, None)
            # 簡單穩定策略：用 slot_idx 映射到第 k 段
            k = slot_idx if slot_idx is not None else 0
            if k < 0:
                k = 0
            if k >= len(pairs):
                k = len(pairs) - 1
            a, b = pairs[k]
            return (int(a), int(b))
        except Exception:
            return (None, None)

    def _fallback_segment_from_task(task_doc, slot_idx: int):
        """
        依 task 裡的 ai_segment_map / ai_segments_compact + subtitle_path 推算秒數
        回傳 (t_start, t_end, subtitle_context)
        """
        try:
            # ① 優先用 ai_segment_map（A 電腦通常有）
            seg_map = task_doc.get("ai_segment_map") or {}
            key1 = str(slot_idx) if slot_idx is not None else "0"
            key2 = f"第{(slot_idx + 1)}格" if slot_idx is not None else "第1格"

            seg = None
            if key1 in seg_map:
                seg = seg_map.get(key1)
            elif key2 in seg_map:
                seg = seg_map.get(key2)

            if isinstance(seg, dict):
                ts = seg.get("start")
                te = seg.get("end")
                # only accept segment if start>0 and end>start
                if (
                    ts is not None
                    and te is not None
                    and float(te) > float(ts)
                    and float(ts) > 0
                ):
                    ctx = seg.get("evidence") or ""
                    return (float(ts), float(te), ctx)
                # otherwise continue to fallback below

            # ② 沒有 map，就用 compact + subtitle_path 做推算（B 電腦常見）
            compact = task_doc.get("ai_segments_compact") or ""
            subtitle_path = (((task_doc.get("prompt_source") or {}).get("subtitle_path")) or "").strip()
            if compact and subtitle_path:
                line_a, line_b = _pick_segment_from_compact(compact, slot_idx or 0)
                if line_a is not None and line_b is not None and line_b > line_a:
                    segs = _read_srt_segments(subtitle_path)
                    # 如果讀不到，嘗試根據 video_id 在 uploads/subtitles 找檔案
                    if not segs:
                        vid = str(task_doc.get("video_id") or "")
                        try:
                            import glob, os
                            for f in glob.glob(os.path.join(os.getcwd(), "uploads", "subtitles", "*.srt")):
                                if vid and vid in os.path.basename(f):
                                    segs = _read_srt_segments(f)
                                    if segs:
                                        break
                        except Exception:
                            pass
                    if segs:
                        a = max(0, min(line_a, len(segs) - 1))
                        b = max(0, min(line_b - 1, len(segs) - 1))
                        ts = float(segs[a]["start"])
                        te = float(segs[b]["end"])
                        # context：取範圍內前幾句
                        ctx_lines = [segs[i]["text"] for i in range(a, min(b + 1, a + 6))]
                        ctx = "\n".join([x for x in ctx_lines if x]).strip()
                        if te > ts:
                            return (ts, te, ctx)

            # ③ 還是找不到？嘗試直接讀 srt 檔並平均分配時間區間
            try:
                segs = []
                if subtitle_path:
                    segs = _read_srt_segments(subtitle_path)
                # 如果還是沒找到，試試看有沒有 video_id 對應的檔案
                if not segs:
                    vid = str(task_doc.get("video_id") or "")
                    if vid:
                        import glob, os
                        for f in glob.glob(os.path.join(os.getcwd(), "uploads", "subtitles", "*.srt")):
                            if vid in os.path.basename(f):
                                segs = _read_srt_segments(f)
                                if segs:
                                    break
                if segs:
                    # 切成 total_slots 份
                    # 計算總格數：優先 template_slots，再 fallback solution_blocks
                    slots = task_doc.get("template_slots") or task_doc.get("solution_blocks") or []
                    total_slots = max(1, len(slots))
                    # if slot_idx 超出範圍，就仍使用整體範圍
                    start = segs[0]["start"]
                    end = segs[-1]["end"]
                    span = float(end) - float(start)
                    part_start = float(start) + span * slot_idx / total_slots
                    part_end = float(start) + span * (slot_idx + 1) / total_slots
                    return (part_start, part_end, "")
            except Exception:
                pass
            return (None, None, "")
        except Exception:
            return (None, None, "")

    # [新增] V1.4：用「template_slots 的順序」做一格一格比對，避免 idx 錯位（第3格變第4格）
    parsed = t5doc_to_parsons_task(task)
    expected_ids = [str(s.get("expected_id")) for s in (parsed.get("template_slots") or [])]

    def _infer_indents_from_structure(blocks: list) -> list:
        out = []
        level = 0
        for b in blocks or []:
            raw = str((b or {}).get("text") or "")
            s = raw.strip()
            low = s.lower()
            if low.startswith(("elif ", "else:", "except", "finally:")):
                level = max(0, level - 1)
            out.append(level * 4)
            if s.endswith(":"):
                level += 1
        return out

    expected_blocks_all = parsed.get("solution_blocks") or []
    expected_indent_list = []
    for b in expected_blocks_all:
        b = b or {}
        raw_text = str(b.get("text") or "")
        if "indent" in b:
            expected_indent_list.append(int(b.get("indent", 0) or 0))
        else:
            expected_indent_list.append(len(raw_text) - len(raw_text.lstrip(" ")))
    if expected_indent_list and all(x == 0 for x in expected_indent_list):
        expected_indent_list = _infer_indents_from_structure(expected_blocks_all)

    # [保留] 仍保留 answer_core_ids 欄位（不改 DB schema）
    answer_core = [bid for bid in answer_ids if str(bid).startswith("b")]

    # [新增] 對齊長度：不足補 None；多出的視為錯（但不會影響 wrong_indices 的 index 對齊）
    aligned = list(answer_ids)
    if len(aligned) < len(expected_ids):
        aligned = aligned + [None] * (len(expected_ids) - len(aligned))

    # [新增] 允許「文字相同但 block_id 不同」視為同一行（避免干擾題與解答文字相同導致誤判）
    def _norm_line_for_compare(s: str) -> str:
        try:
            s = (s or "").replace("	", "    ")
            # 比對時忽略左右空白（縮排由另外的 indentation 機制處理）
            return s.strip()
        except Exception:
            return (s or "").strip()

    pool_by_id_for_compare = {str(b.get("id")): b for b in (parsed.get("pool") or [])}

    wrong_indices = []
    for i in range(len(expected_ids)):
        aid = str(aligned[i]) if aligned[i] is not None else ""
        eid = str(expected_ids[i])

        if aid == eid:
            continue

        a_text = str(pool_by_id_for_compare.get(aid, {}).get("text", "") or "")
        e_text = str(pool_by_id_for_compare.get(eid, {}).get("text", "") or "")

        # 若文字完全相同（忽略左右空白），視為該格正確
        if _norm_line_for_compare(a_text) and _norm_line_for_compare(a_text) == _norm_line_for_compare(e_text):
            continue

        wrong_indices.append(i)

    # [關鍵修正] 縮排檢查要併入最終判定，不能只在前段檢查後被覆蓋。
    for i in range(min(len(expected_ids), len(answer_lines), len(expected_indent_list))):
        expected_indent = int(expected_indent_list[i] or 0)
        user_line = answer_lines[i] or ""
        user_indent = len(user_line) - len(user_line.lstrip(" "))
        if user_indent != expected_indent:
            indent_errors.append(i)
            if i not in wrong_indices:
                wrong_indices.append(i)

    # 額外多填的答案也算錯（不新增不存在的格 index，只影響 is_correct / score）
    extra_wrong = max(0, len(answer_ids) - len(expected_ids))

    wrong_index = wrong_indices[0] if wrong_indices else None
    is_correct = (len(wrong_indices) == 0 and extra_wrong == 0)

    # [新增] 分數：以格數正確率計算
    total_slots = max(1, len(expected_ids))
    score = (total_slots - len(wrong_indices)) / total_slots

    # [新增] 產生回饋需要的欄位（slot_label / actual_text / expected_text）
    slot_label = f"第{(wrong_index + 1)}格" if wrong_index is not None else ""
    pool_by_id = {str(b.get("id")): b for b in (parsed.get("pool") or [])}

    actual_id = str(aligned[wrong_index]) if (wrong_index is not None and aligned[wrong_index] is not None) else ""
    expected_id = str(expected_ids[wrong_index]) if wrong_index is not None else ""

    actual_text = pool_by_id.get(actual_id, {}).get("text", "") if actual_id else ""
    expected_text = pool_by_id.get(expected_id, {}).get("text", "") if expected_id else ""

    # 組建回饋字串（與舊版本邏輯一致）
    feedback = "✅ 完全正確！" if is_correct else ((task.get("ai_feedback") or {}).get("general") or f"❌ 目前正確率 {score:.0%}，建議先確認「輸入 → 計算 → 輸出」的順序。")

    # [修正] expected_lines 需在所有分支先定義，避免 UnboundLocalError（錯誤答案也會走到下方 debug）
    expected_lines = [(b.get("text") or "") for b in expected_blocks_all]
    if len(expected_lines) < len(expected_ids):
        expected_lines += [""] * (len(expected_ids) - len(expected_lines))

    print("\n------ DEBUG INDENT CHECK ------")
    for i in range(min(len(expected_ids), len(answer_lines), len(expected_lines))):
        if i >= len(answer_lines):
            continue

        expected_line = expected_lines[i]
        user_line = answer_lines[i]

        expected_indent = int(expected_indent_list[i] or 0) if i < len(expected_indent_list) else 0
        user_indent = len(user_line) - len(user_line.lstrip(" "))

        print(f"[Slot {i}]")
        print("expected_line =", repr(expected_line))
        print("user_line     =", repr(user_line))
        print("expected_indent =", expected_indent)
        print("user_indent     =", user_indent)
        print("-------------------------------")
    print("------ END INDENT CHECK ------\n")

    video_id_str = normalize_video_id(task.get("video_id"))

    attempt_doc = {
        "task_id": task_id,
        "video_id": video_id_str,
        "unit": task.get("unit"),
        "student_id": student_id or None,
        "participant_id": participant_id or None,
        "level": level or task.get("level") or None,
        "answer_ids": answer_ids,
        "answer_block_ids": answer_ids,
        "answer_core_ids": answer_core,
        "is_correct": is_correct,
        "score": score,
        "feedback": feedback,
        "wrong_index": wrong_index,
        "wrong_indices": wrong_indices,
        "indent_errors": indent_errors,  # [紋緒] 縮排錯誤格數
        "review": {"student_choice": None},
        "created_at": now_utc(),
    }

    ins = db.parsons_attempts.insert_one(attempt_doc)
    attempt_id = str(ins.inserted_id)
    review_attempt_id = (data.get("review_attempt_id") or "").strip()

    if review_attempt_id:
        db.parsons_review_logs.update_one(
            {"attempt_id": review_attempt_id},
            {"$set": {
                "followup_is_correct": bool(is_correct),
                "followup_submitted_at": now_utc(),
                "followup_attempt_id": attempt_id,
            }}
        )

    resp = {
        "ok": True,
        "attempt_id": attempt_id,
        "is_correct": is_correct,
        "score": score,
        "feedback": feedback,
        "wrong_index": wrong_index,
        "wrong_indices": wrong_indices,
        "indent_errors": indent_errors,  # [新增] 縮排錯誤的格數

        # [新增] V1.4：送出後回傳欄位（前端顯示以後端為準）
        "slot_label": slot_label,
        "actual_text": actual_text,
        "expected_text": expected_text,
        "hint": "",
        "review_t": None,
    }

    if not is_correct:
        slot_key = str(wrong_index) if wrong_index is not None else "0"
        
        # [新增] 若是縮排錯誤，特別說明
        if indent_errors and wrong_index is not None and wrong_index in indent_errors:
            expected_blocks = parsed.get("solution_blocks") or []
            expected_lines = [(b.get("text") or "") for b in expected_blocks]
            if wrong_index < len(expected_lines) and wrong_index < len(answer_lines):
                exp_indent = len(expected_lines[wrong_index]) - len(expected_lines[wrong_index].lstrip(" "))
                user_indent = len(answer_lines[wrong_index]) - len(answer_lines[wrong_index].lstrip(" "))
                if user_indent < exp_indent:
                    feedback = f"❌ 你錯在：第{(wrong_index + 1)}格\n\n縮排不足：你的程式碼沒有正確的縮排。"  + f"\n\n預期縮排：{exp_indent} 空格\n你的縮排：{user_indent} 空格\n\n請檢查第 {(wrong_index + 1)} 格的縮排是否符合需求。"
                else:
                    feedback = f"❌ 你錯在：第{(wrong_index + 1)}格\n\n縮排過多：你的程式碼縮排超過預期。"  + f"\n\n預期縮排：{exp_indent} 空格\n你的縮排：{user_indent} 空格\n\n請檢查第 {(wrong_index + 1)} 格的縮排是否符合需求。"

        # [修正] ① 優先從 ai_segment_map 讀字幕對齊片段（老師講解程式碼的片段）
        seg_map = (task.get("ai_segment_map") or {}) if isinstance(task.get("ai_segment_map"), dict) else {}
        seg = seg_map.get(slot_key)
        t_start = None
        t_end = None
        subtitle_context = ""

        if isinstance(seg, dict):
            try:
                _s = float(seg.get("start"))
                _e = float(seg.get("end"))
                if _e > _s:
                    t_start, t_end = _s, _e
                    print(f"DEBUG_SEGMENT [ai_segment_map slot={slot_key}]: {t_start} -> {t_end}")
            except Exception:
                pass

        # ② 通用 AI 診斷（取文字回饋；秒數只在 ai_segment_map 無資料時才用）
        ai_diag = _generate_submit_ai_feedback(task, answer_ids)
        ai_feedback_detail = (ai_diag.get("feedback") or {}) if isinstance(ai_diag, dict) else {}
        recommended_review = (ai_diag.get("recommended_review") or {}) if isinstance(ai_diag, dict) else {}

        resp["ai_feedback_detail"] = ai_feedback_detail
        resp["ai_diagnosis_summary"] = ai_diag.get("diagnosis_summary", "") if isinstance(ai_diag, dict) else ""

        hint = (
            ai_feedback_detail.get("concept_explanation")
            or ai_feedback_detail.get("guiding_question")
            or ""
        )

        # 只有 ai_segment_map 沒有命中時，才採用 AI 診斷給的秒數
        if t_start is None or t_end is None or t_end <= t_start:
            ai_t_start = recommended_review.get("start")
            ai_t_end = recommended_review.get("end")
            try:
                ai_t_start = float(ai_t_start) if ai_t_start is not None else None
            except Exception:
                ai_t_start = None
            try:
                ai_t_end = float(ai_t_end) if ai_t_end is not None else None
            except Exception:
                ai_t_end = None
            if ai_t_start is not None and ai_t_end is not None and ai_t_end > ai_t_start:
                t_start, t_end = ai_t_start, ai_t_end
                print(f"DEBUG_SEGMENT [ai_diag fallback]: {t_start} -> {t_end}")

        # ③ 還是沒有，走 ai_hint_and_segment_for_wrong
        if t_start is None or t_end is None or t_end <= t_start:
            hint2, s2, e2, subtitle_context = ai_hint_and_segment_for_wrong(
                task=task,
                slot_key=slot_key,
                expected_text=expected_text,
                actual_text=actual_text,
                level=(level or task.get("level") or "L1"),
                slot_label=(slot_label or f"第{(wrong_index + 1)}格" if wrong_index is not None else "第1格"),
            )
            if not hint:
                hint = hint2
            t_start, t_end = s2, e2

        print("DEBUG_SEGMENT:", t_start, t_end)

        # =========================
        # ③ 若還是沒有，再走 task 內既有對齊推估
        # =========================
        need_fallback = False
        if t_start is None or t_end is None or t_end <= t_start:
            need_fallback = True
        elif t_start == 0 and wrong_index is not None and wrong_index != 0:
            need_fallback = True

        if need_fallback:
            fb_start, fb_end, fb_ctx = _fallback_segment_from_task(task, wrong_index if wrong_index is not None else 0)
            if fb_start is not None and fb_end is not None and fb_end > fb_start:
                t_start = fb_start
                t_end = fb_end
                if not subtitle_context:
                    subtitle_context = fb_ctx

        # =========================
        # ④ 再不行，吃 subtitle_range / source_subtitle
        # =========================
        if t_start is None or t_end is None or t_end <= t_start:
            sr = task.get("subtitle_range") or {}
            ss = task.get("source_subtitle") or {}

            fb_start = (
                sr.get("start_ts")
                or ss.get("start_ts")
            )
            fb_end = (
                sr.get("end_ts")
                or ss.get("end_ts")
            )

            try:
                if fb_start is not None and fb_end is not None and float(fb_end) > float(fb_start):
                    t_start = float(fb_start)
                    t_end = float(fb_end)
            except Exception:
                pass

        # =========================
        # ⑤ 最後最後才用固定保底值
        # =========================
        if t_start is None or t_end is None or t_end <= t_start:
            t_start = 120.0
            t_end = 170.0

        if not hint:
            hint = "請重新檢查這一格在整體程式流程中的角色。"

        resp["review_t"] = int(float(t_start))
        resp["hint"] = hint

        raw_vid = task.get("video_id") or data.get("video_id") or ""
        vid = normalize_video_id(raw_vid)

        resp["jump"] = {"video_id": vid, "start": float(t_start), "end": float(t_end)}
        resp["data"] = {
            "title": "回答錯誤",
            "error_detail": feedback,
            "segment": {
                "start": float(t_start),
                "end": float(t_end),
                "label": f"影片片段 [{int(float(t_start))}–{int(float(t_end))} 秒]"
            },
            "subtitle_context": subtitle_context or "（未找到字幕）",
            "ai_hint": hint,
            "video_id": vid,
        }

    return jsonify(resp)


# =========================
# (C) POST /review_choice  記錄學生是否選擇回看（yes/no）
# =========================
@parsons_bp.route("/review_choice", methods=["POST", "OPTIONS"], endpoint="parsons_review_choice_v17")
def review_choice():
    # CORS preflight
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(silent=True) or {}

    attempt_id = (data.get("attempt_id") or "").strip()  # 前端有送
    student_choice = (data.get("student_choice") or "").strip().lower()  # yes/no

    # ✅ A 方案：後端盡量補齊 student_id
    student_id = (data.get("student_id") or "").strip()  # 若前端未來願意送，直接吃
    participant_id = (data.get("participant_id") or data.get("token") or "").strip()  # 兼容你登入的 uid

    if not attempt_id:
        return jsonify({"ok": False, "message": "missing attempt_id"}), 400
    if student_choice not in ("yes", "no"):
        return jsonify({"ok": False, "message": "student_choice must be yes/no"}), 400

    # 先從 attempts 補 task_id / video_id（不改 schema）
    task_id_f = None
    video_id_f = None

    try:
        att = db.parsons_attempts.find_one({"_id": ObjectId(attempt_id)})
        if att:
            task_id_f = att.get("task_id") or None
            video_id_f = att.get("video_id") or None
            # 若 attempts 裡本來就有 student_id，也可以吃（目前你多半是 null）
            if not student_id:
                student_id = (att.get("student_id") or "").strip()
    except Exception:
        pass

    # 若仍沒有 student_id，但有 participant_id → 去 users 查 student_id
    if (not student_id) and participant_id:
        try:
            u = db.users.find_one({"_id": ObjectId(participant_id)})
            if u:
                student_id = (u.get("student_id") or "").strip()
        except Exception:
            pass

    if not student_id:
        student_id = "unknown"  # 至少不要空，方便你統計

    db.parsons_review_logs.update_one(
        {"attempt_id": attempt_id},
        {
            "$set": {
                "attempt_id": attempt_id,
                "task_id": task_id_f,
                "video_id": video_id_f,
                "student_id": student_id,
                "participant_id": participant_id or None,
                "student_choice": student_choice,
                "updated_at": now_utc(),
            },
            "$setOnInsert": {"created_at": now_utc()},
        },
        upsert=True,
    )

    return jsonify({"ok": True, "student_id": student_id})


# =========================
# (D) POST/OPTIONS /review_watch  (V1.7-C 完整實現：記錄回看詳情)
# 前端正在呼叫：/api/parsons/review_watch
# 記錄學生回看視頻的所有互動數據
# =========================
@parsons_bp.route("/review_watch", methods=["POST", "OPTIONS"])
def review_watch():
    """
    V1.7 完整實現：記錄視頻回看與互動詳情
    
    Expected Payload:
    {
        "attempt_id": "xxx",                    # 關聯的練習嘗試
        "video_id": "xxx",                      # 觀看影片id
        "task_id": "xxx",                       # 觀看影片對應的任務id      
        "student_id": "xxx",                     # 學號
        "start_sec": 120,                        # 指定回看片段開始時間
        "end_sec": 180,                          # 指定回看片段結束時間
        "watch_seconds": 3600,                   # 本次回看觀看秒數
        "reached_end": true,                     # 是否播放到結束
        "watch_start_at": "2026-02-26T...",     # 開始觀看時間
        "watch_end_at": "2026-02-26T...",       # 停止觀看時間
        "seek_events": [{"from": 10, "to": 50, "timestamp": "..."}, ...],  # V1.7 NEW
        "is_complete_playback": true              # V1.7: 是否完整播放（無中斷）
    }
    """
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(silent=True) or {}
    
    attempt_id = (data.get("attempt_id") or "").strip()
    video_id = (data.get("video_id") or "").strip()
    student_id = (data.get("student_id") or "").strip()
    task_id = (data.get("task_id") or "").strip()
    
    watch_seconds = int(data.get("watch_seconds") or 0)
    reached_end = bool(data.get("reached_end"))
    watch_start_at = data.get("watch_start_at")
    watch_end_at = data.get("watch_end_at")
    start_sec = data.get("start_sec")
    end_sec = data.get("end_sec")
    seek_events = data.get("seek_events") or []  # V1.7: seek 事件列表
    
    # 基本檢驗
    if not attempt_id or not video_id:
        return jsonify({"ok": False, "message": "missing attempt_id or video_id"}), 400
    
    try:
        # 從 parsons_attempts 中找到原始嘗試記錄
        original_attempt = db.parsons_attempts.find_one({"_id": ObjectId(attempt_id)})
        if not original_attempt:
            return jsonify({"ok": False, "message": "attempt not found"}), 404
        
        # 若前端沒傳 student_id，從原始 attempt 取得
        if not student_id:
            student_id = original_attempt.get("student_id")
        
        # 取得用戶 participant_id（用于研究分析）
        participant_id = None
        user = db.users.find_one({"student_id": student_id}) if student_id else None
        if user:
            participant_id = user.get("participant_id")
        
        # 計算 seek 統計
        seek_count = len(seek_events)
        total_seek_distance = sum(abs(e.get("to", 0) - e.get("from", 0)) for e in seek_events)
        avg_seek_distance = total_seek_distance / seek_count if seek_count > 0 else 0
        
        # 創建回看日誌記錄
        rewatch_log = {
            "attempt_id": attempt_id,
            "video_id": video_id,
            "task_id": task_id,
            "student_id": student_id,
            "participant_id": participant_id,
            
            # === 觀看行為 ===
            "watch_seconds": watch_seconds,
            "reached_end": reached_end,
            "watch_start_at": watch_start_at,
            "watch_end_at": watch_end_at,
            "duration_minutes": round(watch_seconds / 60, 2),
            
            # === 回看片段信息 ===
            "segment_start_sec": start_sec,
            "segment_end_sec": end_sec,
            "segment_duration_sec": (end_sec - start_sec) if end_sec and start_sec else None,
            
            # === V1.7 Seek 統計 ===
            "seek_count": seek_count,
            "total_seek_distance": total_seek_distance,
            "avg_seek_distance": round(avg_seek_distance, 2),
            "is_frequent_seeker": seek_count > 5,  # 5次以上視為頻繁 seek（可調整閾值）
            "seek_events": seek_events,  # 保留原始事件供詳細分析
            
            # === 播放完整性 ===
            "completed_fully": reached_end and seek_count <= 2,  # V1.7: 判定為完整播放
            
            # === 後續回答 ===
            "has_followup": bool(original_attempt.get("followup_is_correct") is not None),
            "followup_is_correct": original_attempt.get("followup_is_correct"),
            "followup_attempt_id": original_attempt.get("followup_attempt_id"),
            
            # === 時間戳 ===
            "recorded_at": now_utc(),
        }
        
        # 插入回看日誌
        log_result = db.video_rewatch_logs.insert_one(rewatch_log)
        rewatch_log_id = str(log_result.inserted_id)
        
        # V1.7: 更新用戶的回看統計
        if student_id:
            db.users.update_one(
                {"student_id": student_id},
                {
                    "$inc": {"rewatch_stats.total_rewatch_count": 1},
                    "$push": {
                        "rewatch_stats.rewatch_sessions": {
                            "video_id": video_id,
                            "attempt_id": attempt_id,
                            "watched_at": watch_start_at,
                            "watch_duration_sec": watch_seconds,
                            "reached_end": reached_end,
                            "is_frequent_seeker": seek_count > 5,
                        }
                    },
                    "$set": {"last_login_at": now_utc()}
                }
            )
        
        # V1.7: 更新原始 attempt，記錄回看日誌 ID
        db.parsons_attempts.update_one(
            {"_id": ObjectId(attempt_id)},
            {
                "$set": {
                    "review_log_id": rewatch_log_id,
                    "review_log_recorded_at": now_utc()
                }
            }
        )
        
        return jsonify({
            "ok": True,
            "rewatch_log_id": rewatch_log_id,
            "message": f"✅ 回看記錄已保存 (seek_count={seek_count})",
            "stats": {
                "watch_duration": watch_seconds,
                "reached_end": reached_end,
                "seek_count": seek_count,
                "is_frequent_seeker": seek_count > 5,
            }
        })
    
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


# =========================
# (B) POST /regenerate  (老師端產生題目：預設 draft)
# ✅ V1.8 增量：題目敘事多樣化 + 變數命名多樣化 + 補中文語意（含干擾題）
# 只改這個 API，不動其他功能
# =========================
@parsons_bp.post("/regenerate")
def regenerate():
    data = request.get_json(silent=True) or {}
    video_id = (data.get("video_id") or "").strip()
    level = (data.get("level") or "L1").strip()
    stable_raw = data.get("stable")
    if isinstance(stable_raw, str):
        stable_mode = stable_raw.strip().lower() in {"1", "true", "yes", "on"}
    else:
        stable_mode = bool(stable_raw)

    if not video_id:
        return jsonify({"ok": False, "message": "missing video_id"}), 400

    try:
        vid_oid = ObjectId(video_id)
    except InvalidId:
        return jsonify({"ok": False, "message": "video_id must be a 24-char ObjectId"}), 400

    v = db.videos.find_one({"_id": vid_oid})
    if not v:
        return jsonify({"ok": False, "message": "video not found"}), 404

    # 先照舊產生（不破壞既有生成流程）
    doc, gen_source, gen_error, env = create_task_for_video(v, video_id, level, stable_mode=stable_mode)

    # =========================
    # ✅ V1.8 新增：多樣化（只在 unit=IO 時做）
    # =========================
    try:
        import random as _random
        import re as _re_local

        unit = (doc.get("unit") or "").upper()
        is_io = ("-IO" in unit) or (unit == "IO") or ("U1" in unit and "IO" in unit)

        # --- 1) 題目敘事多樣化：只針對「兩個輸入對調輸出」這類 IO 題 ---
        def _looks_like_swap_two_inputs(sol_blocks: list) -> bool:
            lines = []
            for b in (sol_blocks or []):
                t = (b.get("text") if isinstance(b, dict) else str(b)) or ""
                t = t.strip()
                if t:
                    lines.append(t)
            if len(lines) < 4:
                return False

            # 常見形態：a=input(); b=input(); print(b); print(a)
            # 只做「寬鬆判斷」，避免誤判其他 IO 題
            has_two_input = sum(1 for x in lines if "input(" in x) >= 2
            has_two_print = sum(1 for x in lines if "print(" in x) >= 2
            return has_two_input and has_two_print

        def _story_variant_question() -> str:
            # 你要的「情境化敘事」：同樣是兩個輸入、對調輸出，但文字更像故事
            variants = [
                "曉華與小明各自買了一個禮物（以整數金額表示）。請依序輸入兩個金額，並把輸出順序對調後印出。",
                "有兩個整數資料代表 A 與 B 的值。請依序輸入兩個整數，最後請先輸出第二個，再輸出第一個。",
                "兩位同學交換禮物：先輸入曉華的金額，再輸入小明的金額。請把輸出順序對調後印出（先小明、再曉華）。",
                "請依序輸入兩個整數，並把它們交換位置後輸出（先輸出第二個輸入，再輸出第一個輸入）。",
            ]
            return _random.choice(variants)

        # --- 2) 變數命名多樣化（n1/n2 -> y1/y2, a/b, x1/x2 ...）---
        def _pick_var_pair() -> tuple:
            pairs = [
                ("y1", "y2"),
                ("a", "b"),
                ("x1", "x2"),
                ("p1", "p2"),
                ("gift1", "gift2"),
            ]
            return _random.choice(pairs)

        def _extract_two_input_vars(sol_blocks: list) -> tuple:
            # 找出最前面兩個「var = input()」的 var 名
            vars_found = []
            for b in (sol_blocks or []):
                if not isinstance(b, dict):
                    continue
                line = (b.get("text") or "").strip()
                m = _re_local.match(r"^\s*([A-Za-z_]\w*)\s*=\s*input\s*\(\s*\)\s*$", line)
                if m:
                    vars_found.append(m.group(1))
                if len(vars_found) >= 2:
                    break
            if len(vars_found) >= 2:
                return vars_found[0], vars_found[1]
            return "", ""

        def _rename_vars_in_blocks(blocks: list, old_a: str, old_b: str, new_a: str, new_b: str):
            if not old_a or not old_b or not new_a or not new_b:
                return
            # whole-word replacement
            pa = _re_local.compile(rf"\b{_re_local.escape(old_a)}\b")
            pb = _re_local.compile(rf"\b{_re_local.escape(old_b)}\b")

            for b in (blocks or []):
                if not isinstance(b, dict):
                    continue
                t = b.get("text")
                if t is None:
                    continue
                s = str(t)
                s = pa.sub(new_a, s)
                s = pb.sub(new_b, s)
                b["text"] = s

        # --- 3) 補中文語意（solution + distractor 都補，避免「未提供」）---
        def _infer_semantic_zh(line: str) -> str:
            s = (line or "").strip()
            if not s:
                return ""
            if "input(" in s:
                return "讀取使用者輸入"
            if "print(" in s:
                return "輸出結果"
            if s.startswith("if "):
                return "條件判斷：若成立則執行"
            if s.startswith("elif "):
                return "其他條件判斷"
            if s.startswith("else"):
                return "否則（不成立時）執行"
            if s.startswith("for "):
                return "使用 for 迴圈重複執行"
            if s.startswith("while "):
                return "使用 while 迴圈重複執行"
            if s == "break" or s.startswith("break"):
                return "中斷迴圈"
            if "+=" in s or ("=" in s and "+" in s):
                return "更新/累加變數"
            if "=" in s and ("==" not in s) and ("!=" not in s):
                return "設定/更新變數"
            return "執行這一行程式"

        def _ensure_zh(blocks: list):
            for b in (blocks or []):
                if not isinstance(b, dict):
                    continue
                line = (b.get("text") or "").strip()
                zh = (b.get("semantic_zh") or b.get("zh") or "").strip()
                if not zh:
                    zh = _infer_semantic_zh(line)
                # 同步寫兩種欄位，避免前端讀不同 key
                b["semantic_zh"] = zh
                b["zh"] = zh
                # 1) 保底：solution/distractor/pool 都要有 zh（避免未提供）
        _ensure_zh(doc.get("solution_blocks", []))
        _ensure_zh(doc.get("distractor_blocks", []))
        _ensure_zh(doc.get("pool", []))

        # 2) 補回舊版 ai_slot_hints（以 template_slots 的順序對齊）
        ai_slot_hints = {}
        tpl = doc.get("template_slots") or []
        sol = doc.get("solution_blocks") or []

        # 盡量用 solution_blocks 的順序當 slot 對應（跟你舊版最接近）
        for i in range(min(len(tpl), len(sol))):
            hint = (sol[i].get("semantic_zh") or sol[i].get("zh") or "").strip()
            if not hint:
                hint = _infer_semantic_zh(sol[i].get("text") or "")
            ai_slot_hints[str(i)] = hint

        doc["ai_slot_hints"] = ai_slot_hints

        # 3) 補回舊版 ai_segment_map（提供 submit/跳秒依據）
        #   - 若你有 subtitle_range / source_subtitle，就把它掛到每個 slot 上（最穩）
        sr = doc.get("subtitle_range") or {}
        ss = doc.get("source_subtitle") or {}
        start_ts = sr.get("start_ts") or ss.get("start_ts")
        end_ts   = sr.get("end_ts")   or ss.get("end_ts")
        start_idx = sr.get("start_index") or ss.get("start_index")
        end_idx   = sr.get("end_index")   or ss.get("end_index")
        text_used = doc.get("subtitle_text_used") or ss.get("text_used") or ""

        ai_segment_map = {}
        for i in range(len(tpl) or len(sol)):
            ai_segment_map[str(i)] = {
                "start_ts": start_ts,
                "end_ts": end_ts,
                "start_index": start_idx,
                "end_index": end_idx,
                "text_used": text_used,
                "hint": ai_slot_hints.get(str(i), ""),
            }

        doc["ai_segment_map"] = ai_segment_map

        # 4) 補回舊版 ai_segments_compact（給老師端/前端快速顯示用）
        #    生成一段文字：包含 index 範圍 + text_used 摘要
        def _compact_text(s: str, max_chars: int = 140) -> str:
            s = (s or "").strip()
            s = _re.sub(r"\s+", " ", s)
            if len(s) > max_chars:
                return s[:max_chars] + "..."
            return s

        seg_head = ""
        if start_idx is not None and end_idx is not None:
            seg_head += f"[{start_idx}-{end_idx}] "
        if start_ts is not None and end_ts is not None:
            seg_head += f"({start_ts}-{end_ts}) "

        doc["ai_segments_compact"] = seg_head + _compact_text(text_used)

        # 5) 寫回 DB（確保不是只在記憶體）
        try:
            db.parsons_tasks.update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "solution_blocks": doc.get("solution_blocks", []),
                    "distractor_blocks": doc.get("distractor_blocks", []),
                    "pool": doc.get("pool", []),

                    "ai_slot_hints": doc.get("ai_slot_hints", {}),
                    "ai_segment_map": doc.get("ai_segment_map", {}),
                    "ai_segments_compact": doc.get("ai_segments_compact", ""),
                }}
            )
        except Exception:
            pass

        # ========== 真正套用 ==========
        if is_io and _looks_like_swap_two_inputs(doc.get("solution_blocks", [])):
            # (A) 題目文字換成故事版（仍是相同教學目標）
            doc["question_text"] = _story_variant_question()

            # (B) 變數名換掉：n1/n2 -> 隨機 pair
            old1, old2 = _extract_two_input_vars(doc.get("solution_blocks", []))
            new1, new2 = _pick_var_pair()
            if old1 and old2 and (old1 != new1 or old2 != new2):
                _rename_vars_in_blocks(doc.get("solution_blocks", []), old1, old2, new1, new2)
                _rename_vars_in_blocks(doc.get("distractor_blocks", []), old1, old2, new1, new2)
                _rename_vars_in_blocks(doc.get("pool", []), old1, old2, new1, new2)

        # (C) 不管是不是 swap 題，都保底補語意（避免干擾題「未提供」）
        _ensure_zh(doc.get("solution_blocks", []))
        _ensure_zh(doc.get("distractor_blocks", []))
        _ensure_zh(doc.get("pool", []))

        # (D) 回寫 DB（不改 schema：只是補/覆蓋既有欄位）
        try:
            db.parsons_tasks.update_one(
                {"_id": doc.get("_id")},
                {"$set": {
                    "question_text": doc.get("question_text"),
                    "solution_blocks": doc.get("solution_blocks", []),
                    "distractor_blocks": doc.get("distractor_blocks", []),
                    "pool": doc.get("pool", []),
                }},
            )
        except Exception:
            pass

    except Exception:
        # 保守：多樣化失敗也不影響原本 regenerate 成功
        pass
    return jsonify({
        "ok": True,
        "task": {
            "task_id": str(doc["_id"]),
            "video_id": doc.get("video_id"),
            "unit": doc.get("unit"),
            "title": doc.get("video_title"),
            "level": doc.get("level"),
            "question_text": doc.get("question_text"),
            "template_slots": doc.get("template_slots", []),
            "pool": doc.get("pool", []),
            "ai_feedback": doc.get("ai_feedback", {}),
        },
        "gen_source": gen_source,
        "gen_error": gen_error,
        "stable_mode": stable_mode,
        "env": env,
    })

# =========================
# (D) AI 診斷：根據錯誤格 slot_key，從 task 內既有資料或 OpenAI 推估出 hint + 建議回看片段
def ai_hint_and_segment_for_wrong(task: dict, slot_key: str, expected_text: str, actual_text: str, level: str, slot_label: str) -> Tuple[str, Optional[float], Optional[float], str]:
    """
    錯誤時的 AI 回饋與回看時間軸
    回傳 (hint, start, end, subtitle_context)

    優先順序：
    1) task.ai_segment_map / task.ai_slot_hints
    2) OpenAI 根據字幕推估
    3) 留給 submit() 走其他 fallback
    """
    hint = ""
    start = None
    end = None
    subtitle_context = ""

    seg_map = (task.get("ai_segment_map") or {}) if isinstance(task.get("ai_segment_map"), dict) else {}
    slot_hints = (task.get("ai_slot_hints") or {}) if isinstance(task.get("ai_slot_hints"), dict) else {}

    seg = seg_map.get(str(slot_key)) or None
    if isinstance(seg, dict):
        try:
            # ✅ 同時支援 start/end 與 start_ts/end_ts
            s = seg.get("start", seg.get("start_ts"))
            e = seg.get("end", seg.get("end_ts"))
            s = float(s) if s is not None else None
            e = float(e) if e is not None else None
            if s is not None and e is not None and e > s:
                start, end = s, e
        except Exception:
            start, end = None, None

        # ✅ hint 也可從 seg 裡面取
        try:
            if not hint:
                hint = str(seg.get("hint") or "").strip()
        except Exception:
            pass

    if not hint:
        hint = (slot_hints.get(str(slot_key)) or "").strip()

    try:
        sub_path = ((task.get("prompt_source") or {}).get("subtitle_path") or "").strip()
        if sub_path:
            sub_text = read_subtitle_text(sub_path)
            segs = parse_srt_segments(sub_text)
            if start is not None and end is not None:
                subtitle_context = extract_context_around(segs, start, end, window=5)
            else:
                subtitle_context = compact_segments_for_prompt(segs[:18], max_chars=3000)
    except Exception:
        subtitle_context = ""

    if ai_enabled() and (not hint or start is None or end is None):
        try:
            model = (os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()
            segs_compact = (task.get("ai_segments_compact") or "").strip()

            if not segs_compact:
                sub_path = ((task.get("prompt_source") or {}).get("subtitle_path") or "").strip()
                if sub_path:
                    segs_compact = compact_segments_for_prompt(
                        parse_srt_segments(read_subtitle_text(sub_path)),
                        max_chars=12000
                    )

            prompt = f"""
你是一位 Python 程式設計助教。學生在 Parsons 題目中把某一格放錯了。
請你做兩件事：
1) 給「繁體中文」提示（1~2句，針對該格錯誤）
2) 從字幕時間戳中找出老師「邊輸入程式碼、邊口頭講解」該程式區塊的片段（start/end 必須是字幕裡實際存在的時間戳，不可捏造）

請輸出「純 JSON」，不要多餘文字：
{{
  "hint": "繁體中文提示",
  "start": 12.0,
  "end": 28.0,
  "evidence": "引用字幕關鍵句（可短）"
}}

資訊：
- 難度 level: {level}
- 錯誤格：{slot_label}
- 正確應該是（expected）：{expected_text}
- 學生放的是（actual）：{actual_text if actual_text else "（空白）"}

字幕（含時間戳）如下（格式：[start-end] text）：
{segs_compact}
""".strip()

            data = parsons_ai.call_openai_json(
                model=model,
                system="你是一位 Python 程式設計助教，協助分析 Parsons 錯誤並找出老師講解程式碼的字幕時間戳。只輸出 JSON。",
                user=prompt
            ) or {}

            ai_hint = (data.get("hint") or "").strip()
            ai_s = data.get("start", None)
            ai_e = data.get("end", None)

            ai_start = float(ai_s) if ai_s is not None else None
            ai_end = float(ai_e) if ai_e is not None else None

            if ai_hint:
                hint = ai_hint
            if ai_start is not None and ai_end is not None and ai_end > ai_start:
                start, end = ai_start, ai_end

            try:
                update = {}
                if hint:
                    update[f"ai_slot_hints.{str(slot_key)}"] = hint
                if start is not None and end is not None:
                    update[f"ai_segment_map.{str(slot_key)}"] = {
                        "start": float(start),
                        "end": float(end),
                        "start_ts": float(start),
                        "end_ts": float(end),
                        "evidence": (data.get("evidence") or "").strip(),
                        "hint": hint,
                    }
                if update:
                    db.parsons_tasks.update_one({"_id": task.get("_id")}, {"$set": update})
            except Exception:
                pass

            try:
                sub_path = ((task.get("prompt_source") or {}).get("subtitle_path") or "").strip()
                if sub_path and start is not None and end is not None:
                    sub_text = read_subtitle_text(sub_path)
                    segs = parse_srt_segments(sub_text)
                    subtitle_context = extract_context_around(segs, start, end, window=5)
            except Exception:
                pass

        except Exception:
            pass

    return hint, start, end, subtitle_context


# 給 submit() 用的 AI 回饋生成（不保證一定有，盡量有）
def _generate_submit_ai_feedback(task_doc: dict, answer_ids: list) -> dict:
    """
    學生 submit 後，直接產生 AI 診斷回饋。
    目標：
    1. 不讓 submit 因 AI 回饋失敗而爆掉
    2. 優先回傳可顯示的 AI 訊息
    3. 若 AI 或字幕失敗，仍回傳保底診斷內容
    """
    try:
        parsed = t5doc_to_parsons_task(task_doc)
        template_slots = parsed.get("template_slots") or []
        pool = parsed.get("pool") or []
        question_text = str(task_doc.get("question_text") or "")
        unit = str(task_doc.get("unit") or "")

        pool_map = {str(b.get("id")): b for b in pool if b.get("id") is not None}

        expected_ids = []
        for s in template_slots:
            eid = s.get("expected_id")
            expected_ids.append(str(eid) if eid is not None else "")

        aligned = list(answer_ids or [])
        if len(aligned) < len(expected_ids):
            aligned += [None] * (len(expected_ids) - len(aligned))

        def _norm_line(s: str) -> str:
            try:
                return (s or "").replace("\t", "    ").strip()
            except Exception:
                return (s or "").strip()

        wrong_slots = []
        for i in range(len(expected_ids)):
            aid = str(aligned[i]) if aligned[i] is not None else ""
            eid = str(expected_ids[i])

            if aid == eid:
                continue

            a_text = str(pool_map.get(aid, {}).get("text", "") or "")
            e_text = str(pool_map.get(eid, {}).get("text", "") or "")

            # 文字相同也視為正確
            if _norm_line(a_text) and _norm_line(a_text) == _norm_line(e_text):
                continue

            wrong_slots.append(i)

        extra_wrong = max(0, len(answer_ids or []) - len(expected_ids))
        is_correct = (len(wrong_slots) == 0 and extra_wrong == 0)

        if is_correct:
            return {
                "is_correct": True,
                "error_code": None,
                "wrong_slots": [],
                "diagnosis_summary": "作答正確",
                "feedback": {
                    "feedback_type": "correct",
                    "concept_explanation": "答對了，你已掌握這題的程式流程與區塊排列邏輯。",
                    "possible_causes": [],
                    "impact": "",
                    "guiding_question": ""
                },
                "recommended_review": {
                    "start": None,
                    "end": None,
                    "reason": ""
                }
            }

        wrong_label = "、".join([f"第{i+1}格" for i in wrong_slots]) if wrong_slots else "部分格子"

        expected_blocks = []
        for eid in expected_ids:
            b = pool_map.get(str(eid), {})
            expected_blocks.append({
                "id": str(eid),
                "text": str(b.get("text", "") or ""),
                "type": str(b.get("type", "") or "")
            })

        student_blocks = []
        for aid in aligned[:len(expected_ids)]:
            bid = str(aid) if aid is not None else ""
            b = pool_map.get(bid, {})
            student_blocks.append({
                "id": bid,
                "text": str(b.get("text", "") or ""),
                "type": str(b.get("type", "") or "")
            })

        subtitle_text_used = ""
        try:
            subtitle_path = ((task_doc.get("prompt_source") or {}).get("subtitle_path") or "").strip()
            if subtitle_path:
                raw_text = read_subtitle_text(subtitle_path)
                segs = parse_srt_segments(raw_text)
                subtitle_text_used = compact_segments_for_prompt(segs, max_chars=1800)
        except Exception:
            subtitle_text_used = ""

        # 若未開 AI，直接走保底
        if not ai_enabled():
            return {
                "is_correct": False,
                "error_code": "AI_DISABLED",
                "wrong_slots": wrong_slots,
                "diagnosis_summary": f"學生主要在 {wrong_label} 的排列與預期不一致",
                "feedback": {
                    "feedback_type": "generic_fallback",
                    "concept_explanation": f"你目前在 {wrong_label} 的程式區塊排列與題目要求不一致。",
                    "possible_causes": [
                        "可能把輸入、處理、輸出的先後順序放錯",
                        "可能沒有正確理解這格在整體流程中的角色"
                    ],
                    "impact": "若程式區塊順序錯誤，程式可能無法依照題目要求執行。",
                    "guiding_question": "請想想這一格是在讀取資料、處理資料，還是輸出結果？"
                },
                "recommended_review": {
                    "start": None,
                    "end": None,
                    "reason": ""
                }
            }

        model = (os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()

        prompt = f"""
你是一位 Python Parsons 題診斷助教。
請根據題目、正解區塊、學生作答區塊，產生「繁體中文」診斷回饋。

【規則】
1. 不可直接給完整正解
2. 要說明學生主要錯誤概念
3. 要用教學式語氣
4. 若有字幕摘要，可輔助判斷
5. 回傳純 JSON，不要多餘文字

【題目資訊】
unit: {unit}
question_text: {question_text}
wrong_slots: {json.dumps(wrong_slots, ensure_ascii=False)}

【正解 blocks】
{json.dumps(expected_blocks, ensure_ascii=False, indent=2)}

【學生作答 blocks】
{json.dumps(student_blocks, ensure_ascii=False, indent=2)}

【字幕摘要】
{subtitle_text_used}

請輸出：
{{
  "diagnosis_summary": "一句話摘要主要問題",
  "concept_explanation": "用 2~4 句說明學生主要錯誤概念",
  "possible_causes": ["原因1", "原因2"],
  "impact": "此錯誤可能造成什麼影響",
  "guiding_question": "引導學生反思的問題",
  "recommended_review": {{
    "start": null,
    "end": null,
    "reason": ""
  }}
}}
""".strip()

        ai_data = parsons_ai.call_openai_json(
            model=model,
            system="你是一位 Python Parsons 題診斷助教。只輸出 JSON。",
            user=prompt
        ) or {}
        recommended_review = ai_data.get("recommended_review") or {}

        start = recommended_review.get("start")
        end = recommended_review.get("end")

        try:
            start = float(start) if start is not None else None
        except Exception:
            start = None

        try:
            end = float(end) if end is not None else None
        except Exception:
            end = None

        return {
            "is_correct": False,
            "error_code": "AI_DIAGNOSIS",
            "wrong_slots": wrong_slots,
            "diagnosis_summary": str(ai_data.get("diagnosis_summary") or f"學生主要在 {wrong_label} 的排列與預期不一致"),
            "feedback": {
                "feedback_type": "ai_diagnostic",
                "concept_explanation": str(ai_data.get("concept_explanation") or f"你目前在 {wrong_label} 的程式流程理解上有些混淆。"),
                "possible_causes": ai_data.get("possible_causes") if isinstance(ai_data.get("possible_causes"), list) else [],
                "impact": str(ai_data.get("impact") or ""),
                "guiding_question": str(ai_data.get("guiding_question") or "")
            },
            "recommended_review": {
                "start": start,
                "end": end,
                "reason": str(recommended_review.get("reason") or "")
            }
        }

    except Exception as e:
        return {
            "is_correct": False,
            "error_code": "SUBMIT_AI_DIAGNOSIS_ERROR",
            "wrong_slots": [],
            "diagnosis_summary": f"AI 診斷暫時失敗：{str(e)}",
            "feedback": {
                "feedback_type": "submit_ai_error_fallback",
                "concept_explanation": "系統目前無法產生完整診斷回饋，請先檢查程式區塊的順序與角色。",
                "concept_hint": "請先確認該格在程式流程中的角色與資料型態。",
                "possible_causes": [],
                "impact": "",
                "guiding_question": "哪一格應該先執行？哪一格是在使用前面產生的資料？",
                "reflection_questions": [
                    "這一格的目的，是接收輸入，還是設定初始值？",
                    "這個值在後面會如何被使用？",
                    "用目前的寫法，能順利完成運算嗎？"
                ]
            },
            "recommended_review": {
                "start": None,
                "end": None,
                "reason": ""
            }
        }
    


def _get_blocks_by_ids(task_doc: dict, block_ids: list):
    parsed = t5doc_to_parsons_task(task_doc)
    pool = parsed.get("pool") or []
    pool_map = {str(b.get("id")): b for b in pool if b.get("id") is not None}

    out = []
    for bid in block_ids or []:
        b = pool_map.get(str(bid), {})
        out.append({
            "id": str(bid),
            "text": str(b.get("text", "") or ""),
            "type": str(b.get("type", "") or ""),
        })
    return out

def _normalize_line_for_compare(s: str) -> str:
    """
    比對程式行文字時使用：
    - tab 轉空白
    - 忽略前後空白
    """
    try:
        s = (s or "").replace("\t", "    ")
        return s.strip()
    except Exception:
        return (s or "").strip()


def _get_wrong_slots_and_core_compare(task_doc: dict, student_order: list):
    parsed = t5doc_to_parsons_task(task_doc)
    expected_ids = _get_expected_ids_from_task(task_doc)
    pool = parsed.get("pool") or []
    pool_map = {str(b.get("id")): b for b in pool if b.get("id") is not None}

    aligned = list(student_order or [])
    if len(aligned) < len(expected_ids):
        aligned = aligned + [None] * (len(expected_ids) - len(aligned))

    wrong_slots = []
    for i in range(len(expected_ids)):
        aid = str(aligned[i]) if aligned[i] is not None else ""
        eid = str(expected_ids[i])

        if aid == eid:
            continue

        a_text = str(pool_map.get(aid, {}).get("text", "") or "")
        e_text = str(pool_map.get(eid, {}).get("text", "") or "")

        if _normalize_line_for_compare(a_text) and _normalize_line_for_compare(a_text) == _normalize_line_for_compare(e_text):
            continue

        wrong_slots.append(i)

    extra_wrong = max(0, len(student_order or []) - len(expected_ids))
    is_correct = (len(wrong_slots) == 0 and extra_wrong == 0)

    return {
        "is_correct": is_correct,
        "wrong_slots": wrong_slots,
        "expected_ids": expected_ids,
        "aligned_student_ids": aligned,
        "extra_wrong": extra_wrong
    }


def _get_subtitle_context_for_ai(task_doc: dict) -> dict:
    ai_align = task_doc.get("ai_subtitle_alignment") or {}
    source_subtitle = ai_align.get("source_subtitle") or {}
    prompt_source = task_doc.get("prompt_source") or {}

    subtitle_text_used = (
        ai_align.get("subtitle_text_used")
        or source_subtitle.get("text_used")
        or task_doc.get("subtitle_text_used")
        or ""
    )

    subtitle_range = ai_align.get("subtitle_range") or task_doc.get("subtitle_range") or {}
    ai_segment_map = ai_align.get("ai_segment_map") or task_doc.get("ai_segment_map") or {}
    ai_slot_hints = ai_align.get("ai_slot_hints") or task_doc.get("ai_slot_hints") or {}
    ai_segments_compact = task_doc.get("ai_segments_compact") or ""

    if not subtitle_text_used:
        subtitle_path = (prompt_source.get("subtitle_path") or "").strip()
        if subtitle_path:
            try:
                raw_text = read_subtitle_text(subtitle_path)
                segs = parse_srt_segments(raw_text)
                print(segs[:3])
                subtitle_text_used = compact_segments_for_prompt(segs, max_chars=2000)
            except Exception:
                subtitle_text_used = ""

    return {
        "subtitle_text_used": subtitle_text_used,
        "subtitle_range": subtitle_range,
        "ai_segment_map": ai_segment_map,
        "ai_slot_hints": ai_slot_hints,
        "ai_segments_compact": ai_segments_compact,
    }


def _build_generic_fallback_feedback(task_doc: dict, compare_result: dict) -> dict:
    wrong_slots = compare_result.get("wrong_slots", [])
    wrong_label = "、".join([f"第{i+1}格" for i in wrong_slots]) if wrong_slots else "部分格子"

    subtitle_ctx = _get_subtitle_context_for_ai(task_doc)
    subtitle_text_used = subtitle_ctx.get("subtitle_text_used", "")

    concept_explanation = f"這次作答與題目的預期排列不一致，主要錯誤出現在 {wrong_label}。"
    if subtitle_text_used:
        concept_explanation += " 建議對照影片字幕與題目要求，重新確認每個程式區塊的先後順序與作用。"

    reflection_questions = [
        "這一格的目的，是接收輸入，還是設定初始值？",
        "這個值在後面會如何被使用？",
        "用目前的寫法，能順利完成運算嗎？",
    ]

    return {
        "feedback_type": "generic_ai_fallback",
        "concept_explanation": concept_explanation,
        "concept_hint": concept_explanation,
        "possible_causes": [
            "可能尚未掌握各程式區塊的先後執行關係",
            "可能知道題目要完成的功能，但未正確對應到每一格的程式碼位置",
            "可能將讀取、判斷、計算或輸出等步驟的角色混淆"
        ],
        "impact": "若程式區塊順序或位置錯誤，程式可能無法依照預期流程執行，造成輸出結果錯誤或邏輯不完整。",
        "guiding_question": "你可以回想一下：哪一格應該先執行？哪一格是在使用前面產生的資料或結果？",
        "reflection_questions": reflection_questions,
    }


def _build_correct_feedback() -> dict:
    return {
        "feedback_type": "correct",
        "concept_explanation": "答對了，你已掌握這題的程式流程與區塊排列邏輯。",
        "concept_hint": "",
        "possible_causes": [],
        "impact": "",
        "guiding_question": "",
        "reflection_questions": []
    }

def _generate_submit_ai_feedback(task_doc: dict, answer_ids: list) -> dict:
    try:
        compare_result = _get_wrong_slots_and_core_compare(task_doc, answer_ids)
        return _call_ai_for_generic_diagnosis(task_doc, compare_result)
    except Exception as e:
        import traceback
        print("\n========== SUBMIT AI DIAGNOSIS ERROR ==========")
        print("error =", repr(e))
        traceback.print_exc()
        print("===============================================\n")

        wrong_slots = []
        try:
            compare_result = _get_wrong_slots_and_core_compare(task_doc, answer_ids)
            wrong_slots = compare_result.get("wrong_slots", []) or []
        except Exception:
            pass

        return {
            "is_correct": False,
            "error_code": "SUBMIT_AI_DIAGNOSIS_ERROR",
            "wrong_slots": wrong_slots,
            "diagnosis_summary": f"AI 診斷暫時失敗：{str(e)}",
            "feedback": {
                "feedback_type": "submit_ai_error_fallback",
                "concept_explanation": "系統目前無法產生完整診斷回饋，請先檢查程式區塊的順序與角色。",
                "possible_causes": [],
                "impact": "",
                "guiding_question": "哪一格應該先執行？哪一格是在使用前面產生的資料？"
            },
            "recommended_review": {
                "start": None,
                "end": None,
                "reason": ""
            }
        }



def _call_ai_for_generic_diagnosis(task_doc: dict, compare_result: dict) -> dict:
    if compare_result.get("is_correct"):
        return {
            "is_correct": True,
            "error_code": None,
            "wrong_slots": [],
            "diagnosis_summary": "作答正確",
            "feedback": _build_correct_feedback(),
            "recommended_review": {
                "start": None,
                "end": None,
                "reason": ""
            }
        }

    parsed = t5doc_to_parsons_task(task_doc)
    unit = str(task_doc.get("unit", "") or "")
    level = str(task_doc.get("level", "") or "")
    question_text = str(task_doc.get("question_text", "") or "")

    expected_ids = compare_result.get("expected_ids", []) or []
    aligned_student_ids = compare_result.get("aligned_student_ids", []) or []
    wrong_slots = compare_result.get("wrong_slots", []) or []

    expected_blocks = _get_blocks_by_ids(task_doc, expected_ids)
    student_blocks = _get_blocks_by_ids(task_doc, [x for x in aligned_student_ids if x is not None])

    subtitle_ctx = _get_subtitle_context_for_ai(task_doc)
    subtitle_text_used = subtitle_ctx.get("subtitle_text_used", "")
    subtitle_range = subtitle_ctx.get("subtitle_range", {}) or {}
    ai_segment_map = subtitle_ctx.get("ai_segment_map", {}) or {}
    ai_slot_hints = subtitle_ctx.get("ai_slot_hints", {}) or {}
    ai_segments_compact = subtitle_ctx.get("ai_segments_compact", "") or ""

    prompt = f"""
你是一位 Python Parsons 題診斷助教。
請根據「固定題內容、正解區塊、學生作答順序、字幕教學內容」，
判斷學生主要錯在哪幾格，並給出概念導向的診斷式回饋。

【重要規則】
1. 不可直接給完整正解。
2. 不可逐字要求學生把哪一行放到哪一格。
3. 要用繁體中文。
4. 要用教學方式解釋，不要像編譯器。
5. 若 wrong_slots 已提供，優先以這些格數為主分析。
6. 若字幕內容可協助判斷，請納入解釋。
7. 回傳純 JSON，不要多餘文字。
8. 診斷內容要能套用到以下模板段落：診斷結果、概念提示、請你反思(3題)。
9. reflection_questions 必須剛好 3 題，每題一句。

【題目資訊】
unit: {unit}
question_text: {question_text}

【正解 blocks】
{json.dumps(expected_blocks, ensure_ascii=False, indent=2)}

【學生作答 blocks】
{json.dumps(student_blocks, ensure_ascii=False, indent=2)}

【系統初步比對】
wrong_slots: {json.dumps(wrong_slots, ensure_ascii=False)}

【字幕摘要】
subtitle_text_used:
{subtitle_text_used}

subtitle_range:
{json.dumps(subtitle_range, ensure_ascii=False)}

ai_segment_map:
{json.dumps(ai_segment_map, ensure_ascii=False)}

ai_slot_hints:
{json.dumps(ai_slot_hints, ensure_ascii=False)}

ai_segments_compact:
{ai_segments_compact}

請輸出以下 JSON：
{{
  "is_correct": false,
  "wrong_slots": [0],
  "error_concept": "用一句話描述錯誤概念",
  "diagnosis_summary": "用一句話摘要學生主要問題",
    "concept_hint": "給學生的概念提示（1~2句）",
  "possible_causes": ["原因1", "原因2"],
  "impact": "這類錯誤可能造成什麼影響",
  "guiding_question": "引導學生反思的問題",
    "reflection_questions": [
        "反思問題1",
        "反思問題2",
        "反思問題3"
    ],
  "recommended_review": {{
    "start": null,
    "end": null,
    "reason": "若能從字幕判斷，請說明為何建議回看該片段"
  }}
}}
""".strip()

    try:
        if not ai_enabled():
            raise RuntimeError("AI not enabled")

        model = (os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()
        ai_data = parsons_ai.call_openai_json(
            model=model,
            system="你是一位 Python Parsons 題診斷助教。",
            user=prompt
        ) or {}

        ai_wrong_slots = ai_data.get("wrong_slots")
        if not isinstance(ai_wrong_slots, list) or len(ai_wrong_slots) == 0:
            ai_wrong_slots = wrong_slots

        ai_reflections = ai_data.get("reflection_questions") if isinstance(ai_data.get("reflection_questions"), list) else []
        ai_reflections = [str(x or "").strip() for x in ai_reflections if str(x or "").strip()]
        if len(ai_reflections) < 3:
            gq = str(ai_data.get("guiding_question", "") or "").strip()
            causes = ai_data.get("possible_causes") if isinstance(ai_data.get("possible_causes"), list) else []
            c1 = str(causes[0]).strip() if len(causes) >= 1 else ""
            c2 = str(causes[1]).strip() if len(causes) >= 2 else ""
            fallback_qs = [
                gq or "這一格的目的，是接收輸入，還是設定初始值？",
                (f"你覺得這次是否出現這個狀況：{c1}？" if c1 else "這個值在後面會如何被使用？"),
                (f"如果改掉「{c2}」，目前流程會更接近正確解法嗎？" if c2 else "用目前的寫法，能順利完成運算嗎？"),
            ]
            for q in fallback_qs:
                if len(ai_reflections) >= 3:
                    break
                if q and q not in ai_reflections:
                    ai_reflections.append(q)
        ai_reflections = ai_reflections[:3]

        recommended_review = ai_data.get("recommended_review") or {}
        start = recommended_review.get("start")
        end = recommended_review.get("end")

        try:
            start = float(start) if start is not None else None
        except Exception:
            start = None

        try:
            end = float(end) if end is not None else None
        except Exception:
            end = None

        return {
            "is_correct": False,
            "error_code": "GENERIC_AI_DIAGNOSIS",
            "wrong_slots": ai_wrong_slots,
            "diagnosis_summary": str(ai_data.get("diagnosis_summary", "") or "學生作答與預期程式流程不一致"),
            "feedback": {
                "feedback_type": "generic_ai_diagnostic",
                "concept_explanation": str(ai_data.get("error_concept", "") or "這次的問題主要和程式區塊的排列邏輯有關。"),
                "concept_hint": str(ai_data.get("concept_hint", "") or ai_data.get("error_concept", "") or "請先確認該格在程式流程中的角色與資料型態。"),
                "possible_causes": ai_data.get("possible_causes", []) if isinstance(ai_data.get("possible_causes"), list) else [],
                "impact": str(ai_data.get("impact", "") or ""),
                "guiding_question": str(ai_data.get("guiding_question", "") or ""),
                "reflection_questions": ai_reflections,
            },
            "recommended_review": {
                "start": start,
                "end": end,
                "reason": str(recommended_review.get("reason", "") or "")
            }
        }

    except Exception as e:
        import traceback
        print("\n========== AI GENERIC DIAGNOSIS ERROR ==========")
        print("error =", repr(e))
        traceback.print_exc()
        print("===============================================\n")

        fallback = _build_generic_fallback_feedback(task_doc, compare_result)
        return {
            "is_correct": False,
            "error_code": "GENERIC_AI_FALLBACK",
            "wrong_slots": wrong_slots,
            "diagnosis_summary": f"AI 診斷失敗，改用保底回饋：{str(e)}",
            "feedback": fallback,
            "recommended_review": {
                "start": None,
                "end": None,
                "reason": ""
            }
        }



def diagnose_fixed_task_attempt(task_id: str, student_order: list):
    def _build_pool_map(task):
        pool = task.get("pool", [])
        return {str(b.get("id", "")): b for b in pool}
    
    def _normalize_text(s: str) -> str:
        return (s or "").strip()
    
    task = db.parsons_tasks.find_one({"_id": ObjectId(task_id)})
    if not task:
        raise ValueError("task not found")

    solution_order = task.get("solution_order", [])
    pool_map = _build_pool_map(task)

    if student_order == solution_order:
        return {
            "is_correct": True,
            "error_code": None,
            "wrong_slots": [],
            "diagnosis_summary": "作答正確"
        }

    student_blocks = [pool_map.get(x, {}) for x in student_order]
    student_texts = [_normalize_text(x.get("text", "")) for x in student_blocks]

    # ===== U1-IO: 順序錯誤 =====
    if len(student_texts) >= 2:
        first_text = student_texts[0]
        second_text = student_texts[1]

        if "print" in first_text and "input" in second_text:
            return {
                "is_correct": False,
                "error_code": "IO_ORDER_01",
                "wrong_slots": [0, 1],
                "diagnosis_summary": "學生將輸出放在讀取輸入之前"
            }

    # ===== U1-IO: input 沒存變數 =====
    for idx, text in enumerate(student_texts):
        if "input(" in text and "=" not in text:
            return {
                "is_correct": False,
                "error_code": "IO_VAR_01",
                "wrong_slots": [idx],
                "diagnosis_summary": "學生使用 input() 但沒有將結果存入變數"
            }

    # ===== U1-IO: print 使用問題 =====
    for idx, text in enumerate(student_texts):
        if "print" in text:
            if "hello" not in text and "name" not in text:
                return {
                    "is_correct": False,
                    "error_code": "IO_OUTPUT_01",
                    "wrong_slots": [idx],
                    "diagnosis_summary": "學生的輸出內容與題目需求不一致"
                }

    # ===== U1-IO: 變數名稱不一致 =====
    input_var_name = None
    output_var_name = None

    for text in student_texts:
        if "=" in text and "input(" in text:
            left = text.split("=", 1)[0].strip()
            if left:
                input_var_name = left

        if "print" in text:
            if "name" in text:
                output_var_name = "name"

    if input_var_name and output_var_name and input_var_name != output_var_name:
        return {
            "is_correct": False,
            "error_code": "IO_VAR_NAME_01",
            "wrong_slots": [],
            "diagnosis_summary": "學生前後使用的變數名稱不一致"
        }

    # ===== fallback =====
    return {
        "is_correct": False,
        "error_code": "IO_CONCEPT_01",
        "wrong_slots": [],
        "diagnosis_summary": "學生的輸入輸出流程與預期概念不一致"
    }