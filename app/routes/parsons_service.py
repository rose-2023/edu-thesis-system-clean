import os
import re
_re = re
import json
from datetime import datetime, timezone
from typing import Any, Dict, Tuple, Optional

from bson import ObjectId

from ..db import db
from . import parsons_ai
from .parsons_concept_engine import build_generation_plan, build_template_solution

# ===== [安全版] anti-copy import =====
try:
    from .parsons_anti_copy_rules import (
        build_prompt_guard,
        is_too_similar_to_subtitle,
    )
except Exception:
    # 如果專案沒有 anti-copy 檔案，也不會壞掉
    def build_prompt_guard(prompt, unit=None, subtitle_text=None, video_title=None):
        return prompt

    def is_too_similar_to_subtitle(subtitle_text, question_text):
        return False

SRT_TIME_RE = re.compile(r"(\d+):(\d+):(\d+),(\d+)")

def _strip_py_strings_and_comments(code: str) -> str:
    """Roughly remove Python strings and comments to reduce false regex matches."""
    try:
        code = _re.sub(r"'''[\s\S]*?'''", "", code) 
        code = _re.sub(r'"""[\s\S]*?"""', "", code)
        code = _re.sub(r"'([^'\\]|\\.)*'", "''", code)
        code = _re.sub(r'"([^"\\]|\\.)*"', '""', code)
        code = _re.sub(r"#.*", "", code)
        return code
    except Exception:
        return code


# =========================
# Utils
# =========================

def now_utc() -> datetime:
    return datetime.now(timezone.utc) # type: ignore

def maybe_oid(s: str) -> Optional[ObjectId]:
    try:
        return ObjectId(str(s))
    except Exception:
        return None

def normalize_video_id(x) -> str:
    if x is None:
        return ""
    if isinstance(x, ObjectId):
        return str(x)
    return str(x).strip()



# =========================
# ✅ V1.8 Test (Pre/Post) Utils
# =========================
_TEST_INDEX_READY = False

def log_event(event: str, **kwargs):
    try:
        doc = {"event": event, "created_at": now_utc()}
        doc.update(kwargs)
        db.events.insert_one(doc)
    except Exception:
        pass

def read_subtitle_text(path: str) -> str:
    if not path:
        return ""
    path = path.strip()
    full = path
    if not os.path.isabs(path):
        full = os.path.join(os.getcwd(), path)

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

def safe_json_loads(s: str) -> Optional[dict]:
    try:
        return json.loads(s)
    except Exception:
        return None


def _extract_focus_keywords(text: str, max_keywords: int = 6) -> list:
    text = (text or "").strip().lower()
    if not text:
        return []

    tokens = re.findall(r"[a-z_][a-z0-9_]*|[\u4e00-\u9fff]{2,}", text)
    stop = {
        "請", "以及", "並", "還有", "可以", "需要", "老師", "影片", "說明", "題目", "程式",
        "輸入", "輸出", "整數", "字串", "python", "print", "input", "使用", "依照", "格式",
    }

    out = []
    seen = set()
    for t in tokens:
        if t in stop:
            continue
        if len(t) <= 1:
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= max_keywords:
            break
    return out


def _teacher_alignment_check(teacher_description: str, question_text: str, solution_lines: list) -> Tuple[bool, list]:
    td = (teacher_description or "").strip()
    if not td:
        return True, []

    kws = _extract_focus_keywords(td, max_keywords=6)
    if not kws:
        return True, []

    hay = ((question_text or "") + "\n" + "\n".join(solution_lines or [])).lower()
    hits = [k for k in kws if k in hay]

    # 要求至少命中 2 個關鍵詞（短描述可放寬為 1）
    needed = 1 if len(kws) <= 2 else 2
    ok = len(hits) >= needed
    missing = [k for k in kws if k not in hits]
    return ok, missing


# =========================
# subtitles / parsing helpers
# 字幕相關的工具函式（讀檔、解析、清理、選取片段等）
# =========================
SRT_TIME_RE = re.compile(r"(\d+):(\d+):(\d+),(\d+)")

def srt_time_to_seconds(t: str) -> float:
    m = SRT_TIME_RE.search(t or "")
    if not m:
        return 0.0
    hh, mm, ss, ms = m.groups()
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0

def strip_srt_noise(srt_text: str) -> str:
    if not srt_text:
        return ""
    lines = []
    for ln in srt_text.splitlines():
        t = ln.strip()
        if not t:
            continue
        if t.isdigit():
            continue
        if "-->" in t:
            continue
        lines.append(t)
    return "\n".join(lines).strip()

def parse_srt_segments(srt_text: str) -> list:
    if not srt_text:
        return []
    lines = [ln.rstrip("\n") for ln in srt_text.splitlines()]
    segs = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.isdigit():
            i += 1
            if i >= len(lines):
                break
            time_line = lines[i].strip()
            if "-->" not in time_line:
                continue
            parts = [p.strip() for p in time_line.split("-->")]
            start = srt_time_to_seconds(parts[0])
            end = srt_time_to_seconds(parts[1]) if len(parts) > 1 else start
            i += 1
            text_buf = []
            while i < len(lines) and lines[i].strip() != "":
                text_buf.append(lines[i].strip())
                i += 1
            txt = " ".join(text_buf).strip()
            if txt:
                segs.append({"start": start, "end": end, "text": txt})
        i += 1
    return segs

def compact_segments_for_prompt(segs: list, max_chars: int = 12000) -> str:
    out = []
    total = 0
    for s in segs:
        st = int(float(s.get("start", 0)))
        ed = int(float(s.get("end", 0)))
        txt = (s.get("text") or "").strip()
        line = f"[{st}-{ed}] {txt}"
        if total + len(line) + 1 > max_chars:
            break
        out.append(line)
        total += len(line) + 1
    return "\n".join(out)

def extract_context_around(segs: list, start: float, end: float, window: int = 5) -> str:
    picked = []
    for s in segs:
        st = float(s.get("start", 0))
        ed = float(s.get("end", 0))
        if ed < start:
            continue
        if st > end:
            continue
        picked.append(s)
    if not picked:
        return ""
    picked = picked[: max(1, window)]
    return "\n".join([f"[{int(p.get('start', 0))}-{int(p.get('end', 0))}] {p.get('text','')}" for p in picked])

def pick_latest_subtitle_path(video_doc: dict, video_id_str: str) -> str:
    try:
        vid_oid = video_doc.get("_id") or maybe_oid(video_id_str)
        if vid_oid:
            sub_doc = db.subtitles.find_one(
                {"video_id": vid_oid},
                sort=[("version", -1), ("created_at", -1)]
            )
            if sub_doc and (sub_doc.get("path") or "").strip():
                return (sub_doc.get("path") or "").strip()

        if vid_oid:
            sub_doc2 = db.subtitles.find_one(
                {"video_id": str(vid_oid)},
                sort=[("version", -1), ("created_at", -1)]
            )
            if sub_doc2 and (sub_doc2.get("path") or "").strip():
                return (sub_doc2.get("path") or "").strip()
    except Exception:
        pass

    return (video_doc.get("subtitle_path", "") or "").strip()


# =========================
# t5doc_to_parsons_task (teacher->student normalize)
# =========================

def simple_fallback_generate(sub_text: str, unit: str, video_title: str, level: str = "L1", teacher_description: str = "") -> Dict[str, Any]:
    constraints = resolve_unit_constraints(unit)
    unit_type = (constraints or {}).get("unit_type") or "loop"
    loop_style = (constraints or {}).get("loop_style") or "either"

    if unit_type == "io":
        question_text = "（備援題目）請輸入兩個整數，並輸出它們的總和。"
        solution_lines = ["x = int(input())", "y = int(input())", "print(x + y)"]
        distractor_lines = ["x = input()", "y = input()", "print(x - y)", "print(x * y)"]

    elif unit_type == "condition":
        question_text = "（備援題目）請輸入一個整數，判斷它是否大於等於 60，並輸出對應結果。"
        solution_lines = [
            "score = int(input())",
            "if score >= 60:",
            "    print('pass')",
            "else:",
            "    print('fail')",
        ]
        distractor_lines = [
            "while score >= 60:",
            "print('pass')",
            "elif score >= 60:",
            "score = input()",
        ]

    else:
        if loop_style == "while_only":
            question_text = "（備援題目）請使用 while 迴圈，計算 1 到 3 的總和並輸出結果。"
            solution_lines = [
                "i = 1",
                "total = 0",
                "while i <= 3:",
                "    total += i",
                "    i += 1",
                "print(total)",
            ]
            distractor_lines = [
                "for i in range(1, 4):",
                "total = i",
                "i = i + 2",
                "print(i)",
            ]
        else:
            question_text = "（備援題目）請使用 for 迴圈，計算 1 到 3 的總和並輸出結果。"
            solution_lines = [
                "total = 0",
                "for i in range(1, 4):",
                "    total += i",
                "print(total)",
            ]
            distractor_lines = [
                "while i <= 3:",
                "total = i",
                "i += 1",
                "print(i)",
            ]

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
        "unit_type": unit_type,
        "constraints": constraints,
        "rule_check": build_rule_check(solution_lines, constraints),
        "source_subtitle": {"text_used": ""},
        "subtitle_range": {"start_index": 0, "end_index": 0, "start_ts": 0, "end_ts": 0},
        "subtitle_text_used": "",
    }


# =========================
# ✅ Unit-aware constraints + Traceability + Rule Check (minimal, optional fields)
# =========================

def resolve_unit_constraints(unit: str) -> Dict[str, Any]:
    u = (unit or "").strip().upper()

    # IO
    if "-IO" in u:
        return {"unit_type": "io", "forbid_loop": True, "forbid_condition": True}

    # Condition (IF / IFELSE / ELIF)
    if "-IFELSE" in u:
        return {"unit_type": "condition", "require_if": True, "require_else": True, "require_elif": False, "forbid_loop": True, "forbid_break": True}
    if "-ELIF" in u:
        return {"unit_type": "condition", "require_if": True, "require_else": False, "require_elif": True, "forbid_loop": True, "forbid_break": True}
    if "-IF" in u:
        return {"unit_type": "condition", "require_if": True, "require_else": False, "require_elif": False, "forbid_loop": True, "forbid_break": True}

    # Loop styles
    if "-FOR" in u:
        return {"unit_type": "loop", "loop_style": "for_only"}
    if "-WHILE" in u:
        return {"unit_type": "loop", "loop_style": "while_only"}
    if "-LOOP" in u or u.startswith("U3"):
        return {"unit_type": "loop", "loop_style": "for_only"}

    # default
    return {"unit_type": "loop", "loop_style": "either"}

def _pick_trace_window(segs: list, constraints: dict, max_lines: int = 7) -> Dict[str, Any]:
    unit_type = (constraints or {}).get("unit_type") or "loop"
    kw_map = {
        "io": ["input", "print", "輸入", "輸出", "讀入", "輸出結果"],
        "condition": ["if", "else", "elif", "條件", "判斷", "比較", "大於", "小於", "等於"],
        "loop": ["for", "while", "迴圈", "重複", "range", "次", "循環"],
    }
    kws = kw_map.get(unit_type, [])
    hit = None
    for i, s in enumerate(segs or []):
        t = (s.get("text") or "").lower()
        if any(k.lower() in t for k in kws):
            hit = i
            break
    if hit is None:
        hit = 0

    start_i = max(0, hit - 1)
    end_i = min(len(segs) - 1, start_i + max_lines - 1) if segs else -1
    if segs:
        start_i = max(0, end_i - max_lines + 1)

    window = segs[start_i:end_i + 1] if (segs and end_i >= 0) else []
    text_used = "\n".join((w.get("text") or "").strip() for w in window if (w.get("text") or "").strip())
    start_ts = window[0]["start"] if window else 0.0
    end_ts = window[-1]["end"] if window else 0.0

    return {
        "subtitle_range": {"start_index": start_i, "end_index": end_i, "start_ts": start_ts, "end_ts": end_ts},
        "subtitle_text_used": text_used,
        "keywords": kws,
    }

def build_rule_check(solution_lines: list, constraints: dict) -> Dict[str, Any]:
    text = _strip_py_strings_and_comments("\n".join(solution_lines or []))
    rc = {
        "compile_ok": True,
        "has_for": _re.search(r"^\s*for\b", text, flags=_re.M) is not None,
        "has_while": _re.search(r"^\s*while\b", text, flags=_re.M) is not None,
        "has_if": _re.search(r"^\s*if\b", text, flags=_re.M) is not None,
        "has_elif": _re.search(r"^\s*elif\b", text, flags=_re.M) is not None,
        "has_else": _re.search(r"^\s*else\s*:\s*$", text, flags=_re.M) is not None,
        "has_input": _re.search(r"\binput\s*\(", text) is not None,
        "has_print": _re.search(r"\bprint\s*\(", text) is not None,
        "has_range": _re.search(r"\brange\s*\(", text) is not None,
        "has_accumulate": (_re.search(r"\+=", text) is not None) or (_re.search(r"(\w+)\s*=\s*\1\s*\+\s*\d+", text) is not None),
        "has_break": _re.search(r"\bbreak\b", text) is not None,
    }
    rc["has_loop"] = rc["has_for"] or rc["has_while"]

    try:
        compile("\n".join(solution_lines or []), "<rule_check>", "exec")
    except Exception:
        rc["compile_ok"] = False

    ok = True
    reason = ""

    if not rc["compile_ok"]:
        ok = False
        reason = "compile failed"

    unit_type = (constraints or {}).get("unit_type") or "loop"

    if ok and unit_type == "io":
        if rc["has_loop"]:
            ok = False
            reason = "IO forbids loop"
        elif rc["has_if"] or rc["has_elif"] or rc["has_else"]:
            ok = False
            reason = "IO forbids condition"
        elif not rc["has_input"]:
            ok = False
            reason = "missing input"
        elif not rc["has_print"]:
            ok = False
            reason = "missing print"

    if ok and unit_type == "condition":
        if rc["has_loop"]:
            ok = False
            reason = "condition forbids loop"
        elif constraints.get("forbid_break") and rc["has_break"]:
            ok = False
            reason = "condition forbids break"
        elif constraints.get("require_if") and (not rc["has_if"]):
            ok = False
            reason = "missing if"
        elif constraints.get("require_else") and (not rc["has_else"]):
            ok = False
            reason = "missing else"
        elif constraints.get("require_elif") and (not rc["has_elif"]):
            ok = False
            reason = "missing elif"

    if ok and unit_type == "loop":
        if not rc["has_loop"]:
            ok = False
            reason = "missing loop"
        else:
            loop_style = (constraints or {}).get("loop_style") or "either"
            if loop_style == "for_only" and rc["has_while"]:
                ok = False
                reason = "for_only but contains while"
            elif loop_style == "while_only" and rc["has_for"]:
                ok = False
                reason = "while_only but contains for"

    rc["ok"] = ok
    rc["reason"] = reason
    return rc

def _build_blocks_from_lines(question_text: str, solution_lines: list, distractor_lines: list) -> Dict[str, Any]:
    solution_lines = solution_lines or []
    distractor_lines = distractor_lines or []
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
    }

def ai_generate_condition_from_subtitle(subtitle_text: str, unit: str, video_title: str, teacher_description: str = "", level: str = "L1", stable_mode: bool = False) -> Dict[str, Any]:
    if not ai_enabled():
        raise RuntimeError("AI_ENABLED is false -> skip OpenAI")
    if not subtitle_text:
        raise RuntimeError("subtitle_text is empty")

    segs = parse_srt_segments(subtitle_text)
    segs_compact = compact_segments_for_prompt(segs, max_chars=3500) or ""
    constraints = resolve_unit_constraints(unit)
    trace = _pick_trace_window(segs, constraints, max_lines=7)

    # ✅ 修正：傳入 teacher_description，老師描述優先決定 concept / scenario_hint
    plan = build_generation_plan(unit, trace.get("subtitle_text_used", "") or subtitle_text, video_title, teacher_description)
    concept = (plan.get("concept") or "").strip()
    scenario_hint = (plan.get("scenario") or "").strip()
    anti_copy_rules = plan.get("anti_copy_rules") or []

    model = (os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()
    gen_temperature = 0.05 if stable_mode else 0.2
    system_msg = "你是Python程式設計助教。請嚴格只輸出『合法 JSON』，不要輸出 Markdown 或多餘文字。"

    rule_lines = [
        "【Unit 規則｜條件判斷】",
        "- 本題必須使用 if（必要）。",
        "- 禁止使用 for / while。",
        "- 禁止使用 break。",
    ]
    if constraints.get("require_else"):
        rule_lines.append("- 必須包含 else。")
    if constraints.get("require_elif"):
        rule_lines.append("- 必須包含至少一個 elif。")
    rules = "\n".join(rule_lines)

    base_prompt = f"""
你是 Python 程式設計助教。

【課程單元】
{unit}

【程式概念】
{concept}

【題目情境建議】
{scenario_hint}

【老師指定題目方向】
{teacher_description}

請根據上述資訊設計一題 Parsons 程式重組題。

要求：

1️⃣ 題目必須符合 concept  
2️⃣ 題目情境可改寫，但概念不可改變  
3️⃣ solution_lines 必須是 Python code  
4️⃣ 不可使用 def / class  
5️⃣ 程式行數 4~12 行  

若老師描述明確，請優先依據描述生成題目。

輸出 JSON：

{
 "question_text": "題目敘述",
 "solution_lines": ["code", "..."]
}

字幕參考：
{segs_compact}

{rules}
輸出 JSON 格式：
{{
  "question_text": "題目敘述（繁中）",
  "solution_lines": ["python code line 1", "..."]
}}

本次提供用來出題的字幕片段（證據）：
時間：{trace["subtitle_range"]["start_ts"]:.1f}–{trace["subtitle_range"]["end_ts"]:.1f} 秒（字幕第 {trace["subtitle_range"]["start_index"]}–{trace["subtitle_range"]["end_index"]} 句）
{trace["subtitle_text_used"]}

字幕（含時間戳）（格式：[start-end] text）：
{segs_compact}
""".strip()

    # [新增] 防止題目直接抄影片範例
    base_prompt = build_prompt_guard(
        base_prompt,
        unit,
        trace.get("subtitle_text_used", subtitle_text),
        video_title
    )

    data = parsons_ai.call_openai_json(
        system=system_msg,
        user=base_prompt,
        model=model,
        temperature=gen_temperature,
        max_output_tokens=900
    ) or {}

    question_text = (data.get("question_text") or "").strip()

    # [新增] 第一次生成就先檢查是否太像字幕
    if is_too_similar_to_subtitle(
        trace.get("subtitle_text_used", subtitle_text),
        question_text
    ):
        raise RuntimeError("generated question is too similar to subtitle example")

    solution_lines = data.get("solution_lines") or []

    rc = build_rule_check(solution_lines, constraints)
    if not rc.get("ok"):
        retry_prompt = base_prompt + f"\n\n你上一版未通過自動驗收：{rc.get('reason')}，請修正後重新輸出 JSON。"
        data = parsons_ai.call_openai_json(
            system=system_msg,
            user=retry_prompt,
            model=model,
            temperature=gen_temperature,
            max_output_tokens=900
        ) or {}

        question_text = (data.get("question_text") or "").strip()

        # [新增] retry 後也檢查是否太像字幕
        if is_too_similar_to_subtitle(
            trace.get("subtitle_text_used", subtitle_text),
            question_text
        ):
            raise RuntimeError("generated question is too similar to subtitle example")

        solution_lines = data.get("solution_lines") or []
        rc = build_rule_check(solution_lines, constraints)

    if not rc.get("ok"):
        raise RuntimeError(f"AI condition generation failed: {rc.get('reason')}")

    distractor_lines = []
    for line in solution_lines:
        s = line.strip()
        if s.startswith("if ") and "==" in s:
            distractor_lines.append(s.replace("==", "!="))
        elif s.startswith("if ") and ">" in s:
            distractor_lines.append(s.replace(">", "<"))
        elif s.startswith("elif ") and "==" in s:
            distractor_lines.append(s.replace("==", "!="))
    distractor_lines = (distractor_lines[:4] or ["print('錯誤')", "print('ok')"])

    blocks = _build_blocks_from_lines(question_text, solution_lines, distractor_lines)

    blocks.update({
        "ai_feedback": {"general": "請注意條件判斷的比較運算與縮排層級是否正確。", "common_mistakes": [], "hints": []},
        "unit_type": "condition",
        "constraints": constraints,
        "rule_check": rc,
        "source_subtitle": {
            "subtitle_range": trace["subtitle_range"],
            "text_used": trace["subtitle_text_used"],
            "keywords": trace.get("keywords", []),
        },
        "subtitle_range": trace["subtitle_range"],
        "subtitle_text_used": trace["subtitle_text_used"],
    })
    return blocks

# ========================
# ✅ AI Generate IO from Subtitle (with constraints, traceability, rule check, anti-copy guard)
# ========================
def ai_generate_io_from_subtitle(subtitle_text: str, unit: str, video_title: str, teacher_description: str = "", level: str = "L1", stable_mode: bool = False) -> Dict[str, Any]:
    # [新增] Debug：先印目前 AI 環境狀態
    print("\n========== AI DEBUG START ==========")
    print("[AI DEBUG] unit =", unit)
    print("[AI DEBUG] video_title =", video_title)
    print("[AI DEBUG] level =", level)
    print("[AI DEBUG] teacher_description =", repr(teacher_description))
    print("[AI DEBUG] subtitle_text exists =", bool(subtitle_text))
    print("[AI DEBUG] subtitle_text length =", len(subtitle_text or ""))
    if not ai_enabled():
        raise RuntimeError("AI_ENABLED is false -> skip OpenAI")
    if not subtitle_text:
        raise RuntimeError("subtitle_text is empty")

    segs = parse_srt_segments(subtitle_text)
    segs_compact = compact_segments_for_prompt(segs, max_chars=3200) or ""
    constraints = resolve_unit_constraints(unit)
    trace = _pick_trace_window(segs, constraints, max_lines=7)

    # ✅ 修正：傳入 teacher_description，老師描述優先決定 concept / scenario_hint
    plan = build_generation_plan(unit, trace.get("subtitle_text_used", "") or subtitle_text, video_title, teacher_description)
    concept = (plan.get("concept") or "").strip()
    scenario_hint = (plan.get("scenario") or "").strip()
    anti_copy_rules = plan.get("anti_copy_rules") or []

    model = (os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()
    gen_temperature = 0.05 if stable_mode else 0.2
    system_msg = "你是Python程式設計助教。請嚴格只輸出『合法 JSON』，不要輸出 Markdown 或多餘文字。"

    rules = (
        "【Unit 規則｜輸入輸出】\n"
        "- 必須至少使用 2 次 input() 讀入資料。\n"
        "- 必須至少使用 1 次 print() 輸出結果。\n"
        "- 禁止使用 for / while。\n"
        "- 禁止使用 if / elif / else。\n"
        "- 不得使用 def/class。\n"
    )

    base_prompt = f"""
你是 Python 程式設計助教。

【課程單元】
{unit}

【程式概念】
{concept}

【題目情境建議】
{scenario_hint}

【老師指定需求（最高優先）】
{teacher_description or "（未提供）"}

優先級規則：
1) 老師指定需求（若有）
2) 字幕教學重點
3) 情境建議

如果老師指定需求和字幕例子不一致，請以老師指定需求為準，但保持同單元概念。

請根據上述概念設計題目。

重要規則：
- solution_lines 必須符合 concept
- 題目情境可改寫，但程式概念不可改變
- 不可生成與 concept 不相關的題目

你要產生一題 Parsons 程式重組題（不是選擇題）。

{rules}

輸出 JSON 格式：
{{
  "question_text": "題目敘述（繁中）",
  "solution_lines": ["python code line 1", "..."]
}}


本次提供用來出題的字幕片段（證據）：
時間：{trace["subtitle_range"]["start_ts"]:.1f}–{trace["subtitle_range"]["end_ts"]:.1f} 秒（字幕第 {trace["subtitle_range"]["start_index"]}–{trace["subtitle_range"]["end_index"]} 句）
{trace["subtitle_text_used"]}

字幕（含時間戳）（格式：[start-end] text）：
{segs_compact}
""".strip()

    # [新增] 防止題目直接抄影片範例
    base_prompt = build_prompt_guard(
        base_prompt,
        unit,
        trace.get("subtitle_text_used", subtitle_text),
        video_title
    )

    data = parsons_ai.call_openai_json(
        system=system_msg,
        user=base_prompt,
        model=model,
        temperature=gen_temperature,
        max_output_tokens=900
    ) or {}

    question_text = (data.get("question_text") or "").strip()

    # [新增] 第一次生成就先檢查是否太像字幕
    if is_too_similar_to_subtitle(
        trace.get("subtitle_text_used", subtitle_text),
        question_text
    ):
        raise RuntimeError("generated question is too similar to subtitle example")

    solution_lines = data.get("solution_lines") or []

    align_ok, missing_kws = _teacher_alignment_check(teacher_description, question_text, solution_lines)

    rc = build_rule_check(solution_lines, constraints)
    if (not rc.get("ok")) or (not align_ok):
        extra = []
        if not rc.get("ok"):
            extra.append(f"自動驗收失敗：{rc.get('reason')}")
        if not align_ok:
            extra.append("與老師指定需求不夠對齊，缺少關鍵詞：" + ", ".join(missing_kws[:5]))

        retry_prompt = base_prompt + f"\n\n你上一版需要修正：{'；'.join(extra)}。請修正後重新輸出 JSON。"
        data = parsons_ai.call_openai_json(
            system=system_msg,
            user=retry_prompt,
            model=model,
            temperature=gen_temperature,
            max_output_tokens=900
        ) or {}

        question_text = (data.get("question_text") or "").strip()

        # [新增] retry 後也檢查是否太像字幕
        if is_too_similar_to_subtitle(
            trace.get("subtitle_text_used", subtitle_text),
            question_text
        ):
            raise RuntimeError("generated question is too similar to subtitle example")

        solution_lines = data.get("solution_lines") or []
        rc = build_rule_check(solution_lines, constraints)
        align_ok, missing_kws = _teacher_alignment_check(teacher_description, question_text, solution_lines)

    if not rc.get("ok"):
        raise RuntimeError(f"AI IO generation failed: {rc.get('reason')}")
    if not align_ok:
        raise RuntimeError("AI IO generation failed: not aligned with teacher_description")

    distractor_lines = ["x = input()", "y = input()", "print(x - y)", "print(x * y)"][:4]
    blocks = _build_blocks_from_lines(question_text, solution_lines, distractor_lines)

    blocks.update({
        "ai_feedback": {"general": "請確認輸入讀取與輸出格式是否符合題目要求。", "common_mistakes": [], "hints": []},
        "unit_type": "io",
        "constraints": constraints,
        "rule_check": rc,
        "source_subtitle": {
            "subtitle_range": trace["subtitle_range"],
            "text_used": trace["subtitle_text_used"],
            "keywords": trace.get("keywords", []),
        },
        "subtitle_range": trace["subtitle_range"],
        "subtitle_text_used": trace["subtitle_text_used"],
    })
    return blocks

# =========================
# Loop semantic alignment helpers
# =========================

def _detect_loop_semantic(subtitle_text: str, video_title: str = "") -> Dict[str, Any]:
    src = ((video_title or "") + "\n" + (subtitle_text or "")).lower()
    has_desc = any(k in src for k in ["遞減", "倒數", "由大到小", "descending", "decrease"])
    has_asc = any(k in src for k in ["遞增", "由小到大", "ascending", "increase"])
    has_range = any(k in src for k in ["m到n", "m 到 n", "之間", "數列", "range", "範圍"])
    if has_desc and has_range:
        return {"topic": "range_desc"}
    if has_asc and has_range:
        return {"topic": "range_asc"}
    return {"topic": "generic_loop"}


def _build_semantic_loop_task(topic: str, subtitle_text: str, unit: str, video_title: str, level: str = "L1") -> Optional[Dict[str, Any]]:
    constraints = resolve_unit_constraints(unit)
    segs = parse_srt_segments(subtitle_text)
    trace = _pick_trace_window(segs, constraints, max_lines=7)

    if topic == "range_desc":
        question_text = "某遊戲共有 m 關，玩家需要從第 m 關倒數到第 n 關，請依序輸出每一關的編號。"
        solution_lines = [
            "m = int(input())",
            "n = int(input())",
            "for i in range(m, n - 1, -1):",
            "    print(i)",
        ]
        distractor_lines = [
            "for i in range(m, n + 1):",
            "for i in range(n, m - 1, -1):",
            "print(m)",
        ]
    elif topic == "range_asc":
        question_text = "活動編號從 m 到 n 依序排列，請輸出從 m 到 n 的所有編號。"
        solution_lines = [
            "m = int(input())",
            "n = int(input())",
            "for i in range(m, n + 1):",
            "    print(i)",
        ]
        distractor_lines = [
            "for i in range(m, n - 1, -1):",
            "for i in range(n, m + 1):",
            "print(n)",
        ]
    else:
        return None

    blocks = _build_blocks_from_lines(question_text, solution_lines, distractor_lines)

    def _label_for_solution_line(line: str) -> str:
        raw = (line or "").strip()
        low = raw.lower()
        if "input" in low and "m" in low:
            return "讀入起始值 m"
        if "input" in low and "n" in low:
            return "讀入終點值 n"
        if low.startswith("for ") and "-1" in low:
            return "使用遞減迴圈，從 m 逐步走到 n"
        if low.startswith("for "):
            return "使用遞增迴圈，從 m 逐步走到 n"
        if "print" in low:
            return "逐一輸出範圍中的數列值"
        return "完成此步驟所需的處理"

    low_topic = (topic or "").lower()

    def _distractor_semantic_zh(line: str) -> str:
        s = (line or "").strip().lower()
        if low_topic == "range_desc":
            if "n + 1" in s:
                return "這會變成遞增範圍，與題目的遞減數列不一致"
            if "range(n, m - 1, -1)" in s:
                return "起點與終點顛倒，會輸出錯誤的範圍"
            if s.startswith("print("):
                return "只輸出單一值，沒有逐一列出遞減數列"
        if low_topic == "range_asc":
            if "-1" in s:
                return "這會變成遞減方向，與題目的遞增數列不一致"
            if "range(n, m + 1)" in s:
                return "起點與終點顛倒，會輸出錯誤的範圍"
            if s.startswith("print("):
                return "只輸出單一值，沒有逐一列出遞增數列"
        return "此行邏輯可能與題目需求不一致"

    # if template and len(concept_template_slots) == len(solution_lines):
    #     template_slots = concept_template_slots[:]
    # else:
    #     labels = [_label_for_solution_line(x) for x in solution_lines]
    #     template_slots = [{"label": labels[i], "slot": str(i)} for i in range(len(solution_lines))]
    labels = [_label_for_solution_line(x) for x in solution_lines]
    template_slots = [{"label": labels[i], "slot": str(i)} for i in range(len(solution_lines))]
    blocks["template_slots"] = template_slots

    for i, b in enumerate(blocks.get("solution_blocks", []) or []):
        try:
            b["semantic_zh"] = labels[i]
        except Exception:
            pass

    for b in blocks.get("distractor_blocks", []) or []:
        try:
            b["semantic_zh"] = _distractor_semantic_zh(b.get("text", ""))
        except Exception:
            pass

    blocks.update({
        "ai_feedback": {
            "general": "請確認題目是否對齊字幕中的範圍概念、起點終點與遞增/遞減方向。",
            "common_mistakes": ["range 終點寫錯", "遞減題寫成遞增", "把範圍題誤寫成 quit 結束題"],
            "hints": ["先看影片是不是在講 m 到 n 的範圍", "再檢查 range 的方向與步進", "最後確認輸出內容就是數列值"],
        },
        "unit_type": "loop",
        "constraints": constraints,
        "rule_check": build_rule_check(solution_lines, constraints),
        "source_subtitle": {
            "subtitle_range": trace["subtitle_range"],
            "text_used": trace["subtitle_text_used"],
            "semantic_topic": topic,
        },
        "subtitle_range": trace["subtitle_range"],
        "subtitle_text_used": trace["subtitle_text_used"],
        "ai_segment_map": {},
        "ai_slot_hints": {},
        "ai_segments_compact": compact_segments_for_prompt(segs, max_chars=4000) or "",
    })
    return blocks


def _loop_semantic_guard(subtitle_text: str, question_text: str, solution_lines: list, video_title: str = "") -> Tuple[bool, str]:
    topic = (_detect_loop_semantic(subtitle_text, video_title) or {}).get("topic")
    q = (question_text or "").lower()
    code = "\n".join(solution_lines or []).lower()
    mix = q + "\n" + code
    if topic == "range_desc":
        if any(bad in mix for bad in ["quit", "ok", "猜數字", "選單", "登入"]):
            return False, "字幕是 m 到 n 遞減數列，但生成成其他 while/sentinel 情境"
        if "-1" not in code:
            return False, "字幕是遞減數列，但程式沒有負向步進"
    if topic == "range_asc":
        if any(bad in mix for bad in ["quit", "ok", "猜數字", "選單", "登入"]):
            return False, "字幕是 m 到 n 遞增數列，但生成成其他 while/sentinel 情境"
    return True, ""


# =========================
# OpenAI generator
# =========================

def ai_generate_parsons_from_subtitle(subtitle_text: str, unit: str, video_title: str, teacher_description: str = "", level: str = "L1", stable_mode: bool = False) -> Dict[str, Any]:
    """
    V1.6-goClass-lite（Parsons 低 token + 閉環驗收 + 回看片段）
    - Parsons 題（非選擇題）
    - 低 token：AI 只生「question_text + solution_lines」，干擾題/中文語意用本地規則（0 token）
    - goClass 風：本地驗收確保 solution_lines 可執行且符合題意（閉環），縮排錯也算錯（透過干擾題 + 本地檢查）
    - B：額外用 AI 產出 segment_map（含 evidence）+ slot_hints（每格一句），用於錯誤回看影片片段
    """
    if not ai_enabled():
        raise RuntimeError("AI_ENABLED is false -> skip OpenAI")
    if not subtitle_text:
        raise RuntimeError("subtitle_text is empty")

    # --- subtitles ---
    segs = parse_srt_segments(subtitle_text)
    cleaned = strip_srt_noise(subtitle_text)

    # [新增] 低 token：字幕壓縮（只提供必要片段給 AI）
    segs_compact = compact_segments_for_prompt(segs, max_chars=4000)
    if not segs_compact:
        # 仍允許無字幕，但 segment_map 會走 fallback
        segs_compact = ""

    # ✅ 修正：老師有描述時，任何情境都不得攔截 — 不只圖形類
    teacher_desc_lc = (teacher_description or "").lower()
    has_teacher_desc = bool((teacher_description or "").strip())

    if not has_teacher_desc:
        semantic = _detect_loop_semantic(subtitle_text, video_title)
        semantic_task = _build_semantic_loop_task(
            (semantic or {}).get("topic"),
            subtitle_text,
            unit,
            video_title,
            level=level
        )
        if semantic_task is not None:
            return semantic_task

    import random, hashlib, ast, io, contextlib

    gen_temperature = 0.05 if stable_mode else 0.2
    model = (os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()

    # ===============================
    # [新增] 0 token：用字幕關鍵字挑情境（看不出來就 seed 隨機）
    # ✅ 修正：老師有描述時，完全跳過 keyword matching，直接讓 AI 依描述生題
    # ===============================
    def _kw_hit(*kws: str) -> bool:
        t = (cleaned or "").lower()
        return any((kw.lower() in t) for kw in kws if kw)

    # ===============================
    # ✅ 修正根本問題：依 loop_style 選對應的情境池
    # 原本 scenarios 全是 while/sentinel 型，for 迴圈字幕卻也走這裡，導致出 while 題
    # ===============================
    loop_style = (resolve_unit_constraints(unit) or {}).get("loop_style") or "either"

    scenarios_for = [
        {
            "name": "for_sum",
            "desc": "累加計算：從 1 到 n，計算所有數字的總和並輸出。",
            "tests": [["5"]],
            "check": "nums>=1",
            "keywords": ["總和", "累加", "加總", "sum", "total"],
        },
        {
            "name": "for_print_range",
            "desc": "數列列印：輸入起始值和終點值，依序列出範圍內的每個數字。",
            "tests": [["1", "5"]],
            "check": "nums>=1",
            "keywords": ["列出", "列印", "依序", "range", "數列"],
        },
        {
            "name": "for_even",
            "desc": "偶數列印：列出 1 到 n 之間所有的偶數。",
            "tests": [["10"]],
            "check": "nums>=1",
            "keywords": ["偶數", "even"],
        },
        {
            "name": "for_multiples",
            "desc": "倍數列印：列出 1 到 n 之間所有 3 的倍數。",
            "tests": [["15"]],
            "check": "nums>=1",
            "keywords": ["倍數", "multiple"],
        },
        {
            "name": "for_desc",
            "desc": "倒數列印：從 n 倒數到 1，依序列出每個數字。",
            "tests": [["5"]],
            "check": "nums>=1",
            "keywords": ["遞減", "倒數", "descending"],
        },
        {
            "name": "for_count_input",
            "desc": "固定次數輸入：輸入 n 筆成績，計算總分並輸出。",
            "tests": [["3", "80", "90", "70"]],
            "check": "nums>=1",
            "keywords": ["成績", "分數", "次數", "筆數"],
        },
    ]

    scenarios_while = [
        {
            "name": "avg_scores",
            "desc": "成績平均：持續輸入成績直到輸入 -1（-1 不納入），最後輸出平均與筆數。",
            "tests": [["10", "20", "-1"]],
            "check": "nums>=2",
            "keywords": ["平均", "成績", "分數", "mean", "average"],
        },
        {
            "name": "sum_prices",
            "desc": "購物累計：重複輸入價格直到輸入 0 結束，最後輸出總金額與筆數。",
            "tests": [["30", "70", "0"]],
            "check": "nums>=2",
            "keywords": ["金額", "價格", "總和", "sum", "total"],
        },
        {
            "name": "sentinel_ok",
            "desc": "資料輸入：反覆輸入文字直到輸入 'end' 結束，最後輸出輸入筆數。",
            "tests": [["a", "b", "end"]],
            "check": "nums>=1",
            "keywords": ["直到", "結束", "停止", "ok", "quit", "end"],
        },
        {
            "name": "validate_range",
            "desc": "資料驗證：反覆輸入數值直到介於指定範圍內才結束，最後輸出有效數值。",
            "tests": [["-5", "200", "18"]],
            "check": "nums>=1",
            "keywords": ["驗證", "範圍", "合法", "valid"],
        },
        {
            "name": "guess_game",
            "desc": "猜數字：反覆輸入猜測直到猜中答案（可在程式中寫死答案），輸出猜測次數。",
            "tests": [["3", "7"]],
            "check": "nums>=1",
            "keywords": ["猜", "猜數字", "guess"],
        },
    ]

    # 依 loop_style 選對應情境池
    if loop_style == "for_only":
        scenarios = scenarios_for
    elif loop_style == "while_only":
        scenarios = scenarios_while
    else:
        scenarios = scenarios_for + scenarios_while

    # ✅ 老師有描述時：不走 keyword matching，scenario_desc 直接用老師描述
    seed_src = (unit or "") + "|" + (video_title or "") + "|" + ((cleaned or "")[:1500])
    seed_int = int(hashlib.md5(seed_src.encode("utf-8")).hexdigest(), 16)
    rnd_seed = seed_int if stable_mode else (seed_int ^ random.randint(0, 10**9))
    rnd = random.Random(rnd_seed)

    if has_teacher_desc:
        # ✅ 修正：圖形/數列類題目產生有意義的 scenario_desc，不要直接把老師描述當情境
        _td_check = (teacher_description or "").lower()
        _is_pattern = any(k in _td_check for k in [
            "三角形", "直角", "正方形", "菱形", "星號", "圖形", "pattern", "triangle", "square", "*",
            "數列", "巢狀", "nested",
        ])
        if _is_pattern:
            picked = {
                "name": "teacher_pattern",
                "desc": f"圖形輸出：根據老師指定主題「{teacher_description.strip()}」，使用 for 迴圈輸出對應的圖形或數列。",
                "tests": [],  # 純輸出型，不需測資
                "check": "skip",
            }
        else:
            # 一般老師描述：直接當情境
            picked = {"name": "teacher_defined", "desc": teacher_description.strip(),
                      "tests": [["1", "2", "3"]], "check": "nums>=1"}
    else:
        # keyword pick
        picked = None
        for sc in scenarios:
            if _kw_hit(*sc.get("keywords", [])):
                picked = sc
                break

        if not picked:
            picked = rnd.choice(scenarios)
        else:
            seed_src2 = (unit or "") + "|" + (video_title or "") + "|" + picked["name"] + "|" + ((cleaned or "")[:800])
            seed_int2 = int(hashlib.md5(seed_src2.encode("utf-8")).hexdigest(), 16)
            rnd_seed2 = seed_int2 if stable_mode else (seed_int2 ^ random.randint(0, 10**9))
            rnd = random.Random(rnd_seed2)

    scenario_desc = picked["desc"]
    scenario_tests = picked["tests"]
    scenario_check = picked["check"]

        # ===============================
    # [0309新增] concept layer：讓 prompt 更穩定、更多樣
    # ✅ 修正：傳入 teacher_description，讓 concept 也依老師描述推斷
    # ===============================
    plan = build_generation_plan(unit, subtitle_text, video_title, teacher_description)
    concept = (plan.get("concept") or "").strip()
    scenario_hint = (plan.get("scenario") or "").strip()
    anti_copy_rules = plan.get("anti_copy_rules") or []

    # ✅ 修正 Bug 2：先宣告 concept_template_solution，再後面注入到 base_prompt
    template = build_template_solution(concept) if concept else None
    concept_template_solution = (template or {}).get("solution_lines") or []
    concept_template_slots = (template or {}).get("template_slots") or []

    # ===============================
    # [新增] A 版多樣化（只影響 prompt，不改 schema/路由/閉環驗收）
    # 目的：同一影片多次 regenerate 時，避免一直落在同一模板
    # ===============================
    # 使用 rnd（上方已依影片/字幕建立 seed，再混入一次 random 讓 regenerate 具變化）
    variation_seed = 314159 if stable_mode else rnd.randint(100000, 999999)

    # 依情境挑選 sentinel — ✅ for_only 完全不需要 sentinel 邏輯
    if loop_style == "for_only":
        sentinel_value = ""
        sentinel_note = "本題為 for 迴圈，不需 sentinel 結束條件"
        avoid_while_true = False
    elif picked.get("name") == "sentinel_ok":
        # ✅ 修正問題2：sentinel 字串要和 scenario_desc / tests 同步
        sentinel_value = rnd.choice(["end", "quit", "stop"])
        sentinel_note = f"結束字串為 '{sentinel_value}'（字串比較，非數字）"
        scenario_desc = f"資料輸入：反覆輸入文字直到輸入 '{sentinel_value}' 結束，最後輸出輸入筆數。"
        scenario_tests = [["a", "b", sentinel_value]]
        scenario_check = "nums>=1"
        avoid_while_true = False if stable_mode else rnd.choice([True, True, False])
    else:
        sentinel_value = str(rnd.choice([0, -1, 999]))
        sentinel_note = f"結束數值為 {sentinel_value}"
        avoid_while_true = False if stable_mode else rnd.choice([True, True, False])

    # 變化：輸出格式（不影響 output_check 的 nums>=N）
    output_style = rnd.choice([
        "請輸出『筆數』與『結果』兩個數值（同一行或分行皆可）",
        "請輸出結果並加上簡短說明文字（例如：Total=... 或 Count=...）",
        "請輸出結果，並同時輸出最後一次輸入的值（若題意允許）",
    ])

    # 變化：敘述情境詞（只影響題目文字，不影響程式可執行性）
    theme_word = rnd.choice(["成績", "購物金額", "溫度", "里程", "練習次數", "存款變動", "投票"])

    # [新增] 若情境描述/測資含固定 -1，改成本次 sentinel（避免每次都 -1）
    # ✅ 只對數字 sentinel 做替換；字串 sentinel 已在上方同步更新 scenario_desc/tests
    try:
        if picked.get("name") != "sentinel_ok":
            if isinstance(scenario_desc, str) and "-1" in scenario_desc and sentinel_value != "-1":
                scenario_desc = scenario_desc.replace("-1", sentinel_value)
            new_tests = []
            for case in (scenario_tests or []):
                if isinstance(case, list):
                    new_case = [(sentinel_value if str(x).strip() == "-1" else x) for x in case]
                    new_tests.append(new_case)
                else:
                    new_tests.append(case)
            scenario_tests = new_tests
    except Exception:
        pass

    # ===============================
    # [新增] 本地 0 token：安全驗收（閉環）
    # ===============================
    import re as _re

    _cjk_re = _re.compile(r"[\u4e00-\u9fff]")
    _python_token_re = _re.compile(r"\b(while|for|if|elif|else|break|continue|input|print|int|float|str|len|range)\b|[=:+\-*/()<>]")
    # [新增] 清理 input() 的中文提示字串，避免被本地驗收判定為「含中文」
    # 例：int(input("請輸入...")) -> int(input())
    def _strip_input_prompt(s: str) -> str:
        """Remove prompt text inside input('...') / input("...") to avoid Chinese inside solution_lines."""
        if not s:
            return s
        # input('請輸入...') -> input()
        return _re.sub(r"\binput\s*\(\s*([\"']).*?\1\s*\)", "input()", s)

    def _strip_print_label(s: str) -> str:
        """If a print(...) call starts with a string literal label (often Chinese), drop that label.
        Examples:
          print('筆數:', count) -> print(count)
          print("總和=", total, "筆數=", count) -> print(total, count)  (drops only leading label)
          print('僅文字') -> print(0)  (avoid empty print, keep numeric output)
        """
        if not s or "print" not in s:
            return s
        # 保留原始縮排，避免 for/while 區塊在清洗後失去縮排導致 compile failed
        m = _re.match(r"^(\s*)(.*)$", s)
        indent = m.group(1) if m else ""
        body = m.group(2) if m else s
        try:
            tree = ast.parse(body.strip(), mode="exec")
        except Exception:
            return s

        # Expect single Expr(Call(print,...))
        if not tree.body or not isinstance(tree.body[0], ast.Expr):
            return s
        call = tree.body[0].value
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "print"):
            return s

        args = list(call.args)
        if not args:
            return s

        def _is_str_node(n):
            return (isinstance(n, ast.Constant) and isinstance(n.value, str)) or isinstance(n, ast.JoinedStr)

        # Drop leading string label（僅在後面仍有其他實際值時才移除）
        if _is_str_node(args[0]):
            if len(args) >= 2:
                args = args[1:]
            else:
                # 單純 print("***") 這種輸出圖形行不能改壞
                return s

        # 若移除後沒有參數，保留原始行，避免語意被改壞
        if not args:
            return s

        # Rebuild minimal print(...) source using ast.unparse (py>=3.9)
        try:
            new_call = ast.Call(func=ast.Name(id="print", ctx=ast.Load()), args=args, keywords=call.keywords)
            new_expr = ast.Expr(value=new_call)
            new_mod = ast.Module(body=[new_expr], type_ignores=[])
            return indent + ast.unparse(new_mod).strip()
        except Exception:
            return s

    def _sanitize_solution_lines(lines: list) -> list:
        out = []
        for ln in (lines or []):
            s = str(ln)
            # 1) remove Chinese inside input prompt
            s = _strip_input_prompt(s)
            # 2) drop leading print label (often Chinese) so solution_lines stays code-only
            s = _strip_print_label(s)
            out.append(s)
        return out

    def _looks_like_code(line: str) -> bool:
        s = (line or "").rstrip("\n")

        # ✅ 清除常見隱藏字元（不影響實際輸出）
        s = s.replace("\ufeff", "")   # BOM
        s = s.replace("\u3000", " ")  # 全形空白
        s = s.strip("\r")

        if not s.strip():
            return False

        if _cjk_re.search(s):
            return False

        return bool(_python_token_re.search(s))

    def _compile_ok(lines: list) -> bool:
        try:
            compile("\n".join(lines), "<parsons_ai>", "exec")
            return True
        except Exception:
            return False

    def _compile_error(lines: list) -> str:
        try:
            compile("\n".join(lines), "<parsons_ai>", "exec")
            return ""
        except SyntaxError as e:
            return f"SyntaxError line {e.lineno}: {e.msg} (near: {repr(e.text or '')})"
        except Exception as ex:
            return str(ex)

    def _has_loop(lines: list) -> bool:
        joined = "\n".join(lines).lower()
        return ("while " in joined) or ("for " in joined)

    def _loop_has_body_indent(lines: list) -> bool:
        has_loop_header = False
        for i, ln in enumerate(lines or []):
            stripped = (ln or "").strip()
            if stripped.startswith("for ") or stripped.startswith("while "):
                has_loop_header = True
                if i + 1 >= len(lines):
                    return False
                nxt = lines[i + 1] or ""
                if not (nxt.startswith("    ") or nxt.startswith("\t")):
                    return False
        return True if has_loop_header else False

    def _has_print(lines: list) -> bool:
        return "print(" in ("\n".join(lines).lower())

    def _has_input(lines: list) -> bool:
        return "input(" in ("\n".join(lines).lower())

    def _has_end_control(lines: list) -> bool:
        joined = "\n".join(lines).lower()
        return ("break" in joined) or any(ln.strip().startswith("while ") and ("true" not in ln.lower()) for ln in lines)

    # AST sandbox
    ALLOWED_NODES = (
        ast.Module, ast.Assign, ast.AugAssign, ast.Expr, ast.Call,
        ast.Name, ast.Load, ast.Store, ast.Constant,
        ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
        ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
        ast.If, ast.While, ast.For, ast.Break, ast.Continue, ast.Pass,
        ast.List, ast.Tuple,
    )
    ALLOWED_FUNCS = {"input", "print", "int", "float", "str", "len", "range"}

    def _ast_safe(code: str) -> bool:
        try:
            tree = ast.parse(code, mode="exec")
        except Exception as e:
            print("[AST_SAFE] parse failed:", repr(e))
            print("[AST_SAFE] code:\n" + code)
            return False

        for node in ast.walk(tree):
            # ✅ 寬鬆模式：移除 ALLOWED_NODES 白名單（這是你一直 AST rejected 的主因）
            # if not isinstance(node, ALLOWED_NODES):
            #     return False

            # ✅ 仍保留：擋掉危險/你不希望出現的語法
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.Attribute, ast.Subscript,
                                 ast.Lambda, ast.With, ast.Try, ast.FunctionDef, ast.ClassDef)):
                print("[AST_SAFE] rejected node:", type(node).__name__)
                print("[AST_SAFE] code:\n" + code)
                return False

            # ✅ 仍保留：呼叫只允許白名單函式（避免 eval/exec/open 等）
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id not in ALLOWED_FUNCS:
                        print("[AST_SAFE] rejected call:", node.func.id)
                        print("[AST_SAFE] code:\n" + code)
                        return False
                else:
                    # 例如 obj.method() 會是 Attribute call（上面已擋 Attribute），但這裡保險
                    print("[AST_SAFE] rejected call: non-Name func")
                    print("[AST_SAFE] code:\n" + code)
                    return False

        return True

    def _run_with_inputs(code: str, inputs: list) -> str:
        it = iter(inputs)

        def _fake_input(prompt=""):
            try:
                return next(it)
            except StopIteration:
                return ""

        out = io.StringIO()
        safe_globals = {"__builtins__": {}}
        safe_locals = {
            "input": _fake_input,
            "print": lambda *args, **kwargs: print(*args, file=out, **{k: v for k, v in kwargs.items() if k in ("sep", "end")}),
            "int": int,
            "float": float,
            "str": str,
            "len": len,
            "range": range,
        }
        with contextlib.redirect_stdout(out):
            exec(code, safe_globals, safe_locals)
        return out.getvalue().strip()

    def _output_check(check: str, out: str) -> bool:
        # ✅ 圖形題不需驗測資輸出
        if check == "skip":
            return True
        if not out:
            return False
        # ✅ 修正問題2：支援 'Total entries=3' 這類帶標籤的輸出
        nums = _re.findall(r"-?\d+\.?\d*", out)
        if check.startswith("nums>="):
            try:
                need = int(check.split(">=")[1])
            except Exception:
                need = 1
            return len(nums) >= need
        return True

    def _auto_grade(solution_lines: list) -> str:
        if not _compile_ok(solution_lines):
            detail = _compile_error(solution_lines)
            print(f"[_auto_grade] compile failed: {detail}")
            print(f"[_auto_grade] solution_lines: {solution_lines}")
            return f"compile failed: {detail}" if detail else "compile failed"

        for i, x in enumerate(solution_lines):
            if not _looks_like_code(x):
                print("[AI_VERIFY] bad line index:", i)
                print("[AI_VERIFY] bad line repr :", repr(x))
                return "solution_lines contains Chinese or non-code"

        if any(_re.search(r"^\s*def\s+", x) for x in solution_lines):
            return "contains def"

        if any(_re.search(r"^\s*class\s+", x) for x in solution_lines):
            return "contains class"

        if not _has_loop(solution_lines):
            return "missing loop"

        if not _loop_has_body_indent(solution_lines):
            return "loop body indentation missing"

        if not _has_print(solution_lines):
            return "missing print output"

        # ✅ 修正：圖形/for-range 類題目不需要 input() 也不需要結束條件
        # 判斷是否為「純輸出型」題（for range + print，無需互動）
        is_output_only = (
            not _has_input(solution_lines) and
            _has_loop(solution_lines) and
            not _re.search(r"\bwhile\b", "\n".join(solution_lines))
        )

        if not is_output_only:
            # 互動型題目才要求有 input
            if not _has_input(solution_lines):
                return "missing input"

            # ✅ for_only：不需要 end_control（for 迴圈用 range 控制次數，不用 break/sentinel）
            # 同時拒絕「for range(大數) + break」這種用 for 假裝 while 的 hack
            if loop_style == "for_only":
                code_joined = "\n".join(solution_lines)
                # 拒絕 for _ in range(大數字) 配合 break 的 sentinel hack
                if _re.search(r"for\s+\w+\s+in\s+range\s*\(\s*\d{3,}", code_joined) and "break" in code_joined:
                    return "for_only: detected sentinel hack (for range(large) + break)"
                # 拒絕 while 出現在 for_only 題目裡
                if _re.search(r"\bwhile\b", code_joined):
                    return "for_only: contains while"
            else:
                if not _has_end_control(solution_lines):
                    return "missing end condition/break"

        code = "\n".join(solution_lines)

        if not _ast_safe(code):
            return "AST rejected"

        # 純輸出型：不跑測資（無 input，exec 會卡住）；直接通過
        if is_output_only:
            return ""

        # 互動型：跑測資驗收
        test_inputs = (scenario_tests[:1] or [[]])
        for inputs in test_inputs:
            try:
                out = _run_with_inputs(code, inputs)
            except Exception as e:
                print("[AI_VERIFY] exec failed:", repr(e))
                print("[AI_VERIFY] code:\n", code)
                return f"exec failed: {e}"

            if not _output_check(scenario_check, out):
                print("[AI_VERIFY] output check failed")
                print("[AI_VERIFY] output was:", repr(out))
                return "output check failed"

        return ""

    # ===============================
    # [新增] AI 生成（最小輸出）+ 失敗只修一次（省 token）
    # ===============================
    system_msg = "你是Python程式設計助教。請嚴格只輸出『合法 JSON』，不要輸出 Markdown 或多餘文字。"

    banned_patterns = """
請避免以下過度常見模板與禁止項：
- 直到輸入 'ok' 為止（除非情境就是 ok sentinel）
- def / class
- 中文註解或中文程式碼

- 盡量避免 while True + break（改用 while 條件式）
- 避免固定 sentinel 永遠用 -1（可用 0 或 999）
- 避免每次輸出格式都一樣（例如同時輸出筆數與結果）
"""

    loop_style_rule = ""
    loop_style = (resolve_unit_constraints(unit) or {}).get("loop_style") or "either"
    if loop_style == "for_only":
        loop_style_rule = (
            "- 本題必須使用 for 迴圈搭配 range()，禁止使用 while。\n"
            "- ❌ 嚴格禁止：for _ in range(大數字) + break 這種用 for 假裝 while 的寫法。\n"
            "- for 迴圈的次數必須由 range() 的參數明確決定（例如 range(n) 或 range(1, n+1)）。\n"
            "- 不需要 sentinel / break 結束條件，迴圈跑完就結束。"
        )
    elif loop_style == "while_only":
        loop_style_rule = "- 本題必須使用 while 迴圈，禁止使用 for。"

    # ✅ 根據老師描述判斷是否為「純輸出型」（圖形、數列），動態調整規則
    _td_lc = (teacher_description or "").lower()
    _is_pattern_task = has_teacher_desc and any(k in _td_lc for k in [
        "三角形", "直角", "正方形", "菱形", "星號", "圖形", "pattern", "triangle", "square", "*",
        "數列", "列印", "輸出數字", "輸出星號",
    ])

    if _is_pattern_task:
        loop_rules = (
            "- 使用 for 迴圈（可巢狀）\n"
            "- 迴圈內至少一行縮排（4 spaces）\n"
            "- 只使用 print() 輸出，不需要 input()\n"
            "- 禁止使用 input() / while / break"
        )
        diversity_block = ""  # 圖形題不需要 sentinel/output_style 多樣化
    else:
        loop_rules = (
            "- 迴圈（for 或 while）\n"
            "- 迴圈內至少一行縮排（4 spaces）\n"
            "- 結束條件（可用 break）\n"
            "- 最後要輸出結果（print）"
            " 使用 for 或 while"
            " 迴圈內至少一行縮排（4 spaces）"
            " - 巢狀迴圈內的 print() 必須正確縮排"
            " 最後要輸出結果（print)"
        )
        diversity_block = f"""
【多樣化要求（本次 regenerate 變化）】
- 變化種子：{variation_seed}
- 情境詞請優先使用：{theme_word}
- {sentinel_note}
- 若可行，請避免使用 while True + break：{avoid_while_true}
- 輸出格式要求：{output_style}
- 請至少變化『結束條件』與『輸出格式』兩項，避免與前次相同模板。
"""

    base_prompt = f"""
你要產生一題 Parsons 程式重組題（不是選擇題）。
題型固定：迴圈，不得使用 def/class。

【情境】
{scenario_desc}
【概念對齊】
- 本題優先對應的程式概念：{concept or "generic_loop"}
- 可參考的生活化情境詞：{scenario_hint or theme_word}
- 題目可以沿用相同程式概念，但不要直接照抄字幕範例

【硬性規則（必須遵守）】
- 只輸出合法 JSON（不要 Markdown、不要多餘文字）
- 只輸出以下欄位：question_text、solution_lines
- question_text：繁體中文，描述要做什麼（不要直接透露程式碼）
- solution_lines：英文 Python code 4~14 行，且必須包含：
  {loop_rules}
- solution_lines 禁止中文與註解
- 題目情境不可直接複製影片字幕中的原始例子
- 可保留相同概念，但必須改寫人物、場景、數字或任務目標
- anti_copy_rules：{anti_copy_rules}
【老師指定描述（最高優先）】
若老師有提供描述，請優先依照老師描述生成題目。
不得違反老師指定的限制條件。
{teacher_description}
若老師描述明確，請優先依據描述生成題目。
{loop_style_rule}
{banned_patterns if not _is_pattern_task else ""}
{diversity_block}

輸出 JSON 格式：
{{
  "question_text": "題目敘述（繁中）",
  "solution_lines": ["python code line 1", "..."]
}}

字幕（含時間戳）（格式：[start-end] text）：
{segs_compact}
""".strip()
    if anti_copy_rules:
        base_prompt += "\n\n【避免與影片範例過於相似】\n"
        base_prompt += "\n".join(anti_copy_rules)

    # ✅ 修正 Bug 2（續）：template hint 在 base_prompt 建立後才能注入
    if concept_template_solution:
        base_prompt += "\n\n【參考程式架構（概念：" + concept + "）】\n"
        base_prompt += "\n".join(concept_template_solution)
        base_prompt += "\n\n請依照此邏輯設計題目，但情境必須不同。"

    MAX_RETRY = 3
    data = None
    last_error = ""
    last_candidate = None

    for attempt in range(MAX_RETRY):
        if attempt == 0:
            user_msg = base_prompt
        else:
            prev = last_candidate or {}
            prev_q = (prev.get("question_text") or "").strip()
            prev_sol = prev.get("solution_lines") or []
            err_hint = ""
            if "compile failed" in last_error:
                err_hint = f"\n⚠️  編譯錯誤詳情：{last_error}\n請逐行檢查：縮排是否正確（4 spaces）、冒號是否遺漏、括號是否配對。"
            elif "missing" in last_error:
                err_hint = f"\n⚠️  {last_error}：請確認 solution_lines 包含所需語法元素。"
            elif "for_only" in last_error:
                err_hint = "\n⚠️  不可使用 while 或 break，請改用 for i in range(n) 控制次數。"
            user_msg = f"""
你上一版未通過自動驗收：{last_error}{err_hint}

【只修正 solution_lines】（保持同一情境方向）
- solution_lines 必須是合法 Python，可以 compile 執行
- 禁止中文、禁止 # 註解、禁止 def/class
- 必須包含 for 或 while 迴圈，迴圈內縮排 4 spaces
- 只輸出合法 JSON（欄位仍是 question_text + solution_lines）

上一版 question_text（參考）：{prev_q}
上一版 solution_lines：{prev_sol}
""".strip()

        text = (parsons_ai.call_openai_output_text(system=system_msg, user=user_msg, model=model, temperature=gen_temperature, max_output_tokens=900) or "").strip()
        cand = safe_json_loads(text)
        if not cand:
            last_error = "non-JSON"
            last_candidate = None
            continue

        q = (cand.get("question_text") or "").strip()
        sol = cand.get("solution_lines") or []
        last_candidate = cand

        if not q:
            last_error = "missing question_text"
            continue
        if not isinstance(sol, list) or len(sol) < 4 or len(sol) > 14:
            last_error = "solution_lines length not 4~14"
            continue

        sol = [str(x) for x in sol]
        sol = _sanitize_solution_lines(sol)  # [新增]
        cand["solution_lines"] = sol

        grade_err = _auto_grade(sol)
        if grade_err:
            last_error = grade_err
            continue

        ok_align, align_reason = _loop_semantic_guard(subtitle_text, q, sol, video_title)
        if not ok_align:
            last_error = align_reason
            continue

        data = cand
        break

    if not data:
        if concept_template_solution:
            data = {
                "question_text": f"請根據題意完成一題與「{scenario_hint or theme_word}」相關的迴圈程式重組。",
                "solution_lines": concept_template_solution[:]
            }
        else:
            raise RuntimeError(f"AI generation failed: {last_error}")

    question_text = (data.get("question_text") or "").strip()
    solution_lines = data["solution_lines"]

    # ===============================
    # [新增] B：segment_map + slot_hints（含 evidence）— 低 token 對齊任務
    # ===============================
    seg_map = {}
    slot_hints = {}

    if ai_enabled() and segs_compact:
        try:
            align_system = (
                "你是Python教學助教。你要把『每一行程式（slot）』對齊到字幕時間戳。\n"
                "請嚴格只輸出合法 JSON，不要輸出 Markdown 或多餘文字。\n"
                "必須包含：segment_map、slot_hints。\n"
                "segment_map 每格都要有，並含 evidence（引用字幕關鍵句，可短）。"
            )
            align_user = f"""
請根據題目與字幕，為每一格（slot_index）提供最相關的回看片段時間（start/end，秒），並給一句短提示（hint）。

輸出 JSON 格式：
{{
  "segment_map": [
    {{"slot_index": 0, "start": 12.3, "end": 24.8, "evidence": "字幕關鍵句（繁中，可短）"}},
    ...
  ],
  "slot_hints": [
    {{"slot_index": 0, "hint": "如果第1格錯，提醒學生什麼（繁中，1句）"}},
    ...
  ]
}}

限制：
- slot_index 必須從 0 開始，數量要等於 solution_lines 行數
- start/end 必須是字幕時間戳合理區間（秒數）
- evidence 必須是字幕中的關鍵句（可短，不要超過 15 字）
- hint 每格 1 句，繁體中文，不要直接給出程式碼或變數名

題目：{question_text}
solution_lines：{solution_lines}

字幕（含時間戳）（格式：[start-end] text）：
{segs_compact}
""".strip()

            align = parsons_ai.call_openai_json(
                system=align_system,
                user=align_user,
                model=model,
                temperature=gen_temperature,
                max_output_tokens=900,
            ) or {}

            seg_map_in = align.get("segment_map") or []
            hint_in = align.get("slot_hints") or []

            for it in seg_map_in:
                try:
                    si = int(it.get("slot_index"))
                    s = float(it.get("start"))
                    e = float(it.get("end"))
                    if si < 0 or si >= len(solution_lines) or e <= s:
                        continue
                    seg_map[str(si)] = {
                        "start": s,
                        "end": e,
                        "evidence": (it.get("evidence") or "").strip(),
                    }
                except Exception:
                    continue

            for it in hint_in:
                try:
                    si = int(it.get("slot_index"))
                    if si < 0 or si >= len(solution_lines):
                        continue
                    slot_hints[str(si)] = (it.get("hint") or "").strip()
                except Exception:
                    continue
        except Exception:
            seg_map = {}
            slot_hints = {}

    # [新增] 若 AI 對齊失敗：用字幕片段平均分配 fallback（0 token）
    if not seg_map:
        if segs:
            n = len(solution_lines)
            picked_segs = segs[: max(n, 1)]
            for i in range(n):
                s = picked_segs[min(i, len(picked_segs)-1)]
                seg_map[str(i)] = {
                    "start": float(s.get("start", 0.0)),
                    "end": float(s.get("end", float(s.get("start", 0.0)) + 5.0)),
                    "evidence": (s.get("text", "") or "").strip()[:15],
                }
        else:
            for i in range(len(solution_lines)):
                seg_map[str(i)] = {"start": 0.0, "end": 5.0, "evidence": ""}

    if not slot_hints:
        for i in range(len(solution_lines)):
            slot_hints[str(i)] = "請確認此步驟是否在正確的流程位置與縮排層級。"

    # ===============================
    # [新增] 干擾題 mutation（0 token，含縮排錯）
    # ===============================
    def _normalize_line(s: str) -> str:
        return _re.sub(r"\s+", "", (s or "").strip()).lower()

    def _tokenize(s: str):
        return _re.findall(r"[A-Za-z_]+|\d+|==|!=|<=|>=|\+=|-=|\*=|/=|[=:+\-*/()<>]", (s or ""))

    def _diff_score(a: str, b: str) -> int:
        sa, sb = set(_tokenize(a)), set(_tokenize(b))
        return len(sa.symmetric_difference(sb))

    def _mutate_line(line: str) -> list:
        variants = []
        s = line
        if "+=" in s:
            variants.append(s.replace("+=", "="))
        if "==" in s:
            variants.append(s.replace("==", "!="))
        if "!=" in s:
            variants.append(s.replace("!=", "=="))
        if "<=" in s:
            variants.append(s.replace("<=", ">="))
        if ">=" in s:
            variants.append(s.replace(">=", "<="))
        if s.strip() == "break":
            variants.append("continue")
        if "int(input(" in s:
            variants.append(s.replace("int(input(", "input("))
        if "float(input(" in s:
            variants.append(s.replace("float(input(", "input("))
        if _re.search(r"\bcount\s*\+=\s*1\b", s):
            variants.append(_re.sub(r"\bcount\s*\+=\s*1\b", "count += 0", s))
        return variants

    def _make_indent_wrong(line: str) -> str:
        if line.startswith("    "):
            return line[4:]
        return "    " + line

    # =========================================
    # 干擾提
    def _distractor_semantic_zh(line: str) -> str:
        s = (line or "").strip().lower()
        if not (line or "").startswith("    ") and ("+=" in s or "=" in s):
            return "此行可能不在正確縮排層級，導致流程不符合預期"
        if "==" in s or "!=" in s or "<" in s or ">" in s:
            return "條件判斷可能寫反，導致提早結束或無法結束"
        if "break" in s or "continue" in s:
            return "迴圈控制語句可能使用不當，造成流程錯誤"
        if "+=" in s or "=" in s:
            return "統計更新方式可能錯誤，導致結果不正確"
        if "input" in s:
            return "資料處理方式可能不一致，導致判斷或計算出錯"
        return "此行邏輯可能與題目需求不一致"

    distractor_lines = []
    sol_norm = {_normalize_line(x) for x in solution_lines}

    candidates = []
    for ln in solution_lines:
        for v in _mutate_line(ln):
            if v and _looks_like_code(v):
                candidates.append(v)

    # 強制加入至少一個縮排錯版本
    indented = [x for x in solution_lines if x.startswith("    ")]
    if indented:
        candidates.append(_make_indent_wrong(indented[0]))
    else:
        candidates.append(_make_indent_wrong(solution_lines[-1]))

    uniq = []
    seen = set()
    for c in candidates:
        cn = _normalize_line(c)
        if cn in sol_norm:
            continue
        if cn in seen:
            continue
        seen.add(cn)
        uniq.append(c)

    def _best_diff_to_solution(c: str) -> int:
        return min((_diff_score(c, s) for s in solution_lines), default=0)

    uniq.sort(key=_best_diff_to_solution, reverse=True)
    for c in uniq:
        if len(distractor_lines) >= 3:
            break
        distractor_lines.append(c)

    if len(distractor_lines) < 2:
        fallback = ["count = 1", "total = score", "if score != -1: break", "continue"]
        for f in fallback:
            fn = _normalize_line(f)
            if fn in sol_norm or fn in {_normalize_line(x) for x in distractor_lines}:
                continue
            distractor_lines.append(f)
            if len(distractor_lines) >= 3:
                break

    # ===============================
    # [新增] 中文語意 template_slots（0 token，不洩漏答案/不出現 input） 
    # 備選題
    # ===============================
    def _label_for_solution_line(line: str) -> str:
        raw = (line or "").strip()
        low = raw.lower()
        if _re.search(r"=\s*0\b", low):
            return "設定統計所需的起始狀態"
        if raw.endswith(":") and (low.startswith("while ") or low.startswith("for ")):
            return "建立重複處理的流程架構"
        if "input" in low:
            return "取得新資料並準備後續處理"
        if low.startswith("if ") and "break" in low:
            return "判斷是否達到結束條件並結束流程"
        if low.startswith("if "):
            return "根據條件決定下一步處理"
        if "+=" in low:
            return "更新統計或累積的結果"
        if "print" in low:
            return "輸出整理後的結果"
        if "=" in low:
            return "更新流程中需要記錄的狀態"
        if low.strip() == "break":
            return "在適當時機結束重複流程"
        return "完成此步驟所需的處理"

    labels = [_label_for_solution_line(x) for x in solution_lines]
    template_slots = [{"label": labels[i], "slot": str(i)} for i in range(len(solution_lines))]
    solution_blocks = [{"id": f"b{i+1}", "text": line, "type": "core"} for i, line in enumerate(solution_lines)]
    distractor_blocks = [{"id": f"d{i+1}", "text": line, "type": "distractor"} for i, line in enumerate(distractor_lines)]

    for i in range(len(distractor_blocks)):
        try:
            distractor_blocks[i]["semantic_zh"] = _distractor_semantic_zh(distractor_blocks[i]["text"])
        except Exception:
            pass

    pool = distractor_blocks + solution_blocks

    ai_feedback = {
        "general": "請注意結束條件、統計更新與縮排層級是否正確。",
        "common_mistakes": [
            "結束條件寫反導致無法停止或提早停止",
            "忘記更新統計值（總和/次數）",
            "縮排錯誤導致程式流程不在迴圈內",
        ],
        "hints": [
            "確認結束條件是在迴圈內判斷",
            "確認統計更新行也在迴圈內",
            "最後要輸出題目要求的結果",
        ],
    }
    return {
        "question_text": question_text, # AI 生成的題目敘述（繁體中文）
        "solution_blocks": solution_blocks, # AI 生成的解答程式碼區塊（包含行號與類型）
        "distractor_blocks": distractor_blocks, # AI 生成的干擾程式碼區塊（包含行號、類型與語意提示）
        "pool": pool, # 結合 solution_blocks 與 distractor_blocks 的題庫（可用於洗牌出題）
        "template_slots": template_slots, # 每行程式碼對應的 slot（包含中文語意提示）
        "ai_feedback": ai_feedback, # AI 生成的整體反饋（包含一般建議、常見錯誤與提示）
        "ai_segment_map": seg_map, # AI 生成的程式碼行與字幕時間戳對齊資訊（包含證據）
        "ai_slot_hints": slot_hints, # AI 生成的每行程式碼提示（基於對齊資訊，提供學生錯誤時的提示）
        "ai_segments_compact": segs_compact, # 用於 AI 生成的字幕片段簡化版本（含時間戳與文字），供對齊任務使用
    }


# =========================
# Create task 是否強制使用 fallback（不呼叫 AI，直接用簡單模板產生）— 供測試用
# 備註：正式使用時請勿開啟 force_fallback，否則會完全不呼叫 AI 生成，失去智能題目的意義
# =========================

def create_task_for_video(video_doc: dict, video_id_str: str, level: str, force_fallback: bool = False, stable_mode: bool = False) -> Tuple[dict, str, Optional[str], dict]:
    unit = video_doc.get("unit", "") or ""
    video_title = video_doc.get("title", "") or ""
    subtitle_path = pick_latest_subtitle_path(video_doc, video_id_str)
    sub_text = read_subtitle_text(subtitle_path)

    # 若字幕檔讀取失敗，回退到資料庫中的 preview/text/blocks，避免 AI 在空字幕下退化。
    if not (sub_text or "").strip():
        sub_text = (
            (video_doc.get("subtitle_preview") or "")
            or (video_doc.get("subtitle_text") or "")
        )
    if not (sub_text or "").strip() and isinstance(video_doc.get("subtitle_blocks"), list):
        try:
            sub_text = "\n".join(
                [str(x.get("text") or "").strip() for x in (video_doc.get("subtitle_blocks") or []) if str(x.get("text") or "").strip()]
            )
        except Exception:
            pass
    gen_source = None
    gen_error = None
    env = env_snapshot()
    vid_oid = video_doc.get("_id") or maybe_oid(video_id_str)
    video_id_match = {"$or": [{"video_id": video_id_str}] + ([{"video_id": vid_oid}] if vid_oid else [])}
    teacher_description = ""
    if video_doc:
        teacher_description = (video_doc.get("description") or "").strip()
        # ✅ 修正：若老師沒填 description，改用 video_title 作為題目主題方向
        # 這樣影片標題「直角三角形」才能被 has_teacher_desc 路徑正確使用
        if not teacher_description and video_title:
            teacher_description = (
        video_doc.get("description")
        or video_doc.get("title")
        or ""
        ).strip()

    db.parsons_tasks.update_many({**video_id_match, "level": level, "active": True}, {"$set": {"active": False}})

    try:
        if force_fallback:
            raise RuntimeError("force_fallback")

        constraints = resolve_unit_constraints(unit)

        # [新增] 依單元決定題型（避免 U1-IO 被迴圈 prompt 帶偏）
        # ===== DEBUG：確認描述真的有傳進來 =====
        print("[DEBUG] teacher_description =", teacher_description)
        print("[DEBUG] video_title =", video_title)
        print("[DEBUG] unit =", unit)
        print("[DEBUG] stable_mode =", stable_mode)
        print("[DEBUG] sub_text_preview =", sub_text[:200])
        if constraints.get("unit_type") == "condition":
            ai = ai_generate_condition_from_subtitle(sub_text, unit, video_title, level=level, teacher_description=teacher_description, stable_mode=stable_mode)
        elif constraints.get("unit_type") == "io":
            ai = ai_generate_io_from_subtitle(sub_text, unit, video_title, level=level, teacher_description=teacher_description, stable_mode=stable_mode)
        else:
            ai = ai_generate_parsons_from_subtitle(sub_text, unit, video_title, level=level, teacher_description=teacher_description, stable_mode=stable_mode)

        gen_source = "openai"
    except Exception as e:
        ai = simple_fallback_generate(sub_text, unit, video_title, level=level, teacher_description=teacher_description)
        gen_source = "fallback"
        gen_error = str(e)

    # 記錄「老師描述 vs 生成題目」的對齊結果，便於後台排查為何 fallback 或偏題。
    try:
        _sol_lines_for_debug = [
            str((b or {}).get("text") or "")
            for b in (ai.get("solution_blocks") or [])
            if isinstance(b, dict)
        ]
        _align_ok, _missing_kws = _teacher_alignment_check(
            teacher_description,
            str(ai.get("question_text") or ""),
            _sol_lines_for_debug,
        )
        _focus_kws = _extract_focus_keywords(teacher_description, max_keywords=6)
        _hay = (str(ai.get("question_text") or "") + "\n" + "\n".join(_sol_lines_for_debug)).lower()
        _hit_kws = [k for k in _focus_kws if k in _hay]
    except Exception:
        _align_ok, _missing_kws, _focus_kws, _hit_kws = True, [], [], []

    doc = {
        "video_id": vid_oid if vid_oid else video_id_str,
        "video_id_str": video_id_str,
        "unit": unit,
        "video_title": video_title,
        "level": level,
        "enabled": False,
        "review_status": "draft",
        "prompt_source": {
            "unit": unit,
            "video_title": video_title,
            "teacher_description": teacher_description,
            "subtitle_path": subtitle_path,
            "subtitle_preview": sub_text[:500],
            "stable_mode": bool(stable_mode),
        },
        "question_text": ai.get("question_text"),
        "solution_blocks": ai.get("solution_blocks", []),
        "distractor_blocks": ai.get("distractor_blocks", []),
        "pool": ai.get("pool", []),
        "template_slots": ai.get("template_slots", []),
        "ai_feedback": ai.get("ai_feedback", {}),
        "unit_type": ai.get("unit_type"),
        "constraints": ai.get("constraints"),
        "rule_check": ai.get("rule_check"),
        "source_subtitle": ai.get("source_subtitle"),
        "subtitle_range": ai.get("subtitle_range"),
        "subtitle_text_used": ai.get("subtitle_text_used"),
        "ai_generated": True if gen_source == "openai" else False,
        "ai_segment_map": ai.get("ai_segment_map", {}) or {},
        "ai_slot_hints": ai.get("ai_slot_hints", {}) or {},
        "ai_segments_compact": ai.get("ai_segments_compact", "") or "",
        "created_at": now_utc(),
        "active": True,
        "gen_source": gen_source,
        "gen_error": gen_error,
        "env": env,
        "ai_debug": {
            "alignment_ok": bool(_align_ok),
            "teacher_focus_keywords": _focus_kws,
            "teacher_keyword_hits": _hit_kws,
            "teacher_missing_keywords": _missing_kws,
            "teacher_description_len": len(teacher_description or ""),
            "subtitle_chars": len(sub_text or ""),
            "stable_mode": bool(stable_mode),
            "gen_source": gen_source,
            "gen_error": gen_error,
        },
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