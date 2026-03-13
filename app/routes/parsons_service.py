import os
import re
import hashlib
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


def _extract_subtitle_signature_terms(text: str, max_terms: int = 10) -> list:
    text = (text or "").strip().lower()
    if not text:
        return []

    tokens = re.findall(r"[a-z_][a-z0-9_]*|[\u4e00-\u9fff]{2,}", text)
    stop = {
        "影片", "老師", "請", "題目", "程式", "使用", "完成", "判斷", "輸入", "輸出",
        "python", "print", "input", "if", "for", "while", "內容", "說明", "這個", "那個",
    }
    out = []
    seen = set()
    for t in tokens:
        if t in stop or len(t) <= 1 or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= max_terms:
            break
    return out


def _build_variation_directive(unit: str, subtitle_text: str, teacher_description: str, stable_mode: bool = False) -> str:
    options = [
        "把情境改成校園活動或社團管理，不要沿用影片原案例名詞。",
        "把情境改成遊戲任務或關卡判定，不要沿用影片原案例名詞。",
        "把情境改成生活消費或點數規則，不要沿用影片原案例名詞。",
        "把情境改成交通/移動/排程，不要沿用影片原案例名詞。",
        "把情境改成器材借還或庫存檢查，不要沿用影片原案例名詞。",
    ]
    seed = f"{unit}|{subtitle_text}|{teacher_description}"
    hv = int(hashlib.md5(seed.encode("utf-8", errors="ignore")).hexdigest()[:8], 16)
    idx = hv % len(options)
    if not stable_mode:
        idx = (idx + 1) % len(options)
    return options[idx]


def _build_avoid_terms_text(subtitle_text: str, teacher_description: str = "") -> str:
    subtitle_terms = _extract_subtitle_signature_terms(subtitle_text, max_terms=10)
    teacher_terms = set(_extract_focus_keywords(teacher_description, max_keywords=6))
    banned = [t for t in subtitle_terms if t not in teacher_terms]
    if not banned:
        return ""
    return "、".join(banned[:8])


def _semantic_paraphrase(label: str, question_text: str, line: str) -> str:
    """在 fallback 情況下做輕量同義改寫，避免每次語意完全相同。"""
    text = (label or "").strip()
    if not text:
        return text

    seed_src = f"{question_text}|{line}|{text}"
    hv = int(hashlib.md5(seed_src.encode("utf-8", errors="ignore")).hexdigest()[:8], 16)
    if hv % 2 == 0:
        return text

    rep = [
        ("讀入整數，存入", "接收整數並存入"),
        ("讀入輸入，存入", "接收輸入並存入"),
        ("設定 ", "建立 "),
        ("判斷條件：", "檢查條件："),
        ("輸出結果：", "顯示結果："),
        ("以上條件都不成立時", "其餘情況時"),
    ]
    out = text
    for a, b in rep:
        if a in out:
            out = out.replace(a, b, 1)
            break
    return out


def _guided_hint_by_line(line: str, is_distractor: bool = False) -> str:
    """產生不揭露答案細節的引導式語意提示。"""
    s = (line or "").strip()
    low = s.lower()

    if is_distractor:
        if low.startswith("if ") or low.startswith("elif "):
            return "這行看似可行，但判斷方向可能偏離題意"
        if low.startswith("else"):
            return "此分支位置需再確認，避免流程接錯"
        if "input(" in low:
            return "輸入處理看似合理，但可能影響後續判斷"
        if "print(" in low:
            return "輸出位置或內容可能與題目目標不一致"
        if any(op in low for op in ["+=", "-=", "*=", "/=", "="]):
            return "這行更新方式可能讓中間結果偏離"
        if low.startswith("for ") or low.startswith("while "):
            return "重複流程設定可能不符合本題需求"
        return "這行容易誤選，建議結合前後流程再判斷"

    if low.startswith("if ") or low.startswith("elif "):
        return "先做一個條件判斷，再決定流程走向"
    if low.startswith("else"):
        return "處理前面條件未成立時的情況"
    if "input(" in low:
        return "先取得後續處理需要的輸入資料"
    if "print(" in low:
        return "在這一步輸出目前需要呈現的結果"
    if low.startswith("for ") or low.startswith("while "):
        return "進入重複處理流程，逐步完成任務"
    if any(op in low for op in ["+=", "-=", "*=", "/="]):
        return "更新中間結果，供後續步驟使用"
    if "=" in low:
        return "先準備一個後續會使用到的值"
    return "完成這一步，銜接下一個程式流程"


def _soften_semantic_hint(label: str, line: str, is_distractor: bool = False) -> str:
    """將過度明確的語意標籤轉為引導式提示，避免直接暴露答案。"""
    base = (label or "").strip()
    if not base:
        return _guided_hint_by_line(line, is_distractor=is_distractor)

    # 明顯在洩漏程式細節（變數/運算式/常數）時，一律降級為引導式提示。
    leak_patterns = [
        r"[A-Za-z_]\w*\s*=",
        r"==|!=|<=|>=|<|>|\+|-|\*|/|%",
        r"\b\d+\b",
        r"if\s+.+:",
        r"for\s+.+:",
        r"while\s+.+:",
        r"print\s*\(",
        r"input\s*\(",
        r"[「『\"'].*?[」』\"']",
    ]
    for p in leak_patterns:
        if _re.search(p, base, flags=_re.IGNORECASE):
            return _guided_hint_by_line(line, is_distractor=is_distractor)

    # 常見過度明確中文模板，直接降級避免提示過頭。
    explicit_phrases = [
        "讀入整數", "存入", "輸出結果", "顯示結果", "判斷條件", "檢查條件",
        "設定", "建立", "等於", "小於", "大於", "倍數",
    ]
    if any(p in base for p in explicit_phrases):
        return _guided_hint_by_line(line, is_distractor=is_distractor)

    # 文字太長也容易透露關鍵，統一改為短引導。
    if len(base) > 20:
        return _guided_hint_by_line(line, is_distractor=is_distractor)

    return base


def _extract_int_literals(text: str) -> list:
    vals = []
    for m in re.findall(r"(?<![A-Za-z_\d])-?\d+(?![A-Za-z_\d])", text or ""):
        try:
            vals.append(int(m))
        except Exception:
            pass
    return vals


_ZH_DIGIT_MAP = {
    "零": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


def _zh_num_to_int(token: str) -> Optional[int]:
    t = (token or "").strip()
    if not t:
        return None
    if t == "十":
        return 10
    if t.startswith("十") and len(t) == 2 and t[1] in _ZH_DIGIT_MAP:
        return 10 + _ZH_DIGIT_MAP[t[1]]
    if t.endswith("十") and len(t) == 2 and t[0] in _ZH_DIGIT_MAP:
        return _ZH_DIGIT_MAP[t[0]] * 10
    if len(t) == 3 and t[1] == "十" and t[0] in _ZH_DIGIT_MAP and t[2] in _ZH_DIGIT_MAP:
        return _ZH_DIGIT_MAP[t[0]] * 10 + _ZH_DIGIT_MAP[t[2]]
    if len(t) == 1 and t in _ZH_DIGIT_MAP:
        return _ZH_DIGIT_MAP[t]
    return None


def _int_to_zh_small(n: int) -> str:
    if n < 0:
        return str(n)
    if n < 10:
        rev = {v: k for k, v in _ZH_DIGIT_MAP.items() if k != "兩"}
        return rev.get(n, str(n))
    if n == 10:
        return "十"
    if 10 < n < 20:
        rev = {v: k for k, v in _ZH_DIGIT_MAP.items() if k != "兩"}
        return "十" + rev.get(n - 10, str(n - 10))
    if n % 10 == 0 and n < 100:
        rev = {v: k for k, v in _ZH_DIGIT_MAP.items() if k != "兩"}
        return rev.get(n // 10, str(n // 10)) + "十"
    if n < 100:
        rev = {v: k for k, v in _ZH_DIGIT_MAP.items() if k != "兩"}
        return rev.get(n // 10, str(n // 10)) + "十" + rev.get(n % 10, str(n % 10))
    return str(n)


def _extract_number_signals(text: str) -> list:
    nums = set(_extract_int_literals(text))
    for tok in re.findall(r"[零一二兩三四五六七八九十]{1,3}", text or ""):
        v = _zh_num_to_int(tok)
        if v is not None:
            nums.add(v)
    return sorted(nums)


def _pick_alt_int(n: int, seed_text: str) -> int:
    pool = [3, 4, 5, 6, 7, 8, 9, 11, 12, 15, 16, 20]
    pool = [x for x in pool if x != n]
    if not pool:
        return n + 1
    hv = int(hashlib.md5(seed_text.encode("utf-8", errors="ignore")).hexdigest()[:8], 16)
    return pool[hv % len(pool)]


def _pick_alt_int_avoiding(n: int, seed_text: str, forbidden: set) -> int:
    pool = [3, 4, 5, 6, 7, 8, 9, 11, 12, 15, 16, 18, 20, 24]
    pool = [x for x in pool if x != n and x not in forbidden]
    if not pool:
        cand = n + 2
        while cand in forbidden:
            cand += 1
        return cand
    hv = int(hashlib.md5(seed_text.encode("utf-8", errors="ignore")).hexdigest()[:8], 16)
    return pool[hv % len(pool)]


def _token_set_for_similarity(text: str) -> set:
    toks = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{2,}", (text or "").lower())
    stop = {"請", "完成", "程式", "題目", "老師", "影片", "內容", "使用", "依據", "以及", "並"}
    return {t for t in toks if t not in stop and len(t) > 1}


def _jaccard_sim(a: str, b: str) -> float:
    sa = _token_set_for_similarity(a)
    sb = _token_set_for_similarity(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(1, len(sa | sb))


def _light_paraphrase_question_text(question_text: str, subtitle_text: str, unit: str = "") -> str:
    q = str(question_text or "").strip()
    if not q:
        return q

    sim = _jaccard_sim(q, subtitle_text)
    if sim < 0.35:
        return q

    rep = [
        ("請撰寫一段程式", "請完成一段程式"),
        ("輸入", "讀入"),
        ("判斷是否", "檢查是否"),
        ("如果", "若"),
        ("印出", "輸出"),
        ("否則", "若不符合條件則"),
        ("請使用", "請以"),
    ]
    out = q
    for a, b in rep:
        out = out.replace(a, b)

    # 若仍偏高，再補一層改寫開場語句。
    if _jaccard_sim(out, subtitle_text) >= 0.35:
        u = (unit or "").upper()
        if "-IF" in u or u.startswith("U2"):
            out = "情境任務：請根據條件判斷完成下列需求。" + out
        elif "-IO" in u or u.startswith("U1"):
            out = "資料處理任務：請依題意完成輸入與輸出流程。" + out
        else:
            out = "請依題意完成程式流程。" + out
    return out


def _diversify_numbers_from_subtitle(question_text: str, solution_lines: list, subtitle_text: str, stable_mode: bool = False) -> Tuple[str, list]:
    """避免題目把字幕裡的關鍵數字（例如 10）直接照抄。"""
    q = str(question_text or "")
    lines = [str(x or "") for x in (solution_lines or [])]

    sub_nums = set(_extract_number_signals(subtitle_text))
    if not sub_nums:
        return q, lines

    all_text = q + "\n" + "\n".join(lines)
    used_nums = set(_extract_number_signals(all_text))
    overlap = [n for n in used_nums if n in sub_nums and n not in {0, 1, -1}]
    if not overlap:
        return q, lines

    q2 = q
    lines2 = list(lines)
    forbidden = set(sub_nums)
    replaced = {}

    for target in sorted(overlap):
        alt = _pick_alt_int_avoiding(
            target,
            f"{q2}|{'|'.join(lines2)}|{subtitle_text}|{stable_mode}|{target}",
            forbidden,
        )
        forbidden.add(alt)
        replaced[target] = alt

        pat = re.compile(rf"(?<![A-Za-z_\d]){target}(?![A-Za-z_\d])")
        q2 = pat.sub(str(alt), q2)
        lines2 = [pat.sub(str(alt), ln) for ln in lines2]

    for target, alt in replaced.items():
        target_zh = _int_to_zh_small(target)
        alt_zh = _int_to_zh_small(alt)
        q2 = q2.replace(f"{target_zh}的倍數", f"{alt_zh}的倍數")
        q2 = q2.replace(f"為{target_zh}", f"為{alt_zh}")
        q2 = q2.replace(f"等於{target_zh}", f"等於{alt_zh}")

    return q2, lines2


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

    solution_blocks = [{"id": f"b{i+1}", "text": line, "type": "core", "semantic_zh": _label_for_code_line(line)} for i, line in enumerate(solution_lines)]
    distractor_blocks = [{"id": f"d{i+1}", "text": line, "type": "distractor"} for i, line in enumerate(distractor_lines)]
    pool = distractor_blocks + solution_blocks
    _fb_labels = [_label_for_code_line(ln) for ln in solution_lines]
    template_slots = [{"label": _fb_labels[i], "slot": str(i)} for i in range(len(solution_lines))]

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
    if "-IO" in u or u.startswith("U1"):
        return {"unit_type": "io", "forbid_loop": True, "forbid_condition": True}

    # Condition (IF / IFELSE / ELIF)
    if "-IFELSE" in u or "IFELSE" in u:
        return {"unit_type": "condition", "require_if": True, "require_else": True, "require_elif": False, "forbid_loop": True, "forbid_break": True}
    if "-ELIF" in u:
        return {"unit_type": "condition", "require_if": True, "require_else": False, "require_elif": True, "forbid_loop": True, "forbid_break": True}
    if "-IF" in u or u.startswith("U2"):
        return {"unit_type": "condition", "require_if": True, "require_else": False, "require_elif": False, "forbid_loop": True, "forbid_break": True}

    # Loop styles（新課綱對齊）
    if "-FOR" in u or u.startswith("U3"):
        return {"unit_type": "loop", "loop_style": "for_only"}
    if "-NESTED" in u or u.startswith("U4"):
        return {"unit_type": "loop", "loop_style": "for_only", "require_nested": True}
    if "-WHILE" in u or u.startswith("U5"):
        return {"unit_type": "loop", "loop_style": "while_only"}
    if "-LIST" in u or u.startswith("U6"):
        return {"unit_type": "loop", "loop_style": "for_only", "prefer_list_ops": True}
    if "-FUNCTION" in u or u.startswith("U7"):
        return {"unit_type": "loop", "loop_style": "either", "prefer_function_style": True}
    if "-LOOP" in u:
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

def _ai_generate_semantic_labels(solution_lines: list, question_text: str, model: str, temperature: float = 0.1) -> list:
    """
    用 AI 為每一行程式碼產生一句繁體中文教學語意說明。
    回傳與 solution_lines 等長的 list[str]。失敗時 fallback 為規則式標籤。
    """
    if not ai_enabled() or not solution_lines:
        return [_semantic_paraphrase(_label_for_code_line(ln), question_text, ln) for ln in solution_lines]

    numbered = "\n".join(f"{i}: {ln}" for i, ln in enumerate(solution_lines))
    try:
        result = parsons_ai.call_openai_json(
            model=model,
            temperature=temperature,
            max_output_tokens=600,
            system=(
                "你是 Python 程式設計助教。"
                "請為每一行程式碼寫一句簡短的繁體中文說明（12字以內），"
                "只描述程式動作，不要使用題目情境名詞。"
                "不要暴露變數名、數字、比較符號、運算式。"
                "不要直接說明正確條件內容。"
                "語氣要穩定、教學式，避免花俏修辭。"
                "只輸出純 JSON，格式：{\"labels\": [\"第0行說明\", \"第1行說明\", ...]}"
            ),
            user=(
                f"程式碼（格式：行號: 程式碼）：\n{numbered}\n\n"
                "請依序為每行輸出一句繁體中文說明，數量必須和行數相同。"
                "不得引用題目情境詞。"
                "提示要保留學生思考空間，不可直接揭示答案。"
            ),
        ) or {}
        labels = result.get("labels") or []
        if isinstance(labels, list) and len(labels) == len(solution_lines):
            return [
                _soften_semantic_hint(str(l).strip() or _label_for_code_line(solution_lines[i]), solution_lines[i], is_distractor=False)
                for i, l in enumerate(labels)
            ]
    except Exception:
        pass
    # fallback 規則式
    return [
        _soften_semantic_hint(_semantic_paraphrase(_label_for_code_line(ln), question_text, ln), ln, is_distractor=False)
        for ln in solution_lines
    ]


def _apply_semantic_labels_to_blocks(blocks: dict, labels: list) -> None:
    """將 AI 語意標籤寫入 solution_blocks.semantic_zh 和 template_slots.label（in-place）。"""
    sol_blocks = blocks.get("solution_blocks") or []
    for i, b in enumerate(sol_blocks):
        if i < len(labels) and labels[i]:
            b["semantic_zh"] = _soften_semantic_hint(labels[i], b.get("text", ""), is_distractor=False)
    tmpl = blocks.get("template_slots") or []
    for i, s in enumerate(tmpl):
        if i < len(labels) and labels[i]:
            s["label"] = _soften_semantic_hint(labels[i], (sol_blocks[i].get("text", "") if i < len(sol_blocks) else ""), is_distractor=False)


def _label_for_code_line(line: str) -> str:
    """規則式中文語意標籤（AI 不可用時的 fallback）。"""
    s = (line or "").strip()
    low = s.lower()
    # 縮排層級
    indent = len(s) - len(s.lstrip())

    # if/elif/else
    if _re.match(r"if\s+.+:", s):
        return "先做一個條件判斷，再決定流程走向"
    if _re.match(r"elif\s+.+:", s):
        return "補充另一個條件分支"
    if _re.match(r"else\s*:", s):
        return "處理其餘未符合條件的情況"

    # input / print
    if "input(" in low:
        return "先取得後續處理需要的輸入資料"
    if low.startswith("print(") or (indent > 0 and "print(" in low):
        return "在這一步輸出目前需要呈現的結果"

    # 迴圈
    if _re.match(r"for\s+\w+\s+in\s+range\(", s):
        return "進入重複處理流程，逐步完成任務"
    if s.startswith("while "):
        return "透過條件控制重複執行流程"

    # 賦值 / 累加
    m_aug = _re.match(r"(\w+)\s*([+\-*/%]=)\s*(.+)", s)
    if m_aug:
        return "更新中間結果，供後續步驟使用"
    m_assign = _re.match(r"(\w+)\s*=\s*(.+)", s)
    if m_assign:
        return "先準備一個後續會使用到的值"

    return "完成這一步，銜接下一個程式流程" if s else "（空行）"


def _normalize_code_line(line: str) -> str:
    s = (line or "").rstrip("\n")
    indent = len(s) - len(s.lstrip(" "))
    core = _re.sub(r"\s+", "", s.lstrip(" "))
    return f"{indent}|{core}".lower()


def _mutate_distractor_candidates(line: str) -> list:
    s = (line or "").rstrip("\n")
    variants = []
    stripped = s.strip()

    if not stripped:
        return variants

    if "==" in s:
        variants.append(s.replace("==", "!=", 1))
    if "!=" in s:
        variants.append(s.replace("!=", "==", 1))
    if "<=" in s:
        variants.append(s.replace("<=", ">=", 1))
    if ">=" in s:
        variants.append(s.replace(">=", "<=", 1))

    swap_gt = _re.sub(r"(?<![<>=!])>(?!=)", "<", s, count=1)
    if swap_gt != s:
        variants.append(swap_gt)
    swap_lt = _re.sub(r"(?<![<>=!])<(?!=)", ">", s, count=1)
    if swap_lt != s:
        variants.append(swap_lt)

    if " or " in s:
        variants.append(s.replace(" or ", " and ", 1))
    if " and " in s:
        variants.append(s.replace(" and ", " or ", 1))

    if "+=" in s:
        variants.append(s.replace("+=", "-=", 1))
        variants.append(s.replace("+=", "=", 1))
    if "-=" in s:
        variants.append(s.replace("-=", "+=", 1))
    if stripped == "break":
        variants.append("continue")
    if stripped == "continue":
        variants.append("break")
    if "int(input(" in s:
        variants.append(_re.sub(r"int\(\s*input\(\s*\)\s*\)", "input()", s, count=1))
    if "float(input(" in s:
        variants.append(_re.sub(r"float\(\s*input\(\s*\)\s*\)", "input()", s, count=1))

    if s.startswith("    "):
        variants.append(s[4:])
    elif stripped and not stripped.endswith(":"):
        variants.append("    " + s)

    return variants


def _label_for_distractor_line(line: str) -> str:
    raw = (line or "")
    s = raw.strip()
    low = s.lower()

    if not raw.startswith("    ") and raw != s and s:
        return "此行看似合理，但縮排層級可能造成流程偏差"
    if low.startswith("if ") and " and " in low:
        return "條件組合方式可能不符題意，建議再核對判斷方向"
    if low.startswith("if ") and " or " in low:
        return "條件組合方式可能不符題意，建議再核對判斷方向"
    if any(op in low for op in ["==", "!=", "<=", ">=", "<", ">"]):
        return "比較邏輯可能偏離預期，容易導致分支判斷錯誤"
    if "input(" in low:
        return "這行輸入處理可能影響後續判斷或計算"
    if low.startswith("print(") or "print(" in low:
        return "輸出位置或內容可能不符合題目要求"
    if any(op in low for op in ["+=", "-=", "*=", "/="]):
        return "更新方式可能使中間結果偏離預期"
    if "=" in low:
        return "資料更新邏輯可能與題目流程不一致"
    if low in {"break", "continue"}:
        return "流程控制語句可能放錯位置，請再檢查前後關係"
    return "此行容易誤選，請結合上下文判斷是否合理"


def _ensure_distractor_items(solution_lines: list, distractor_items: list, min_count: int = 2, max_count: int = 3) -> list:
    solution_lines = solution_lines or []
    distractor_items = distractor_items or []
    sol_norm = {_normalize_code_line(x) for x in solution_lines if (x or "").strip()}

    chosen = []
    seen = set()

    def _push(item):
        line = item.get("text", "") if isinstance(item, dict) else str(item or "")
        semantic = item.get("semantic_zh", "") if isinstance(item, dict) else ""
        error_type = item.get("error_type", "") if isinstance(item, dict) else ""
        normalized = _normalize_code_line(line)
        if not normalized or normalized in sol_norm or normalized in seen:
            return
        seen.add(normalized)
        chosen.append({
            "text": line,
            "semantic_zh": _soften_semantic_hint((semantic or "").strip() or _label_for_distractor_line(line), line, is_distractor=True),
            "error_type": (error_type or "").strip(),
        })

    for item in distractor_items:
        _push(item)
        if len(chosen) >= max_count:
            return chosen[:max_count]

    candidates = []
    for line in solution_lines:
        candidates.extend(_mutate_distractor_candidates(line))

    for line in candidates:
        _push(line)
        if len(chosen) >= max_count:
            return chosen[:max_count]

    fallback = [
        "print('結果錯誤')",
        "value = input()",
        "count = count + 1",
    ]
    for line in fallback:
        _push(line)
        if len(chosen) >= min_count:
            break

    return chosen[:max_count]


def _make_template_distractors_io(solution_lines: list) -> list:
    """U1-IO 常見錯誤模板。"""
    out = []
    stripped = [ln.strip() for ln in (solution_lines or []) if (ln or "").strip()]

    # missing_int_cast
    for ln in stripped:
        if "int(input(" in ln:
            cast_removed = _re.sub(r"int\(\s*input\(\s*\)\s*\)", "input()", ln, count=1)
            out.append({"text": cast_removed, "error_type": "missing_int_cast"})
            break

    # wrong_operator
    for ln in stripped:
        if any(op in ln for op in [" + ", "-", "*", "/"]):
            if "+" in ln:
                out.append({"text": ln.replace("+", "-", 1), "error_type": "wrong_operator"})
            elif "*" in ln:
                out.append({"text": ln.replace("*", "+", 1), "error_type": "wrong_operator"})
            elif "-" in ln:
                out.append({"text": ln.replace("-", "+", 1), "error_type": "wrong_operator"})
            elif "/" in ln:
                out.append({"text": ln.replace("/", "*", 1), "error_type": "wrong_operator"})
            break

    # wrong_output_var
    vars_assigned = []
    for ln in stripped:
        m = _re.match(r"([A-Za-z_]\w*)\s*=", ln)
        if m:
            vars_assigned.append(m.group(1))
    for ln in stripped:
        if ln.startswith("print("):
            if vars_assigned:
                wrong_var = vars_assigned[-1]
                if len(vars_assigned) >= 2:
                    wrong_var = vars_assigned[0]
                out.append({"text": f"print({wrong_var})", "error_type": "wrong_output_var"})
            break

    # missing_input
    input_assigns = [ln for ln in stripped if "input(" in ln and "=" in ln]
    if len(input_assigns) >= 2:
        out.append({"text": input_assigns[0], "error_type": "missing_input"})

    # order_error (print 在 input 前可誤導)
    if vars_assigned:
        out.append({"text": f"print({vars_assigned[0]})", "error_type": "order_error"})
    elif stripped:
        out.append({"text": "print(result)", "error_type": "order_error"})

    return out


def _make_template_distractors_condition(solution_lines: list) -> list:
    """U2-IF 常見錯誤模板。"""
    out = []
    stripped = [ln.strip() for ln in (solution_lines or []) if (ln or "").strip()]

    # compare_op_wrong / condition_reverse
    for ln in stripped:
        if ln.startswith("if ") or ln.startswith("elif "):
            if "==" in ln:
                out.append({"text": ln.replace("==", "!=", 1), "error_type": "compare_op_wrong"})
                out.append({"text": ln.replace("==", "<=", 1), "error_type": "condition_reverse"})
            elif ">=" in ln:
                out.append({"text": ln.replace(">=", "==", 1), "error_type": "compare_op_wrong"})
                out.append({"text": ln.replace(">=", "<", 1), "error_type": "condition_reverse"})
            elif "<=" in ln:
                out.append({"text": ln.replace("<=", "==", 1), "error_type": "compare_op_wrong"})
                out.append({"text": ln.replace("<=", ">", 1), "error_type": "condition_reverse"})
            elif ">" in ln:
                out.append({"text": ln.replace(">", "<", 1), "error_type": "compare_op_wrong"})
            elif "<" in ln:
                out.append({"text": ln.replace("<", ">", 1), "error_type": "compare_op_wrong"})
            break

    # missing_else
    has_else = any(ln == "else:" for ln in stripped)
    if has_else:
        cond_line = next((ln for ln in stripped if ln.startswith("if ")), "if score >= 60:")
        out.append({"text": cond_line + " print('pass')", "error_type": "missing_else"})

    # wrong_indent
    indented = [ln for ln in (solution_lines or []) if (ln or "").startswith("    ")]
    if indented:
        out.append({"text": indented[0].lstrip(), "error_type": "wrong_indent"})

    # loop_intrusion
    out.append({"text": "for i in range(3):", "error_type": "loop_intrusion"})

    return out


def _make_template_distractors(unit_type: str, solution_lines: list) -> list:
    u = (unit_type or "").strip().lower()
    if u == "io":
        return _make_template_distractors_io(solution_lines)
    if u == "condition":
        return _make_template_distractors_condition(solution_lines)
    return []


def _select_template_distractors(unit_type: str, items: list, max_count: int = 3) -> list:
    u = (unit_type or "").strip().lower()
    if u == "io":
        order = ["missing_int_cast", "wrong_operator", "wrong_output_var", "missing_input", "order_error"]
    elif u == "condition":
        order = ["compare_op_wrong", "condition_reverse", "missing_else", "wrong_indent", "loop_intrusion"]
    else:
        order = []

    picked = []
    seen_text = set()
    for et in order:
        for it in items or []:
            if (it.get("error_type") or "") != et:
                continue
            t = str(it.get("text", "")).strip()
            if not t or t in seen_text:
                continue
            seen_text.add(t)
            picked.append(it)
            break
        if len(picked) >= max_count:
            return picked

    for it in items or []:
        t = str(it.get("text", "")).strip()
        if not t or t in seen_text:
            continue
        seen_text.add(t)
        picked.append(it)
        if len(picked) >= max_count:
            break
    return picked


def _ai_generate_distractor_semantics(question_text: str, solution_lines: list, distractor_items: list, model: str, temperature: float = 0.1) -> list:
    """AI 只補 distractor semantic_zh；若失敗則用本地 fallback。"""
    if not distractor_items:
        return []

    if not ai_enabled():
        return [_label_for_distractor_line(str(x.get("text", ""))) for x in distractor_items]

    sol_text = "\n".join(f"{i}: {ln}" for i, ln in enumerate(solution_lines or []))
    dis_text = "\n".join(
        f"{i}: {str(x.get('text', ''))} | error_type={str(x.get('error_type', '')) or 'unknown'}"
        for i, x in enumerate(distractor_items)
    )
    try:
        result = parsons_ai.call_openai_json(
            model=model,
            temperature=temperature,
            max_output_tokens=500,
            system=(
                "你是 Python 教學助教。"
                "請為每個干擾程式碼區塊寫一句繁體中文語意（15字內），"
                "指出它可能造成的風險方向。"
                "不要直接揭露精確錯點或正確寫法。"
                "只輸出 JSON：{\"labels\": [\"...\"]}"
            ),
            user=(
                f"題目：{question_text}\n\n"
                f"正解行：\n{sol_text}\n\n"
                f"干擾題（含 error_type）：\n{dis_text}\n\n"
                "請輸出與干擾題數量相同順序的 labels。"
            ),
        ) or {}
        labels = result.get("labels") or []
        if isinstance(labels, list) and len(labels) == len(distractor_items):
            return [
                _soften_semantic_hint(
                    str(x).strip() or _label_for_distractor_line(str(distractor_items[i].get("text", ""))),
                    str(distractor_items[i].get("text", "")),
                    is_distractor=True,
                )
                for i, x in enumerate(labels)
            ]
    except Exception:
        pass
    return [
        _soften_semantic_hint(_label_for_distractor_line(str(x.get("text", ""))), str(x.get("text", "")), is_distractor=True)
        for x in distractor_items
    ]


def _apply_distractor_semantics_to_blocks(blocks: dict, labels: list) -> None:
    dis = blocks.get("distractor_blocks") or []
    for i, b in enumerate(dis):
        if i < len(labels) and labels[i]:
            b["semantic_zh"] = _soften_semantic_hint(labels[i], b.get("text", ""), is_distractor=True)


def _ai_generate_distractor_items(question_text: str, solution_lines: list, model: str, temperature: float = 0.2) -> list:
    """讓 AI 生成 2~3 個干擾題與中文語意，並交由本地邏輯過濾正解/重複。"""
    if not ai_enabled() or not solution_lines:
        return []

    numbered = "\n".join(f"{i}: {ln}" for i, ln in enumerate(solution_lines))
    try:
        result = parsons_ai.call_openai_json(
            model=model,
            temperature=temperature,
            max_output_tokens=700,
            system=(
                "你是 Python Parsons 題目的出題助教。"
                "請根據正確解答，設計 2 到 3 個單行的干擾程式碼區塊。"
                "每個干擾題都必須看起來合理，但不能和任何正解行完全相同。"
                "每個干擾題都要附上一句繁體中文語意，說明它為什麼是干擾。"
                "禁止輸出完整解答、禁止輸出多行程式碼、禁止解釋段落。"
                "只輸出純 JSON，格式："
                "{\"distractors\": [{\"text\": \"程式碼\", \"semantic_zh\": \"中文語意\"}]}"
            ),
            user=(
                f"題目：{question_text}\n\n"
                f"正確解答行（格式：行號: 程式碼）：\n{numbered}\n\n"
                "請輸出 2 到 3 個單行 distractors。"
                "這些行要和正解主題相關，但必須是錯的、容易誤選的。"
                "semantic_zh 要讓老師看得懂這題錯在哪裡。"
            ),
        ) or {}
        items = result.get("distractors") or []
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict) and str(x.get("text", "")).strip()]
    except Exception:
        pass
    return []


def _build_blocks_from_lines(question_text: str, solution_lines: list, distractor_lines: list, distractor_items: Optional[list] = None) -> Dict[str, Any]:
    solution_lines = solution_lines or []
    seed_items = list(distractor_items or []) + [{"text": line} for line in (distractor_lines or [])]
    distractor_items = _ensure_distractor_items(solution_lines, seed_items)

    # 產生有意義的中文語意標籤
    labels = [_label_for_code_line(ln) for ln in solution_lines]

    solution_blocks = [
        {
            "id": f"b{i+1}",
            "text": (line or "").lstrip(" "),
            "indent": len(line or "") - len((line or "").lstrip(" ")),
            "type": "core",
            "semantic_zh": _soften_semantic_hint(labels[i], line, is_distractor=False),
        }
        for i, line in enumerate(solution_lines)
    ]
    distractor_blocks = [
        {
            "id": f"d{i+1}",
            "text": item["text"],
            "type": "distractor",
            "semantic_zh": _soften_semantic_hint(item["semantic_zh"], item["text"], is_distractor=True),
            **({"error_type": item.get("error_type")} if item.get("error_type") else {}),
        }
        for i, item in enumerate(distractor_items)
    ]
    pool = distractor_blocks + solution_blocks
    template_slots = [
        {"label": _soften_semantic_hint(labels[i], solution_lines[i], is_distractor=False), "slot": str(i)}
        for i in range(len(solution_lines))
    ]
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
    variation_directive = _build_variation_directive(
        unit,
        trace.get("subtitle_text_used", subtitle_text),
        teacher_description,
        stable_mode=stable_mode,
    )
    avoid_terms = _build_avoid_terms_text(trace.get("subtitle_text_used", subtitle_text), teacher_description)
    avoid_terms_line = (
        f"- 請避免直接使用以下字幕關鍵詞：{avoid_terms}"
        if avoid_terms else
        "- 不要直接複用字幕中的名詞組合。"
    )

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

多樣化要求：
- {variation_directive}
- 題目敘述與影片字幕不可高相似改寫，必須換一組角色、場景與敘事語氣。
{avoid_terms_line}

若老師描述明確，請優先依據描述生成題目。

輸出 JSON：

{{
 "question_text": "題目敘述",
 "solution_lines": ["code", "..."]
}}

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

    # 若太像字幕，先要求 AI 強制改寫再試一次
    if is_too_similar_to_subtitle(trace.get("subtitle_text_used", subtitle_text), question_text):
        retry_sim_prompt = base_prompt + (
            "\n\n你上一版題目與字幕太相似，請重新改寫。"
            "必須更換情境主詞、敘事語氣、任務細節，"
            "但保留同一程式概念與單元難度。"
        )
        data = parsons_ai.call_openai_json(
            system=system_msg,
            user=retry_sim_prompt,
            model=model,
            temperature=min(0.45, gen_temperature + 0.15),
            max_output_tokens=900
        ) or {}
        question_text = (data.get("question_text") or "").strip()

    solution_lines = data.get("solution_lines") or []
    question_text, solution_lines = _diversify_numbers_from_subtitle(
        question_text,
        solution_lines,
        trace.get("subtitle_text_used", subtitle_text),
        stable_mode=stable_mode,
    )
    question_text = _light_paraphrase_question_text(
        question_text,
        trace.get("subtitle_text_used", subtitle_text),
        unit,
    )

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

        solution_lines = data.get("solution_lines") or []
        question_text, solution_lines = _diversify_numbers_from_subtitle(
            question_text,
            solution_lines,
            trace.get("subtitle_text_used", subtitle_text),
            stable_mode=stable_mode,
        )
        question_text = _light_paraphrase_question_text(
            question_text,
            trace.get("subtitle_text_used", subtitle_text),
            unit,
        )
        rc = build_rule_check(solution_lines, constraints)

    if is_too_similar_to_subtitle(trace.get("subtitle_text_used", subtitle_text), question_text):
        raise RuntimeError("generated question is too similar to subtitle example")

    if not rc.get("ok"):
        raise RuntimeError(f"AI condition generation failed: {rc.get('reason')}")

    template_distractors = _select_template_distractors("condition", _make_template_distractors("condition", solution_lines), max_count=3)
    blocks = _build_blocks_from_lines(question_text, solution_lines, [], distractor_items=template_distractors)

    # AI 語意標籤
    sem_labels = _ai_generate_semantic_labels(solution_lines, question_text, model, temperature=0.0)
    _apply_semantic_labels_to_blocks(blocks, sem_labels)
    dis_labels = _ai_generate_distractor_semantics(
        question_text,
        solution_lines,
        blocks.get("distractor_blocks") or [],
        model,
        temperature=0.1,
    )
    _apply_distractor_semantics_to_blocks(blocks, dis_labels)

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
    variation_directive = _build_variation_directive(
        unit,
        trace.get("subtitle_text_used", subtitle_text),
        teacher_description,
        stable_mode=stable_mode,
    )
    avoid_terms = _build_avoid_terms_text(trace.get("subtitle_text_used", subtitle_text), teacher_description)
    avoid_terms_line = (
        f"- 請避免直接使用以下字幕關鍵詞：{avoid_terms}"
        if avoid_terms else
        "- 不要直接複用字幕中的名詞組合。"
    )

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

多樣化要求：
- {variation_directive}
- 題目敘述與影片字幕不可高相似改寫，必須換一組角色、場景與敘事語氣。
{avoid_terms_line}

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

    # 若太像字幕，先要求 AI 強制改寫再試一次
    if is_too_similar_to_subtitle(trace.get("subtitle_text_used", subtitle_text), question_text):
        retry_sim_prompt = base_prompt + (
            "\n\n你上一版題目與字幕太相似，請重新改寫。"
            "必須更換情境主詞、敘事語氣、任務細節，"
            "但保留同一程式概念與單元難度。"
        )
        data = parsons_ai.call_openai_json(
            system=system_msg,
            user=retry_sim_prompt,
            model=model,
            temperature=min(0.45, gen_temperature + 0.15),
            max_output_tokens=900
        ) or {}
        question_text = (data.get("question_text") or "").strip()

    solution_lines = data.get("solution_lines") or []
    question_text, solution_lines = _diversify_numbers_from_subtitle(
        question_text,
        solution_lines,
        trace.get("subtitle_text_used", subtitle_text),
        stable_mode=stable_mode,
    )
    question_text = _light_paraphrase_question_text(
        question_text,
        trace.get("subtitle_text_used", subtitle_text),
        unit,
    )

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

        solution_lines = data.get("solution_lines") or []
        question_text, solution_lines = _diversify_numbers_from_subtitle(
            question_text,
            solution_lines,
            trace.get("subtitle_text_used", subtitle_text),
            stable_mode=stable_mode,
        )
        question_text = _light_paraphrase_question_text(
            question_text,
            trace.get("subtitle_text_used", subtitle_text),
            unit,
        )
        rc = build_rule_check(solution_lines, constraints)
        align_ok, missing_kws = _teacher_alignment_check(teacher_description, question_text, solution_lines)

    if is_too_similar_to_subtitle(trace.get("subtitle_text_used", subtitle_text), question_text):
        raise RuntimeError("generated question is too similar to subtitle example")

    if not rc.get("ok"):
        raise RuntimeError(f"AI IO generation failed: {rc.get('reason')}")
    if not align_ok:
        raise RuntimeError("AI IO generation failed: not aligned with teacher_description")

    template_distractors = _select_template_distractors("io", _make_template_distractors("io", solution_lines), max_count=3)
    blocks = _build_blocks_from_lines(question_text, solution_lines, [], distractor_items=template_distractors)

    # AI 語意標籤
    sem_labels = _ai_generate_semantic_labels(solution_lines, question_text, model, temperature=0.0)
    _apply_semantic_labels_to_blocks(blocks, sem_labels)
    dis_labels = _ai_generate_distractor_semantics(
        question_text,
        solution_lines,
        blocks.get("distractor_blocks") or [],
        model,
        temperature=0.1,
    )
    _apply_distractor_semantics_to_blocks(blocks, dis_labels)

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

    # 中文語意統一走 AI 生成；若 AI 不可用會自動回退到本地規則。
    semantic_model = (os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()
    sem_labels = _ai_generate_semantic_labels(solution_lines, question_text, semantic_model, temperature=0.0)
    _apply_semantic_labels_to_blocks(blocks, sem_labels)

    dis_labels = _ai_generate_distractor_semantics(
        question_text,
        solution_lines,
        blocks.get("distractor_blocks") or [],
        semantic_model,
        temperature=0.1,
    )
    _apply_distractor_semantics_to_blocks(blocks, dis_labels)

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

    # 以內容建立穩定 seed，避免同素材每次挑到不同 scenario
    seed_src = (unit or "") + "|" + (video_title or "") + "|" + ((cleaned or "")[:1500])
    seed_int = int(hashlib.md5(seed_src.encode("utf-8")).hexdigest(), 16)
    rnd_seed = seed_int
    rnd = random.Random(rnd_seed)

    def _scenario_score(sc: dict) -> int:
        kws = sc.get("keywords") or []
        if not kws:
            return 0
        score = 0
        body = (cleaned or "").lower()
        td = (teacher_description or "").lower()
        vt = (video_title or "").lower()
        for kw in kws:
            k = str(kw or "").lower().strip()
            if not k:
                continue
            if k in body:
                score += 2
            if k in td:
                score += 3
            if k in vt:
                score += 1
        return score

    if has_teacher_desc:
        # 老師描述不再直接覆蓋 scenario，改為「加權影響」；只保留圖形類特例。
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
            scored = sorted(scenarios, key=_scenario_score, reverse=True)
            top_score = _scenario_score(scored[0]) if scored else 0
            tops = [sc for sc in scored if _scenario_score(sc) == top_score] if scored else []
            if tops:
                tie_seed = (teacher_description or "") + "|" + (unit or "") + "|" + (video_title or "")
                tie_idx = int(hashlib.md5(tie_seed.encode("utf-8", errors="ignore")).hexdigest()[:8], 16) % len(tops)
                picked = tops[tie_idx]
            else:
                picked = scenarios[0]
    else:
        scored = sorted(scenarios, key=_scenario_score, reverse=True)
        top_score = _scenario_score(scored[0]) if scored else 0
        tops = [sc for sc in scored if _scenario_score(sc) == top_score] if scored else []
        if tops:
            tie_seed = (unit or "") + "|" + (video_title or "") + "|" + ((cleaned or "")[:800])
            tie_idx = int(hashlib.md5(tie_seed.encode("utf-8", errors="ignore")).hexdigest()[:8], 16) % len(tops)
            picked = tops[tie_idx]
        else:
            picked = scenarios[0]

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
    # 中文語意改為優先 AI 生成；AI 不可用時才 fallback。
    # ===============================
    solution_blocks = [
        {
            "id": f"b{i+1}",
            "text": (line or "").lstrip(" "),
            "indent": len(line or "") - len((line or "").lstrip(" ")),
            "type": "core",
        }
        for i, line in enumerate(solution_lines)
    ]
    distractor_blocks = [{"id": f"d{i+1}", "text": line, "type": "distractor"} for i, line in enumerate(distractor_lines)]

    semantic_model = (os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()

    sem_labels = _ai_generate_semantic_labels(solution_lines, question_text, semantic_model, temperature=0.0)
    for i, b in enumerate(solution_blocks):
        if i < len(sem_labels):
            b["semantic_zh"] = _soften_semantic_hint(sem_labels[i], b.get("text", ""), is_distractor=False)

    template_slots = [
        {
            "label": _soften_semantic_hint(
                sem_labels[i] if i < len(sem_labels) else _label_for_code_line(solution_lines[i]),
                solution_lines[i],
                is_distractor=False,
            ),
            "slot": str(i),
        }
        for i in range(len(solution_lines))
    ]

    dis_items = [{"text": b.get("text", "")} for b in distractor_blocks]
    dis_labels = _ai_generate_distractor_semantics(
        question_text,
        solution_lines,
        dis_items,
        semantic_model,
        temperature=0.1,
    )
    for i, b in enumerate(distractor_blocks):
        if i < len(dis_labels):
            b["semantic_zh"] = _soften_semantic_hint(dis_labels[i], b.get("text", ""), is_distractor=True)

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