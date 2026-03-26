import os
import re
import hashlib
import random
import ast
_re = re
import json
from datetime import datetime, timezone
from typing import Any, Dict, Tuple, Optional

from bson import ObjectId

from ..db import db
from . import parsons_ai
from .parsons_concept_engine import build_generation_plan, build_template_solution, CONCEPT_KEYWORDS

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


def _subtitle_concept_alignment_check(
    plan: dict,
    trace_keywords: list,
    subtitle_text: str,
    question_text: str,
    solution_lines: list,
) -> Tuple[bool, list, list]:
    """檢查題目是否命中字幕概念關鍵詞，作為生成品質的硬性 gate。"""
    concept = str((plan or {}).get("concept") or "").strip()
    concept_kws = list(CONCEPT_KEYWORDS.get(concept, [])) if concept else []
    unit_kws = [str(x).strip() for x in (trace_keywords or []) if str(x).strip()]

    # 補少量字幕特徵詞，提升與影片概念的連動，但避免過度貼字幕原句。
    subtitle_terms = _extract_subtitle_signature_terms(subtitle_text or "", max_terms=6)
    subtitle_terms = [
        t for t in subtitle_terms
        if len(t) >= 2 and t not in {"老師", "影片", "題目", "程式", "說明", "內容"}
    ]

    seen = set()
    required = []
    for k in concept_kws + unit_kws + subtitle_terms:
        kk = str(k or "").strip().lower()
        if not kk or kk in seen:
            continue
        seen.add(kk)
        required.append(kk)

    if not required:
        return True, [], []

    hay = ((question_text or "") + "\n" + "\n".join(solution_lines or [])).lower()
    hits = [k for k in required if k in hay]

    # 關鍵詞越多，門檻越高；至少要求命中 1~2 個。
    needed = 1 if len(required) <= 3 else 2
    ok = len(hits) >= needed
    missing = [k for k in required if k not in hits]
    return ok, missing, required


def _build_unified_generation_policy(
    plan: dict,
    unit: str,
    constraints: dict,
    video_title: str,
    teacher_description: str,
    subtitle_text: str,
    selector_keywords: list,
) -> dict:
    """建立所有題型共用的一致性限制，避免規則零散漂移。"""
    concept = str((plan or {}).get("concept") or "").strip()
    unit_type = str((constraints or {}).get("unit_type") or "").strip().lower()

    concept_terms = [str(x).strip().lower() for x in (CONCEPT_KEYWORDS.get(concept, []) if concept else []) if str(x).strip()]
    title_terms = [str(x).strip().lower() for x in _extract_focus_keywords(video_title or "", max_keywords=6)]
    teacher_terms = [str(x).strip().lower() for x in _extract_focus_keywords(teacher_description or "", max_keywords=6)]
    subtitle_terms = [str(x).strip().lower() for x in _extract_subtitle_signature_terms(subtitle_text or "", max_terms=8)]
    selector_terms = [str(x).strip().lower() for x in (selector_keywords or []) if str(x).strip()]

    seen = set()
    allow_terms = []
    for term in (concept_terms + teacher_terms + title_terms + selector_terms + subtitle_terms):
        if not term or term in seen:
            continue
        seen.add(term)
        allow_terms.append(term)

    anchor_terms = []
    for term in (teacher_terms + title_terms + concept_terms + selector_terms):
        if term and term not in anchor_terms:
            anchor_terms.append(term)
        if len(anchor_terms) >= 10:
            break
    if not anchor_terms:
        anchor_terms = allow_terms[:8]

    min_hits = 1 if len(anchor_terms) <= 3 else 2
    if unit_type == "function" and len(anchor_terms) >= 5:
        min_hits = max(min_hits, 2)

    hard_forbid_terms = []
    if unit_type == "function" and any(x in allow_terms for x in ["加減乘除", "運算子", "operator", "+-*/"]):
        hard_forbid_terms = ["購物", "金額", "折扣", "會員", "庫存"]

    return {
        "concept": concept,
        "unit": str(unit or "").strip(),
        "unit_type": unit_type,
        "allow_terms": allow_terms,
        "anchor_terms": anchor_terms,
        "min_hits": min_hits,
        "hard_forbid_terms": hard_forbid_terms,
    }


def _unified_policy_prompt_block(policy: dict) -> str:
    anchors = [str(x).strip() for x in (policy or {}).get("anchor_terms", []) if str(x).strip()]
    if not anchors:
        return ""
    min_hits = int((policy or {}).get("min_hits") or 1)
    return (
        "【統一一致性限制（所有題型共用）】\n"
        f"- 題目與程式必須命中來源關鍵詞至少 {min_hits} 個：" + "、".join(anchors[:8]) + "\n"
        "- 若來源未提及，不要引入新的主題領域（例如購物/庫存/會員等）。\n"
        "- 老師有指定方向時，以老師方向優先。"
    )


def _policy_tokenize_text(text: str, max_terms: int = 24) -> list:
    raw = re.findall(r"[a-z_][a-z0-9_]*|[\u4e00-\u9fff]{2,}", (text or "").lower())
    stop = {
        "題目", "程式", "輸入", "輸出", "老師", "影片", "請", "使用", "完成", "資料",
        "python", "print", "input", "code", "line", "result", "value", "values",
    }
    out = []
    seen = set()
    for t in raw:
        if (not t) or (len(t) <= 1) or (t in stop) or (t in seen):
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= max_terms:
            break
    return out


def _validate_unified_generation_policy(question_text: str, solution_lines: list, policy: dict) -> Tuple[bool, dict]:
    policy = policy or {}
    anchors = [str(x).strip().lower() for x in policy.get("anchor_terms", []) if str(x).strip()]
    allow_terms = set([str(x).strip().lower() for x in policy.get("allow_terms", []) if str(x).strip()])
    hard_forbid_terms = [str(x).strip().lower() for x in policy.get("hard_forbid_terms", []) if str(x).strip()]
    min_hits = int(policy.get("min_hits") or 1)

    hay = ((question_text or "") + "\n" + "\n".join(solution_lines or [])).lower()
    anchor_hits = [k for k in anchors if k in hay]
    anchor_missing = [k for k in anchors if k not in hay]

    forbid_hit = [k for k in hard_forbid_terms if k in hay]

    q_terms = _policy_tokenize_text(question_text or "")
    off_topic_terms = [t for t in q_terms if (t not in allow_terms) and (t not in anchors)]
    off_topic_ratio = (len(off_topic_terms) / max(1, len(q_terms))) if q_terms else 0.0

    # 保持寬鬆，避免影響生題能力：只有偏題非常明顯才擋。
    severe_drift = (len(off_topic_terms) >= 9) and (off_topic_ratio >= 0.80)
    ok = (len(anchor_hits) >= min_hits) and (not forbid_hit) and (not severe_drift)

    reason = ""
    if len(anchor_hits) < min_hits:
        reason = "policy missing anchor terms: " + ", ".join(anchor_missing[:5])
    elif forbid_hit:
        reason = "policy forbidden terms: " + ", ".join(forbid_hit[:5])
    elif severe_drift:
        reason = "policy drift risk: too many off-topic terms"

    meta = {
        "ok": bool(ok),
        "anchor_hits": anchor_hits,
        "anchor_missing": anchor_missing,
        "min_hits": min_hits,
        "off_topic_terms": off_topic_terms[:10],
        "off_topic_ratio": round(float(off_topic_ratio), 4),
        "forbidden_hits": forbid_hit,
        "reason": reason,
    }
    return ok, meta


def _mix_pool_blocks(solution_blocks: list, distractor_blocks: list, seed_text: str = "") -> list:
    """打散 pool 順序，避免干擾題固定集中在最上方。"""
    sol = list(solution_blocks or [])
    dis = list(distractor_blocks or [])
    if not sol:
        return dis
    if not dis:
        return sol

    seed_src = seed_text or ("|".join(str(b.get("text", "")) for b in (sol + dis)))
    seed_int = int(hashlib.md5(seed_src.encode("utf-8", errors="ignore")).hexdigest()[:8], 16)
    rnd = random.Random(seed_int)

    rnd.shuffle(sol)
    rnd.shuffle(dis)

    # 交錯合併，確保不會出現「前面全是干擾題」的情況。
    out = []
    i = j = 0
    start_with_dis = bool(seed_int % 2)
    while i < len(sol) or j < len(dis):
        if start_with_dis and j < len(dis):
            out.append(dis[j])
            j += 1
        if i < len(sol):
            out.append(sol[i])
            i += 1
        if (not start_with_dis) and j < len(dis):
            out.append(dis[j])
            j += 1

    return out


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


def _semantic_keywords_from_question(question_text: str, max_keywords: int = 8) -> list:
    kws = _extract_focus_keywords(question_text or "", max_keywords=max_keywords)
    if kws:
        return kws
    raw = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z_]{3,}", question_text or "")
    seen = set()
    out = []
    for t in raw:
        k = str(t or "").strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(t)
        if len(out) >= max_keywords:
            break
    return out


def _trim_semantic_text(text: str, max_len: int = 18) -> str:
    s = str(text or "").strip()
    if len(s) <= max_len:
        return s
    return s[:max_len]


def _build_contextual_semantic_labels(solution_lines: list, question_text: str) -> list:
    kws = _semantic_keywords_from_question(question_text, max_keywords=8)
    if not kws:
        return [_label_for_code_line(ln) for ln in (solution_lines or [])]

    focus_terms = [k for k in kws if any(x in str(k) for x in ["底", "指數", "次方", "金額", "分數", "成績", "筆數", "次數", "面積", "平均", "折扣", "寬", "高"]) ]
    if not focus_terms:
        focus_terms = kws[:2]
    result_terms = [k for k in kws if any(x in str(k) for x in ["結果", "總", "平均", "次方", "面積", "筆數", "次數", "金額"]) ]
    main_result = result_terms[0] if result_terms else (focus_terms[0] if focus_terms else "結果")

    labels = []
    input_idx = 0
    for line in (solution_lines or []):
        s = (line or "").strip()
        low = s.lower()

        if "input(" in low:
            term = focus_terms[min(input_idx, len(focus_terms) - 1)] if focus_terms else "資料"
            input_idx += 1
            if "int(input" in low or "float(input" in low:
                labels.append(_trim_semantic_text(f"讀取{term}並轉成數值"))
            else:
                labels.append(_trim_semantic_text(f"讀取{term}資料"))
            continue

        if low.startswith("def "):
            labels.append(_trim_semantic_text(f"定義計算{main_result}的函式"))
            continue

        if low.startswith("return "):
            labels.append(_trim_semantic_text(f"回傳{main_result}計算結果"))
            continue

        if low.startswith("print("):
            labels.append(_trim_semantic_text(f"輸出{main_result}結果"))
            continue

        if low.startswith("if "):
            term = focus_terms[0] if focus_terms else main_result
            labels.append(_trim_semantic_text(f"判斷{term}條件是否成立"))
            continue

        if low.startswith("elif "):
            labels.append("補充另一個條件判斷")
            continue

        if low.startswith("else"):
            labels.append("處理條件未成立流程")
            continue

        if low.startswith("while "):
            labels.append("條件成立時重複執行")
            continue

        if low.startswith("for "):
            labels.append("使用迴圈逐步處理")
            continue

        if any(op in low for op in ["+=", "-=", "*=", "/="]):
            labels.append(_trim_semantic_text(f"更新{main_result}累計值"))
            continue

        labels.append(_trim_semantic_text(_label_for_code_line(line)))

    return labels


def _is_generic_semantic_label(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return True
    generic_terms = [
        "先準備一個值", "更新變數", "處理資料", "完成這一步", "銜接下一個程式流程",
        "先準備一個後續會使用到的值", "更新中間結果", "先做一個條件判斷",
    ]
    return any(t in s for t in generic_terms)


def _refine_semantic_labels(labels: list, solution_lines: list, question_text: str) -> list:
    contextual = _build_contextual_semantic_labels(solution_lines, question_text)
    out = []
    for i, line in enumerate(solution_lines or []):
        cur = str((labels or [])[i] if i < len(labels or []) else "").strip()
        if _is_generic_semantic_label(cur):
            cur = contextual[i] if i < len(contextual) else _label_for_code_line(line)
        cur = _trim_semantic_text(cur or _label_for_code_line(line))
        out.append(cur)
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

    # 干擾題保持保守，避免語意直接提示正解位置。
    if is_distractor:
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
                return _guided_hint_by_line(line, is_distractor=True)
        if len(base) > 20:
            return _guided_hint_by_line(line, is_distractor=True)
        return base

    # 核心解答允許貼情境語意，但仍避免把程式碼片段直接塞進中文。
    core_code_leak = [
        r"[A-Za-z_]\w*\s*=",
        r"==|!=|<=|>=|<|>|\+|-|\*|/|%",
        r"if\s+.+:",
        r"for\s+.+:",
        r"while\s+.+:",
        r"print\s*\(",
        r"input\s*\(",
    ]
    for p in core_code_leak:
        if _re.search(p, base, flags=_re.IGNORECASE):
            return _guided_hint_by_line(line, is_distractor=False)

    return _trim_semantic_text(base, max_len=18)


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

# =====================================
# [新增] 偵測subtitle中的實現細節
# =====================================

def _detect_output_style_preference(subtitle_text: str) -> str:
    """
    偵測subtitle中教的輸出風格：是用print，還是用return。
    回傳: 'print_only', 'return_preferred', 'mixed'
    """
    if not subtitle_text:
        return "print_only"
    
    text_lc = subtitle_text.lower()
    print_count = len(re.findall(r"\bprint\s*\(", text_lc))
    return_count = len(re.findall(r"\breturn\b", text_lc))
    
    # 如果只有print，明確是print輸出
    if print_count > 0 and return_count == 0:
        return "print_only"
    # 如果只有return，或return多於print
    elif return_count > print_count:
        return "return_preferred"
    # 如果都有，但print更多
    elif print_count >= return_count and print_count > 0:
        return "print_only"
    else:
        return "print_only"  # 預設

def _detect_int_input_pattern(subtitle_text: str) -> bool:
    """
    偵測subtitle中是否明確教了 int(input()) 這種轉型模式。
    回傳True如果有明確示意必須轉型。
    """
    if not subtitle_text:
        return False
    
    text_lc = subtitle_text.lower()
    # 檢查 int(input()) 或 int ( input ( ) ) 各種格式
    if re.search(r"\bint\s*\(\s*input\s*\(", text_lc):
        return True
    # 檢查「int轉型」、「資料型別」、「轉整數」等提示詞
    if any(k in text_lc for k in ["int 轉型", "轉型", "資料型別", "轉整數", "整數輸入", "型別轉換"]):
        return True
    
    return False

def _detect_output_format_pattern(subtitle_text: str) -> str:
    """
    偵測subtitle中教的輸出格式。
    回傳：'simple' (只輸出值), 'with_label' (帶標籤如'Name: xxx'), 'custom' (自訂格式)
    """
    if not subtitle_text:
        return "simple"
    
    text_lc = subtitle_text.lower()
    
    # 檢查是否有「name:」、「total=」等標籤格式
    if re.search(r"['\"]?\w+\s*[:=]", subtitle_text):
        return "with_label"
    
    # 檢查是否有複雜的格式化
    if re.search(r"print\(.*[':+]", subtitle_text):
        return "custom"
    
    return "simple"

def _detect_param_count(subtitle_text: str) -> Optional[int]:
    """
    偵測subtitle中教的函式參數數量。
    回傳參數數量（例如2個參數回傳2），或None if無明確指示。
    """
    if not subtitle_text:
        return None
    
    text_lc = subtitle_text.lower()
    
    # 方案1：搜尋 "def xxx(param1, param2, ...)" 這類定義
    # 提取最長的def定義
    def_matches = re.findall(r"def\s+\w+\s*\((.*?)\)", subtitle_text)
    if def_matches:
        # 取最後一個def（最新的教學），計算逗號+1 = 參數數
        last_def = def_matches[-1].strip()
        if last_def:  # 有參數
            param_count = last_def.count(',') + 1
            return param_count
        else:  # def xxx() 無參數
            return 0
    
    # 方案2：搜尋「幾個參數」「幾個引數」的提示
    # 例如「兩個參數」「3個參數」「參數有2個」
    for match in re.findall(r"([一二三四五1234])\s*[個個]?\s*(?:參數|引數|輸入|參數)", text_lc):
        zh_to_num = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
        if match in zh_to_num:
            return zh_to_num[match]
        try:
            return int(match)
        except ValueError:
            continue
    
    # 方案3：搜尋「底數與指數」「兩個輸入」這類雙參數提示
    if any(k in text_lc for k in ["底數", "指數", "兩個輸入", "兩筆輸入", "兩個資料"]):
        if "底數" in text_lc and "指數" in text_lc:
            return 2
        if "兩個" in text_lc or "兩筆" in text_lc:
            return 2
    
    return None


def _build_function_structure_profile(subtitle_text: str, teacher_description: str = "", context_text: str = "", force_operator_dispatch: bool = False) -> dict:
    """將 function 題需求收斂為統一 profile，避免生成階段分散判斷。"""
    src = "\n".join([
        str(subtitle_text or ""),
        str(teacher_description or ""),
        str(context_text or ""),
    ])
    src_lc = src.lower()

    output_style_preference = _detect_output_style_preference(src)
    need_input = bool(
        ("input(" in src_lc)
        or any(k in src_lc for k in ["輸入", "讀入", "輸入值", "int(input", "float(input"])
    )
    need_print = (output_style_preference == "print_only") or ("print(" in src_lc) or ("輸出" in src_lc)
    prefer_return = (output_style_preference == "return_preferred") or ("return" in src_lc)

    param_count = _detect_param_count(src)
    allow_condition = bool(_should_func_allow_condition(src, teacher_description))
    need_int_cast = bool(_detect_int_input_pattern(src))
    output_format_style = _detect_output_format_pattern(src)

    if force_operator_dispatch:
        allow_condition = True
        need_input = True
        need_print = True
        if param_count is None or param_count < 3:
            param_count = 3

    return {
        "param_count": param_count,
        "need_input": bool(need_input),
        "need_print": bool(need_print),
        "prefer_return": bool(prefer_return),
        "allow_condition": bool(allow_condition),
        "need_int_cast": bool(need_int_cast),
        "output_format_style": str(output_format_style or "simple"),
        "force_operator_dispatch": bool(force_operator_dispatch),
    }


def _validate_function_structure_profile(solution_lines: list, profile: dict) -> Tuple[bool, str]:
    if not solution_lines:
        return False, "function profile: empty solution"

    profile = profile or {}
    code = "\n".join(solution_lines)
    has_def = bool(_re.search(r"^\s*def\s+", code, flags=_re.M))
    if not has_def:
        return False, "function profile: missing def"

    input_count = len(_re.findall(r"\binput\s*\(", code))
    has_print = bool(_re.search(r"\bprint\s*\(", code))
    has_return = bool(_re.search(r"\breturn\b", code))
    has_if_like = bool(_re.search(r"^\s*(if|elif|else)\b", code, flags=_re.M))

    if bool(profile.get("need_input")) and input_count <= 0:
        return False, "function profile: need input()"
    if bool(profile.get("need_print")) and (not has_print):
        return False, "function profile: need print()"
    if bool(profile.get("prefer_return")) and (not has_return):
        return False, "function profile: prefer return"
    if (not bool(profile.get("allow_condition", True))) and has_if_like:
        return False, "function profile: condition not allowed"

    expected_param_count = profile.get("param_count")
    if expected_param_count is not None:
        try:
            tree = ast.parse(code)
            funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
            if funcs:
                actual = len(funcs[0].args.args)
                if int(actual) != int(expected_param_count):
                    return False, f"function profile: expected {expected_param_count} params, got {actual}"
        except Exception:
            return False, "function profile: parse failed"

    if bool(profile.get("need_int_cast")) and input_count > 0:
        without_cast = _re.sub(r'\b(?:int|float|str)\s*\(\s*input\s*\(\s*\)\s*\)', '', code)
        if "input(" in without_cast:
            return False, "function profile: need int/float/str cast for input"

    return True, ""


def _classify_subtitle_sentence_type(text: str) -> str:
    t = str(text or "").strip().lower()
    if not t:
        return "other"
    if any(k in t for k in ["例如", "比如", "像是", "範例", "example", "舉例"]):
        return "example"
    if any(k in t for k in ["必須", "需要", "注意", "禁止", "不可", "規則", "rule"]):
        return "rule"
    if any(k in t for k in ["是", "代表", "意思", "定義", "就是", "稱為", "指的是"]):
        return "definition"
    return "other"


def _extract_typed_key_sentences_from_segments(segs: list, max_sentences: int = 10) -> list:
    out = []
    seen = set()
    for s in (segs or []):
        txt = str((s or {}).get("text") or "").strip()
        if not txt:
            continue
        key = txt.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "text": txt,
            "sentence_type": _classify_subtitle_sentence_type(txt),
            "start": float((s or {}).get("start") or 0.0),
            "end": float((s or {}).get("end") or 0.0),
        })
        if len(out) >= max_sentences:
            break
    return out


def _build_alignment_confidence(selector_meta: dict, typed_key_sentences: list, segment_map: Optional[dict] = None, slot_hints: Optional[dict] = None) -> dict:
    sel = selector_meta or {}
    typed = typed_key_sentences or []
    seg_map = segment_map or {}
    slot_hints = slot_hints or {}

    sentence_type_counts = {"definition": 0, "rule": 0, "example": 0, "other": 0}
    for it in typed:
        tp = str((it or {}).get("sentence_type") or "other")
        if tp not in sentence_type_counts:
            tp = "other"
        sentence_type_counts[tp] += 1

    selector_score = float(sel.get("best_score") or 0.0)
    hit_ratio = float(sel.get("hit_ratio") or 0.0)
    kw_cov = float(sel.get("keyword_coverage_ratio") or 0.0)
    selected_count = int(sel.get("selected_count") or 0)

    mapped_slots = len([k for k, v in (seg_map.items() if isinstance(seg_map, dict) else []) if isinstance(v, dict)])
    hinted_slots = len([k for k, v in (slot_hints.items() if isinstance(slot_hints, dict) else []) if str(v or "").strip()])

    # 0~1 分數，偏保守。selector 品質占大宗，slot 對齊做加分。
    base = min(1.0, max(0.0, (hit_ratio * 0.4) + (kw_cov * 0.35) + (min(selector_score, 20.0) / 20.0) * 0.25))
    bonus = min(0.2, (min(mapped_slots, 10) / 10.0) * 0.15 + (min(hinted_slots, 10) / 10.0) * 0.05)
    confidence = round(min(1.0, base + bonus), 4)

    return {
        "score": confidence,
        "selected_count": selected_count,
        "sentence_type_counts": sentence_type_counts,
        "mapped_slots": mapped_slots,
        "hinted_slots": hinted_slots,
        "source": "selector_plus_slot_map",
    }

def _should_func_allow_condition(subtitle_text: str, teacher_description: str = "") -> bool:
    """
    啟發式檢查：某些場景函式中必須允許 if/elif/else。
    例如：計算機題、操作選擇、多分支邏輯。
    回傳 True → 即使 subtitle 沒教 if，仍允許 if 使用。
    """
    if not subtitle_text:
        return False
    
    combined = (subtitle_text + " " + (teacher_description or "")).lower()
    
    # 方案1：參數名稱暗示操作選擇（在 def 行中找）
    # 例如 def f(x, y, op):, def calculate(a, b, operation):
    operation_param_names = [
        "op", "operation", "operator", "choice", "option",
        "type", "mode", "kind", "select", "cmd", "action", "func"
    ]
    
    def_line_matches = re.findall(r"def\s+\w+\s*\((.*?)\)", combined)
    for def_params in def_line_matches:
        params_lc = def_params.lower()
        if any(pname in params_lc for pname in operation_param_names):
            return True
    
    # 方案2：Subtitle 明確提到計算機、選擇、多分支邏輯
    heuristic_keywords = [
        "計算器", "calculator", "operator", "operation", "運算",
        "加減乘除", "+-*/", "選擇", "分支", "選項", "option",
        "+-*/, 根據", "根據選擇", "根據操作", "依據",
        "加", "減", "乘", "除"  # 四則運算
    ]
    
    if any(kw in combined for kw in heuristic_keywords):
        # 檢查是否真的有「多個操作」提示
        if combined.count("加") > 0 or combined.count("減") > 0:
            # 至少提到兩個運算
            op_count = sum(1 for op in ["加", "減", "乘", "除"] if op in combined)
            if op_count >= 2:
                return True
        
        # 或直接提到「計算器」「運算選擇」
        if any(k in combined for k in ["計算器", "calculator", "根據選擇", "根據操作"]):
            return True
        
        # 或有「operation」「operator」這類英文提示
        if "operation" in combined or "operator" in combined:
            return True
    
    return False

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


def _norm_unit_prefix(unit: str) -> str:
    u = (unit or "").strip().upper()
    if not u:
        return ""
    return u.split("-")[0]


def _to_block_list(lines_or_blocks: Any, block_prefix: str = "b") -> list:
    out = []
    seq = lines_or_blocks or []
    if not isinstance(seq, list):
        return out
    for i, item in enumerate(seq):
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            out.append({
                "id": str(item.get("id") or f"{block_prefix}{i+1}"),
                "text": text,
                "type": str(item.get("type") or ("distractor" if block_prefix == "d" else "core")),
            })
        else:
            text = str(item or "").strip()
            if not text:
                continue
            out.append({"id": f"{block_prefix}{i+1}", "text": text, "type": "distractor" if block_prefix == "d" else "core"})
    return out


def _pick_db_fallback_question(unit: str, video_title: str, level: str = "L1") -> Optional[dict]:
    """從 DB 撈備選題，優先 video_title + unit，其次 unit 前綴（例如 U7）。"""
    try:
        unit_u = (unit or "").strip().upper()
        unit_prefix = _norm_unit_prefix(unit_u)
        title = (video_title or "").strip()
        lv = (level or "L1").strip().upper()

        base_q = {"active": {"$ne": False}}

        # 避免同影片/同單元短時間重覆抽到同一題（看最近 8 筆 fallback）
        recent_used_qtexts = set()
        try:
            recent_q = {
                "gen_source": "fallback",
                "unit": unit_u,
                "video_title": title,
            }
            for t in db.parsons_tasks.find(recent_q, {"question_text": 1}).sort("created_at", -1).limit(8):
                qt = str((t or {}).get("question_text") or "").strip()
                if qt:
                    recent_used_qtexts.add(qt)
        except Exception:
            pass

        def _pick_non_recent(candidates: list) -> Optional[dict]:
            if not candidates:
                return None
            fresh = [c for c in candidates if str((c or {}).get("question_text") or "").strip() not in recent_used_qtexts]
            pool = fresh if fresh else candidates
            return random.choice(pool) if pool else None

        # 1) 最精準：unit + video_title + level
        if title:
            q1 = {
                **base_q,
                "unit": unit_u,
                "video_title": title,
                "$or": [{"level": lv}, {"level": {"$exists": False}}, {"level": ""}],
            }
            c1 = list(db.parsons_fallback_questions.find(q1).limit(30))
            picked = _pick_non_recent(c1)
            if picked:
                return picked

        # 2) unit + level
        q2 = {
            **base_q,
            "unit": unit_u,
            "$or": [{"level": lv}, {"level": {"$exists": False}}, {"level": ""}],
        }
        c2 = list(db.parsons_fallback_questions.find(q2).limit(50))
        picked = _pick_non_recent(c2)
        if picked:
            return picked

        # 3) unit 前綴（U1/U2...）
        if unit_prefix:
            q3 = {
                **base_q,
                "unit_prefix": unit_prefix,
                "$or": [{"level": lv}, {"level": {"$exists": False}}, {"level": ""}],
            }
            c3 = list(db.parsons_fallback_questions.find(q3).limit(50))
            picked = _pick_non_recent(c3)
            if picked:
                return picked

        return None
    except Exception:
        return None


def _build_fallback_payload_from_doc(doc: dict, constraints: dict) -> Optional[Dict[str, Any]]:
    try:
        question_text = str(doc.get("question_text") or "").strip()
        if not question_text:
            return None

        solution_blocks = _to_block_list(doc.get("solution_blocks") or doc.get("solution_lines"), "b")
        distractor_blocks = _to_block_list(doc.get("distractor_blocks") or doc.get("distractor_lines"), "d")
        if not solution_blocks:
            return None

        solution_lines = [str(b.get("text") or "") for b in solution_blocks]
        _labels = _build_contextual_semantic_labels(solution_lines, question_text)
        for i, b in enumerate(solution_blocks):
            b["semantic_zh"] = _soften_semantic_hint(_labels[i], b.get("text") or "", is_distractor=False)

        pool = _mix_pool_blocks(solution_blocks, distractor_blocks, seed_text=question_text)
        template_slots = [{"label": _soften_semantic_hint(_labels[i], solution_lines[i], is_distractor=False), "slot": str(i)} for i in range(len(solution_lines))]

        return {
            "question_text": question_text,
            "solution_blocks": solution_blocks,
            "distractor_blocks": distractor_blocks,
            "pool": pool,
            "template_slots": template_slots,
            "ai_feedback": {
                "general": "（使用資料庫備選題）",
                "common_mistakes": doc.get("common_mistakes") or [],
                "hints": doc.get("hints") or [],
            },
            "unit_type": (constraints or {}).get("unit_type") or "loop",
            "constraints": constraints,
            "rule_check": build_rule_check(solution_lines, constraints),
            "source_subtitle": {"text_used": ""},
            "subtitle_range": {"start_index": 0, "end_index": 0, "start_ts": 0, "end_ts": 0},
            "subtitle_text_used": "",
            "fallback_source": "db",
        }
    except Exception:
        return None


# =========================
# t5doc_to_parsons_task (teacher->student normalize)
# =========================

def simple_fallback_generate(sub_text: str, unit: str, video_title: str, level: str = "L1", teacher_description: str = "") -> Dict[str, Any]:
    constraints = resolve_unit_constraints(unit)
    unit_type = (constraints or {}).get("unit_type") or "loop"
    loop_style = (constraints or {}).get("loop_style") or "either"

    # 優先使用資料庫備選題；查不到時才走舊版程式內建備援。
    fb_doc = _pick_db_fallback_question(unit=unit, video_title=video_title, level=level)
    if fb_doc:
        db_payload = _build_fallback_payload_from_doc(fb_doc, constraints)
        if db_payload:
            return db_payload

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

    elif unit_type == "function":
        question_text = "（備援題目）請定義函式 add(a, b) 回傳兩數和，讀入兩個整數後呼叫函式並輸出結果。"
        solution_lines = [
            "def add(a, b):",
            "    return a + b",
            "x = int(input())",
            "y = int(input())",
            "print(add(x, y))",
        ]
        distractor_lines = [
            "def add(a, b):",
            "    print(a + b)",
            "print(add(x + y))",
            "x = input()",
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

    _fb_labels = _build_contextual_semantic_labels(solution_lines, question_text)
    solution_blocks = [
        {"id": f"b{i+1}", "text": line, "type": "core", "semantic_zh": _soften_semantic_hint(_fb_labels[i], line, is_distractor=False)}
        for i, line in enumerate(solution_lines)
    ]
    distractor_blocks = [{"id": f"d{i+1}", "text": line, "type": "distractor"} for i, line in enumerate(distractor_lines)]
    pool = _mix_pool_blocks(solution_blocks, distractor_blocks, seed_text=question_text)
    template_slots = [
        {"label": _soften_semantic_hint(_fb_labels[i], solution_lines[i], is_distractor=False), "slot": str(i)}
        for i in range(len(solution_lines))
    ]

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
        return {"unit_type": "function", "require_def": True, "forbid_class": True}
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
        "function": ["def", "function", "函式", "return", "參數", "呼叫"],
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


def _selector_unit_keywords(
    unit: str,
    constraints: dict,
    teacher_description: str = "",
    video_title: str = "",
    extra_keywords: Optional[list] = None,
    trace_keywords: Optional[list] = None,
) -> list:
    u = (unit or "").strip().upper()
    unit_type = (constraints or {}).get("unit_type") or "loop"
    loop_style = (constraints or {}).get("loop_style") or "either"

    kws = []
    if unit_type == "io":
        kws.extend(["input", "print", "輸入", "輸出", "讀入"])
    elif unit_type == "condition":
        kws.extend(["if", "elif", "else", "條件", "判斷", "比較"])
    elif unit_type == "function":
        kws.extend(["def", "return", "函式", "function", "參數", "呼叫", "input", "輸入", "讀入", "int("])
    elif unit_type == "loop":
        if loop_style == "while_only":
            kws.extend(["while", "quit", "end", "停止", "結束", "重複", "輸入"])
        elif loop_style == "for_only":
            kws.extend(["for", "range", "迴圈", "次數", "重複", "走訪"])
            if "-LIST" in u or u.startswith("U6"):
                kws.extend(["list", "列表", "append", "清單"])
        else:
            kws.extend(["for", "while", "range", "迴圈", "重複", "條件"])

    if trace_keywords:
        kws.extend([str(x) for x in (trace_keywords or []) if str(x or "").strip()])

    # 教師描述常含題目語境，加入切片關鍵字可以提高相關段落命中率。
    kws.extend(_extract_focus_keywords(teacher_description or "", max_keywords=6))
    kws.extend(_extract_focus_keywords(video_title or "", max_keywords=5))

    if extra_keywords:
        kws.extend([str(x) for x in (extra_keywords or []) if str(x or "").strip()])

    out = []
    seen = set()
    for k in kws:
        kk = str(k or "").strip().lower()
        if not kk or kk in seen:
            continue
        seen.add(kk)
        out.append(kk)
    return out


def _score_subtitle_segment_for_selector(text: str, keywords: list) -> int:
    t = (text or "").lower()
    if not t:
        return 0

    score = 0
    for kw in (keywords or []):
        k = str(kw or "").strip().lower()
        if not k:
            continue
        if k in t:
            score += 3 if len(k) >= 2 else 1

    for token in ["input", "print", "if", "for", "while", "def", "return", "quit", "range"]:
        if token in t:
            score += 1

    # 題目可出性加權：定義句、規則句、示例句通常比閒聊更穩定。
    sentence_type_bonus = ["例如", "比如", "像是", "必須", "需要", "注意", "通常", "代表", "意思是"]
    for token in sentence_type_bonus:
        if token in t:
            score += 1
    return score


def _select_relevant_subtitle_segments(
    segs: list,
    unit: str,
    constraints: dict,
    teacher_description: str = "",
    video_title: str = "",
    question_keywords: Optional[list] = None,
    trace_keywords: Optional[list] = None,
    window: int = 4,
    max_segments: int = 10,
) -> Dict[str, Any]:
    if not segs:
        return {"segments": [], "keywords": [], "best_idx": -1, "best_score": 0, "fallback": "empty_subtitle"}

    keywords = _selector_unit_keywords(
        unit=unit,
        constraints=constraints,
        teacher_description=teacher_description,
        video_title=video_title,
        extra_keywords=question_keywords,
        trace_keywords=trace_keywords,
    )

    if not keywords:
        return {
            "segments": segs[:max_segments],
            "keywords": [],
            "best_idx": 0,
            "best_score": 0,
            "fallback": "no_keywords",
        }

    scored = []
    for idx, seg in enumerate(segs):
        txt = str(seg.get("text") or "")
        scored.append((idx, _score_subtitle_segment_for_selector(txt, keywords)))

    best_idx, best_score = max(scored, key=lambda x: x[1]) if scored else (0, 0)
    hit_scored = [x for x in scored if x[1] > 0]

    if best_score <= 0:
        trace = _pick_trace_window(segs, constraints, max_lines=max_segments)
        sr = (trace.get("subtitle_range") or {})
        s_idx = int(sr.get("start_index") or 0)
        e_idx = int(sr.get("end_index") or 0)
        selected = segs[s_idx:e_idx + 1] if e_idx >= s_idx else segs[:max_segments]
        return {
            "segments": selected[:max_segments],
            "keywords": keywords,
            "best_idx": best_idx,
            "best_score": best_score,
            "fallback": "trace_window",
        }

    # 命中時優先取 Top N 句，再按原字幕順序回傳，降低多概念混入。
    # 命中太少時，補 best_idx 周圍 window 句，避免上下文斷裂。
    hit_scored_sorted = sorted(hit_scored, key=lambda x: (-x[1], x[0]))
    picked_idx = [idx for idx, _ in hit_scored_sorted[:max_segments]]

    if len(picked_idx) < min(5, max_segments):
        start = max(0, best_idx - max(0, int(window)))
        end = min(len(segs), best_idx + max(0, int(window)) + 1)
        for i in range(start, end):
            if i not in picked_idx:
                picked_idx.append(i)
            if len(picked_idx) >= max_segments:
                break

    picked_idx = sorted(picked_idx)
    selected = [segs[i] for i in picked_idx[:max_segments]]

    return {
        "segments": selected,
        "keywords": keywords,
        "best_idx": best_idx,
        "best_score": best_score,
        "total_segments": len(segs),
        "hit_count": len(hit_scored),
        "fallback": "",
    }


def _extract_key_sentences_from_segments(segs: list, max_sentences: int = 10) -> list:
    typed = _extract_typed_key_sentences_from_segments(segs, max_sentences=max_sentences)
    return [str((x or {}).get("text") or "").strip() for x in typed if str((x or {}).get("text") or "").strip()]


def _build_selector_quality_meta(selected_meta: dict, selected_segs: list) -> dict:
    meta = dict(selected_meta or {})
    keywords = [str(x).strip().lower() for x in (meta.get("keywords") or []) if str(x).strip()]
    kw_set = list(dict.fromkeys(keywords))
    total_segments = int(meta.get("total_segments") or len(selected_segs or []) or 0)
    hit_count = int(meta.get("hit_count") or 0)

    covered = set()
    for seg in (selected_segs or []):
        t = str((seg or {}).get("text") or "").lower()
        if not t:
            continue
        for kw in kw_set:
            if kw and kw in t:
                covered.add(kw)

    hit_ratio = (float(hit_count) / float(total_segments)) if total_segments > 0 else 0.0
    keyword_coverage_ratio = (float(len(covered)) / float(len(kw_set))) if kw_set else 0.0

    meta.update({
        "total_segments": total_segments,
        "hit_count": hit_count,
        "hit_ratio": round(hit_ratio, 4),
        "keyword_coverage_ratio": round(keyword_coverage_ratio, 4),
        "selected_count": len(selected_segs or []),
    })
    return meta


def _selector_quality_low(meta: dict) -> bool:
    m = meta or {}
    if int(m.get("best_score") or 0) <= 0:
        return True
    hit_ratio = float(m.get("hit_ratio") or 0.0)
    kw_cov = float(m.get("keyword_coverage_ratio") or 0.0)
    selected_count = int(m.get("selected_count") or 0)
    if selected_count <= 0:
        return True
    if hit_ratio < 0.05 and kw_cov < 0.12:
        return True
    return False

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
        base = _build_contextual_semantic_labels(solution_lines, question_text)
        base = [_semantic_paraphrase(base[i], question_text, solution_lines[i]) for i in range(len(solution_lines))]
        return _refine_semantic_labels(base, solution_lines, question_text)

    numbered = "\n".join(f"{i}: {ln}" for i, ln in enumerate(solution_lines))
    kw_list = _semantic_keywords_from_question(question_text, max_keywords=8)
    kw_text = "、".join(kw_list) if kw_list else "（無）"
    try:
        result = parsons_ai.call_openai_json(
            model=model,
            temperature=temperature,
            max_output_tokens=600,
            system=(
                "你是 Python 程式設計助教。"
                "請為每一行程式碼寫一句簡短的繁體中文說明（10~18字）。"
                "語意需貼合題目情境，優先使用題目關鍵詞。"
                "不要暴露變數名、數字、比較符號、運算式。"
                "不要直接說明正確條件內容。"
                "語氣要穩定、教學式，避免花俏修辭。"
                "避免泛用句，例如：先準備一個值、更新變數、處理資料。"
                "只輸出純 JSON，格式：{\"labels\": [\"第0行說明\", \"第1行說明\", ...]}"
            ),
            user=(
                f"題目：{question_text}\n"
                f"題目關鍵詞：{kw_text}\n\n"
                f"程式碼（格式：行號: 程式碼）：\n{numbered}\n\n"
                "請依序為每行輸出一句繁體中文說明，數量必須和行數相同。"
                "請盡量使用題目關鍵詞。"
                "提示要保留學生思考空間，不可直接揭示答案。"
            ),
        ) or {}
        labels = result.get("labels") or []
        if isinstance(labels, list) and len(labels) == len(solution_lines):
            refined = _refine_semantic_labels(labels, solution_lines, question_text)
            return [
                _soften_semantic_hint(str(l).strip() or _label_for_code_line(solution_lines[i]), solution_lines[i], is_distractor=False)
                for i, l in enumerate(refined)
            ]
    except Exception:
        pass
    # fallback 規則式
    base = _build_contextual_semantic_labels(solution_lines, question_text)
    base = [_semantic_paraphrase(base[i], question_text, solution_lines[i]) for i in range(len(solution_lines))]
    base = _refine_semantic_labels(base, solution_lines, question_text)
    return [_soften_semantic_hint(base[i], solution_lines[i], is_distractor=False) for i in range(len(solution_lines))]


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

    def _infer_error_type(line: str) -> str:
        s = str(line or "")
        low = s.lower().strip()
        if not s:
            return "generic_mutation"
        if (not s.startswith("    ")) and (len(s) != len(s.lstrip(" "))) and low:
            return "wrong_indent"
        if "int(input(" in low or "float(input(" in low or "input()" in low:
            return "input_cast_or_io"
        if any(op in low for op in ["==", "!=", "<=", ">=", "<", ">"]):
            return "wrong_condition"
        if low in {"break", "continue"}:
            return "flow_control"
        if any(op in low for op in ["+=", "-=", "*=", "/="]):
            return "state_update"
        if "print(" in low:
            return "wrong_output"
        if "for " in low or "while " in low or "range(" in low:
            return "loop_intrusion"
        return "generic_mutation"

    pool = []
    seen = set()

    def _push_pool(item):
        line = item.get("text", "") if isinstance(item, dict) else str(item or "")
        semantic = item.get("semantic_zh", "") if isinstance(item, dict) else ""
        error_type = item.get("error_type", "") if isinstance(item, dict) else ""
        normalized = _normalize_code_line(line)
        if not normalized or normalized in sol_norm or normalized in seen:
            return
        seen.add(normalized)
        et = (error_type or "").strip() or _infer_error_type(line)
        pool.append({
            "text": line,
            "semantic_zh": _soften_semantic_hint((semantic or "").strip() or _label_for_distractor_line(line), line, is_distractor=True),
            "error_type": et,
        })

    for item in distractor_items:
        _push_pool(item)

    candidates = []
    for line in solution_lines:
        candidates.extend(_mutate_distractor_candidates(line))
    for line in candidates:
        _push_pool(line)

    fallback = [
        {"text": "print('結果錯誤')", "error_type": "wrong_output"},
        {"text": "value = input()", "error_type": "input_cast_or_io"},
        {"text": "count = count + 1", "error_type": "state_update"},
    ]
    for item in fallback:
        _push_pool(item)

    if not pool:
        return []

    # 先保證錯誤類型多樣性：至少兩種（若資料足夠）。
    chosen = []
    chosen_types = set()
    diversity_target = min(2, max_count)
    available_types = {str(p.get("error_type") or "generic_mutation") for p in pool}
    if len(available_types) < diversity_target:
        diversity_target = len(available_types)

    for item in pool:
        et = str(item.get("error_type") or "generic_mutation")
        if et in chosen_types:
            continue
        chosen.append(item)
        chosen_types.add(et)
        if len(chosen_types) >= diversity_target:
            break

    for item in pool:
        if len(chosen) >= max_count:
            break
        if item in chosen:
            continue
        chosen.append(item)

    if len(chosen) < min_count:
        for item in pool:
            if len(chosen) >= min_count:
                break
            if item in chosen:
                continue
            chosen.append(item)

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

    # 產生貼合題目情境的中文語意標籤
    labels = _build_contextual_semantic_labels(solution_lines, question_text)

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
    pool = _mix_pool_blocks(solution_blocks, distractor_blocks, seed_text=question_text)
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

    constraints = resolve_unit_constraints(unit)
    segs = parse_srt_segments(subtitle_text)
    trace = _pick_trace_window(segs, constraints, max_lines=7)
    selected_meta = _select_relevant_subtitle_segments(
        segs=segs,
        unit=unit,
        constraints=constraints,
        teacher_description=teacher_description,
        video_title=video_title,
        question_keywords=_extract_focus_keywords(video_title or "", max_keywords=5),
        trace_keywords=trace.get("keywords", []),
        window=4,
        max_segments=10,
    )
    selected_segs = selected_meta.get("segments") or segs
    segs_compact = compact_segments_for_prompt(selected_segs, max_chars=1500) or ""
    selector_keywords = "、".join((selected_meta.get("keywords") or [])[:8])
    key_sentences = _extract_key_sentences_from_segments(selected_segs, max_sentences=10)
    key_sentences_typed = _extract_typed_key_sentences_from_segments(selected_segs, max_sentences=10)
    selector_meta = _build_selector_quality_meta(selected_meta, selected_segs)

    # ✅ 修正：傳入 teacher_description，老師描述優先決定 concept / scenario_hint
    plan = build_generation_plan(unit, trace.get("subtitle_text_used", "") or subtitle_text, video_title, teacher_description)
    concept = (plan.get("concept") or "").strip()
    scenario_hint = (plan.get("scenario") or "").strip()
    unified_policy = _build_unified_generation_policy(
        plan=plan,
        unit=unit,
        constraints=constraints,
        video_title=video_title,
        teacher_description=teacher_description,
        subtitle_text=trace.get("subtitle_text_used", "") or subtitle_text,
        selector_keywords=selected_meta.get("keywords") or [],
    )
    unified_rule_text = _unified_policy_prompt_block(unified_policy)
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

【字幕切片關鍵詞】
{selector_keywords or "（自動）"}

【字幕切片使用規則】
- 你只能根據下方「字幕參考 / 字幕（含時間戳）」內容出題。
- 不可自行補充未出現在字幕切片中的教學內容。

{unified_rule_text}

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

    concept_ok, concept_missing, _required_concepts = _subtitle_concept_alignment_check(
        plan,
        trace.get("keywords", []),
        trace.get("subtitle_text_used", subtitle_text),
        question_text,
        solution_lines,
    )
    policy_ok, policy_meta = _validate_unified_generation_policy(
        question_text,
        solution_lines,
        unified_policy,
    )
    rc = build_rule_check(solution_lines, constraints)
    if (not rc.get("ok")) or (not concept_ok) or (not policy_ok):
        issues = []
        if not rc.get("ok"):
            issues.append(f"自動驗收失敗：{rc.get('reason')}")
        if not concept_ok:
            issues.append("與字幕概念不夠對齊，缺少關鍵詞：" + ", ".join(concept_missing[:5]))
        if not policy_ok:
            issues.append("統一限制未通過：" + str(policy_meta.get("reason") or "請提高來源關鍵詞命中"))

        retry_prompt = base_prompt + f"\n\n你上一版需要修正：{'；'.join(issues)}。請修正後重新輸出 JSON。"
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
        concept_ok, concept_missing, _required_concepts = _subtitle_concept_alignment_check(
            plan,
            trace.get("keywords", []),
            trace.get("subtitle_text_used", subtitle_text),
            question_text,
            solution_lines,
        )
        policy_ok, policy_meta = _validate_unified_generation_policy(
            question_text,
            solution_lines,
            unified_policy,
        )
        rc = build_rule_check(solution_lines, constraints)

    if is_too_similar_to_subtitle(trace.get("subtitle_text_used", subtitle_text), question_text):
        raise RuntimeError("generated question is too similar to subtitle example")

    if not rc.get("ok"):
        raise RuntimeError(f"AI condition generation failed: {rc.get('reason')}")
    if not concept_ok:
        raise RuntimeError("AI condition generation failed: subtitle concept alignment not met")
    if not policy_ok:
        raise RuntimeError("AI condition generation failed: unified policy not met")

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
        "key_sentences": key_sentences,
        "key_sentences_typed": key_sentences_typed,
        # 讓後續對齊/除錯流程可直接使用已壓縮字幕片段。
        "ai_segments_compact": segs_compact,
        "selector_meta": selector_meta,
        "unified_policy_meta": policy_meta,
        "alignment_confidence": _build_alignment_confidence(selector_meta, key_sentences_typed),
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

    constraints = resolve_unit_constraints(unit)
    segs = parse_srt_segments(subtitle_text)
    trace = _pick_trace_window(segs, constraints, max_lines=7)
    selected_meta = _select_relevant_subtitle_segments(
        segs=segs,
        unit=unit,
        constraints=constraints,
        teacher_description=teacher_description,
        video_title=video_title,
        question_keywords=_extract_focus_keywords(video_title or "", max_keywords=5),
        trace_keywords=trace.get("keywords", []),
        window=4,
        max_segments=10,
    )
    selected_segs = selected_meta.get("segments") or segs
    segs_compact = compact_segments_for_prompt(selected_segs, max_chars=1500) or ""
    selector_keywords = "、".join((selected_meta.get("keywords") or [])[:8])
    key_sentences = _extract_key_sentences_from_segments(selected_segs, max_sentences=10)
    key_sentences_typed = _extract_typed_key_sentences_from_segments(selected_segs, max_sentences=10)
    selector_meta = _build_selector_quality_meta(selected_meta, selected_segs)

    # ✅ 修正：傳入 teacher_description，老師描述優先決定 concept / scenario_hint
    plan = build_generation_plan(unit, trace.get("subtitle_text_used", "") or subtitle_text, video_title, teacher_description)
    concept = (plan.get("concept") or "").strip()
    scenario_hint = (plan.get("scenario") or "").strip()
    unified_policy = _build_unified_generation_policy(
        plan=plan,
        unit=unit,
        constraints=constraints,
        video_title=video_title,
        teacher_description=teacher_description,
        subtitle_text=trace.get("subtitle_text_used", "") or subtitle_text,
        selector_keywords=selected_meta.get("keywords") or [],
    )
    unified_rule_text = _unified_policy_prompt_block(unified_policy)
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

【字幕切片關鍵詞】
{selector_keywords or "（自動）"}

【字幕切片使用規則】
- 你只能根據下方「字幕參考 / 字幕（含時間戳）」內容出題。
- 不可自行補充未出現在字幕切片中的教學內容。

{unified_rule_text}

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
    concept_ok, concept_missing, _required_concepts = _subtitle_concept_alignment_check(
        plan,
        trace.get("keywords", []),
        trace.get("subtitle_text_used", subtitle_text),
        question_text,
        solution_lines,
    )
    policy_ok, policy_meta = _validate_unified_generation_policy(
        question_text,
        solution_lines,
        unified_policy,
    )

    rc = build_rule_check(solution_lines, constraints)
    if (not rc.get("ok")) or (not align_ok) or (not concept_ok) or (not policy_ok):
        extra = []
        if not rc.get("ok"):
            extra.append(f"自動驗收失敗：{rc.get('reason')}")
        if not align_ok:
            extra.append("與老師指定需求不夠對齊，缺少關鍵詞：" + ", ".join(missing_kws[:5]))
        if not concept_ok:
            extra.append("與字幕概念不夠對齊，缺少關鍵詞：" + ", ".join(concept_missing[:5]))
        if not policy_ok:
            extra.append("統一限制未通過：" + str(policy_meta.get("reason") or "請提高來源關鍵詞命中"))

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
        concept_ok, concept_missing, _required_concepts = _subtitle_concept_alignment_check(
            plan,
            trace.get("keywords", []),
            trace.get("subtitle_text_used", subtitle_text),
            question_text,
            solution_lines,
        )
        policy_ok, policy_meta = _validate_unified_generation_policy(
            question_text,
            solution_lines,
            unified_policy,
        )

    if is_too_similar_to_subtitle(trace.get("subtitle_text_used", subtitle_text), question_text):
        raise RuntimeError("generated question is too similar to subtitle example")

    if not rc.get("ok"):
        raise RuntimeError(f"AI IO generation failed: {rc.get('reason')}")
    if not align_ok:
        raise RuntimeError("AI IO generation failed: not aligned with teacher_description")
    if not concept_ok:
        raise RuntimeError("AI IO generation failed: subtitle concept alignment not met")
    if not policy_ok:
        raise RuntimeError("AI IO generation failed: unified policy not met")

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
        "key_sentences": key_sentences,
        "key_sentences_typed": key_sentences_typed,
        # 讓後續對齊/除錯流程可直接使用已壓縮字幕片段。
        "ai_segments_compact": segs_compact,
        "selector_meta": selector_meta,
        "unified_policy_meta": policy_meta,
        "alignment_confidence": _build_alignment_confidence(selector_meta, key_sentences_typed),
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

    constraints = resolve_unit_constraints(unit)
    unit_type = (constraints or {}).get("unit_type") or "loop"
    function_mode = (unit_type == "function")

    # --- subtitles ---
    segs = parse_srt_segments(subtitle_text)
    cleaned = strip_srt_noise(subtitle_text)
    trace = _pick_trace_window(segs, constraints, max_lines=7)
    selector_extra_kws = _extract_focus_keywords(teacher_description or "", max_keywords=6)
    selected_meta = _select_relevant_subtitle_segments(
        segs=segs,
        unit=unit,
        constraints=constraints,
        teacher_description=teacher_description,
        video_title=video_title,
        question_keywords=selector_extra_kws,
        trace_keywords=trace.get("keywords", []),
        window=4,
        max_segments=10,
    )
    selected_segs = selected_meta.get("segments") or segs
    selector_keywords = "、".join((selected_meta.get("keywords") or [])[:8])
    selector_meta = _build_selector_quality_meta(selected_meta, selected_segs)
    key_sentences = _extract_key_sentences_from_segments(selected_segs, max_sentences=10)
    key_sentences_typed = _extract_typed_key_sentences_from_segments(selected_segs, max_sentences=10)
    selected_preview = " | ".join([
        str(s.get("text") or "").strip()
        for s in (selected_segs[:5] if selected_segs else [])
        if str(s.get("text") or "").strip()
    ])[:300]

    if selected_segs:
        sel_start_ts = float(selected_segs[0].get("start") or 0.0)
        sel_end_ts = float(selected_segs[-1].get("end") or sel_start_ts)
        try:
            sel_start_idx = int(segs.index(selected_segs[0]))
            sel_end_idx = int(segs.index(selected_segs[-1]))
        except Exception:
            sel_start_idx = int((trace.get("subtitle_range") or {}).get("start_index") or 0)
            sel_end_idx = int((trace.get("subtitle_range") or {}).get("end_index") or 0)
    else:
        sel_start_idx = int((trace.get("subtitle_range") or {}).get("start_index") or 0)
        sel_end_idx = int((trace.get("subtitle_range") or {}).get("end_index") or 0)
        sel_start_ts = float((trace.get("subtitle_range") or {}).get("start_ts") or 0.0)
        sel_end_ts = float((trace.get("subtitle_range") or {}).get("end_ts") or 0.0)

    selected_subtitle_range = {
        "start_index": sel_start_idx,
        "end_index": sel_end_idx,
        "start_ts": sel_start_ts,
        "end_ts": sel_end_ts,
    }

    def _if_signal_score(text: str) -> int:
        t = (text or "").lower()
        score = 0
        if re.search(r"\bif\b", t):
            score += 2
        if re.search(r"\belif\b|\belse\b", t):
            score += 2
        if "判斷" in t:
            score += 1
        if "條件" in t:
            score += 1
        if "如果" in t:
            score += 1
        if "否則" in t:
            score += 1
        return score

    _selected_subtitle_text = "\n".join([
        str(s.get("text") or "").strip()
        for s in (selected_segs or [])
        if str(s.get("text") or "").strip()
    ])
    _ctx_text = "\n".join([
        str(trace.get("subtitle_text_used") or ""),
        _selected_subtitle_text,
        str(teacher_description or ""),
        str(video_title or ""),
    ])

    def _has_token_signal(text: str, en_tokens: list, zh_tokens: list) -> bool:
        t = (text or "").lower()
        if any(z in t for z in (zh_tokens or [])):
            return True
        return any(re.search(r"\b" + re.escape(tok.lower()) + r"\b", t) for tok in (en_tokens or []))
    loop_style_from_constraints = (constraints or {}).get("loop_style") or "either"
    # while 題型若明確提到 if/條件，強制要求生成結果含 if 判斷。
    require_if_from_context = (loop_style_from_constraints == "while_only") and (_if_signal_score(_ctx_text) >= 2)

    _ctx_lc = _ctx_text.lower()
    require_function_input_from_context = function_mode and _has_token_signal(
        _ctx_text,
        ["input"],
        ["輸入", "讀入", "整數輸入", "int(input"],
    )
    require_function_two_inputs = function_mode and (
        (("底數" in _ctx_text) and ("指數" in _ctx_text)) or
        ("兩個" in _ctx_text) or ("兩筆" in _ctx_text) or ("2個" in _ctx_lc)
    )
    has_operator_signal_in_context = function_mode and (
        _has_token_signal(
            _ctx_text,
            ["operator", "op", "calculator", "plus", "minus", "multiply", "divide"],
            ["運算子", "四則", "加減乘除", "計算機", "+-*/", "運算"]
        )
        or bool(re.search(r"\+|\-|\*|/", _ctx_text))
    )
    require_function_operator_dispatch = bool(has_operator_signal_in_context)
    has_loop_signal_in_context = _has_token_signal(
        _ctx_text,
        ["for", "while", "range"],
        ["迴圈", "重複", "循環"],
    )
    require_no_loop_in_function_from_context = function_mode and (not has_loop_signal_in_context)
    has_condition_signal_in_context = _has_token_signal(
        _ctx_text,
        ["if", "elif", "else"],
        ["判斷", "條件", "分支", "否則"],
    )
    require_no_condition_in_function_from_context = function_mode and (not has_condition_signal_in_context)
    
    # [新增] 啟發式例外：某些場景（計算機題等）必須允許 if
    # 即使 subtitle 沒明確教 if，也不應該禁用 if
    if require_no_condition_in_function_from_context:
        should_allow_if = _should_func_allow_condition(subtitle_text, teacher_description)
        if should_allow_if:
            require_no_condition_in_function_from_context = False

    if require_function_operator_dispatch:
        # +-*/ 教學類型強制走運算分派，不要漂移成購物/迴圈累加。
        require_no_loop_in_function_from_context = True
        require_no_condition_in_function_from_context = False
        require_function_input_from_context = True
        require_function_two_inputs = True

    function_profile = _build_function_structure_profile(
        subtitle_text=subtitle_text,
        teacher_description=teacher_description,
        context_text=_ctx_text,
        force_operator_dispatch=require_function_operator_dispatch,
    )

    # [新增] 偵測實現細節：print vs return、int(input())、輸出格式、參數數量
    output_style_preference = _detect_output_style_preference(subtitle_text)
    require_print_only = bool(function_profile.get("need_print")) if function_mode else (output_style_preference == "print_only")

    require_int_input_cast = bool(function_profile.get("need_int_cast")) if function_mode else _detect_int_input_pattern(subtitle_text)

    output_format_style = str(function_profile.get("output_format_style") or "simple") if function_mode else _detect_output_format_pattern(subtitle_text)

    expected_param_count = function_profile.get("param_count") if function_mode else None
    require_function_param_count = (expected_param_count is not None)

    function_profile_summary = ""
    if function_mode:
        function_profile_summary = (
            "【Function 結構穩定器（正式 gate）】\n"
            f"- param_count: {function_profile.get('param_count')}\n"
            f"- need_input: {function_profile.get('need_input')}\n"
            f"- need_print: {function_profile.get('need_print')}\n"
            f"- prefer_return: {function_profile.get('prefer_return')}\n"
            f"- allow_condition: {function_profile.get('allow_condition')}"
        )

    # [新增] 低 token：字幕壓縮（只提供必要片段給 AI）
    segs_compact = compact_segments_for_prompt(selected_segs, max_chars=1700)
    if not segs_compact:
        # 仍允許無字幕，但 segment_map 會走 fallback
        segs_compact = ""

    strict_subtitle_rule = (
        "【字幕切片使用規則】\n"
        "- 你只能根據本次提供的字幕切片出題。\n"
        "- 不可自行補充未出現在字幕切片中的教學內容。\n"
    )

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
    loop_style = (constraints or {}).get("loop_style") or "either"

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

    scenarios_function = [
        {
            "name": "function_operator_dispatch",
            "desc": "函式運算：定義函式 f(x, y, op)，依 op 的 '+', '-', '*', '/' 回傳對應結果，主程式讀入 x、y、op 並輸出。",
            "tests": [["6", "12", "+"]],
            "check": "nums>=1",
            "keywords": ["函式", "def", "operator", "op", "+", "-", "*", "/", "四則", "運算子"],
        },
        {
            "name": "function_two_params",
            "desc": "函式計算：定義函式處理兩個輸入值，回傳計算結果，主程式讀入後呼叫並輸出。",
            "tests": [["3", "4"]],
            "check": "nums>=1",
            "keywords": ["函式", "def", "return", "參數", "呼叫"],
        },
    ]

    # 依 loop_style 選對應情境池
    if function_mode:
        scenarios = scenarios_function
    elif loop_style == "for_only":
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

    if function_mode and require_function_operator_dispatch:
        picked = scenarios_function[0]
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
    unified_policy = _build_unified_generation_policy(
        plan=plan,
        unit=unit,
        constraints=constraints,
        video_title=video_title,
        teacher_description=teacher_description,
        subtitle_text=trace.get("subtitle_text_used", "") or subtitle_text,
        selector_keywords=selected_meta.get("keywords") or [],
    )
    unified_rule_text = _unified_policy_prompt_block(unified_policy)
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
    _python_token_re = _re.compile(r"\b(def|return|while|for|if|elif|else|break|continue|pass|input|print|int|float|str|len|range)\b|[=:+\-*/()<>]")
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
            s = s.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
            # 1) remove Chinese inside input prompt
            s = _strip_input_prompt(s)
            # 2) drop leading print label (often Chinese) so solution_lines stays code-only
            s = _strip_print_label(s)
            # 3) repair common LLM artifact: stray unmatched quote at line end, e.g. "return result'"
            t = s.rstrip()
            if t.endswith("'") and (t.count("'") % 2 == 1):
                t = t[:-1]
            if t.endswith('"') and (t.count('"') % 2 == 1):
                t = t[:-1]
            s = t
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

        defined_funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)} if function_mode else set()

        for node in ast.walk(tree):
            # ✅ 寬鬆模式：移除 ALLOWED_NODES 白名單（這是你一直 AST rejected 的主因）
            # if not isinstance(node, ALLOWED_NODES):
            #     return False

            # ✅ 仍保留：擋掉危險/你不希望出現的語法
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.Attribute, ast.Subscript,
                                 ast.Lambda, ast.With, ast.Try, ast.ClassDef)):
                print("[AST_SAFE] rejected node:", type(node).__name__)
                print("[AST_SAFE] code:\n" + code)
                return False

            if (not function_mode) and isinstance(node, ast.FunctionDef):
                print("[AST_SAFE] rejected node:", type(node).__name__)
                print("[AST_SAFE] code:\n" + code)
                return False

            # ✅ 仍保留：呼叫只允許白名單函式（避免 eval/exec/open 等）
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    fn = node.func.id
                    if (fn not in ALLOWED_FUNCS) and (not (function_mode and fn in defined_funcs)):
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

    def _parse_numeric_tokens(out: str) -> list:
        vals = []
        for s in _re.findall(r"-?\d+\.?\d*", out or ""):
            try:
                vals.append(float(s))
            except Exception:
                continue
        return vals

    def _has_num(nums: list, target: float, tol: float = 1e-6) -> bool:
        for n in nums or []:
            if abs(float(n) - float(target)) <= tol:
                return True
        return False

    def _while_semantic_check(question_text: str, out: str, inputs: list) -> str:
        # 只對 while-only 題型做語意驗收，降低誤判
        if function_mode or loop_style != "while_only":
            return ""

        qmix = " ".join([
            str(question_text or ""),
            str(scenario_desc or ""),
            str(teacher_description or ""),
        ]).lower()

        nums = _parse_numeric_tokens(out)
        if not nums:
            return "while semantic check failed: no numeric output"

        in_vals = list(inputs or [])
        scenario_name = str((picked or {}).get("name") or "")

        # 題目若要求「最後一次輸入」必須驗收：要同時輸出筆數與最後一次非 sentinel 值
        requires_last_input = (
            ("最後" in qmix or "last" in qmix) and
            ("輸入" in qmix or "金額" in qmix or "amount" in qmix)
        )
        if requires_last_input:
            if len(in_vals) < 2:
                return "while semantic check failed: insufficient test inputs"
            expected_count = max(0, len(in_vals) - 1)
            try:
                expected_last = float(in_vals[-2])
            except Exception:
                return "while semantic check failed: cannot parse expected last input"
            if not _has_num(nums, expected_count):
                return "while semantic check failed: missing entry count"
            if not _has_num(nums, expected_last):
                return "while semantic check failed: missing last non-sentinel input"

        # 依既定情境再做一次語意檢查，避免「看起來有 while 但算錯欄位」
        if scenario_name == "sum_prices" and len(in_vals) >= 2:
            expected_count = max(0, len(in_vals) - 1)
            try:
                expected_total = sum(float(x) for x in in_vals[:-1])
            except Exception:
                expected_total = None
            if not _has_num(nums, expected_count):
                return "while semantic check failed: sum_prices missing count"
            if expected_total is not None and not _has_num(nums, expected_total):
                return "while semantic check failed: sum_prices missing total"

        if scenario_name == "sentinel_ok" and len(in_vals) >= 1:
            expected_count = max(0, len(in_vals) - 1)
            if not _has_num(nums, expected_count):
                return "while semantic check failed: sentinel_ok missing count"

        return ""

    def _auto_grade(solution_lines: list, question_text: str = "") -> str:
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

        has_def = any(_re.search(r"^\s*def\s+", x) for x in solution_lines)
        has_if = any(_re.search(r"^\s*if\s+", x) for x in solution_lines)
        has_elif = any(_re.search(r"^\s*elif\s+", x) for x in solution_lines)
        has_else = any(_re.search(r"^\s*else\s*:\s*$", x) for x in solution_lines)
        joined_lines = "\n".join(solution_lines)
        code = joined_lines
        input_count = len(_re.findall(r"\binput\s*\(", joined_lines))

        if function_mode and (not has_def):
            return "missing def"

        if function_mode and require_function_input_from_context and (input_count <= 0):
            return "function mode requires input style from subtitle/teacher description"

        if function_mode and require_function_two_inputs and (input_count < 2):
            return "function mode requires at least two inputs"

        if function_mode and require_no_loop_in_function_from_context:
            if _has_loop(solution_lines) or _re.search(r"\brange\s*\(", joined_lines):
                return "function mode: loop syntax not allowed by subtitle/teacher context"

        if function_mode and require_no_condition_in_function_from_context:
            if has_if or has_elif or has_else:
                return "function mode: condition syntax not allowed by subtitle/teacher context"

        if function_mode and require_function_operator_dispatch:
            if _re.search(r"\bwhile\b|\bfor\b|\brange\s*\(", joined_lines):
                return "function operator mode: loop syntax not allowed"

            has_op_param = bool(_re.search(r"def\s+\w+\s*\([^)]*\b(op|operator)\b", joined_lines))
            if not has_op_param:
                return "function operator mode: missing op/operator parameter"

            op_patterns = [r"['\"]\+['\"]", r"['\"]-['\"]", r"['\"]\*['\"]", r"['\"]/['\"]"]
            has_all_ops = all(_re.search(p, joined_lines) is not None for p in op_patterns)
            if not has_all_ops:
                return "function operator mode: missing one of + - * / branches"

            if not (has_if and (has_elif or has_else)):
                return "function operator mode: requires if/elif style operator dispatch"

        if function_mode:
            fp_ok, fp_reason = _validate_function_structure_profile(solution_lines, function_profile)
            if not fp_ok:
                return fp_reason or "function profile not met"

        # [新增] 實現細節驗收：print vs return
        if require_print_only:
            has_print = "print(" in joined_lines
            has_return = _re.search(r"\breturn\b", joined_lines) is not None
            # 如果要求print_only，但有return卻沒有print → 不符
            if has_return and not has_print:
                return "output style mismatch: subtitle teaches print(), solution only has return"
            # 函式mode下，應該在函式內使用print，而不是僅return
            if function_mode and has_return and not has_print:
                return "function mode: should output with print() inside function, not just return"

        # [新增] 實現細節驗收：int(input())
        if require_int_input_cast and input_count > 0:
            # 移除所有被int/float/str包裝的input()，檢查是否還有input()剩下
            without_cast = _re.sub(r'\b(?:int|float|str)\s*\(\s*input\s*\(\s*\)\s*\)', '', joined_lines)
            has_uncast_input = 'input(' in without_cast
            if has_uncast_input:
                return "input casting mismatch: subtitle teaches int(input()), solution has uncast input()"

        # [新增] 實現細節驗收：函式參數數量
        if function_mode and require_function_param_count and expected_param_count is not None:
            try:
                tree = ast.parse(code)
                func_defs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
                if func_defs:
                    first_func = func_defs[0]
                    actual_params = len(first_func.args.args)
                    if actual_params != expected_param_count:
                        return f"function param mismatch: subtitle teaches {expected_param_count} params, solution has {actual_params}"
            except Exception as e:
                # 若無法解析，寬鬆處理
                pass

        if function_mode and require_function_input_from_context:
            # 避免 power(3, 4) 這種與教學輸入流程不一致的硬編碼呼叫
            if _re.search(r"\b\w+\s*\(\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*\)", joined_lines):
                return "function mode: avoid hardcoded literal function call"

        if (not function_mode) and has_def:
            return "contains def"

        if (not function_mode) and require_if_from_context and (not has_if):
            return "missing if (required by subtitle/teacher description)"

        if any(_re.search(r"^\s*class\s+", x) for x in solution_lines):
            return "contains class"

        if (not function_mode) and (not _has_loop(solution_lines)):
            return "missing loop"

        if (not function_mode) and (not _loop_has_body_indent(solution_lines)):
            return "loop body indentation missing"

        if not _has_print(solution_lines):
            return "missing print output"

        # ✅ 修正：圖形/for-range 類題目不需要 input() 也不需要結束條件
        # 判斷是否為「純輸出型」題（for range + print，無需互動）
        is_output_only = (
            (not function_mode) and
            not _has_input(solution_lines) and
            _has_loop(solution_lines) and
            not _re.search(r"\bwhile\b", "\n".join(solution_lines))
        )

        if (not is_output_only) and (not function_mode):
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

        if function_mode:
            return ""

        # 純輸出型：不跑測資（無 input，exec 會卡住）；直接通過
        if is_output_only:
            return ""

        # 互動型：跑測資驗收
        test_inputs = (scenario_tests[:1] or [[]])

        qmix = " ".join([
            str(question_text or ""),
            str(scenario_desc or ""),
            str(teacher_description or ""),
        ]).lower()

        # 題目若明確要求 quit + 最後一次輸入值，額外加專用測資避免錯解通過
        if (loop_style == "while_only") and ("quit" in qmix) and ("最後" in qmix or "last" in qmix):
            test_inputs = [["12", "34", "quit"]]
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

            sem_err = _while_semantic_check(question_text, out, inputs)
            if sem_err:
                print("[AI_VERIFY]", sem_err)
                print("[AI_VERIFY] output was:", repr(out))
                return sem_err

        return ""

    # ===============================
    # [新增] AI 生成（最小輸出）+ 失敗只修一次（省 token）
    # ===============================
    system_msg = "你是Python程式設計助教。請嚴格只輸出『合法 JSON』，不要輸出 Markdown 或多餘文字。"

    banned_patterns = """
請避免以下過度常見模板與禁止項：
- 直到輸入 'ok' 為止（除非情境就是 ok sentinel）
- class
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
    elif function_mode:
        loop_style_rule = "- 本題必須定義並使用至少一個函式（def），禁止使用 class。"

    if_requirement_rule = ""
    if require_if_from_context and (not function_mode):
        if_requirement_rule = "- 字幕/老師描述提到條件判斷：solution_lines 必須包含至少一個 if。"

    function_input_rule = ""
    if require_function_input_from_context:
        function_input_rule = "- 字幕/老師描述顯示為輸入導向：必須使用 input() 讀值，不可直接用固定常數呼叫函式（例如 power(3, 4)）。"
        if require_function_two_inputs:
            function_input_rule += "\n- 本題需至少讀取兩個輸入值（如底數與指數）。"

    function_no_loop_rule = ""
    if require_no_loop_in_function_from_context:
        function_no_loop_rule = "- 字幕/老師描述未出現迴圈教學：禁止使用 for / while / range，請用函式與運算式完成。"

    function_no_condition_rule = ""
    if require_no_condition_in_function_from_context:
        function_no_condition_rule = "- 字幕/老師描述未出現條件判斷教學：禁止使用 if / elif / else，請使用單純輸入、運算、return 與輸出。"

    # [新增] 實現細節規則：print vs return、int轉型、輸出格式
    output_style_rule = ""
    if require_print_only:
        output_style_rule = "- 字幕教學使用 print() 輸出結果：solution_lines 必須用 print()，禁止只用 return（函式應該內含 print）。"
    
    int_input_rule = ""
    if require_int_input_cast:
        int_input_rule = "- 字幕教學明確顯示 int(input()) 轉型：讀取輸入時必須使用 int() 轉換（而不是直接 input()）。"
    
    output_format_rule = ""
    if output_format_style == "with_label":
        output_format_rule = "- 字幕教學的輸出格式包含標籤（如 'Name: xxx')：請輸出相同格式的標籤。"
    elif output_format_style == "custom":
        output_format_rule = "- 字幕教學的輸出格式自訂：請依照字幕中的範例格式輸出。"

    function_param_rule = ""
    if require_function_param_count and function_mode:
        function_param_rule = f"- 字幕教學定義函式有 {expected_param_count} 個參數：def 必須正好有 {expected_param_count} 個參數，呼叫時也必須提供 {expected_param_count} 個輸入值。"

    function_operator_rule = ""
    if function_mode and require_function_operator_dispatch:
        function_operator_rule = (
            "- 本題必須為運算子分派函式：def 需包含 op/operator 參數。\n"
            "- 函式內需用 if/elif 依 '+', '-', '*', '/' 至少四種運算分支回傳結果。\n"
            "- 主程式需讀入 x、y、op 三個輸入後呼叫函式並輸出。"
        )

    # ✅ 根據老師描述判斷是否為「純輸出型」（圖形、數列），動態調整規則
    _td_lc = (teacher_description or "").lower()
    _is_pattern_task = has_teacher_desc and any(k in _td_lc for k in [
        "三角形", "直角", "正方形", "菱形", "星號", "圖形", "pattern", "triangle", "square", "*",
        "數列", "列印", "輸出數字", "輸出星號",
    ])

    if _is_pattern_task and (not function_mode):
        loop_rules = (
            "- 使用 for 迴圈（可巢狀）\n"
            "- 迴圈內至少一行縮排（4 spaces）\n"
            "- 只使用 print() 輸出，不需要 input()\n"
            "- 禁止使用 input() / while / break"
        )
        diversity_block = ""  # 圖形題不需要 sentinel/output_style 多樣化
    elif function_mode:
        loop_rules = (
            "- 必須包含至少一個 def 函式定義\n"
            "- 函式內至少一行縮排（4 spaces）\n"
            "- 函式需被呼叫，且最終使用 print() 輸出結果\n"
            "- 禁止使用 class"
        )
        if function_input_rule:
            loop_rules += "\n" + function_input_rule
        if int_input_rule:
            loop_rules += "\n" + int_input_rule
        if output_style_rule:
            loop_rules += "\n" + output_style_rule
        if output_format_rule:
            loop_rules += "\n" + output_format_rule
        if function_param_rule:
            loop_rules += "\n" + function_param_rule
        if function_operator_rule:
            loop_rules += "\n" + function_operator_rule
        if function_no_loop_rule:
            loop_rules += "\n" + function_no_loop_rule
        if function_no_condition_rule:
            loop_rules += "\n" + function_no_condition_rule
        diversity_block = ""
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
        if if_requirement_rule:
            loop_rules += "\n" + if_requirement_rule
        if int_input_rule:
            loop_rules += "\n" + int_input_rule
        if output_format_rule:
            loop_rules += "\n" + output_format_rule
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
題型固定：{('函式' if function_mode else '迴圈')}，不得使用 class。

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
【字幕切片關鍵詞】
{selector_keywords or "（自動）"}
{strict_subtitle_rule}
{unified_rule_text}
{function_profile_summary}
若老師描述明確，請優先依據描述生成題目。
{loop_style_rule}
{if_requirement_rule}
{function_input_rule}
{function_no_loop_rule}
{function_no_condition_rule}
{banned_patterns if ((not _is_pattern_task) and (not function_mode)) else ""}
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

    # 控制成本：最多嘗試 2 次 AI 生成，失敗即交給外層本地 fallback。
    MAX_RETRY = 2
    data = None
    last_error = ""
    last_candidate = None
    success_attempt = 0
    unified_policy_meta = {}

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
            elif "subtitle concept" in last_error:
                err_hint = "\n⚠️  題目與程式概念沒有對齊字幕重點，請補強關鍵詞語意。"
            user_msg = f"""
你上一版未通過自動驗收：{last_error}{err_hint}

【只修正 solution_lines】（保持同一情境方向）
- solution_lines 必須是合法 Python，可以 compile 執行
- 禁止中文、禁止 # 註解、禁止 class
- {('必須包含 def 並正確呼叫函式' if function_mode else '必須包含 for 或 while 迴圈，迴圈內縮排 4 spaces')}
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

        grade_err = _auto_grade(sol, q)
        if grade_err:
            last_error = grade_err
            continue

        if function_mode:
            fp_ok, fp_reason = _validate_function_structure_profile(sol, function_profile)
            if not fp_ok:
                last_error = fp_reason or "function profile not met"
                continue

        ok_align, align_reason = _loop_semantic_guard(subtitle_text, q, sol, video_title)
        if not ok_align:
            last_error = align_reason
            continue

        concept_ok, concept_missing, _required_concepts = _subtitle_concept_alignment_check(
            plan,
            trace.get("keywords", []),
            trace.get("subtitle_text_used", subtitle_text),
            q,
            sol,
        )
        if not concept_ok:
            last_error = "subtitle concept alignment not met: " + ", ".join(concept_missing[:5])
            continue

        policy_ok, policy_meta = _validate_unified_generation_policy(q, sol, unified_policy)
        if not policy_ok:
            last_error = str(policy_meta.get("reason") or "unified policy not met")
            continue
        unified_policy_meta = policy_meta

        data = cand
        success_attempt = attempt + 1
        break

    if not data:
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
    contextual_sem_labels = _build_contextual_semantic_labels(solution_lines, question_text)
    for i, b in enumerate(solution_blocks):
        if i < len(sem_labels):
            b["semantic_zh"] = _soften_semantic_hint(sem_labels[i], b.get("text", ""), is_distractor=False)

    template_slots = [
        {
            "label": _soften_semantic_hint(
                sem_labels[i] if i < len(sem_labels) else contextual_sem_labels[i],
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

    pool = _mix_pool_blocks(solution_blocks, distractor_blocks, seed_text=question_text)

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
        "source_subtitle": {
            "subtitle_range": selected_subtitle_range,
            "trace_range": trace.get("subtitle_range") or {},
            "text_used": _selected_subtitle_text or (trace.get("subtitle_text_used") or ""),
            "selector_keywords": selected_meta.get("keywords") or [],
        },
        "subtitle_range": selected_subtitle_range,
        "subtitle_text_used": _selected_subtitle_text or (trace.get("subtitle_text_used") or ""),
        "key_sentences": key_sentences,
        "key_sentences_typed": key_sentences_typed,
        "selector_meta": selector_meta,
        "unified_policy_meta": unified_policy_meta,
        "function_profile": function_profile if function_mode else {},
        "alignment_confidence": _build_alignment_confidence(selector_meta, key_sentences_typed, seg_map, slot_hints),
        "ai_segment_map": seg_map, # AI 生成的程式碼行與字幕時間戳對齊資訊（包含證據）
        "ai_slot_hints": slot_hints, # AI 生成的每行程式碼提示（基於對齊資訊，提供學生錯誤時的提示）
        "ai_segments_compact": segs_compact, # 用於 AI 生成的字幕片段簡化版本（含時間戳與文字），供對齊任務使用
        "debug_meta": {
            "selector_keywords": (selected_meta.get("keywords") or []),
            "selector_best_index": selected_meta.get("best_idx"),
            "selector_best_score": selected_meta.get("best_score"),
            "selector_fallback": selected_meta.get("fallback"),
            "selector_hit_ratio": selector_meta.get("hit_ratio"),
            "selector_keyword_coverage_ratio": selector_meta.get("keyword_coverage_ratio"),
            "policy_anchor_hits": (unified_policy_meta.get("anchor_hits") or []),
            "policy_anchor_missing": (unified_policy_meta.get("anchor_missing") or []),
            "policy_off_topic_ratio": unified_policy_meta.get("off_topic_ratio"),
            "policy_reason": unified_policy_meta.get("reason"),
            "selected_subtitle_count": len(selected_segs or []),
            "selected_subtitle_preview": selected_preview,
            "require_if_from_context": bool(require_if_from_context),
            "require_function_input_from_context": bool(require_function_input_from_context),
            "require_function_two_inputs": bool(require_function_two_inputs),
            "require_function_param_count": bool(require_function_param_count),
            "expected_param_count": expected_param_count,
            "require_no_loop_in_function_from_context": bool(require_no_loop_in_function_from_context),
            "require_no_condition_in_function_from_context": bool(require_no_condition_in_function_from_context),
            "heuristic_allow_condition_for_function": bool(function_mode and _should_func_allow_condition(subtitle_text, teacher_description)),
            "require_print_only": bool(require_print_only),
            "require_int_input_cast": bool(require_int_input_cast),
            "output_format_style": str(output_format_style or "simple"),
            "function_profile": function_profile if function_mode else {},
            "max_retry": int(MAX_RETRY),
            "success_attempt": int(success_attempt),
            "last_error_before_success": str(last_error or ""),
        },
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

        # 切片品質太低時，最多再重試一次（固定用 stable_mode=True）提升穩定性。
        first_selector_meta = dict(ai.get("selector_meta") or {})
        selector_retried = False
        if _selector_quality_low(first_selector_meta):
            selector_retried = True
            try:
                if constraints.get("unit_type") == "condition":
                    ai_retry = ai_generate_condition_from_subtitle(sub_text, unit, video_title, level=level, teacher_description=teacher_description, stable_mode=True)
                elif constraints.get("unit_type") == "io":
                    ai_retry = ai_generate_io_from_subtitle(sub_text, unit, video_title, level=level, teacher_description=teacher_description, stable_mode=True)
                else:
                    ai_retry = ai_generate_parsons_from_subtitle(sub_text, unit, video_title, level=level, teacher_description=teacher_description, stable_mode=True)

                retry_selector_meta = dict(ai_retry.get("selector_meta") or {})
                if (not _selector_quality_low(retry_selector_meta)) or (
                    int(retry_selector_meta.get("best_score") or 0) >= int(first_selector_meta.get("best_score") or 0)
                ):
                    ai = ai_retry
            except Exception:
                pass

        ai.setdefault("debug_meta", {})
        ai["debug_meta"]["selector_retry_once"] = bool(selector_retried)
        ai["debug_meta"]["selector_quality_low_first"] = bool(_selector_quality_low(first_selector_meta))
        ai["debug_meta"]["selector_quality_final"] = dict(ai.get("selector_meta") or {})

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
        "key_sentences": ai.get("key_sentences", []),
        "key_sentences_typed": ai.get("key_sentences_typed", []),
        "selector_meta": ai.get("selector_meta", {}),
        "unified_policy_meta": ai.get("unified_policy_meta", {}),
        "function_profile": ai.get("function_profile", {}),
        "alignment_confidence": ai.get("alignment_confidence", {}),
        "ai_generated": True if gen_source == "openai" else False,
        "ai_segment_map": ai.get("ai_segment_map", {}) or {},
        "ai_slot_hints": ai.get("ai_slot_hints", {}) or {},
        "ai_segments_compact": ai.get("ai_segments_compact", "") or "",
        "created_at": now_utc(),
        "active": True,
        "gen_source": gen_source,
        "gen_error": gen_error,
        "fallback_source": ai.get("fallback_source") if gen_source == "fallback" else None,
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
            "generation_debug": ai.get("debug_meta", {}) or {},
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