import os
import re
import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Tuple, Optional

from flask import Blueprint, request, jsonify
from bson import ObjectId
from bson.errors import InvalidId

from ..db import db

# OpenAI SDK
try:
    from openai import OpenAI
except Exception:
    OpenAI = None


parsons_bp = Blueprint("parsons", __name__)


# =========================
# Utils
# =========================
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def get_project_root() -> str:
    return os.getcwd()


def read_subtitle_text(subtitle_path: str) -> str:
    if not subtitle_path:
        return ""
    full = os.path.join(get_project_root(), subtitle_path.replace("/", os.sep))
    if not os.path.exists(full):
        return ""
    try:
        with open(full, "r", encoding="utf-8-sig", errors="ignore") as f:
            return f.read()
    except Exception:
        try:
            with open(full, "r", encoding="cp950", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""


def env_snapshot() -> Dict[str, Any]:
    return {
        "AI_ENABLED": os.getenv("AI_ENABLED"),
        "OPENAI_MODEL": os.getenv("OPENAI_MODEL"),
        "OPENAI_API_KEY_exists": bool(os.getenv("OPENAI_API_KEY")),
    }


def ai_enabled() -> bool:
    v = (os.getenv("AI_ENABLED") or "").strip().lower()
    return v in ["1", "true", "yes", "y", "on"]


def get_openai_client() -> "OpenAI":
    if OpenAI is None:
        raise RuntimeError("openai 套件未安裝，請先 pip install openai")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 未設定（請在 .env 設定 OPENAI_API_KEY=...）")
    return OpenAI(api_key=api_key)


def safe_json_loads(s: str) -> Optional[dict]:
    try:
        return json.loads(s)
    except Exception:
        return None


def strip_srt_noise(text: str) -> str:
    if not text:
        return ""
    lines = []
    for ln in text.splitlines():
        t = ln.strip()
        if not t:
            continue
        if re.fullmatch(r"\d+", t):
            continue
        if "-->" in t:
            continue
        if re.search(r"\d{2}:\d{2}:\d{2}", t):
            continue
        lines.append(t)
    return "\n".join(lines)


# =========================
# SRT parser (for AI time axis)
# =========================
# [新增] 解析 SRT，保留時間戳，讓 AI 能回傳 start/end 秒數
_SRT_TIME_RE = re.compile(
    r"(?P<h1>\d{2}):(?P<m1>\d{2}):(?P<s1>\d{2})[,.](?P<ms1>\d{1,3})\s*-->\s*"
    r"(?P<h2>\d{2}):(?P<m2>\d{2}):(?P<s2>\d{2})[,.](?P<ms2>\d{1,3})"
)

def _to_sec(h: str, m: str, s: str, ms: str) -> float:
    ms_i = int(ms)
    # ms 可能是 1~3 位
    if len(ms) == 1:
        ms_i *= 100
    elif len(ms) == 2:
        ms_i *= 10
    return int(h) * 3600 + int(m) * 60 + int(s) + ms_i / 1000.0

def parse_srt_segments(srt_text: str):
    """回傳 [{start,end,text}]，start/end 為秒數（float）"""
    if not srt_text:
        return []
    segs = []
    cur = None
    buf = []
    for raw in srt_text.splitlines():
        line = raw.strip("\ufeff").rstrip()
        m = _SRT_TIME_RE.search(line)
        if m:
            # flush previous
            if cur and buf:
                cur["text"] = " ".join([t.strip() for t in buf if t.strip()]).strip()
                segs.append(cur)
            cur = {
                "start": _to_sec(m.group("h1"), m.group("m1"), m.group("s1"), m.group("ms1")),
                "end": _to_sec(m.group("h2"), m.group("m2"), m.group("s2"), m.group("ms2")),
                "text": ""
            }
            buf = []
            continue

        # index line
        if re.fullmatch(r"\d+", line or ""):
            continue

        # blank line => flush
        if not line:
            if cur and buf:
                cur["text"] = " ".join([t.strip() for t in buf if t.strip()]).strip()
                segs.append(cur)
            cur = None
            buf = []
            continue

        if cur is not None:
            buf.append(line)

    if cur and buf:
        cur["text"] = " ".join([t.strip() for t in buf if t.strip()]).strip()
        segs.append(cur)

    # 去掉空白 text
    segs = [s for s in segs if (s.get("text") or "").strip()]
    return segs

def compact_segments_for_prompt(segs, max_chars: int = 12000) -> str:
    """把 segs 壓成帶時間戳的文字，控制長度"""
    if not segs:
        return ""
    out = []
    total = 0
    for s in segs:
        start = float(s.get("start") or 0)
        end = float(s.get("end") or 0)
        text = (s.get("text") or "").strip()
        if not text:
            continue
        line = f"[{start:.1f}-{end:.1f}] {text}"
        if total + len(line) + 1 > max_chars:
            break
        out.append(line)
        total += len(line) + 1
    return "\n".join(out)

def extract_context_around(segs, center_start: float, center_end: float, window: int = 6) -> str:
    """取出目標片段前後 window 個字幕片段作為上下文"""
    if not segs:
        return ""
    # 找最接近的 index
    idx = 0
    best = 1e18
    mid = (float(center_start) + float(center_end)) / 2.0
    for i, s in enumerate(segs):
        m = (float(s.get("start") or 0) + float(s.get("end") or 0)) / 2.0
        d = abs(m - mid)
        if d < best:
            best = d
            idx = i
    a = max(0, idx - window)
    b = min(len(segs), idx + window + 1)
    return compact_segments_for_prompt(segs[a:b], max_chars=4000)


def log_event(event_type: str, **payload):
    try:
        db.events.insert_one({"type": event_type, "payload": payload, "created_at": now_utc()})
    except Exception:
        pass


def maybe_oid(s: str) -> Optional[ObjectId]:
    try:
        return ObjectId(s)
    except Exception:
        return None


def normalize_video_id(v) -> str:
    """把 video_id 無論是 ObjectId 或字串，都轉成字串回給前端"""
    if v is None:
        return ""
    return str(v)


# =========================
# [新增] Subtitle chooser（優先使用 subtitles collection 最新校正版）
# =========================
def pick_latest_subtitle_path(video_doc: dict, video_id_str: str) -> str:
    """
    目的：避免一直讀 videos.subtitle_path 造成空字幕 -> fallback
    策略：
    1) 優先找 subtitles collection 裡該 video 的最新版本（version 最大，其次 created_at 最新）
    2) 找不到才退回 videos.subtitle_path
    """
    # 影片 ObjectId（優先用 video_doc._id，否則用傳入字串轉 oid）
    vid_oid = video_doc.get("_id") or maybe_oid(video_id_str)

    # 先找 subtitles 最新版本
    try:
        if vid_oid:
            sub_doc = db.subtitles.find_one(
                {"video_id": vid_oid},
                sort=[("version", -1), ("created_at", -1)]
            )
            if sub_doc and (sub_doc.get("path") or "").strip():
                return (sub_doc.get("path") or "").strip()
    except Exception:
        pass

    # 若 subtitles 的 video_id 有存成字串（保守兼容）
    try:
        if vid_oid:
            sub_doc2 = db.subtitles.find_one(
                {"video_id": str(vid_oid)},
                sort=[("version", -1), ("created_at", -1)]
            )
            if sub_doc2 and (sub_doc2.get("path") or "").strip():
                return (sub_doc2.get("path") or "").strip()
    except Exception:
        pass

    # fallback：回到 videos.subtitle_path
    return (video_doc.get("subtitle_path", "") or "").strip()


# =========================
# Fallback generator
# =========================
def simple_fallback_generate(sub_text: str, unit: str, video_title: str, level: str = "L1") -> Dict[str, Any]:
    question_text = "（備援題目）請完成一段程式：根據題目需求輸入資料並輸出結果。"
    solution_lines = ["x = int(input())", "y = int(input())", "print(x + y)"]
    distractor_lines = ["x = input()", "y = input()", "print(x - y)", "print(x * y)"]

    solution_blocks = [{"id": f"b{i+1}", "text": line, "type": "core"} for i, line in enumerate(solution_lines)]
    distractor_blocks = [{"id": f"d{i+1}", "text": line, "type": "distractor"} for i, line in enumerate(distractor_lines)]
    pool = distractor_blocks + solution_blocks
    template_slots = [{"label": f"請放入正確的第{i+1}行", "slot": str(i)} for i in range(len(solution_lines))]

    return {
        "question_text": question_text,
        "solution_blocks": solution_blocks,
        "distractor_blocks": distractor_blocks,
        "pool": pool,
        "template_slots": template_slots,
        "ai_feedback": {"general": "（AI 生成失敗，使用系統備援題目）", "common_mistakes": [], "hints": []},
    }


# =========================
# OpenAI generator
# =========================
def ai_generate_parsons_from_subtitle(subtitle_text: str, unit: str, video_title: str, level: str = "L1") -> Dict[str, Any]:
    """
    [修改] V1.5：
    - 仍從字幕生成 Parsons 題目
    - 額外產出每一格建議回看時間軸（start/end 秒）與錯誤提示（slot_hint）
    - 不改既有 student 端路由/格式：這些會存進 parsons_tasks，submit 時再回傳
    """
    if not ai_enabled():
        raise RuntimeError("AI_ENABLED is false -> skip OpenAI")

    if not subtitle_text:
        raise RuntimeError("subtitle_text is empty")

    # [新增] 解析 SRT（保留時間戳）給 AI 做時間軸對應
    segs = parse_srt_segments(subtitle_text)
    segs_compact = compact_segments_for_prompt(segs, max_chars=12000)

    # [保留] 也保留乾淨版字幕（避免純時間戳太吵）
    cleaned = strip_srt_noise(subtitle_text)
    if not cleaned:
        raise RuntimeError("subtitle_text is empty after cleaning")

    model = (os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()

    prompt = f"""
你是一位 Python 程式設計助教，要根據「影片字幕（含時間戳）」生成一題 Parsons 題目（程式重組），難度={level}。
請輸出「純 JSON」（不能有多餘文字），格式如下：

{{
  "question_text": "題目敘述（繁中）",
  "solution_lines": ["正確程式第1行", "第2行", "...（3~7行）"],
  "distractor_lines": ["干擾行1", "干擾行2", "...（2~4行）"],
  "template_slot_labels": ["第1格的中文提示", "第2格的中文提示", "...（對應 solution_lines 行數）"],

  "segment_map": [
    {{"slot_index": 0, "start": 12.3, "end": 24.8, "evidence": "字幕關鍵句（繁中，可簡短）"}},
    {{"slot_index": 1, "start": 25.0, "end": 38.6, "evidence": "..."}}
  ],

  "slot_hints": [
    {{"slot_index": 0, "hint": "如果第1格錯，應該提醒學生什麼（繁中，1~2句）"}},
    {{"slot_index": 1, "hint": "..." }}
  ],

  "ai_feedback": {{
    "general": "整體回饋（繁中，1~2句）",
    "common_mistakes": ["常見錯誤1", "常見錯誤2"],
    "hints": ["提示1", "提示2"]
  }}
}}

限制：
- solution_lines 必須是可執行且一致的 Python 程式片段
- distractor_lines 要像學生常犯錯
- template_slot_labels 要對應每一行的語意
- segment_map 的 start/end 必須來自字幕時間戳的合理區間（秒數），每格都要給
- slot_hints 每格都要給（用來 submit 時回饋）
- 全部用繁體中文
- 不要輸出 Markdown，只要 JSON

影片資訊：
- unit: {unit}
- title: {video_title}

字幕（含時間戳）如下（格式：[start-end] text）：
{segs_compact}

（若需要純字幕參考）：
{cleaned[:4000]}
""".strip()

    client = get_openai_client()
    resp = client.responses.create(model=model, input=prompt)

    text = (resp.output_text or "").strip()
    data = safe_json_loads(text)
    if not data:
        raise RuntimeError("OpenAI 回傳不是合法 JSON")

    question_text = (data.get("question_text") or "").strip()
    solution_lines = data.get("solution_lines") or []
    distractor_lines = data.get("distractor_lines") or []
    labels = data.get("template_slot_labels") or []
    ai_fb = data.get("ai_feedback") or {}

    if not question_text or not solution_lines:
        raise RuntimeError("OpenAI JSON 缺少 question_text 或 solution_lines")

    solution_blocks = [{"id": f"b{i+1}", "text": line, "type": "core"} for i, line in enumerate(solution_lines)]
    distractor_blocks = [{"id": f"d{i+1}", "text": line, "type": "distractor"} for i, line in enumerate(distractor_lines)]
    pool = distractor_blocks + solution_blocks

    if not labels or len(labels) != len(solution_lines):
        labels = [f"請放入正確的第{i+1}行" for i in range(len(solution_lines))]

    template_slots = [{"label": labels[i], "slot": str(i)} for i in range(len(solution_lines))]

    # [新增] V1.5：segment_map 與 slot_hints（以 slot index 對齊）
    seg_map_in = data.get("segment_map") or []
    hint_in = data.get("slot_hints") or []

    seg_map = {}
    for it in seg_map_in:
        try:
            si = int(it.get("slot_index"))
            s = float(it.get("start"))
            e = float(it.get("end"))
            if si < 0 or si >= len(solution_lines):
                continue
            if e <= s:
                continue
            seg_map[str(si)] = {
                "start": s,
                "end": e,
                "evidence": (it.get("evidence") or "").strip(),
            }
        except Exception:
            continue

    slot_hints = {}
    for it in hint_in:
        try:
            si = int(it.get("slot_index"))
            if si < 0 or si >= len(solution_lines):
                continue
            slot_hints[str(si)] = (it.get("hint") or "").strip()
        except Exception:
            continue

    return {
        "question_text": question_text,
        "solution_blocks": solution_blocks,
        "distractor_blocks": distractor_blocks,
        "pool": pool,
        "template_slots": template_slots,
        "ai_feedback": {
            "general": (ai_fb.get("general") or "").strip(),
            "common_mistakes": ai_fb.get("common_mistakes") or [],
            "hints": ai_fb.get("hints") or [],
        },
        # [新增] V1.5
        "ai_segment_map": seg_map,
        "ai_slot_hints": slot_hints,
        "ai_segments_compact": segs_compact,
    }



# =========================
# Create task
# =========================
def create_task_for_video(video_doc: dict, video_id_str: str, level: str, force_fallback: bool = False) -> Tuple[dict, str, Optional[str], dict]:
    unit = video_doc.get("unit", "") or ""
    video_title = video_doc.get("title", "") or ""

    # [修改] V1.5：生成題目時，優先用 subtitles collection 最新校正版字幕
    subtitle_path = pick_latest_subtitle_path(video_doc, video_id_str)  # [新增]
    sub_text = read_subtitle_text(subtitle_path)

    gen_source = None
    gen_error = None
    env = env_snapshot()

    # [修改] V1.5：同時兼容 video_id 存字串 / ObjectId
    vid_oid = video_doc.get("_id") or maybe_oid(video_id_str)
    video_id_match = {"$or": [{"video_id": video_id_str}] + ([{"video_id": vid_oid}] if vid_oid else [])}

    # 只取消同 video+level 的 active（避免不同 level 互相覆蓋）
    db.parsons_tasks.update_many({**video_id_match, "level": level, "active": True}, {"$set": {"active": False}})

    try:
        if force_fallback:
            raise RuntimeError("force_fallback")
        ai = ai_generate_parsons_from_subtitle(sub_text, unit, video_title, level=level)
        gen_source = "openai"
    except Exception as e:
        ai = simple_fallback_generate(sub_text, unit, video_title, level=level)
        gen_source = "fallback"
        gen_error = str(e)

    # ✅ 新產生的題目預設是 draft（學生端看不到）
    doc = {
        # [修改] V1.5：video_id 優先用 ObjectId（老師端好查），但學生端 get_task 已兼容字串/oid
        "video_id": vid_oid if vid_oid else video_id_str,
        # [新增] 保留原始字串，讓 debug / 追查更穩（不改既有 schema，只新增欄位）
        "video_id_str": video_id_str,

        "unit": unit,
        "video_title": video_title,
        "level": level,

        "enabled": False,
        "review_status": "draft",

        "prompt_source": {"subtitle_path": subtitle_path},
        "question_text": ai.get("question_text"),
        "solution_blocks": ai.get("solution_blocks", []),
        "distractor_blocks": ai.get("distractor_blocks", []),
        "pool": ai.get("pool", []),
        "template_slots": ai.get("template_slots", []),
        "ai_feedback": ai.get("ai_feedback", {}),

        # [新增] V1.5：AI 時間軸與每格提示（不改學生端路由/格式，只存 DB，submit 再回傳）
        "ai_generated": True if gen_source == "openai" else False,
        "ai_segment_map": ai.get("ai_segment_map", {}) or {},
        "ai_slot_hints": ai.get("ai_slot_hints", {}) or {},
        "ai_segments_compact": ai.get("ai_segments_compact", "") or "",

        "created_at": now_utc(),
        "active": True,

        "gen_source": gen_source,
        "gen_error": gen_error,
        "env": env,
    }

    inserted = db.parsons_tasks.insert_one(doc)
    doc["_id"] = inserted.inserted_id

    log_event(
        "parsons_task_generated",
        video_id=video_id_str,
        task_id=str(inserted.inserted_id),
        gen_source=gen_source,
        gen_error=gen_error,
        unit=unit,
        title=video_title,
        level=level,
    )

    return doc, gen_source, gen_error, env


# =========================
# t5doc_to_parsons_task（你已有，保留原本版本即可）
# =========================
def t5doc_to_parsons_task(doc: dict) -> dict:
    q = doc.get("question") or {}
    question_text = doc.get("question_text") or q.get("prompt") or ""

    sol = doc.get("solution_blocks") or []
    dis = doc.get("distractor_blocks") or []
    dis = [b for b in dis if b.get("enabled", True) is True]

    pool = []
    for b in sol:
        pool.append({
            "id": str(b.get("id")),
            "text": b.get("text", ""),
            "meaning_zh": b.get("semantic_zh") or b.get("meaning_zh") or "",
            "type": "solution"
        })
    for b in dis:
        pool.append({
            "id": str(b.get("id")),
            "text": b.get("text", ""),
            "meaning_zh": b.get("semantic_zh") or b.get("meaning_zh") or "",
            "type": "distractor"
        })

    order = doc.get("solution_order") or []
    if not order:
        order = [b.get("id") for b in sol if b.get("id")]

    # [新增] 優先沿用 DB 裡已存在的 template_slots[].label（AI 先生成＋老師審核後存進去的固定中文題詞）
    # - 你的學生端左側提示會用 slot.label 顯示（你要求：一進來就看得到中文提示）
    # - 若 DB 沒有 label，才退回顯示「第 N 格」
    label_by_slot = {}
    try:
        for s in (doc.get("template_slots") or []):
            sk = s.get("slot")
            if sk is None:
                continue
            lab = (s.get("label") or "").strip()
            if lab:
                label_by_slot[str(sk)] = lab
    except Exception:
        label_by_slot = {}

    # [修正] template_slots 的 label：以 label_by_slot 為主，避免被覆蓋成「第 N 格」
    template_slots = []
    for i, bid in enumerate(order):
        template_slots.append({
            "slot": str(i),
            "expected_id": str(bid),
            "label": label_by_slot.get(str(i)) or f"第 {i+1} 格"
        })

    return {"question_text": question_text, "pool": pool, "template_slots": template_slots}

# =========================
# (A) GET /task  (學生端只抓 published)
# ==========================
# =========================
# (A) GET /task 取得學生端要做的題目（只取已發布 enabled=True 的最新一筆）
# =========================
@parsons_bp.get("/task")
def get_task():
    video_id = (request.args.get("video_id") or "").strip()
    level = (request.args.get("level") or "L2").strip()

    if not video_id:
        return jsonify({"ok": False, "message": "missing video_id"}), 400

    # 兼容 video_id 可能是 ObjectId 或字串存法
    try:
        vid = ObjectId(video_id)
        video_q = {"$or": [{"video_id": vid}, {"video_id": str(vid)}]}
    except Exception:
        # 若傳入不是 ObjectId，就直接當字串比對
        video_q = {"video_id": video_id}

    # ✅ 只取已發布（enabled=True）的最新一筆
    q = {
        **video_q,
        "level": level,
        "enabled": True,
    }

    task = db.parsons_tasks.find_one(q, sort=[("created_at", -1)])
    if not task:
        # 沒有已發布題目：維持你原本「尚未發布」的行為（不要噴錯）
        return jsonify({
            "ok": True,
            "noTask": True,
            "message": "此影片尚未發布題目",
        })

    # 兼容多種欄位命名（你 teacher_t5.py 也是這樣兼容）
    # - prompt 可能在 question.prompt 或 t.prompt
    question_obj = task.get("question", {}) or {}
    prompt = question_obj.get("prompt") or task.get("prompt") or task.get("question_text") or ""

    solution_blocks = task.get("solution_blocks", []) or task.get("blocks", []) or []
    distractor_blocks = task.get("distractor_blocks", []) or task.get("distractors", []) or []
    solution_order = task.get("solution_order", []) or task.get("solution_ids", []) or []

    # ✅ 重要：把 semantic_zh 原樣回傳（學生端就能顯示中文語意）
    # 同時提供舊欄位 question_text，避免你 parsons.vue 舊版用這個欄位
    return jsonify({
        "ok": True,
        "noTask": False,

        "task_id": str(task.get("_id")),
        "video_id": str(task.get("video_id")) if task.get("video_id") else None,
        "level": task.get("level", level),

        # 新舊相容
        "question_text": prompt,
        "question": {"prompt": prompt},

        "solution_blocks": solution_blocks,
        "distractor_blocks": distractor_blocks,
        "solution_order": solution_order,

        # 方便前端一次拿全部 blocks
        "blocks": (solution_blocks or []) + (distractor_blocks or []),

        "segment_label": task.get("segment_label", "—"),
        "subtitle_version": task.get("subtitle_version", None),
        "version": task.get("version", "v1"),
    })



def t5doc_to_parsons_task(doc: dict) -> dict:
    q = doc.get("question") or {}
    question_text = doc.get("question_text") or q.get("prompt") or ""

    sol = doc.get("solution_blocks") or []
    dis = doc.get("distractor_blocks") or []
    dis = [b for b in dis if b.get("enabled", True) is True]

    pool = []
    for b in sol:
        pool.append({
            "id": str(b.get("id")),
            "text": b.get("text", ""),
            "meaning_zh": b.get("semantic_zh") or b.get("meaning_zh") or "",
            "type": "solution"
        })
    for b in dis:
        pool.append({
            "id": str(b.get("id")),
            "text": b.get("text", ""),
            "meaning_zh": b.get("semantic_zh") or b.get("meaning_zh") or "",
            "type": "distractor"
        })

    order = doc.get("solution_order") or []
    if not order:
        order = [b.get("id") for b in sol if b.get("id")]

    # [新增] 讓學生端一進來就看到固定中文提示：
    # 你的 DB 中文提示詞目前放在 solution_blocks[].semantic_zh（不是 template_slots[].label）
    semantic_by_id = {}
    for b in sol:
        bid = b.get("id")
        if not bid:
            continue
        zh = (b.get("semantic_zh") or b.get("meaning_zh") or "")
        if isinstance(zh, str):
            zh = zh.strip()
        semantic_by_id[str(bid)] = zh or ""

    # [新增] 若未來 DB 有存 template_slots[].label，也優先沿用（更保守）
    label_by_slot = {}
    label_by_expected = {}
    try:
        for s in (doc.get("template_slots") or []):
            sk = s.get("slot")
            eid = s.get("expected_id")
            lab = (s.get("label") or "")
            lab = lab.strip() if isinstance(lab, str) else ""
            if sk is not None and lab:
                label_by_slot[str(sk)] = lab
            if eid is not None and lab:
                label_by_expected[str(eid)] = lab
    except Exception:
        label_by_slot = {}
        label_by_expected = {}

    template_slots = []
    for i, bid in enumerate(order):
        bid_str = str(bid)
        template_slots.append({
            "slot": str(i),
            "expected_id": bid_str,
            # [修改] label 優先順序：
            # 1) DB 既存 label（slot / expected_id）
            # 2) solution_blocks.semantic_zh
            # 3) fallback「第 N 格」
            "label": (
                label_by_slot.get(str(i))
                or label_by_expected.get(bid_str)
                or semantic_by_id.get(bid_str)
                or f"第 {i+1} 格"
            )
        })

    return {"question_text": question_text, "pool": pool, "template_slots": template_slots}

# =========================
# ✅ 發布 API（後端保留給你測試/備用）
#    重要修正：發布時「同影片同 level」只保留一筆 published，避免學生拿到舊題
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

    # ✅ 先把同影片同 level 的其他題目全部取消發布
    db.parsons_tasks.update_many(
        {"video_id": task_video_id, "level": task_level, "_id": {"$ne": oid}},
        {"$set": {"enabled": False, "review_status": "draft"}}
    )
    # 如果別的資料是 ObjectId 存 video_id，也一併處理（更穩）
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

    # 1) 優先用 DB 已存的 segment/hint
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

    # 2) 嘗試補上下文（可用於 debug / data.ai_hint）
    try:
        sub_path = ((task.get("prompt_source") or {}).get("subtitle_path") or "").strip()
        if sub_path:
            sub_text = read_subtitle_text(sub_path)
            segs = parse_srt_segments(sub_text)
            if start is not None and end is not None:
                subtitle_context = extract_context_around(segs, start, end, window=5)
            else:
                # 沒有 start/end 時，先給前面一點內容
                subtitle_context = compact_segments_for_prompt(segs[:18], max_chars=3000)
    except Exception:
        subtitle_context = ""

    # 3) 若缺少 hint 或缺少 start/end 且 AI 可用：即時推估並回寫
    if ai_enabled() and (not hint or start is None or end is None):
        try:
            model = (os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()
            # 優先用生成時存的 compact（更快）
            segs_compact = (task.get("ai_segments_compact") or "").strip()
            if not segs_compact:
                sub_path = ((task.get("prompt_source") or {}).get("subtitle_path") or "").strip()
                segs_compact = compact_segments_for_prompt(parse_srt_segments(read_subtitle_text(sub_path)), max_chars=12000)

            prompt = f"""
你是一位 Python 程式設計助教。學生在 Parsons 題目中把某一格放錯了。
請你做兩件事：
1) 給「繁體中文」提示（1~2句，針對該格錯誤）
2) 從字幕時間戳中選出最適合回看的片段 start/end（秒數，必須是字幕裡存在的合理範圍）

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

            client = get_openai_client()
            resp = client.responses.create(model=model, input=prompt)
            data = safe_json_loads((resp.output_text or "").strip()) or {}

            ai_hint = (data.get("hint") or "").strip()
            ai_s = data.get("start", None)
            ai_e = data.get("end", None)

            ai_start = float(ai_s) if ai_s is not None else None
            ai_end = float(ai_e) if ai_e is not None else None

            if ai_hint:
                hint = ai_hint
            if ai_start is not None and ai_end is not None and ai_end > ai_start:
                start, end = ai_start, ai_end

            # 回寫到 task（不改 schema）
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

            # 補上下文
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
            # AI 即時推估失敗：留空，走外層 fallback
            pass

    return hint, start, end, subtitle_context


# =========================
# (B) POST /submit 送出作答
# =========================
@parsons_bp.post("/submit")
def submit_answer():
    data = request.get_json(silent=True) or {}

    task_id = (data.get("task_id") or "").strip()
    answer_ids = data.get("answer_ids") or []
    student_id = (data.get("student_id") or "").strip()
    level = (data.get("level") or "").strip()

    if not task_id:
        return jsonify({"ok": False, "message": "missing task_id"}), 400

    # 1️⃣ 取得題目
    try:
        task = db.parsons_tasks.find_one({"_id": ObjectId(task_id)})
    except Exception:
        return jsonify({"ok": False, "message": "invalid task_id"}), 400

    if not task:
        return jsonify({"ok": False, "message": "task not found"}), 404

    solution_ids = task.get("solution_order") or None
    if not solution_ids:
        solution_blocks = task.get("solution_blocks", []) or []
        solution_ids = [b.get("id") for b in solution_blocks if b.get("type") in ("core", "solution", None)]
        solution_ids = [sid for sid in solution_ids if sid]

    # [新增] V1.4：用「template_slots 的順序」做一格一格比對，避免 idx 錯位（第3格變第4格）
    parsed = t5doc_to_parsons_task(task)
    expected_ids = [str(s.get("expected_id")) for s in (parsed.get("template_slots") or [])]

    # [保留] 仍保留 answer_core_ids 欄位（不改 DB schema）
    answer_core = [bid for bid in answer_ids if str(bid).startswith("b")]

    # [新增] 對齊長度：不足補 None；多出的視為錯（但不會影響 wrong_indices 的 index 對齊）
    aligned = list(answer_ids)
    if len(aligned) < len(expected_ids):
        aligned = aligned + [None] * (len(expected_ids) - len(aligned))

    wrong_indices = []
    for i in range(len(expected_ids)):
        if str(aligned[i]) != str(expected_ids[i]):
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

    feedback = "✅ 完全正確！" if is_correct else ((task.get("ai_feedback") or {}).get("general") or f"❌ 目前正確率 {score:.0%}，建議先確認「輸入 → 計算 → 輸出」的順序。")

    video_id_str = normalize_video_id(task.get("video_id"))

    attempt_doc = {
        "task_id": task_id,
        "video_id": video_id_str,
        "unit": task.get("unit"),
        "student_id": student_id or None,
        "level": level or task.get("level") or None,
        "answer_ids": answer_ids,
        "answer_block_ids": answer_ids,
        "answer_core_ids": answer_core,
        "is_correct": is_correct,
        "score": score,
        "feedback": feedback,
        "wrong_index": wrong_index,
        "wrong_indices": wrong_indices,
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

        # [新增] V1.4：送出後回傳欄位（前端顯示以後端為準）
        "slot_label": slot_label,
        "actual_text": actual_text,
        "expected_text": expected_text,
        "hint": "",
        "review_t": None,
    }

    if not is_correct:
        # [修改] V1.5：錯誤回饋由 AI（提示 + 建議回看時間軸）決定；若 AI 不可用則保留既有 fallback
        slot_key = str(wrong_index) if wrong_index is not None else "0"
        hint, t_start, t_end, subtitle_context = ai_hint_and_segment_for_wrong(
            task=task,
            slot_key=slot_key,
            expected_text=expected_text,
            actual_text=actual_text,
            level=(level or task.get("level") or "L1"),
            slot_label=(slot_label or f"第{(wrong_index + 1)}格" if wrong_index is not None else "第1格"),
        )

        # fallback（維持你原本行為：一定會有 jump 秒數，不讓學生卡住）
        if t_start is None or t_end is None or t_end <= t_start:
            t_start = 120.0
            t_end = 170.0
        if not hint:
            hint = "（AI 暫時不可用）請回看影片片段並檢查輸入型別與運算順序。"

        resp["review_t"] = int(float(t_start))
        resp["hint"] = hint

        raw_vid = task.get("video_id") or data.get("video_id") or ""
        vid = normalize_video_id(raw_vid)

        resp["jump"] = {"video_id": vid, "start": float(t_start), "end": float(t_end)}
        resp["data"] = {
            "title": "回答錯誤",
            "error_detail": feedback,
            "segment": {"start": float(t_start), "end": float(t_end), "label": f"影片片段 [{int(float(t_start))}–{int(float(t_end))} 秒]"},
            "subtitle_context": subtitle_context or "（未找到字幕）",
            "ai_hint": hint,
            "video_id": vid,
        }

    return jsonify(resp)


# =========================
# (B) POST /regenerate  (老師端產生題目：預設 draft)
# =========================
@parsons_bp.post("/regenerate")
def regenerate():
    data = request.get_json(silent=True) or {}
    video_id = (data.get("video_id") or "").strip()
    level = (data.get("level") or "L1").strip()

    if not video_id:
        return jsonify({"ok": False, "message": "missing video_id"}), 400

    try:
        vid_oid = ObjectId(video_id)
    except InvalidId:
        return jsonify({"ok": False, "message": "video_id must be a 24-char ObjectId"}), 400

    v = db.videos.find_one({"_id": vid_oid})
    if not v:
        return jsonify({"ok": False, "message": "video not found"}), 404

    doc, gen_source, gen_error, env = create_task_for_video(v, video_id, level)

    return jsonify({
        "ok": True,
        "task": {
            "task_id": str(doc["_id"]),
            "video_id": doc.get("video_id"),
            "unit": doc.get("unit"),
            "title": doc.get("video_title"),
            "level": doc.get("level"),
            "question_text": doc.get("question_text"),
            "pool": doc.get("pool", []),
            "template_slots": doc.get("template_slots", []),
            "enabled": doc.get("enabled"),
            "review_status": doc.get("review_status"),
        },
        "gen_source": gen_source,
        "gen_error": gen_error,
        "env": env,
    }), 200


# =========================
# (D) POST /review_choice（你原本就有：寫 parsons_review_logs）
# =========================
@parsons_bp.post("/review_choice")
def review_choice():
    data = request.get_json(silent=True) or {}
    attempt_id = (data.get("attempt_id") or "").strip()
    student_choice = (data.get("student_choice") or "").strip()

    if not attempt_id:
        return jsonify({"ok": False, "message": "missing attempt_id"}), 400

    doc = {
        "attempt_id": attempt_id,
        "student_choice": student_choice,
        "created_at": datetime.now(timezone.utc),
        "followup_is_correct": None,
        "followup_submitted_at": None,
        "followup_attempt_id": None,
    }
    db.parsons_review_logs.update_one(
        {"attempt_id": attempt_id},
        {"$set": doc},
        upsert=True
    )
    return jsonify({"ok": True})


# =========================
# (D) Debug / health (保留既有)
# =========================
@parsons_bp.get("/debug/last_gen")
def debug_last_gen():
    video_id = request.args.get("video_id", "").strip()
    if not video_id:
        return jsonify({"ok": False, "message": "missing video_id"}), 400

    vid_oid = maybe_oid(video_id)
    if not vid_oid:
        return jsonify({"ok": False, "message": "invalid video_id"}), 400

    doc = db.parsons_tasks.find_one({"video_id": {"$in": [video_id, vid_oid]}}, sort=[("created_at", -1)])
    if not doc:
        return jsonify({"ok": True, "found": False}), 200

    # [新增] 方便你判斷是不是 fallback、字幕有沒有讀到
    subtitle_path = ((doc.get("prompt_source") or {}).get("subtitle_path") or "").strip()
    qtext = (doc.get("question_text") or "").strip()

    return jsonify({
        "ok": True,
        "found": True,
        "id": str(doc.get("_id")),
        "level": doc.get("level"),
        "enabled": doc.get("enabled"),
        "review_status": doc.get("review_status"),
        "status": doc.get("status"),  # [新增] 兼容 teacher_t5 的 publish 欄位
        "created_at": doc.get("created_at"),
        "published_at": doc.get("published_at"),
        "updated_at": doc.get("updated_at"),  # [新增]
        "gen_source": doc.get("gen_source"),  # [新增]
        "gen_error": doc.get("gen_error"),    # [新增]
        "subtitle_path": subtitle_path,        # [新增]
        "question_preview": qtext[:60],        # [新增]
    }), 200



# =========================
# Jobs（保留）
# =========================
def _job_worker(job_id: str):
    try:
        db.parsons_jobs.update_one({"_id": ObjectId(job_id)}, {"$set": {"status": "running", "started_at": now_utc()}})
        job = db.parsons_jobs.find_one({"_id": ObjectId(job_id)})
        if not job:
            return

        video_id = job.get("video_id")
        level = job.get("level", "L1")
        mode = job.get("mode", "generate")

        vid_oid = ObjectId(video_id)
        v = db.videos.find_one({"_id": vid_oid})
        if not v:
            raise RuntimeError("video not found")

        if mode == "regenerate":
            db.parsons_tasks.update_many({"video_id": video_id, "level": level, "active": True}, {"$set": {"active": False}})

        doc, gen_source, gen_error, env = create_task_for_video(v, video_id, level)

        db.parsons_jobs.update_one(
            {"_id": ObjectId(job_id)},
            {"$set": {
                "status": "done",
                "finished_at": now_utc(),
                "result_task_id": str(doc["_id"]),
                "gen_source": gen_source,
                "gen_error": gen_error,
                "env": env
            }}
        )
    except Exception as e:
        db.parsons_jobs.update_one(
            {"_id": ObjectId(job_id)},
            {"$set": {"status": "failed", "finished_at": now_utc(), "error": str(e)}}
        )


@parsons_bp.post("/jobs")
def create_job():
    data = request.get_json(silent=True) or {}
    video_id = (data.get("video_id") or "").strip()
    level = (data.get("level") or "L1").strip()
    mode = (data.get("mode") or "generate").strip()

    if not video_id:
        return jsonify({"ok": False, "message": "missing video_id"}), 400

    ObjectId(video_id)

    inserted = db.parsons_jobs.insert_one({
        "video_id": video_id,
        "level": level,
        "mode": mode,
        "status": "queued",
        "created_at": now_utc()
    })

    job_id = str(inserted.inserted_id)
    t = threading.Thread(target=_job_worker, args=(job_id,), daemon=True)
    t.start()

    return jsonify({"ok": True, "job_id": job_id, "status": "queued"}), 200


@parsons_bp.get("/jobs/<job_id>")
def get_job(job_id):
    oid = ObjectId(job_id)
    job = db.parsons_jobs.find_one({"_id": oid})
    if not job:
        return jsonify({"ok": False, "message": "job not found"}), 404

    return jsonify({
        "ok": True,
        "job": {
            "job_id": job_id,
            "status": job.get("status"),
            "video_id": job.get("video_id"),
            "level": job.get("level"),
            "mode": job.get("mode"),
            "result_task_id": job.get("result_task_id"),
            "gen_source": job.get("gen_source"),
            "gen_error": job.get("gen_error"),
            "env": job.get("env"),
            "error": job.get("error"),
            "created_at": job.get("created_at"),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
        }
    }), 200


# =========================
# 學生在 learning 回看時，離開或按「返回練習」→ 把回看資料存起來（保留，未來可以分析學生回看行為）
# =========================
@parsons_bp.post("/review_watch")
def review_watch():
    data = request.get_json(silent=True) or {}

    attempt_id = (data.get("attempt_id") or "").strip()
    if not attempt_id:
        return jsonify({"ok": False, "message": "missing attempt_id"}), 400

    doc = {
        "attempt_id": attempt_id,
        "video_id": (data.get("video_id") or "").strip(),
        "task_id": (data.get("task_id") or "").strip(),

        "watch_start_at": data.get("watch_start_at"),
        "watch_end_at": data.get("watch_end_at"),
        "watch_seconds": int(data.get("watch_seconds") or 0),
        "reached_end": bool(data.get("reached_end")),

        "created_at": datetime.now(timezone.utc),
        # 下面兩個欄位：等「回看後再次 submit」時更新
        "followup_is_correct": None,
        "followup_submitted_at": None,
        "followup_attempt_id": None,
    }
    # 同一個 attempt_id：用 upsert 方式更新/寫入（避免送多次變多筆）
    db.parsons_review_logs.update_one(
        {"attempt_id": attempt_id},
        {"$set": doc},
        upsert=True
    )
    return jsonify({"ok": True})
