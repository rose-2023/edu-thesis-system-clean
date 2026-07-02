"""
parsons_concept_align.py 老師定義章節 + AI 協助對齊模組
========================
概念章節對齊模組（YouTube 章節風格）

用途：
    取代原本精準逐行對齊（align_fixed_task_subtitle），
    改為將影片字幕自動切分成「概念章節」，
    再把每個 Parsons block 對應到最相關的章節。

使用方式（在 parsons.py 或新 Blueprint 中）：
    from .parsons_concept_align import (
        extract_concept_chapters,
        map_blocks_to_chapters,
        build_concept_segment_map,
        align_task_by_concept,
    )

Flask route 範例：
    @parsons_bp.post("/fixed_task/align_concept")
    def align_fixed_task_concept():
        from .parsons_concept_align import align_task_by_concept
        data = request.get_json(silent=True) or {}
        return align_task_by_concept(data)

系統流程（需求保留）：
    老師：
        定義章節（教材結構）+ 最後判斷 AI 推薦是否有對齊影片章節
    AI：
        判斷錯誤 + 給提示 + 推薦方向
    系統：
        對應章節
    學生：
        選擇是否觀看

LLM 不穩定點（需求保留）：
    - 每次切法不同
    - concept 名稱不一致（函式 / 函數 / 定義）
    - start/end 會飄
    - 會切錯教學邏輯
"""

import os
import re
_re = re
import json
from typing import Optional, List

from flask import jsonify
from bson import ObjectId

from ..db import db
from . import parsons_ai
from .parsons_service import (
    now_utc,
    maybe_oid,
    ai_enabled,
    parse_srt_segments,
    compact_segments_for_prompt,
    read_subtitle_text,
    pick_latest_subtitle_path,
)
from .parsons_retrieval import build_subtitle_index


# ─────────────────────────────────────────────
# 模型設定

def _model_for_concept_align() -> str:
    """概念對齊使用的 LLM 模型。"""
    return (
        os.getenv("OPENAI_MODEL_ALIGN")
        or os.getenv("OPENAI_MODEL")
        or "gpt-4.1-mini"
    ).strip()


# ─────────────────────────────────────────────
# Concept 正規化 / 驗證
# ─────────────────────────────────────────────
_CONCEPT_TAG_LABELS = {
    "loop_count_control": "迴圈次數控制",
    "loop_reverse_range": "反向 range 迴圈",
    "nested_loop_structure": "巢狀迴圈結構",
    "if_condition_logic": "條件判斷邏輯",
    "if_branch_order": "分支順序",
    "edge_case_condition": "邊界條件",
    "star_formula_2i_minus_1": "星號公式 2i-1",
    "space_formula_n_minus_i": "空白公式 n-i",
    "input_int_cast": "輸入轉整數",
    "print_separator": "輸出分隔格式",
    "python_syntax": "Python 語法",
}

_LEGACY_CONCEPT_TAG_ALIASES = {
    "loop_count_control": ["迴圈", "for", "while", "loop", "迴圈次數控制"],
    "loop_reverse_range": ["反向迴圈", "reverse range", "倒著", "遞減 range"],
    "nested_loop_structure": ["巢狀迴圈", "nested loop", "雙層迴圈", "多層迴圈"],
    "if_condition_logic": ["條件判斷", "if", "condition", "判斷邏輯"],
    "if_branch_order": ["分支順序", "elif", "else if", "branch order"],
    "edge_case_condition": ["邊界條件", "edge case", "特殊情況", "例外條件"],
    "star_formula_2i_minus_1": ["2i-1", "2*i-1", "星號公式", "奇數列", "star formula"],
    "space_formula_n_minus_i": ["n-i", "n - i", "空白公式", "space formula"],
    "input_int_cast": ["input", "int(input", "轉整數", "輸入轉整數", "讀取輸入"],
    "print_separator": ["print", "分隔", "separator", "sep=", "輸出分隔格式"],
    "python_syntax": ["def", "return", "函式", "語法", "syntax", "python"],
}

_LEGACY_SURFACE_TAG_ALIASES = {
    "def": ["def", "function", "函式", "函數", "定義"],
    "input": ["input", "read", "讀取", "輸入"],
    "print": ["print", "output", "輸出", "印出", "顯示"],
    "if": ["if", "elif", "else", "條件", "判斷"],
    "for": ["for", "while", "迴圈", "loop"],
    "return": ["return", "回傳", "傳回"],
    "operator": ["operator", "運算", "運算子", "算式"],
    "call": ["call", "呼叫", "函式呼叫"],
}


def concept_tag_to_label(tag: str) -> str:
    tag_key = str(tag or "").strip().lower()
    if not tag_key:
        return ""
    return _CONCEPT_TAG_LABELS.get(tag_key, tag_key)


def normalize_surface_tag(surface: str) -> str:
    raw = str(surface or "").strip().lower()
    if not raw:
        return ""

    for canonical, aliases in _LEGACY_SURFACE_TAG_ALIASES.items():
        if raw == canonical:
            return canonical
        for alias in aliases:
            alias_low = str(alias or "").strip().lower()
            if alias_low and (alias_low == raw or alias_low in raw):
                return canonical

    return raw


def _legacy_surface_from_concept_tag(tag: str) -> str:
    tag_key = normalize_concept_name(tag)
    if tag_key in {"loop_count_control", "loop_reverse_range", "nested_loop_structure", "star_formula_2i_minus_1", "space_formula_n_minus_i"}:
        return "for"
    if tag_key in {"if_condition_logic", "if_branch_order", "edge_case_condition"}:
        return "if"
    if tag_key == "input_int_cast":
        return "input"
    if tag_key == "print_separator":
        return "print"
    if tag_key == "python_syntax":
        return "def"
    return ""


def infer_surface_tag_from_text(text: str) -> str:
    q = str(text or "").strip().lower()
    if not q:
        return ""

    if "int(input" in q or "input(" in q or "輸入" in q or "讀取" in q:
        return "input"
    if re.match(r"^\s*elif\b", q) or re.match(r"^\s*if\b", q) or "條件" in q or "判斷" in q:
        return "if"
    if "print(" in q or "輸出" in q or "印出" in q or "顯示" in q:
        return "print"
    if re.match(r"^\s*for\b", q) or re.search(r"\bfor\b", q) or "迴圈" in q or "range(" in q:
        return "for"
    if re.match(r"^\s*while\b", q) or "while" in q:
        return "for"
    if re.match(r"^\s*return\b", q) or "回傳" in q or "傳回" in q:
        return "return"
    if re.match(r"^\s*def\b", q) or "函式" in q or "函數" in q or "function" in q:
        return "def"
    if any(op in q for op in ["==", "!=", "<=", ">=", "+", "-", "*", "/", "%"]):
        return "operator"
    if re.search(r"\b[a-z_][a-z0-9_]*\s*\(", q):
        return "call"
    return ""


def infer_concept_tag_from_text(text: str) -> str:
    q = str(text or "").strip().lower()
    if not q:
        return "python_syntax"

    surface = infer_surface_tag_from_text(q)

    if any(term in q for term in ["2i-1", "2*i-1", "星號", "star", "奇數列"]):
        return "star_formula_2i_minus_1"
    if any(term in q for term in ["n-i", "n - i", "空白", "space", "spacing"]):
        return "space_formula_n_minus_i"
    if any(term in q for term in ["nested", "巢狀", "雙層", "多層", "兩層迴圈"]):
        return "nested_loop_structure"
    if any(term in q for term in ["reverse", "倒著", "反向", "遞減", "由大到小"]):
        return "loop_reverse_range"
    if surface == "for":
        return "loop_count_control"
    if surface == "if":
        if any(term in q for term in ["elif", "else if", "分支順序", "分支排列", "排序"]):
            return "if_branch_order"
        if any(term in q for term in ["edge", "邊界", "特殊", "例外", "空值", "沒有", "最後一筆", "0", "1"]):
            return "edge_case_condition"
        return "if_condition_logic"
    if surface == "input":
        return "input_int_cast"
    if surface == "print":
        return "print_separator"
    if surface in {"def", "return", "call", "operator"}:
        return "python_syntax"

    if any(term in q for term in ["input", "print", "if", "for", "while", "def", "return"]):
        return "python_syntax"
    return "python_syntax"


def _concept_family(tag: str) -> str:
    tag_key = normalize_concept_name(tag)
    if tag_key in {"loop_count_control", "loop_reverse_range", "nested_loop_structure", "star_formula_2i_minus_1", "space_formula_n_minus_i"}:
        return "loop"
    if tag_key in {"if_condition_logic", "if_branch_order", "edge_case_condition"}:
        return "branch"
    if tag_key in {"input_int_cast", "print_separator"}:
        return "io"
    if tag_key == "python_syntax":
        return "syntax"
    return tag_key or ""


def normalize_concept_name(concept: str) -> str:
    """將概念名稱正規化成穩定的 concept_tag。"""
    raw = str(concept or "").strip()
    if not raw:
        return ""

    low = raw.lower()
    for canonical, aliases in _LEGACY_CONCEPT_TAG_ALIASES.items():
        if low == canonical:
            return canonical
        for alias in aliases:
            alias_low = str(alias or "").strip().lower()
            if alias_low and (alias_low == low or alias_low in low):
                return canonical

    if low in _CONCEPT_TAG_LABELS:
        return low

    return low


def validate_chapters(raw_chapters) -> list[dict]:
    """驗證並正規化章節資料。"""
    out = []
    for ch in raw_chapters or []:
        if not isinstance(ch, dict):
            continue
        concept_tag = normalize_concept_name(
            ch.get("concept_tag")
            or ch.get("wrong_type")
            or ch.get("concept")
            or infer_concept_tag_from_text(ch.get("concept_label") or "")
        )
        if not concept_tag:
            continue
        surface_tag = normalize_surface_tag(ch.get("surface_tag") or ch.get("wrong_type") or ch.get("surface") or _legacy_surface_from_concept_tag(concept_tag))
        try:
            start = float(ch.get("start"))
            end = float(ch.get("end"))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        out.append({
            "cell_id": ch.get("cell_id") or ch.get("cell_index") or ch.get("index"),
            "concept": concept_tag,
            "concept_tag": concept_tag,
            "concept_label": str(ch.get("concept_label") or ch.get("label") or concept_tag_to_label(concept_tag) or concept_tag).strip(),
            "surface_tag": surface_tag,
            "wrong_type": surface_tag,
            "start": round(start, 2),
            "end": round(end, 2),
        })
    for idx, ch in enumerate(out, start=1):
        try:
            cell_id = int(ch.get("cell_id") or 0)
        except Exception:
            cell_id = 0
        ch["cell_id"] = cell_id if cell_id > 0 else idx
    return out


def _chapter_conflicts_with_existing(start: float, end: float, chapters: list[dict], threshold: float = 0.98) -> bool:
    """檢查新章節區間是否與既有章節高度重疊。"""
    try:
        s = float(start)
        e = float(end)
    except Exception:
        return False
    if e <= s:
        return False

    span = e - s
    if span <= 0:
        return False

    for chapter in chapters or []:
        if not isinstance(chapter, dict):
            continue
        try:
            existing_start = float(chapter.get("start", 0.0))
            existing_end = float(chapter.get("end", 0.0))
        except Exception:
            continue
        if existing_end <= existing_start:
            continue

        overlap = min(e, existing_end) - max(s, existing_start)
        if overlap <= 0:
            continue
        if (overlap / span) >= float(threshold):
            return True

    return False


def _collect_chapter_warning_codes(chapters: list[dict]) -> list[str]:
    """從章節清單收集簡單警告碼。"""
    warnings = []
    valid = validate_chapters(chapters)
    if not valid:
        return warnings

    seen_labels = set()
    seen_ranges = []
    for ch in valid:
        label = str(ch.get("concept_label") or ch.get("concept_tag") or "").strip().lower()
        if label:
            if label in seen_labels:
                warnings.append("duplicate_concept_label")
            seen_labels.add(label)

        try:
            start = float(ch.get("start", 0.0))
            end = float(ch.get("end", 0.0))
        except Exception:
            continue
        if end <= start:
            warnings.append("invalid_time_range")
            continue
        if _chapter_conflicts_with_existing(start, end, seen_ranges, threshold=0.98):
            warnings.append("time_overlap")
        seen_ranges.append({"start": start, "end": end})

    return list(dict.fromkeys(warnings))


def _apply_semantic_constraint_to_chapters(chapters: list[dict], task: dict, subtitle_segments=None, code_start_ts=None) -> dict:
    """保守版語意約束：只做基本驗證與時間正規化，不改動章節結構。"""
    valid = validate_chapters(chapters)
    warnings = _collect_chapter_warning_codes(valid)
    if not valid:
        return {"chapters": [], "warnings": warnings}

    subtitle_segments = subtitle_segments or []
    subtitle_count = len([seg for seg in subtitle_segments if isinstance(seg, dict)])
    if subtitle_count == 0:
        warnings.append("no_subtitle_segments")

    if code_start_ts is not None:
        try:
            start_ts = float(code_start_ts)
        except Exception:
            start_ts = None
        if start_ts is not None:
            for ch in valid:
                try:
                    if float(ch.get("end", 0.0)) <= start_ts:
                        warnings.append("chapter_before_code_start")
                        break
                except Exception:
                    continue

    return {"chapters": valid, "warnings": list(dict.fromkeys(warnings))}


def derive_concept_version_key(task_doc: dict, subtitle_version: str = "") -> str:
    """Derive a stable key for the current subtitle version / source."""
    candidates = [
        subtitle_version,
        (task_doc.get("teacher_concept_version_key") or ""),
        (task_doc.get("subtitle_version") or ""),
        ((task_doc.get("prompt_source") or {}).get("subtitle_version") or ""),
        ((task_doc.get("prompt_source") or {}).get("subtitle_path") or ""),
        (task_doc.get("subtitle_path") or ""),
        ((task_doc.get("source_subtitle") or {}).get("subtitle_version") or ""),
        ((task_doc.get("source_subtitle") or {}).get("subtitle_path") or ""),
    ]
    for value in candidates:
        key = str(value or "").strip().lower()
        if key:
            return key
    return ""


def _normalize_subtitle_segments_for_suggestions(subtitle_segments) -> list[dict]:
    normalized = []
    for idx, seg in enumerate(subtitle_segments or []):
        if not isinstance(seg, dict):
            continue
        try:
            start = float(seg.get("start", seg.get("start_ts", seg.get("start_time", 0.0))))
            end = float(seg.get("end", seg.get("end_ts", seg.get("end_time", 0.0))))
        except Exception:
            continue
        if end <= start:
            continue
        text = str(seg.get("text") or seg.get("evidence") or "").strip()
        if not text:
            continue
        normalized.append({
            "id": seg.get("id") or seg.get("anchor_id") or idx + 1,
            "start": round(start, 2),
            "end": round(end, 2),
            "text": text,
        })
    return normalized


def _chapter_label_needs_rename(concept_tag: str, concept_label: str) -> bool:
    tag = normalize_concept_name(concept_tag)
    label = str(concept_label or "").strip()
    if not tag or not label:
        return False

    canonical = concept_tag_to_label(tag)
    if not canonical:
        return False
    if label == canonical:
        return False

    low = label.lower()
    technical_signals = ["_", "(", ")", "input", "print", "return", "def", "if", "for", "while", "sep", "int("]
    if any(token in low for token in technical_signals):
        return True

    if label == tag or label == canonical:
        return True

    if len(label) <= 3 and len(canonical) >= 4:
        return True

    generic_terms = ["概念", "章節", "程式", "程式碼", "操作", "語法"]
    if any(term in label for term in generic_terms) and canonical not in label:
        return True

    if canonical not in label and any(term in label for term in ["輸入", "輸出", "條件", "迴圈", "函式", "邊界"]):
        return True

    return False


def _build_ai_chapter_suggestion_candidates(draft_chapters: list[dict], subtitle_segments: list[dict]) -> tuple[list[dict], list[dict]]:
    normalized_drafts = validate_chapters(draft_chapters)
    normalized_subtitles = _normalize_subtitle_segments_for_suggestions(subtitle_segments)

    rename_suggestions = []
    existing_tags = set()
    for ch in normalized_drafts:
        tag = normalize_concept_name(ch.get("concept_tag") or ch.get("concept") or ch.get("wrong_type"))
        label = str(ch.get("concept_label") or concept_tag_to_label(tag) or tag).strip()
        if tag:
            existing_tags.add(tag)
        if _chapter_label_needs_rename(tag, label):
            canonical = concept_tag_to_label(tag) or label
            if canonical and canonical != label:
                rename_suggestions.append({
                    "original": label,
                    "suggested": canonical,
                    "_concept_tag": tag,
                })

    tag_scores = {}
    for seg in normalized_subtitles:
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        low = text.lower()
        for concept_tag in sorted(_CONCEPT_TAG_LABELS.keys()):
            if concept_tag in existing_tags:
                continue
            terms = list(get_query_terms_for_concept_tag(concept_tag) or [])
            context = get_context_rules_for_concept_tag(concept_tag) or {}
            positive_terms = list(context.get("positive") or [])

            hits = []
            score = 0.0
            for term in terms:
                term_low = str(term or "").strip().lower()
                if term_low and term_low in low:
                    hits.append(str(term).strip())
                    score += 1.0

            for term in positive_terms:
                term_low = str(term or "").strip().lower()
                if term_low and term_low in low:
                    hits.append(str(term).strip())
                    score += 1.2

            if concept_tag == "input_int_cast":
                if "input(" in low or "int(input" in low:
                    score += 2.8
                    hits.append("input(")
                if "轉整數" in text or "型別轉換" in text:
                    score += 1.5
            elif concept_tag == "print_separator":
                if "sep=" in low or "print(" in low:
                    score += 2.4
                    hits.append("print/separator")
                if "空格" in text or "分隔" in text:
                    score += 1.4
            elif concept_tag == "loop_count_control":
                if "for" in low or "range(" in low:
                    score += 2.0
                    hits.append("for/range")
            elif concept_tag == "if_condition_logic":
                if re.search(r"\bif\b", low) or "判斷" in text or "條件" in text:
                    score += 2.0
                    hits.append("if/條件")
            elif concept_tag == "if_branch_order":
                if "elif" in low or "else" in low:
                    score += 2.0
                    hits.append("elif/else")
            elif concept_tag == "edge_case_condition":
                if any(term in text for term in ["最後", "特殊", "邊界", "沒有", "例外"]):
                    score += 1.8
            elif concept_tag == "python_syntax":
                if "def" in low or "return" in low:
                    score += 1.6

            if score <= 0:
                continue

            item = tag_scores.get(concept_tag)
            if not item:
                tag_scores[concept_tag] = {
                    "concept_tag": concept_tag,
                    "label": concept_tag_to_label(concept_tag),
                    "score": float(score),
                    "hits": hits[:4],
                    "evidence": [text],
                    "count": 1,
                }
            else:
                item["score"] = float(item.get("score", 0.0)) + float(score)
                item["count"] = int(item.get("count", 0)) + 1
                if len(item.get("evidence") or []) < 4:
                    item.setdefault("evidence", []).append(text)
                if hits:
                    current_hits = list(item.get("hits") or [])
                    for hit in hits:
                        if hit not in current_hits:
                            current_hits.append(hit)
                    item["hits"] = current_hits[:4]

    missing_candidates = []
    for concept_tag, payload in tag_scores.items():
        score = float(payload.get("score") or 0.0)
        count = int(payload.get("count") or 0)
        if score < 2.0 or count <= 0:
            continue
        evidence = [str(x).strip() for x in (payload.get("evidence") or []) if str(x or "").strip()]
        hits = [str(x).strip() for x in (payload.get("hits") or []) if str(x or "").strip()]
        reason_terms = "/".join(hits[:3]) if hits else "字幕中有對應概念線索"
        missing_candidates.append({
            "label": str(payload.get("label") or concept_tag_to_label(concept_tag) or concept_tag).strip(),
            "reason": f"字幕中出現 {reason_terms}，但草稿尚未涵蓋這個概念。",
            "_concept_tag": concept_tag,
            "_score": score,
            "_evidence": evidence[:3],
        })

    missing_candidates.sort(key=lambda item: (-float(item.get("_score") or 0.0), str(item.get("label") or "")))
    return rename_suggestions, missing_candidates[:3]


def _sanitize_ai_chapter_suggestions(raw_data, rename_pool: list[dict], missing_pool: list[dict]) -> dict:
    allowed_originals = {str(item.get("original") or "").strip(): item for item in (rename_pool or []) if str(item.get("original") or "").strip()}
    allowed_missing = {str(item.get("label") or "").strip(): item for item in (missing_pool or []) if str(item.get("label") or "").strip()}

    if not isinstance(raw_data, dict):
        return {"rename_suggestions": [], "missing_concepts": []}

    rename_suggestions = []
    for item in raw_data.get("rename_suggestions") or []:
        if not isinstance(item, dict):
            continue
        original = str(item.get("original") or "").strip()
        suggested = str(item.get("suggested") or "").strip()
        if not original or not suggested:
            continue
        if original not in allowed_originals:
            continue
        rename_suggestions.append({"original": original, "suggested": suggested})

    missing_concepts = []
    for item in raw_data.get("missing_concepts") or []:
        if len(missing_concepts) >= 3:
            break
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not label or not reason:
            continue
        if label not in allowed_missing:
            continue
        missing_concepts.append({"label": label, "reason": reason})

    return {
        "rename_suggestions": rename_suggestions[:8],
        "missing_concepts": missing_concepts[:3],
    }


def _build_chapter_candidate_pool(chapter: dict, subtitle_segments: list[dict], window_size: int = 4) -> tuple[list[dict], str]:
    chapter = chapter or {}
    tag = normalize_concept_name(chapter.get("concept_tag") or chapter.get("concept") or chapter.get("wrong_type"))
    label = str(chapter.get("concept_label") or concept_tag_to_label(tag) or tag).strip()
    try:
        start = float(chapter.get("start", 0.0))
        end = float(chapter.get("end", 0.0))
    except Exception:
        start = 0.0
        end = 0.0

    valid_segments = []
    for idx, seg in enumerate(subtitle_segments or []):
        if not isinstance(seg, dict):
            continue
        try:
            seg_start = float(seg.get("start", 0.0))
            seg_end = float(seg.get("end", 0.0))
        except Exception:
            continue
        if seg_end <= seg_start:
            continue

        overlap = min(end, seg_end) - max(start, seg_start)
        distance = min(abs(seg_start - end), abs(seg_end - start))
        score = 0.0
        if overlap > 0:
            score += overlap * 10.0
        score += max(0.0, 5.0 - distance)
        text = str(seg.get("text") or seg.get("content") or seg.get("subtitle") or "").strip()
        if text:
            score += 0.5
        valid_segments.append({
            "candidate_id": str(seg.get("id") or idx + 1),
            "id": str(seg.get("id") or idx + 1),
            "start": round(seg_start, 2),
            "end": round(seg_end, 2),
            "text": text,
            "score": round(score, 3),
            "anchor_id": seg.get("id") or idx + 1,
            "reason": "字幕片段與目前章節範圍接近",
        })

    valid_segments.sort(key=lambda item: (-float(item.get("score") or 0.0), float(item.get("start") or 0.0), float(item.get("end") or 0.0)))
    if not valid_segments:
        return [], label

    pool = valid_segments[: max(3, window_size)]
    if len(pool) < window_size:
        pool = valid_segments[:window_size]
    return pool, label


def _build_chapter_ai_recommendations(draft_chapters: list[dict], subtitle_segments: list[dict]) -> list[dict]:
    recommendations = []
    normalized_drafts = validate_chapters(draft_chapters or [])
    segs = [seg for seg in (subtitle_segments or []) if isinstance(seg, dict)]

    for idx, chapter in enumerate(normalized_drafts, start=1):
        candidate_pool, concept_label = _build_chapter_candidate_pool(chapter, segs)
        if not candidate_pool:
            recommendations.append({
                "cell_id": chapter.get("cell_id") or idx,
                "concept_tag": chapter.get("concept_tag") or chapter.get("concept") or "",
                "concept_label": concept_label,
                "candidate_id": None,
                "best_candidate_id": None,
                "alternative_candidate_ids": [],
                "chapter_title": concept_label,
                "chapter_title_candidates": [concept_label] if concept_label else [],
                "chapter_note": "目前候選片段不足，請調整教學區間後再判斷。",
                "confidence": None,
                "candidates": [],
            })
            continue

        subtitle_window = "\n".join(
            f"[{item['start']:.2f}-{item['end']:.2f}] {item['text']}" for item in candidate_pool[:8]
        )
        reranked = rerank_chapter_candidates_with_ai(
            concept_tag=chapter.get("concept_tag") or chapter.get("concept") or "",
            wrong_type=chapter.get("wrong_type") or chapter.get("concept_tag") or "",
            query_text=str(chapter.get("concept_label") or concept_label or "").strip(),
            candidates=candidate_pool,
            subtitle_window=subtitle_window,
            chapter_name_candidates=[str(chapter.get("concept_label") or concept_label or "").strip(), concept_label],
        )

        if not reranked:
            # [修正] AI 停用或回傳空時，用 rule-based fallback，並標示來源讓前端顯示
            best_candidate = candidate_pool[0]
            _ai_was_disabled = not ai_enabled()
            reranked = {
                "best_candidate_id": str(best_candidate.get("candidate_id") or best_candidate.get("id") or idx),
                "alternative_candidate_ids": [str(item.get("candidate_id") or item.get("id") or i + 1) for i, item in enumerate(candidate_pool[1:3])],
                "chapter_title": concept_label,
                "chapter_title_candidates": [concept_label] if concept_label else [],
                "chapter_note": (
                    f"（AI 未啟動）依時間最近原則，自動選取候選 #{best_candidate.get('candidate_id') or idx}。建議老師確認內容是否符合「{concept_label or chapter.get('concept_tag') or '目前概念'}」教學重點。"
                    if _ai_was_disabled else
                    f"（AI 判斷失敗）依時間最近原則，選取候選 #{best_candidate.get('candidate_id') or idx}。建議老師手動確認。"
                ),
                "confidence": None,
                "rerank_source": "rule_fallback",
            }
        else:
            reranked.setdefault("rerank_source", "ai")

        recommendations.append({
            "cell_id": chapter.get("cell_id") or idx,
            "concept_tag": chapter.get("concept_tag") or chapter.get("concept") or "",
            "concept_label": concept_label,
            **reranked,
            "candidate_id": reranked.get("best_candidate_id"),
            "candidates": candidate_pool,
        })

    return recommendations


def generate_ai_chapter_suggestions(draft_chapters, subtitle_segments) -> dict:
    """Generate conservative AI suggestions for chapter naming / missing concepts."""
    rename_pool, missing_pool = _build_ai_chapter_suggestion_candidates(draft_chapters or [], subtitle_segments or [])

    deterministic = {
        "rename_suggestions": [
            {"original": item["original"], "suggested": item["suggested"]}
            for item in rename_pool
        ],
        "missing_concepts": [
            {"label": item["label"], "reason": item["reason"]}
            for item in missing_pool[:3]
        ],
    }

    if not ai_enabled():
        return deterministic

    draft_summary = []
    for idx, ch in enumerate(validate_chapters(draft_chapters or []), start=1):
        draft_summary.append({
            "idx": idx,
            "concept_tag": ch.get("concept_tag") or "",
            "concept_label": ch.get("concept_label") or "",
            "start": ch.get("start"),
            "end": ch.get("end"),
        })

    subtitle_preview = []
    for seg in _normalize_subtitle_segments_for_suggestions(subtitle_segments or [])[:40]:
        subtitle_preview.append(f"[{seg['start']:.2f}-{seg['end']:.2f}] {seg['text']}")

    rename_candidate_text = json.dumps(
        [{"original": item["original"], "suggested": item["suggested"]} for item in rename_pool],
        ensure_ascii=False,
        indent=2,
    )
    missing_candidate_text = json.dumps(
        [{"label": item["label"], "reason": item["reason"], "score": round(float(item.get("_score") or 0.0), 2), "evidence": item.get("_evidence") or []} for item in missing_pool],
        ensure_ascii=False,
        indent=2,
    )

    system = (
        "你是 Python 教學影片的概念章節協助者。\n"
        "你只能做兩件事：\n"
        "1. 針對既有草稿章節，提出更自然的中文名稱，但不可改變概念。\n"
        "2. 依字幕內容補充最多 3 個缺少的概念。\n"
        "嚴格規則：\n"
        "- 不可修改任何 start/end。\n"
        "- 不可刪除章節。\n"
        "- 不可重新切章節。\n"
        "- 不可超過 3 個 missing_concepts。\n"
        "- missing_concepts 必須從候選清單中挑選，不可發明新概念。\n"
        "- 輸出必須是合法 JSON，且只包含 rename_suggestions 與 missing_concepts。\n"
        "Quality constraints:\n"
        "* 不得建議超過3個 missing concepts\n"
        "* 不得重複現有 concept\n"
        "* 不得產生過於泛化名稱（例如：程式、邏輯）\n"
        "* 必須根據 subtitle_segments 判斷，不可憑空生成"
    )

    user = f"""已存在的草稿章節：
{json.dumps(draft_summary, ensure_ascii=False, indent=2)}

字幕片段（優先依這些片段判斷是否還缺概念）：
{chr(10).join(subtitle_preview) if subtitle_preview else '（無）'}

可用的 rename 候選：
{rename_candidate_text}

可用的 missing_concepts 候選：
{missing_candidate_text}

輸出格式：
{{
  "rename_suggestions": [
    {{"original": "輸出結果", "suggested": "print輸出"}}
  ],
  "missing_concepts": [
    {{"label": "輸入處理", "reason": "影片中有 input 語句但未形成章節"}}
  ]
}}

要求：
1. rename_suggestions 只保留真正更自然的命名。
2. missing_concepts 最多 3 筆，且必須從候選清單挑選。
3. 不要輸出時間、不要輸出分析過程、不要輸出多餘欄位。"""

    try:
        raw_text = parsons_ai.call_openai_output_text(
            model=_model_for_concept_align(),
            system=system,
            user=user,
            temperature=0.0,
            max_output_tokens=900,
        )
    except Exception:
        return deterministic

    clean = re.sub(r"```(?:json)?\s*(.*?)\s*```", r"\1", raw_text or "", flags=re.DOTALL).strip()
    parsed = {}
    if clean:
        try:
            parsed = json.loads(clean)
        except Exception:
            lb = clean.find("{")
            rb = clean.rfind("}")
            if lb != -1 and rb != -1 and rb > lb:
                try:
                    parsed = json.loads(clean[lb:rb + 1])
                except Exception:
                    parsed = {}

    sanitized = _sanitize_ai_chapter_suggestions(parsed, rename_pool, missing_pool)
    if sanitized.get("rename_suggestions") or sanitized.get("missing_concepts"):
        return sanitized

    return deterministic


# ─────────────────────────────────────────────
# Step 1：字幕 → 概念章節清單
# ─────────────────────────────────────────────

def extract_concept_chapters(subtitle_compact: str, strict_ai_only: bool = False, code_start_ts=None, teaching_range_end=None) -> list[dict]:
    """
    輸入精簡字幕文字（compact 格式或純文字），
    先切出與程式相關的字幕區段，再將區段分類成固定 6 大概念。

    回傳格式：
        [
            {"concept": "函式定義", "start": 12.0, "end": 45.0},
            {"concept": "讀取輸入", "start": 46.0, "end": 78.0},
            ...
        ]

    teaching_range_end：老師設定的教學結束時間（秒），章節不可超過此值。
    若 AI 失敗，回傳空 list。
    """
    if not subtitle_compact or not subtitle_compact.strip():
        return []

    def _parse_compact_segments(text: str) -> list[dict]:
        segs = []
        for ln in str(text or "").splitlines():
            m = re.match(r"\s*\[(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\]\s*(.*)$", str(ln or "").strip())
            if not m:
                continue
            try:
                start = float(m.group(1))
                end = float(m.group(2))
            except Exception:
                continue
            if end <= start:
                continue
            segs.append({"start": start, "end": end, "text": str(m.group(3) or "").strip()})
        return segs

    def _infer_concept_from_text(text: str) -> str:
        return infer_concept_tag_from_text(text)

    def _first_real_code_start_time(text: str):
        segs = _parse_compact_segments(text)
        for seg in segs:
            if _looks_like_real_code_start(seg.get("text") or ""):
                return float(seg.get("start") or 0.0)
        return None

    def _hard_cut_compact_text(text: str, start_ts: float, end_ts: float = None) -> str:
        """
        裁切字幕文字，只保留 [start_ts, end_ts] 區間內的句子。
        - start_ts：教學區間開始（老師設定）
        - end_ts：教學區間結束（老師設定）；若為 None，不限制結束點
        不可超出 start_ts 以前，也不可超出 end_ts 以後。
        """
        segs = _parse_compact_segments(text)
        if not segs:
            return ""

        try:
            cue_time = float(start_ts)
        except Exception:
            cue_time = None

        try:
            end_limit = float(end_ts) if end_ts is not None else None
        except Exception:
            end_limit = None

        kept_lines = []
        for seg in segs:
            try:
                seg_start = float(seg.get("start") or 0.0)
                seg_end = float(seg.get("end") or 0.0)
            except Exception:
                continue
            # 在開始時間以前的句子，整句跳過
            if cue_time is not None and seg_end <= cue_time:
                continue
            # 跨越開始時間的句子，截斷起點
            if cue_time is not None and seg_start < cue_time < seg_end:
                seg_start = cue_time
            # 在結束時間以後的句子，整句跳過
            if end_limit is not None and seg_start >= end_limit:
                continue
            # 跨越結束時間的句子，截斷終點
            if end_limit is not None and seg_end > end_limit:
                seg_end = end_limit
            if seg_end <= seg_start:
                continue
            kept_lines.append(f"[{round(seg_start, 2)}-{round(seg_end, 2)}] {str(seg.get('text') or '').strip()}")

        return "\n".join(kept_lines)

    def _to_draft_output(chapters: list[dict], draft_source: str) -> list[dict]:
        source = "ai" if str(draft_source or "").strip().lower() == "ai" else "rule"
        out = []
        for ch in validate_chapters(chapters):
            out.append({
                "concept_tag": ch.get("concept_tag") or ch.get("concept") or "",
                "concept_label": ch.get("concept_label") or concept_tag_to_label(ch.get("concept_tag") or ch.get("concept") or ""),
                "start": ch.get("start"),
                "end": ch.get("end"),
                "draft_source": source,
            })
        return out

    def _build_rule_based_chapters(text: str) -> list[dict]:
        segs = _parse_compact_segments(text)
        if not segs:
            return []

        chapters = []
        current = None
        for seg in segs:
            concept = _infer_concept_from_text(seg.get("text") or "")
            if not concept:
                if current:
                    current["end"] = seg["end"]
                continue

            if current and current["concept"] == concept:
                current["end"] = seg["end"]
                continue

            if current:
                chapters.append(current)
            current = {
                "concept": concept,
                "concept_tag": concept,
                "concept_label": concept_tag_to_label(concept),
                "surface_tag": normalize_surface_tag(infer_surface_tag_from_text(seg.get("text") or "")),
                "wrong_type": normalize_surface_tag(infer_surface_tag_from_text(seg.get("text") or "")),
                "start": seg["start"],
                "end": seg["end"],
            }

        if current:
            chapters.append(current)

        return validate_chapters(chapters)

    def _segments_to_chapters(segments: list[dict]) -> list[dict]:
        chapters = []
        current = None
        for seg in segments or []:
            concept = infer_concept_tag_from_text(seg.get("evidence") or seg.get("text") or "")
            if not concept:
                continue
            start = float(seg.get("start") or 0.0)
            end = float(seg.get("end") or 0.0)
            if end <= start:
                continue
            if current and current["concept"] == concept:
                current["end"] = end
                continue
            if current:
                chapters.append(current)
            current = {
                "concept": concept,
                "concept_tag": concept,
                "concept_label": concept_tag_to_label(concept),
                "surface_tag": normalize_surface_tag(infer_surface_tag_from_text(seg.get("evidence") or seg.get("text") or "")),
                "wrong_type": normalize_surface_tag(infer_surface_tag_from_text(seg.get("evidence") or seg.get("text") or "")),
                "start": start,
                "end": end,
            }
        if current:
            chapters.append(current)
        return validate_chapters(chapters)

    def _trim_intro_chapters(chapters: list[dict], text: str) -> list[dict]:
        """砍掉開始時間以前的章節；end 方向由 _clamp_chapters_to_range 負責。"""
        cue_time = _first_real_code_start_time(text)
        if cue_time is None:
            return validate_chapters(chapters)

        cleaned = []
        for ch in validate_chapters(chapters):
            start = float(ch.get("start") or 0.0)
            end = float(ch.get("end") or 0.0)
            if end <= cue_time and start < cue_time:
                continue
            if start < cue_time < end:
                ch = dict(ch)
                ch["start"] = round(cue_time, 2)
            cleaned.append(ch)
        return validate_chapters(cleaned)

    def _clamp_chapters_to_range(chapters: list[dict], range_start: float = None, range_end: float = None) -> list[dict]:
        """
        強制將章節時間 clamp 在 [range_start, range_end] 以內。
        - 整個章節在邊界以外的 → 直接刪除
        - 跨越邊界的章節 → 截斷
        不修改 concept_tag / concept_label 等語意欄位。
        """
        if range_start is None and range_end is None:
            return list(chapters or [])

        clamped = []
        for ch in validate_chapters(chapters or []):
            s = float(ch.get("start") or 0.0)
            e = float(ch.get("end") or 0.0)

            # 範圍外整句丟棄
            if range_start is not None and e <= range_start:
                continue
            if range_end is not None and s >= range_end:
                continue

            item = dict(ch)
            # 截斷起點
            if range_start is not None and s < range_start:
                item["start"] = round(range_start, 2)
            # 截斷終點
            if range_end is not None and e > range_end:
                item["end"] = round(range_end, 2)

            # 確保截斷後還是有效區間
            if float(item.get("end", 0.0)) <= float(item.get("start", 0.0)):
                continue

            clamped.append(item)
        return validate_chapters(clamped)

    # [修正1] 先解析教學區間邊界，再對字幕做 hard cut
    manual_start_ts = None
    if code_start_ts is not None and str(code_start_ts).strip() != "":
        try:
            manual_start_ts = float(code_start_ts)
        except Exception:
            manual_start_ts = None

    manual_end_ts = None
    if teaching_range_end is not None and str(teaching_range_end).strip() != "":
        try:
            manual_end_ts = float(teaching_range_end)
        except Exception:
            manual_end_ts = None

    cut_start_ts = manual_start_ts if manual_start_ts is not None else _first_real_code_start_time(subtitle_compact)
    analysis_compact = _hard_cut_compact_text(subtitle_compact, cut_start_ts, manual_end_ts) if (cut_start_ts is not None or manual_end_ts is not None) else subtitle_compact
    analysis_compact = str(analysis_compact or "").strip()
    if not analysis_compact:
        return []

    # [修正2] 只在裁切後字幕上做最小可切分檢查；少於 15 秒直接回空
    analysis_segments = _parse_compact_segments(analysis_compact)
    if not analysis_segments:
        return []

    try:
        analysis_start_ts = min(float(seg.get("start") or 0.0) for seg in analysis_segments)
        analysis_end_ts = max(float(seg.get("end") or 0.0) for seg in analysis_segments)
    except Exception:
        return []

    if analysis_end_ts - analysis_start_ts < 15.0:
        return []

    # [修正3] 沒有程式語意就不切：先在裁切後字幕上做 rule-based segmentation
    rule_based_chapters = _build_rule_based_chapters(analysis_compact)
    if not rule_based_chapters:
        return []

    fallback_segments = [] if strict_ai_only else [{"start": ch["start"], "end": ch["end"], "evidence": ch["concept"]} for ch in rule_based_chapters]

    try:
        if not ai_enabled():
            if strict_ai_only:
                return []
            trimmed = _trim_intro_chapters(rule_based_chapters, analysis_compact)
            # [修正4] 最終輸出再 clamp，避免章節超出教學區間
            clamped = _clamp_chapters_to_range(trimmed, manual_start_ts, manual_end_ts)
            return _to_draft_output(clamped, "rule")

        # [修正5] AI 的 segmentation / classification 都只吃裁切後字幕或其切分結果
        range_constraint = ""
        if cut_start_ts is not None or manual_end_ts is not None:
            start_str = f"{cut_start_ts:.2f}" if cut_start_ts is not None else "影片起點"
            end_str = f"{manual_end_ts:.2f}" if manual_end_ts is not None else "影片終點"
            range_constraint = f"\n\n⚠️ 重要限制：所有輸出的 start/end 必須嚴格在 [{start_str}, {end_str}] 秒以內，不可超出此範圍。"

        segmentation_prompt = f"""你是一位 Python 教學助教。
以下字幕已先依教學區間 hard cut，請只在這段字幕內做 segmentation。

任務：只找出「與程式操作直接相關」的區段，請先做 segmentation，不要分類概念。

判斷標準：
- 出現 def / input / if / for / while / print / return
- 或明確在解釋程式邏輯

規則：
1. 只標記重要程式概念段，不需要涵蓋整支影片
2. 題目前言、題目說明、寒暄、操作說明、介面提示（例如「來到同步編輯器」）都不要獨立成段
3. 每段需有明確教學意義，不要過度切分
4. 相鄰且同一概念可合併
5. 輸出只要時間區間與 evidence，不要輸出 concept{range_constraint}

字幕內容：
{analysis_compact[:8000]}

請只輸出 JSON array：
[
  {{"start": 12.3, "end": 25.6, "evidence": "if x > 0..."}},
  ...
]"""

        raw_text = parsons_ai.call_openai_output_text(
            model=_model_for_concept_align(),
            system="你是一位 Python 教學助教，只輸出合法 JSON，不加任何說明文字。",
            user=segmentation_prompt,
            temperature=0.0,
            max_output_tokens=1200,
        )

        clean = re.sub(r"```(?:json)?\s*(.*?)\s*```", r"\1", raw_text, flags=re.DOTALL).strip()
        segments = []
        lb = clean.find("[")
        rb = clean.rfind("]")
        if lb != -1 and rb != -1 and rb > lb:
            try:
                parsed = json.loads(clean[lb:rb + 1])
                if isinstance(parsed, list):
                    segments = parsed
            except Exception:
                pass

        if not segments:
            try:
                obj = json.loads(clean)
                if isinstance(obj, dict):
                    segments = obj.get("segments") or obj.get("data") or obj.get("result") or []
            except Exception:
                pass

        if not segments and not strict_ai_only:
            segments = fallback_segments

        classified_chapters = _segments_to_chapters(segments)
        if len(classified_chapters) >= 1:
            trimmed = _trim_intro_chapters(classified_chapters, analysis_compact)
            # [修正6] clamp 到教學區間邊界，防止 AI 超出範圍
            clamped = _clamp_chapters_to_range(trimmed, manual_start_ts, manual_end_ts)
            return _to_draft_output(clamped, "ai")

        classification_prompt = f"""你是一位 Python 教學助教。
以下是已先 hard cut 並切好的程式相關區段，請將每個區段分類成穩定的 concept_tag，不要再用語法字面當最終概念。

可用的 concept_tag 例子：
- loop_count_control
- loop_reverse_range
- nested_loop_structure
- if_condition_logic
- if_branch_order
- edge_case_condition
- star_formula_2i_minus_1
- space_formula_n_minus_i
- input_int_cast
- print_separator
- python_syntax

規則：
1. 同一區段如果同時符合多個概念，請以主要教學焦點為準
2. 不要改動 start / end
3. concept_tag 欄位只能輸出上述穩定 tag
4. surface_tag 請輸出最表層的語法線索，例如 def / if / for / input / print / return
5. 不要輸出任何多餘文字{range_constraint}

區段資料：
{json.dumps(segments, ensure_ascii=False)[:8000]}

請只輸出 JSON array：
[
    {{"concept_tag": "if_condition_logic", "surface_tag": "if", "start": 12.3, "end": 25.6}},
    ...
]"""

        raw_classification = parsons_ai.call_openai_output_text(
            model=_model_for_concept_align(),
            system="你是一位 Python 教學助教，只輸出合法 JSON，不加任何說明文字。",
            user=classification_prompt,
            temperature=0.0,
            max_output_tokens=1200,
        )

        clean_class = re.sub(r"```(?:json)?\s*(.*?)\s*```", r"\1", raw_classification, flags=re.DOTALL).strip()
        chapters = []
        lb = clean_class.find("[")
        rb = clean_class.rfind("]")
        if lb != -1 and rb != -1 and rb > lb:
            try:
                parsed = json.loads(clean_class[lb:rb + 1])
                if isinstance(parsed, list):
                    chapters = parsed
            except Exception:
                pass

        if not chapters:
            try:
                obj = json.loads(clean_class)
                if isinstance(obj, dict):
                    chapters = obj.get("chapters") or obj.get("data") or obj.get("result") or []
            except Exception:
                pass

        validated_ai = validate_chapters(chapters)
        if validated_ai:
            trimmed = _trim_intro_chapters(validated_ai, analysis_compact)
            # [修正7] clamp 到教學區間邊界，避免任何回傳章節越界
            clamped = _clamp_chapters_to_range(trimmed, manual_start_ts, manual_end_ts)
            return _to_draft_output(clamped, "ai")
        if strict_ai_only:
            return []
        trimmed_rule = _trim_intro_chapters(rule_based_chapters, analysis_compact)
        clamped_rule = _clamp_chapters_to_range(trimmed_rule, manual_start_ts, manual_end_ts)
        return _to_draft_output(clamped_rule, "rule")

    except Exception as e:
        import traceback
        print(f"[parsons_concept_align] extract_concept_chapters error: {e}")
        traceback.print_exc()
        if strict_ai_only:
            return []
        trimmed_rule = _trim_intro_chapters(rule_based_chapters, analysis_compact)
        clamped_rule = _clamp_chapters_to_range(trimmed_rule, manual_start_ts, manual_end_ts)
        return _to_draft_output(clamped_rule, "rule")


# ─────────────────────────────────────────────
# Step 2：block → 概念章節對應
# ─────────────────────────────────────────────

def map_blocks_to_chapters(
    solution_blocks: list[dict],
    chapters: list[dict],
) -> dict:
    """
    將每個 solution block 對應到最相關的概念章節。
    以 concept_tag 為主，surface_tag 與舊語法關鍵字只作輔助。
    """
    if not solution_blocks or not chapters:
        return {}

    result = {}

    for i, block in enumerate(solution_blocks):
        slot_str = str(i)
        code = str((block or {}).get("text") or "").strip().lower()
        sem = str((block or {}).get("semantic_zh") or "").strip().lower()
        combined = code + " " + sem
        block_tag = infer_concept_tag_from_text(combined)
        block_surface = normalize_surface_tag(infer_surface_tag_from_text(combined))

        best_ch = None
        best_idx = -1
        best_score = 0

        for j, ch in enumerate(chapters):
            chapter_tag = normalize_concept_name(ch.get("concept_tag") or ch.get("concept") or ch.get("wrong_type"))
            chapter_surface = normalize_surface_tag(ch.get("surface_tag") or ch.get("wrong_type") or _legacy_surface_from_concept_tag(chapter_tag))
            chapter_family = _concept_family(chapter_tag)
            block_family = _concept_family(block_tag)
            score = 0

            if chapter_tag and block_tag and chapter_tag == block_tag:
                score += 5
            elif chapter_family and block_family and chapter_family == block_family:
                score += 2

            if chapter_surface and block_surface and chapter_surface == block_surface:
                score += 1

            if chapter_tag == "python_syntax" and block_surface in {"def", "return", "input", "print", "if", "for", "operator"}:
                score += 1

            if normalize_concept_name(rule_based_concept(code)) == chapter_tag:
                score += 1

            if score > best_score:
                best_score = score
                best_ch = ch
                best_idx = j

        if best_ch and best_score > 0:
            matched_tag = normalize_concept_name(best_ch.get("concept_tag") or best_ch.get("concept") or best_ch.get("wrong_type"))
            result[slot_str] = {
                "concept": matched_tag,
                "concept_tag": matched_tag,
                "surface_tag": normalize_surface_tag(best_ch.get("surface_tag") or best_ch.get("wrong_type") or _legacy_surface_from_concept_tag(matched_tag)),
                "concept_label": concept_tag_to_label(matched_tag),
                "start": best_ch["start"],
                "end": best_ch["end"],
                "chapter_index": best_idx,
                "method": "rule_first",
            }

    return result


# ─────────────────────────────────────────────
# Step 3：整合成 concept_segment_map
# ─────────────────────────────────────────────

def build_concept_segment_map(
    block_chapter_map: dict,
    chapters: list[dict],
) -> dict:
    """
    從 block → chapter 的對應，整合成 concept → segment 的聚合地圖。
    """
    csm = {}
    for slot_data in block_chapter_map.values():
        if not isinstance(slot_data, dict):
            continue
        concept = normalize_concept_name(slot_data.get("concept_tag") or slot_data.get("concept") or slot_data.get("wrong_type"))
        s = float(slot_data.get("start") or 0.0)
        e = float(slot_data.get("end") or 0.0)
        if not concept or e <= s:
            continue
        if concept not in csm:
            csm[concept] = {
                "start": s,
                "end": e,
                "concept": concept,
                "concept_tag": concept,
                "concept_label": concept_tag_to_label(concept),
            }
        else:
            csm[concept]["start"] = min(csm[concept]["start"], s)
            csm[concept]["end"] = max(csm[concept]["end"], e)
    return csm


# ─────────────────────────────────────────────
# 字幕取得輔助（從 task doc 取字幕）
# ─────────────────────────────────────────────

def _get_subtitle_for_task(task: dict) -> str:
    """
    從 task doc 取得「最完整」字幕文字。
    會在多個候選來源中，挑選時間跨度最大的字幕，避免誤用短版 preview/compact。
    """
    prompt_source = task.get("prompt_source") or {}

    raw_vid = task.get("video_id")
    vid_oid = raw_vid if isinstance(raw_vid, ObjectId) else maybe_oid(str(raw_vid or ""))
    vid_str = task.get("video_id_str") or str(raw_vid or "")

    video_doc = {}
    try:
        if vid_oid:
            video_doc = db.videos.find_one({"_id": vid_oid}) or {}
        elif vid_str:
            maybe_video_oid = maybe_oid(vid_str)
            if maybe_video_oid:
                video_doc = db.videos.find_one({"_id": maybe_video_oid}) or {}
    except Exception:
        video_doc = {}

    def _subtitle_span_seconds(raw_text: str) -> float:
        text = str(raw_text or "").strip()
        if not text:
            return 0.0

        try:
            segs = parse_srt_segments(text) if "-->" in text else _parse_timed_segments_from_subtitle_text(text)
        except Exception:
            return 0.0

        if not segs:
            return 0.0

        starts = []
        ends = []
        for seg in segs:
            if not isinstance(seg, dict):
                continue
            try:
                s = float(seg.get("start", 0.0))
                e = float(seg.get("end", 0.0))
            except Exception:
                continue
            if e <= s:
                continue
            starts.append(s)
            ends.append(e)

        if not starts or not ends:
            return 0.0
        return max(0.0, float(max(ends) - min(starts)))

    candidates = []

    def _add_candidate(raw_text, source_name: str):
        text = str(raw_text or "").strip()
        if not text:
            return
        candidates.append({
            "source": source_name,
            "text": text,
            "span": _subtitle_span_seconds(text),
            "length": len(text),
        })

    # 候選來源（同時納入 full 與 compact），最後以跨度決定。
    _add_candidate(video_doc.get("subtitle_text"), "video.subtitle_text")
    _add_candidate(video_doc.get("subtitle_preview"), "video.subtitle_preview")
    _add_candidate(task.get("subtitle_text_used"), "task.subtitle_text_used")
    _add_candidate(prompt_source.get("subtitle_text"), "prompt_source.subtitle_text")
    _add_candidate(prompt_source.get("subtitle_preview"), "prompt_source.subtitle_preview")
    _add_candidate(task.get("ai_segments_compact"), "task.ai_segments_compact")

    subtitle_path = (
        str(prompt_source.get("subtitle_path") or "").strip()
        or str(task.get("subtitle_path") or "").strip()
        or pick_latest_subtitle_path(video_doc or {}, vid_str)
    )
    if subtitle_path:
        _add_candidate(read_subtitle_text(subtitle_path), "subtitle_path")

    if not candidates:
        return ""

    # 優先時間跨度；同分時再看字數長度。
    candidates.sort(key=lambda item: (float(item.get("span") or 0.0), int(item.get("length") or 0)), reverse=True)
    return str(candidates[0].get("text") or "").strip()


# ─────────────────────────────────────────────
# Rule-based block concept
# ─────────────────────────────────────────────

def rule_based_concept(text: str) -> str:
    """簡易規則：由程式碼文字推估穩定 concept_tag。"""
    if not text:
        return "python_syntax"
    return infer_concept_tag_from_text(text)


def rule_baseed_concept(text: str) -> str:
    """保留舊命名（含拼字）以相容既有呼叫。"""
    return rule_based_concept(text)


# ─────────────────────────────────────────────
# 錯誤子概念 → IR 查詢詞
# 給 parsons.py 的 retrieve_segment_for_wrong_slot 使用
# ─────────────────────────────────────────────

# 每個 concept_tag 對應「字幕裡老師講這個概念時會說的詞」
# 這些詞比語法字（for/if/int）更能命中正確片段
_CONCEPT_TAG_QUERY_TERMS = {
    "loop_count_control":      ["迴圈", "range", "跑幾次", "1到n", "執行次數", "幾次"],
    "loop_reverse_range":      ["遞減", "倒數", "反向", "-1", "由大到小", "倒著跑"],
    "nested_loop_structure":   ["巢狀迴圈", "外層迴圈", "內層迴圈", "雙層", "外迴圈", "內迴圈"],
    "star_formula_2i_minus_1": ["1 3 5", "奇數", "2倍減1", "星號數量", "2*i", "2i-1"],
    "space_formula_n_minus_i": ["空格", "前置空格", "n減i", "遞減空格", "2 1 0", "n-i"],
    "if_condition_logic":      ["判斷", "條件", "if", "成立", "不成立", "True", "False"],
    "if_branch_order":         ["否則", "elif", "else", "多個分支", "第二條件", "分支順序"],
    "edge_case_condition":     ["邊界", "特殊情況", "例外", "最後一筆", "範圍", "區間"],
    "input_int_cast":          ["輸入整數", "讀取", "int", "型別轉換", "input", "eval", "輸入"],
    "print_separator":         ["sep", "空格", "分隔", "print參數", "消除空格", "sep="],
    "python_syntax":           ["函式", "定義", "def", "回傳", "return", "呼叫"],
}

_WRONG_TYPE_TO_CONCEPT_TAG = {
    "loop_count": "loop_count_control",
    "loop_reverse_range": "loop_reverse_range",
    "nested_loop_structure": "nested_loop_structure",
    "star_count": "star_formula_2i_minus_1",
    "space_count": "space_formula_n_minus_i",
    "output_format": "print_separator",
}

_WRONG_TYPE_QUERY_TERMS = {
    "loop_count": ["跑幾次", "1到n", "range", "重複", "次數", "迴圈次數"],
    "loop_reverse_range": ["倒數", "遞減", "反向", "-1", "由大到小", "倒著跑"],
    "nested_loop_structure": ["巢狀迴圈", "外層迴圈", "內層迴圈", "雙層", "多層", "for 中再 for"],
    "star_count": ["1 3 5", "奇數", "2i-1", "2倍減1", "星星", "星號"],
    "space_count": ["2 1 0", "遞減", "n-i", "空格", "空白", "左移"],
    "output_format": ["sep", "separator", "分隔", "逗號", "空格", "print", "輸出格式"],
}

# 每個 concept_tag 的正/負關鍵字，用於 IR top-k 後的輕量 rerank。
_CONCEPT_TAG_CONTEXT_RULES = {
    "input_int_cast": {
        "positive": ["input(", "int(", "int(input", "輸入", "讀取", "轉整數", "型別轉換", "cast"],
        "negative": ["def", "函數", "函式", "參數", "parameter", "print(", "輸出"],
    },
    "print_separator": {
        "positive": ["print", "sep", "separator", "分隔", "空格", "逗號", "輸出格式"],
        "negative": ["def", "函數", "函式", "參數", "parameter", "input(", "讀取"],
    },
    "python_syntax": {
        "positive": ["def", "return", "函式", "語法", "syntax", "python", ":"],
        "negative": ["input(", "print(", "輸入", "輸出", "sep", "separator"],
    },
}


def build_chapter_rerank_prompt(
    *,
    concept_tag: str,
    wrong_type: str,
    query_text: str,
    candidates: list[dict],
    subtitle_window: str = "",
    chapter_name_candidates: Optional[List[str]] = None,
) -> tuple[str, str]:
    """
    Build an AI prompt for chapter reranking.

    The model only chooses among candidate IDs / segments and proposes a chapter name.
    It must not invent timestamps or modify candidate boundaries.
    """
    normalized_candidates = []
    for idx, item in enumerate(candidates or []):
        if not isinstance(item, dict):
            continue
        normalized_candidates.append({
            "candidate_id": str(item.get("candidate_id") or item.get("id") or idx),
            "start": item.get("start"),
            "end": item.get("end"),
            "text": str(item.get("text") or "").strip(),
            "score": item.get("score"),
            "reason": str(item.get("reason") or "").strip(),
            "anchor_id": item.get("anchor_id"),
        })

    name_candidates = [str(x or "").strip() for x in (chapter_name_candidates or []) if str(x or "").strip()]
    name_candidates_text = " / ".join(name_candidates[:5]) if name_candidates else "（無）"

    system = (
        "你是 Python 教學影片的章節判斷助教。\n"
        "你的任務只有兩件，缺一不可：\n"
        "1. 在候選清單（candidates）中選出最符合概念的 candidate_id，作為 best_candidate_id 輸出。\n"
        "   - 不可重複後端已有的對應結果，不可覆蓋後端已設定的 start/end 時間。\n"
        "   - 只能從候選清單中選，不可另外發明新片段。\n"
        "2. 為該章節提出更好的 chapter_title（教學語意標題）與一句 chapter_note（說明選取理由）。\n"
        "   - chapter_title 以教學語意為主，不要照抄 raw concept tag。\n"
        "   - chapter_note 說明這段對應的錯誤焦點或教學重點。\n"
        "嚴格規則：\n"
        "- 只根據候選內容與字幕證據做判斷。\n"
        "- 不要重新估算時間，不要輸出 start/end。\n"
        "- 不要新增候選以外的片段。\n"
        "- 如果候選都不理想，仍然只能從候選中選最接近者，並在 chapter_note 說明理由。\n"
        "- 輸出必須是合法 JSON，不可有多餘文字。"
    )

    user = f"""概念標籤：{concept_tag}
錯誤類型：{wrong_type}
查詢語意：{query_text}

可用章節名稱候選：{name_candidates_text}

字幕證據（可能已經包含時間窗，但你不要改時間）：
{subtitle_window or '（無）'}

候選清單：
{json.dumps(normalized_candidates, ensure_ascii=False, indent=2)}

請只輸出 JSON，格式如下：
{{
  "best_candidate_id": "candidate_id",
  "alternative_candidate_ids": ["candidate_id_2", "candidate_id_3"],
  "chapter_title": "章節名稱",
  "chapter_title_candidates": ["名稱1", "名稱2"],
  "chapter_note": "一句簡短說明，讓老師知道為什麼選它",
  "confidence": 0.0
}}

規則：
1. best_candidate_id 必須來自候選清單。
2. chapter_title 以教學語意為主，不要照抄 raw concept tag。
3. chapter_note 要說明這段對應的錯誤焦點或教學重點。
4. confidence 介於 0 到 1。
5. 不要輸出時間、不要輸出分析過程、不要輸出多餘文字。"""

    return system, user


def rerank_chapter_candidates_with_ai(
    *,
    concept_tag: str,
    wrong_type: str,
    query_text: str,
    candidates: list[dict],
    subtitle_window: str = "",
    chapter_name_candidates: Optional[List[str]] = None,
    model: Optional[str] = None,
) -> dict:
    """
    Use AI to choose the best candidate ID among existing chapter candidates.

    The helper is intentionally defensive: if AI is disabled, the response is invalid,
    or the selected ID is not in the candidate list, it returns an empty dict.
    """
    if not candidates:
        return {}
    if not ai_enabled():
        return {}

    system, user = build_chapter_rerank_prompt(
        concept_tag=concept_tag,
        wrong_type=wrong_type,
        query_text=query_text,
        candidates=candidates,
        subtitle_window=subtitle_window,
        chapter_name_candidates=chapter_name_candidates,
    )

    try:
        raw = parsons_ai.call_openai_output_text(
            model=model or _model_for_concept_align(),
            system=system,
            user=user,
            temperature=0.0,
            max_output_tokens=700,
        )
    except Exception:
        return {}

    clean = re.sub(r"```(?:json)?\s*(.*?)\s*```", r"\1", raw, flags=re.DOTALL).strip()
    parsed = {}
    if clean:
        try:
            parsed = json.loads(clean)
        except Exception:
            lb = clean.find("{")
            rb = clean.rfind("}")
            if lb != -1 and rb != -1 and rb > lb:
                try:
                    parsed = json.loads(clean[lb:rb + 1])
                except Exception:
                    parsed = {}

    if not isinstance(parsed, dict):
        return {}

    valid_ids = {
        str(item.get("candidate_id") or item.get("id") or idx)
        for idx, item in enumerate(candidates)
        if isinstance(item, dict)
    }
    best_candidate_id = str(parsed.get("best_candidate_id") or "").strip()
    if not best_candidate_id or best_candidate_id not in valid_ids:
        return {}

    alt_ids = []
    for item in parsed.get("alternative_candidate_ids") or []:
        sid = str(item or "").strip()
        if sid and sid in valid_ids and sid not in alt_ids and sid != best_candidate_id:
            alt_ids.append(sid)

    chapter_title = str(parsed.get("chapter_title") or "").strip()
    chapter_title_candidates = [
        str(x or "").strip() for x in (parsed.get("chapter_title_candidates") or []) if str(x or "").strip()
    ]
    chapter_note = str(parsed.get("chapter_note") or "").strip()
    confidence = parsed.get("confidence")
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except Exception:
        confidence = None

    return {
        "best_candidate_id": best_candidate_id,
        "alternative_candidate_ids": alt_ids,
        "chapter_title": chapter_title,
        "chapter_title_candidates": chapter_title_candidates,
        "chapter_note": chapter_note,
        "confidence": confidence,
        "raw": parsed,
    }


def get_context_rules_for_concept_tag(concept_tag: str) -> dict:
    """取得 concept_tag 的正負關鍵字規則，用於 IR rerank。"""
    tag = normalize_concept_name(str(concept_tag or ""))
    rules = _CONCEPT_TAG_CONTEXT_RULES.get(tag) or {}
    return {
        "positive": list(rules.get("positive") or []),
        "negative": list(rules.get("negative") or []),
    }


def get_query_terms_for_concept_tag(concept_tag: str) -> list:
    """
    從 concept_tag 取得 IR 查詢詞列表。
    這些詞是字幕裡老師實際講解時會說的詞，
    比直接用語法字（for/if/int）更能命中正確片段。
    """
    tag = normalize_concept_name(str(concept_tag or ""))
    terms = list(_CONCEPT_TAG_QUERY_TERMS.get(tag) or [])

    # 補充 alias 表的詞（排除太短或純英文語法字）
    skip = {"for", "if", "while", "def", "return", "input", "print", "else", "elif"}
    for alias in (_LEGACY_CONCEPT_TAG_ALIASES.get(tag) or []):
        a = str(alias or "").strip()
        if a and a.lower() not in skip and a not in terms:
            terms.append(a)

    return terms


def infer_wrong_type_from_code(code_text: str, semantic_zh: str = "") -> str:
    """先判斷學生可能錯在哪一類，再交給 concept_tag 做檢索。"""
    combined = (str(code_text or "") + " " + str(semantic_zh or "")).strip().lower()
    if not combined:
        return ""

    has_for = bool(_re.search(r"(^|\n)\s*for\s+", combined))
    has_while = bool(_re.search(r"(^|\n)\s*while\s+", combined))
    has_range = "range(" in combined
    has_nested_loop = combined.count("for ") >= 2 or combined.count("while ") >= 2 or (has_for and has_range and combined.count("\n") >= 2 and "內層" in combined)
    has_reverse_loop = has_range and any(term in combined for term in ["-1", "倒", "reverse", "遞減", "反向"])

    if has_nested_loop:
        return "nested_loop_structure"
    if has_reverse_loop:
        return "loop_reverse_range"
    if has_for or has_while or has_range:
        return "loop_count"
    if any(term in combined for term in ["2i-1", "2*i-1", "星號", "星星", "奇數列", "1 3 5", "奇數"]):
        return "star_count"
    if any(term in combined for term in ["n-i", "n - i", "空白", "空格", "spacing"]):
        return "space_count"
    if any(term in combined for term in ["if ", "elif ", "else:", "else ", "條件", "判斷"]):
        if any(term in combined for term in ["print(", "輸出", "印出", "顯示"]):
            return "if_condition_logic"
    if any(term in combined for term in ["sep=", "separator", "分隔", "逗號", "空格", "print(", "end=", "newline", "換行"]):
        return "output_format"

    # 退回到最接近的語意標籤。
    if infer_concept_tag_from_text(combined) in {"loop_count_control", "loop_reverse_range", "nested_loop_structure"}:
        return "loop_count"
    return ""


def resolve_concept_tag_from_wrong_type(wrong_type: str, code_text: str = "", semantic_zh: str = "") -> str:
    wt = str(wrong_type or "").strip().lower()
    if wt in _WRONG_TYPE_TO_CONCEPT_TAG:
        return _WRONG_TYPE_TO_CONCEPT_TAG[wt]

    combined = (str(code_text or "") + " " + str(semantic_zh or "")).strip()
    if not combined:
        return ""

    tag = infer_sub_concept_from_code(code_text, semantic_zh)
    if tag:
        return tag
    return infer_concept_tag_from_text(combined)


def infer_sub_concept_from_code(code_text: str, semantic_zh: str = "") -> str:
    """
    從程式碼文字（block text）推斷 concept_tag。
    給 parsons.py 的 retrieve_segment_for_wrong_slot 呼叫。
    """
    combined = (str(code_text or "") + " " + str(semantic_zh or "")).strip()
    if not combined:
        return ""
    tag = infer_concept_tag_from_text(combined)
    # python_syntax 太粗，不用它查詢（會命中太多無關片段）
    if tag == "python_syntax":
        return ""
    return tag


def build_system_concept_chapters(subtitle_compact: str) -> list[dict]:
    """Pure rule-based chapter draft from subtitle text; no AI involvement."""
    if not subtitle_compact or not subtitle_compact.strip():
        return []

    segments = _parse_timed_segments_from_subtitle_text(subtitle_compact)

    if not segments:
        return []

    chapters = []
    current = None
    for seg in segments:
        concept = infer_concept_tag_from_text(seg.get("text") or "")
        if not concept:
            if current:
                current["end"] = seg["end"]
            continue

        if current and current["concept_tag"] == concept:
            current["end"] = seg["end"]
            continue

        if current:
            chapters.append(current)
        current = {
            "concept": concept,
            "concept_tag": concept,
            "concept_label": concept_tag_to_label(concept),
            "surface_tag": normalize_surface_tag(infer_surface_tag_from_text(seg.get("text") or "")),
            "wrong_type": normalize_surface_tag(infer_surface_tag_from_text(seg.get("text") or "")),
            "start": seg["start"],
            "end": seg["end"],
        }

    if current:
        chapters.append(current)

    return validate_chapters(chapters)


def _looks_like_real_code_start(text: str) -> bool:
    low = str(text or "").strip().lower()
    if not low:
        return False

    code_patterns = [
        r"\bdef\b.*[(:]",
        r"\bif\b.*[:(]",
        r"\belif\b.*[:(]",
        r"\belse\b\s*:",
        r"\bfor\b.*[:(]",
        r"\bwhile\b.*[:(]",
        r"\bprint\s*\(",
        r"\binput\s*\(",
        r"\breturn\b",
    ]
    if any(re.search(pattern, low) for pattern in code_patterns):
        return True

    if any(token in low for token in ["=", "==", "!=", "+", "-", "*", "/", "%"]):
        return True

    operation_terms = [
        "這一行", "這一段", "先輸入", "存到", "指定給", "判斷是否", "輸出結果", "回傳", "變數", "條件",
    ]
    return any(term in low for term in operation_terms)


def _parse_timed_segments_from_subtitle_text(subtitle_text: str) -> list[dict]:
    segments = []
    for ln in str(subtitle_text or "").splitlines():
        line = str(ln or "").strip()
        m = re.match(r"\s*\[(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\]\s*(.*)$", line)
        if not m:
            continue
        try:
            start = float(m.group(1))
            end = float(m.group(2))
        except Exception:
            continue
        if end <= start:
            continue
        segments.append({"start": start, "end": end, "text": str(m.group(3) or "").strip()})
    return segments


def _build_subtitle_index_from_text(subtitle_text: str) -> dict:
    raw = str(subtitle_text or "").strip()
    if not raw:
        return {}

    if "-->" in raw:
        segs = parse_srt_segments(raw)
    else:
        segs = _parse_timed_segments_from_subtitle_text(raw)

    if not segs:
        return {}

    try:
        return build_subtitle_index(segs)
    except Exception:
        return {}


def _snap_window_to_subtitle_bounds(subtitle_index: dict, start_sec: float, end_sec: float) -> dict:
    segs = (subtitle_index or {}).get("segments") or []
    if not segs:
        return {}

    try:
        s = float(start_sec)
        e = float(end_sec)
    except Exception:
        return {}
    if e <= s:
        return {}

    overlaps = []
    nearest = None
    nearest_distance = None

    for seg in segs:
        if not isinstance(seg, dict):
            continue
        try:
            ss = float(seg.get("start", 0.0))
            ee = float(seg.get("end", 0.0))
        except Exception:
            continue
        if ee <= ss:
            continue

        overlap = min(e, ee) - max(s, ss)
        if overlap > 0:
            overlaps.append(seg)
            continue

        distance = min(abs(ss - e), abs(ee - s))
        if nearest is None or distance < nearest_distance:
            nearest = seg
            nearest_distance = distance

    chosen = overlaps if overlaps else ([nearest] if isinstance(nearest, dict) else [])
    if not chosen:
        return {}

    chosen = sorted(chosen, key=lambda item: (float(item.get("start", 0.0)), float(item.get("end", 0.0))))
    start = float(chosen[0].get("start", s))
    end = float(chosen[-1].get("end", e))
    anchor_ids = []
    for seg in chosen:
        seg_id = seg.get("id")
        if seg_id is None:
            continue
        if seg_id in anchor_ids:
            continue
        anchor_ids.append(seg_id)

    return {
        "start": round(float(start), 2),
        "end": round(float(end), 2),
        "anchor_ids": anchor_ids,
        "primary_anchor_id": anchor_ids[0] if anchor_ids else None,
        "anchor_id": anchor_ids[0] if anchor_ids else None,
    }


def _snap_chapters_to_subtitle_bounds(chapters: list[dict], subtitle_index: dict) -> list[dict]:
    snapped = []
    for ch in chapters or []:
        if not isinstance(ch, dict):
            continue
        item = dict(ch)
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except Exception:
            continue
        if end <= start:
            continue
        snap = _snap_window_to_subtitle_bounds(subtitle_index, start, end)
        if snap:
            item["start"] = snap["start"]
            item["end"] = snap["end"]
        snapped.append(item)
    return validate_chapters(snapped)


def _timecode_to_seconds(value) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        pass

    parts = text.split(":")
    try:
        if len(parts) == 2:
            return (float(parts[0]) * 60.0) + float(parts[1])
        if len(parts) == 3:
            return (float(parts[0]) * 3600.0) + (float(parts[1]) * 60.0) + float(parts[2])
    except Exception:
        return None
    return None


def _subtitle_bounds(subtitle_segments: list[dict]) -> dict:
    valid_ranges = []
    for seg in subtitle_segments or []:
        if not isinstance(seg, dict):
            continue
        try:
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
        except Exception:
            continue
        if end <= start:
            continue
        valid_ranges.append((start, end))
    if not valid_ranges:
        return {}
    return {
        "start": round(min(start for start, _ in valid_ranges), 2),
        "end": round(max(end for _, end in valid_ranges), 2),
    }


def _subtitle_segments_in_range(subtitle_segments: list[dict], start_sec: Optional[float], end_sec: Optional[float]) -> list[dict]:
    if start_sec is None or end_sec is None:
        return []
    try:
        start_limit = float(start_sec)
        end_limit = float(end_sec)
    except Exception:
        return []
    if end_limit <= start_limit:
        return []

    filtered = []
    for seg in subtitle_segments or []:
        if not isinstance(seg, dict):
            continue
        try:
            seg_start = float(seg.get("start", 0.0))
            seg_end = float(seg.get("end", 0.0))
        except Exception:
            continue
        if seg_end <= seg_start:
            continue

        overlap_start = max(seg_start, start_limit)
        overlap_end = min(seg_end, end_limit)
        if overlap_end <= overlap_start:
            continue

        clipped = dict(seg)
        clipped["start"] = round(overlap_start, 2)
        clipped["end"] = round(overlap_end, 2)
        filtered.append(clipped)

    filtered.sort(key=lambda item: (float(item.get("start", 0.0)), float(item.get("end", 0.0))))
    return filtered


def infer_concept_window_from_subtitles(
    concept_tag: str,
    subtitle_segments: list[dict],
    existing_chapters: Optional[list[dict]] = None,
    code_start_ts: Optional[float] = None,
) -> dict:
    """從字幕片段推測單一概念章節的時間視窗。"""
    tag_key = normalize_concept_name(concept_tag)
    segs = [seg for seg in (subtitle_segments or []) if isinstance(seg, dict)]
    if not tag_key or not segs:
        return {}

    keywords = {tag_key}
    label = concept_tag_to_label(tag_key)
    if label:
        keywords.add(label.lower())
    for alias in _LEGACY_CONCEPT_TAG_ALIASES.get(tag_key, []):
        alias_text = str(alias or "").strip().lower()
        if alias_text:
            keywords.add(alias_text)

    def _segment_text(seg: dict) -> str:
        return str(seg.get("text") or seg.get("content") or seg.get("subtitle") or "").strip().lower()

    def _segment_score(seg: dict) -> float:
        text = _segment_text(seg)
        if not text:
            return 0.0
        score = 0.0
        for keyword in keywords:
            if keyword and keyword in text:
                score += 2.0
        if tag_key == "python_syntax" and any(token in text for token in ["def", "return", "if", "for", "while"]):
            score += 1.5
        try:
            start = float(seg.get("start", 0.0))
        except Exception:
            start = 0.0
        if code_start_ts is not None and start >= float(code_start_ts):
            score += 0.5
        return score

    scored = []
    for idx, seg in enumerate(segs):
        try:
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
        except Exception:
            continue
        if end <= start:
            continue
        if code_start_ts is not None and end < float(code_start_ts) - 1.0:
            continue
        scored.append((idx, start, end, _segment_score(seg)))

    if not scored:
        return {}

    scored.sort(key=lambda item: (item[3], -item[1], -item[2]), reverse=True)
    best_idx = scored[0][0]

    chosen_indices = [best_idx]
    best_start = float(segs[best_idx].get("start", 0.0))
    best_end = float(segs[best_idx].get("end", 0.0))

    left = best_idx - 1
    while left >= 0:
        try:
            left_start = float(segs[left].get("start", 0.0))
            left_end = float(segs[left].get("end", 0.0))
        except Exception:
            break
        if best_start - left_end > 3.5:
            break
        chosen_indices.insert(0, left)
        best_start = min(best_start, left_start)
        left -= 1

    right = best_idx + 1
    while right < len(segs):
        try:
            right_start = float(segs[right].get("start", 0.0))
            right_end = float(segs[right].get("end", 0.0))
        except Exception:
            break
        if right_start - best_end > 3.5:
            break
        chosen_indices.append(right)
        best_end = max(best_end, right_end)
        right += 1

    if existing_chapters:
        for chapter in existing_chapters:
            if not isinstance(chapter, dict):
                continue
            try:
                chapter_start = float(chapter.get("start", 0.0))
                chapter_end = float(chapter.get("end", 0.0))
            except Exception:
                continue
            if chapter_end <= chapter_start:
                continue
            if chapter_start <= best_end and chapter_end >= best_start:
                if chapter_end <= best_start:
                    best_start = max(best_start, chapter_end)
                elif chapter_start >= best_end:
                    best_end = min(best_end, chapter_start)

    if best_end <= best_start:
        best_end = best_start + 1.0

    anchor_ids = []
    for idx in chosen_indices:
        seg_id = segs[idx].get("id")
        if seg_id is not None and seg_id not in anchor_ids:
            anchor_ids.append(seg_id)

    return {
        "start": round(float(best_start), 2),
        "end": round(float(best_end), 2),
        "concept_tag": tag_key,
        "concept_label": label or tag_key,
        "anchor_ids": anchor_ids,
        "primary_anchor_id": anchor_ids[0] if anchor_ids else None,
        "anchor_id": anchor_ids[0] if anchor_ids else None,
    }


# ─────────────────────────────────────────────
# 主入口：Flask route 可直接呼叫
# ─────────────────────────────────────────────

def _json_safe_segments(segs: list[dict]) -> list[dict]:
    out = []
    for seg in segs or []:
        if not isinstance(seg, dict):
            continue
        try:
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
        except Exception:
            continue
        if end <= start:
            continue
        out.append({
            "id": seg.get("id"),
            "start": round(start, 2),
            "end": round(end, 2),
            "text": str(seg.get("text") or "").strip(),
        })
    return out


def _safe_bounds(bounds: dict) -> Optional[dict]:
    if not isinstance(bounds, dict):
        return None
    try:
        start = float(bounds.get("start"))
        end = float(bounds.get("end"))
    except Exception:
        return None
    if end <= start:
        return None
    return {"start": round(start, 2), "end": round(end, 2)}


def _safe_bounds_fallback(subtitle_segments: list[dict]) -> Optional[dict]:
    """Fallback subtitle bounds from segment list when primary bounds calculation fails."""
    segs = _json_safe_segments(subtitle_segments)
    if not segs:
        return None
    try:
        start = min(float(seg.get("start", 0.0)) for seg in segs)
        end = max(float(seg.get("end", 0.0)) for seg in segs)
    except Exception:
        return None
    if end <= start:
        return None
    return {"start": round(start, 2), "end": round(end, 2)}


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _get_time_alignment_config(task: dict) -> dict:
    """
    Build video<->subtitle time alignment config.
    subtitle_time = video_time * scale + offset
    """
    task = task or {}
    prompt_source = task.get("prompt_source") or {}
    source_subtitle = task.get("source_subtitle") or {}

    cfg = {
        "offset": 0.0,
        "scale": 1.0,
    }

    for key in (
        "subtitle_time_alignment",
        "time_alignment",
    ):
        raw = task.get(key)
        if isinstance(raw, dict):
            cfg["offset"] = _safe_float(raw.get("offset", cfg["offset"]), cfg["offset"])
            cfg["scale"] = _safe_float(raw.get("scale", cfg["scale"]), cfg["scale"])

    for key in (
        "subtitle_time_offset",
        "subtitle_offset",
    ):
        if task.get(key) is not None:
            cfg["offset"] = _safe_float(task.get(key), cfg["offset"])

    for key in (
        "subtitle_time_scale",
        "subtitle_scale",
    ):
        if task.get(key) is not None:
            cfg["scale"] = _safe_float(task.get(key), cfg["scale"])

    if isinstance(prompt_source, dict):
        if prompt_source.get("subtitle_time_offset") is not None:
            cfg["offset"] = _safe_float(prompt_source.get("subtitle_time_offset"), cfg["offset"])
        if prompt_source.get("subtitle_time_scale") is not None:
            cfg["scale"] = _safe_float(prompt_source.get("subtitle_time_scale"), cfg["scale"])

    if isinstance(source_subtitle, dict):
        if source_subtitle.get("subtitle_time_offset") is not None:
            cfg["offset"] = _safe_float(source_subtitle.get("subtitle_time_offset"), cfg["offset"])
        if source_subtitle.get("subtitle_time_scale") is not None:
            cfg["scale"] = _safe_float(source_subtitle.get("subtitle_time_scale"), cfg["scale"])

    if cfg["scale"] == 0.0:
        cfg["scale"] = 1.0

    return {
        "offset": round(float(cfg["offset"]), 6),
        "scale": round(float(cfg["scale"]), 6),
    }


def _video_to_subtitle_time(video_time: Optional[float], align_cfg: dict) -> Optional[float]:
    if video_time is None:
        return None
    scale = _safe_float((align_cfg or {}).get("scale"), 1.0)
    offset = _safe_float((align_cfg or {}).get("offset"), 0.0)
    if scale == 0.0:
        scale = 1.0
    return round((float(video_time) * scale) + offset, 2)


def _subtitle_to_video_time(subtitle_time: Optional[float], align_cfg: dict) -> Optional[float]:
    if subtitle_time is None:
        return None
    scale = _safe_float((align_cfg or {}).get("scale"), 1.0)
    offset = _safe_float((align_cfg or {}).get("offset"), 0.0)
    if scale == 0.0:
        scale = 1.0
    return round((float(subtitle_time) - offset) / scale, 2)


def _build_block_chapter_code_map(solution_blocks: list[dict], chapters: list[dict], block_chapter_map: dict) -> list[dict]:
    """Return block-level mapping payload for UI display."""
    out = []
    safe_chapters = validate_chapters(chapters or [])

    for idx, block in enumerate(solution_blocks or []):
        slot = str(idx)
        mapped = block_chapter_map.get(slot) or {}
        chapter_index = mapped.get("chapter_index")
        chapter = safe_chapters[chapter_index] if isinstance(chapter_index, int) and 0 <= chapter_index < len(safe_chapters) else None

        out.append({
            "slot": slot,
            "slot_index": idx,
            "code": str((block or {}).get("text") or ""),
            "semantic_zh": str((block or {}).get("semantic_zh") or ""),
            "matched": bool(mapped),
            "chapter_index": chapter_index,
            "chapter_concept_tag": str((mapped.get("concept_tag") or mapped.get("concept") or (chapter or {}).get("concept_tag") or "")).strip(),
            "chapter_concept_label": str((mapped.get("concept_label") or (chapter or {}).get("concept_label") or "")).strip(),
            "chapter_start": mapped.get("start") if mapped else ((chapter or {}).get("start") if chapter else None),
            "chapter_end": mapped.get("end") if mapped else ((chapter or {}).get("end") if chapter else None),
            "method": str(mapped.get("method") or ""),
        })

    return out


def _build_chapter_code_map(block_chapter_code_map: list[dict], chapters: list[dict]) -> list[dict]:
    """Aggregate mapped block code by chapter for UI section view."""
    grouped = {}
    for item in block_chapter_code_map or []:
        if not isinstance(item, dict):
            continue
        if not item.get("matched"):
            continue
        chapter_index = item.get("chapter_index")
        if not isinstance(chapter_index, int):
            continue
        grouped.setdefault(chapter_index, []).append(item)

    out = []
    safe_chapters = validate_chapters(chapters or [])
    for chapter_index, blocks in sorted(grouped.items(), key=lambda pair: pair[0]):
        chapter = safe_chapters[chapter_index] if 0 <= chapter_index < len(safe_chapters) else {}
        out.append({
            "chapter_index": chapter_index,
            "concept_tag": str(chapter.get("concept_tag") or chapter.get("concept") or "").strip(),
            "concept_label": str(chapter.get("concept_label") or "").strip(),
            "start": chapter.get("start"),
            "end": chapter.get("end"),
            "blocks": blocks,
        })
    return out


def align_task_by_concept(data: dict):
    try:
        # ── 1. 找 task ──────────────────────────────
        task_id_str = str(data.get("task_id") or "").strip()
        video_id = str(data.get("video_id") or "").strip()

        task = None
        if task_id_str:
            try:
                task = db.parsons_tasks.find_one({"_id": ObjectId(task_id_str)})
            except Exception:
                task = None

        if not task and video_id:
            vid_oid = maybe_oid(video_id)
            q = {
                "$and": [
                    {"$or": [
                        {"gen_source": "fixed"},
                        {"source_type": "fixed"},
                        {"gen_source": "openai"},
                        {"gen_source": "fallback"},
                    ]},
                    (
                        {"$or": [{"video_id": vid_oid}, {"video_id_str": video_id}]}
                        if vid_oid else
                        {"video_id_str": video_id}
                    ),
                ]
            }
            task = db.parsons_tasks.find_one(q)

        if not task:
            return jsonify({
                "ok": False,
                "message": "找不到題目（需 task_id 或 video_id）",
            }), 404

        real_task_id = str(task["_id"])

        # ── 2. 解析模式 / 版本 / 教師輸入區間（影片時間）──────────────
        raw_mode = str(data.get("mode") or data.get("teaching_range_mode") or "").strip().lower()
        teacher_range_only = raw_mode == "teacher_range_only"
        force_rebuild = bool(data.get("force_rebuild"))

        requested_subtitle_version = str(data.get("subtitle_version") or "").strip()
        current_version_key = derive_concept_version_key(task, requested_subtitle_version)

        # 老師前端輸入的是影片時間
        teacher_video_range_start = _timecode_to_seconds(data.get("teaching_range_start"))
        teacher_video_range_end = _timecode_to_seconds(data.get("teaching_range_end"))

        # 讀取字幕/影片時間校正設定
        align_cfg = _get_time_alignment_config(task)

        # 後端真正用來驗證的是字幕時間
        teaching_range_start = (
            _video_to_subtitle_time(teacher_video_range_start, align_cfg)
            if teacher_video_range_start is not None else None
        )
        teaching_range_end = (
            _video_to_subtitle_time(teacher_video_range_end, align_cfg)
            if teacher_video_range_end is not None else None
        )

        if teacher_range_only:
            code_start_ts = teaching_range_start
        else:
            code_start_ts = _timecode_to_seconds(data.get("code_start_ts"))
            if code_start_ts is None:
                code_start_ts = (
                    teaching_range_start
                    or _timecode_to_seconds(task.get("teacher_code_start_ts"))
                    or _timecode_to_seconds(task.get("concept_code_start_ts"))
                )
            if teaching_range_start is None:
                teaching_range_start = code_start_ts
            if teaching_range_end is None:
                teaching_range_end = (
                    _timecode_to_seconds(task.get("teacher_code_end_ts"))
                    or _timecode_to_seconds(task.get("concept_code_end_ts"))
                )

        # ── 3. 取字幕：全集字幕 only ─────────────────
        subtitle_raw = _get_subtitle_for_task(task)
        if not subtitle_raw:
            return jsonify({
                "ok": False,
                "message": "找不到字幕內容，請確認此題目的影片有字幕資料",
                "task_id": real_task_id,
            }), 404

        subtitle_index = _build_subtitle_index_from_text(subtitle_raw)
        subtitle_segments = subtitle_index.get("segments") or []

        # fallback：若 build_subtitle_index 沒解析到，再試 SRT parser
        if not subtitle_segments and "-->" in subtitle_raw:
            try:
                subtitle_segments = parse_srt_segments(subtitle_raw)
            except Exception:
                subtitle_segments = []

        subtitle_segments_safe = _json_safe_segments(subtitle_segments)

        if not subtitle_segments_safe:
            return jsonify({
                "ok": False,
                "message": "字幕解析失敗或字幕內容為空",
                "task_id": real_task_id,
                "mode": "teacher_range_only" if teacher_range_only else (raw_mode or "legacy"),
                "subtitle_segments": [],
                "subtitle_segments_count": 0,
                "teaching_range_start": teacher_video_range_start,
                "teaching_range_end": teacher_video_range_end,
                "teaching_range_start_subtitle": teaching_range_start,
                "teaching_range_end_subtitle": teaching_range_end,
                "subtitle_time_alignment": align_cfg,
            }), 400

        subtitle_bounds = _safe_bounds(_subtitle_bounds(subtitle_segments_safe))
        if not subtitle_bounds:
            subtitle_bounds = _safe_bounds_fallback(subtitle_segments_safe)

        if not subtitle_bounds:
            return jsonify({
                "ok": False,
                "message": "字幕範圍計算失敗",
                "task_id": real_task_id,
                "mode": "teacher_range_only" if teacher_range_only else (raw_mode or "legacy"),
                "subtitle_segments": subtitle_segments_safe,
                "subtitle_segments_count": len(subtitle_segments_safe),
                "teaching_range_start": teacher_video_range_start,
                "teaching_range_end": teacher_video_range_end,
                "teaching_range_start_subtitle": teaching_range_start,
                "teaching_range_end_subtitle": teaching_range_end,
                "subtitle_time_alignment": align_cfg,
            }), 400

        # compact 只給 AI / draft 用
        subtitle_compact = compact_segments_for_prompt(subtitle_segments_safe, max_chars=8000)
        if not subtitle_compact:
            return jsonify({
                "ok": False,
                "message": "字幕 compact 結果為空",
                "task_id": real_task_id,
            }), 400

        recommended_video_range = {
            "start": _subtitle_to_video_time(subtitle_bounds["start"], align_cfg),
            "end": _subtitle_to_video_time(subtitle_bounds["end"], align_cfg),
        }

        # ── 4. 區間驗證 ────────────────────────────
        effective_segments = subtitle_segments_safe
        teaching_range_warning = None
        teaching_range_notice = None

        def _teacher_range_error(message: str, code: str, status_code: int = 400):
            return jsonify({
                "ok": False,
                "message": message,
                "warning": code,
                "mode": "teacher_range_only",
                "teaching_range_mode": "teacher_range_only",
                "task_id": real_task_id,
                "subtitle_version_key": current_version_key,

                # 前端顯示用：影片時間
                "teaching_range_start": teacher_video_range_start,
                "teaching_range_end": teacher_video_range_end,

                # 後端驗證用：字幕時間
                "teaching_range_start_subtitle": teaching_range_start,
                "teaching_range_end_subtitle": teaching_range_end,

                "subtitle_time_alignment": align_cfg,

                "subtitle_segments": subtitle_segments_safe,
                "subtitle_segments_count": len(subtitle_segments_safe),

                "effective_segments": [],
                "effective_segments_count": 0,

                "teaching_range": None,
                "teaching_range_segments": [],
                "teaching_range_segments_count": 0,
                "teaching_range_source": "teacher_range_only_invalid",
                "teaching_range_warning": {
                    "code": code,
                    "message": message,
                    "recommended_range": subtitle_bounds,
                    "recommended_range_video": recommended_video_range,
                },
                "teaching_range_recommended_range": subtitle_bounds,
                "teaching_range_recommended_range_video": recommended_video_range,

                "code_summary_range": None,
                "code_summary_segments": [],
                "code_summary_segments_count": 0,
                "code_summary_range_source": "teacher_range_only_invalid",

                "code_start_warning": {
                    "code": code,
                    "message": message,
                    "recommended_range": subtitle_bounds,
                    "recommended_range_video": recommended_video_range,
                },
                "code_start_recommended_range": subtitle_bounds,
                "code_start_recommended_range_video": recommended_video_range,

                "teaching_range_effective": False,
                "code_start_effective": False,

                "ai_suggestions": {
                    "rename_suggestions": [],
                    "missing_concepts": [],
                    "chapter_recommendations": [],
                },
                "chapter_recommendations": [],
                "warnings": [code],
            }), status_code

        teaching_range_requested = (
            data.get("teaching_range_start") is not None
            or data.get("teaching_range_end") is not None
            or code_start_ts is not None
        )

        if teacher_range_only:
            if teaching_range_start is None or teaching_range_end is None:
                return _teacher_range_error("請先設定有效的教學區間。", "teaching_range_invalid")

            if teaching_range_end <= teaching_range_start:
                return _teacher_range_error("教學區間結束時間必須大於開始時間。", "teaching_range_invalid")

            # teacher_range_only：超出邊界時改為自動夾回字幕範圍（非致命），避免老師微調時被擋下。
            subtitle_start = float(subtitle_bounds["start"])
            subtitle_end = float(subtitle_bounds["end"])
            if teaching_range_start < subtitle_start or teaching_range_end > subtitle_end:
                clamped_start = max(float(teaching_range_start), subtitle_start)
                clamped_end = min(float(teaching_range_end), subtitle_end)
                if clamped_end <= clamped_start:
                    return _teacher_range_error("教學區間超出全片字幕範圍", "teaching_range_out_of_bounds")

                teaching_range_notice = {
                    "code": "teaching_range_clamped",
                    "message": "教學區間超出全片字幕範圍，已自動調整到字幕可用範圍。",
                    "requested_range": {
                        "start": round(float(teaching_range_start), 2),
                        "end": round(float(teaching_range_end), 2),
                    },
                    "applied_range": {
                        "start": round(float(clamped_start), 2),
                        "end": round(float(clamped_end), 2),
                    },
                    "recommended_range": subtitle_bounds,
                    "recommended_range_video": recommended_video_range,
                }

                teaching_range_start = round(float(clamped_start), 2)
                teaching_range_end = round(float(clamped_end), 2)
                teacher_video_range_start = _subtitle_to_video_time(teaching_range_start, align_cfg)
                teacher_video_range_end = _subtitle_to_video_time(teaching_range_end, align_cfg)
                code_start_ts = teaching_range_start

            effective_segments = _json_safe_segments(
                _subtitle_segments_in_range(subtitle_segments_safe, teaching_range_start, teaching_range_end)
            )

            if not effective_segments:
                return _teacher_range_error(
                    "教學區間內沒有足夠字幕可產生章節草稿，請放大教學區間。",
                    "teaching_range_empty"
                )

        elif teaching_range_start is not None and teaching_range_end is not None and teaching_range_end > teaching_range_start:
            effective_segments = _json_safe_segments(
                _subtitle_segments_in_range(subtitle_segments_safe, teaching_range_start, teaching_range_end)
            )
            if not effective_segments:
                teaching_range_warning = {
                    "code": "teaching_range_empty",
                    "message": "教學區間內沒有足夠字幕可產生章節草稿，請放大教學區間。",
                    "recommended_range": subtitle_bounds,
                    "recommended_range_video": recommended_video_range,
                }

        elif teaching_range_requested:
            teaching_range_warning = {
                "code": "teaching_range_invalid",
                "message": "請先設定有效的教學區間（結束時間必須大於開始時間）。",
                "recommended_range": subtitle_bounds,
                "recommended_range_video": recommended_video_range,
            }

        if teaching_range_warning:
            existing_teacher_chapters = validate_chapters(task.get("teacher_concept_chapters") or [])
            existing_recommendations = _build_chapter_ai_recommendations(
                task.get("concept_chapters_draft") or existing_teacher_chapters,
                subtitle_segments_safe
            )
            return jsonify({
                "ok": True,
                "message": teaching_range_warning["message"],
                "task_id": real_task_id,
                "mode": raw_mode or "legacy",
                "teaching_range_mode": raw_mode or "legacy",
                "subtitle_version_key": current_version_key,

                "concept_chapters_draft": task.get("concept_chapters_draft") or [],
                "teacher_concept_chapters": existing_teacher_chapters,
                "concept_chapters_formal": existing_teacher_chapters,
                "concept_align_status": task.get("concept_align_status") or "draft_generated",

                "subtitle_segments": subtitle_segments_safe,
                "subtitle_segments_count": len(subtitle_segments_safe),

                "effective_segments": effective_segments,
                "effective_segments_count": len(effective_segments),

                # 前端顯示用：影片時間
                "teaching_range_start": teacher_video_range_start,
                "teaching_range_end": teacher_video_range_end,

                # 後端驗證用：字幕時間
                "teaching_range_start_subtitle": teaching_range_start,
                "teaching_range_end_subtitle": teaching_range_end,

                "subtitle_time_alignment": align_cfg,

                "teaching_range": _safe_bounds(_subtitle_bounds(effective_segments)) if effective_segments else None,
                "teaching_range_segments": effective_segments,
                "teaching_range_segments_count": len(effective_segments),
                "teaching_range_source": "teaching_range_empty",
                "teaching_range_warning": teaching_range_warning,
                "teaching_range_recommended_range": teaching_range_warning.get("recommended_range"),
                "teaching_range_recommended_range_video": teaching_range_warning.get("recommended_range_video"),

                "code_summary_range": None,
                "code_summary_segments": effective_segments,
                "code_summary_segments_count": len(effective_segments),
                "code_summary_range_source": "teaching_range_empty",
                "code_start_warning": teaching_range_warning,
                "code_start_recommended_range": teaching_range_warning.get("recommended_range"),
                "code_start_recommended_range_video": teaching_range_warning.get("recommended_range_video"),

                "teaching_range_effective": False,
                "code_start_effective": False,

                "ai_suggestions": {
                    "rename_suggestions": [],
                    "missing_concepts": [],
                    "chapter_recommendations": existing_recommendations,
                },
                "chapter_recommendations": existing_recommendations,
                "warnings": [teaching_range_warning["code"]],
            }), 200

        # ===== 這裡以下保留你原本後半段 draft / formal align 邏輯 =====

        solution_blocks = task.get("solution_blocks") or []

        # ── 5. 生成 draft ─────────────────────────
        effective_subtitle_index = build_subtitle_index(effective_segments) if effective_segments else subtitle_index
        effective_compact = compact_segments_for_prompt(effective_segments, max_chars=8000) if effective_segments else subtitle_compact

        validated_teacher_chapters = validate_chapters(task.get("teacher_concept_chapters") or [])
        teacher_version_key = str(task.get("teacher_concept_version_key") or "").strip().lower()

        draft_code_start_ts = teaching_range_start if teacher_range_only else (teaching_range_start or code_start_ts)
        draft_raw = extract_concept_chapters(
            effective_compact,
            strict_ai_only=False,
            code_start_ts=draft_code_start_ts,
            teaching_range_end=teaching_range_end,
        )
        draft_source = str((draft_raw[0] or {}).get("draft_source") or "rule").strip().lower() if draft_raw else "rule"
        draft_chapters = validate_chapters(draft_raw)
        draft_chapters = _snap_chapters_to_subtitle_bounds(draft_chapters, effective_subtitle_index)
        draft_block_chapter_map = map_blocks_to_chapters(solution_blocks, draft_chapters) if solution_blocks else {}
        draft_block_chapter_code_map = _build_block_chapter_code_map(solution_blocks, draft_chapters, draft_block_chapter_map)
        draft_chapter_code_map = _build_chapter_code_map(draft_block_chapter_code_map, draft_chapters)
        ai_suggestions = generate_ai_chapter_suggestions(draft_chapters, effective_segments)
        chapter_recommendations = _build_chapter_ai_recommendations(draft_chapters, effective_segments)

        # ── 6. 若只要 draft，先回 draft ─────────────
        if force_rebuild or not validated_teacher_chapters or teacher_version_key != current_version_key:
            db.parsons_tasks.update_one(
                {"_id": ObjectId(real_task_id)},
                {
                    "$set": {
                        "concept_chapters_draft": draft_chapters,
                        "concept_align_status": "draft_generated",
                        "concept_align_source": f"draft_{draft_source}",
                        "concept_align_version_key": current_version_key,
                        "subtitle_ir_cache_updated_at": now_utc(),
                    }
                }
            )

            return jsonify({
                "ok": True,
                "message": "已產生系統概念章節草稿，請老師確認、合併或調整邊界。",
                "task_id": real_task_id,
                "subtitle_version_key": current_version_key,
                "draft_source": draft_source,
                "forced_rebuild": bool(force_rebuild),
                "mode": "teacher_range_only" if teacher_range_only else (raw_mode or "legacy"),
                "teaching_range_mode": "teacher_range_only" if teacher_range_only else (raw_mode or "legacy"),
                "code_start_ts": code_start_ts,
               "teaching_range_start": teacher_video_range_start,
                "teaching_range_end": teacher_video_range_end,
                "teaching_range_start_subtitle": teaching_range_start,
                "teaching_range_end_subtitle": teaching_range_end,
                "subtitle_time_alignment": align_cfg,
                "teaching_range_recommended_range_video": recommended_video_range,
                "code_start_recommended_range_video": recommended_video_range,
                "concept_chapters_draft": draft_chapters,
                "teacher_concept_chapters": validated_teacher_chapters,
                "concept_chapters_formal": validated_teacher_chapters,
                "block_chapter_map": draft_block_chapter_map,
                "block_chapter_code_map": draft_block_chapter_code_map,
                "chapter_code_map": draft_chapter_code_map,
                "concept_align_status": "draft_generated",
                "subtitle_segments": subtitle_segments_safe,         # 全片
                "subtitle_segments_count": len(subtitle_segments_safe),
                "effective_segments": effective_segments,            # 區間
                "effective_segments_count": len(effective_segments),
                "teaching_range": _safe_bounds(_subtitle_bounds(effective_segments)) if effective_segments else None,
                "teaching_range_segments": effective_segments,
                "teaching_range_segments_count": len(effective_segments),
                "teaching_range_source": "teacher_range_only" if teacher_range_only else "teaching_range",
                "teaching_range_warning": teaching_range_notice,
                "teaching_range_recommended_range": subtitle_bounds,
                "code_summary_range": _safe_bounds(_subtitle_bounds(effective_segments)) if effective_segments else None,
                "code_summary_segments": effective_segments,
                "code_summary_segments_count": len(effective_segments),
                "code_summary_range_source": "teacher_range_only" if teacher_range_only else "teaching_range",
                "code_start_warning": teaching_range_notice,
                "code_start_recommended_range": subtitle_bounds,
                "teaching_range_effective": True,
                "code_start_effective": True,
                "ai_suggestions": ai_suggestions,
                "chapter_recommendations": chapter_recommendations,
                "warnings": [teaching_range_notice["code"]] if teaching_range_notice else [],
            }), 200

        # ── 7. 使用老師正式章節做 block 對應 ─────────
        if validated_teacher_chapters and (
            not teacher_version_key
            or not current_version_key
            or teacher_version_key == current_version_key
        ):
            chapters = _snap_chapters_to_subtitle_bounds(validated_teacher_chapters, effective_subtitle_index)
            align_source = "teacher_defined_cached"
        else:
            return jsonify({
                "ok": False,
                "message": "沒有找到老師正式標註的概念章節，請先由教師完成人工標註。",
                "task_id": real_task_id,
                "subtitle_version_key": current_version_key,
            }), 200

        if not solution_blocks:
            return jsonify({
                "ok": False,
                "message": "此題目沒有 solution_blocks",
                "task_id": real_task_id,
            }), 400

        block_chapter_map = map_blocks_to_chapters(solution_blocks, chapters)
        block_chapter_code_map = _build_block_chapter_code_map(solution_blocks, chapters, block_chapter_map)
        chapter_code_map = _build_chapter_code_map(block_chapter_code_map, chapters)

        total_slots = len(solution_blocks)
        mapped_slots = len(block_chapter_map)

        if mapped_slots == 0:
            db.parsons_tasks.update_one(
                {"_id": ObjectId(real_task_id)},
                {
                    "$set": {
                        "concept_align_status": "teacher_chapters_ready_but_no_rule_match",
                        "concept_align_source": "rule_first_only",
                        "concept_align_unmapped_slots": [str(i) for i in range(total_slots)],
                        "subtitle_ir_cache_updated_at": now_utc(),
                    }
                }
            )
            return jsonify({
                "ok": False,
                "message": "正式章節已存在，但目前沒有任何 block 能以 rule-first 對應成功；未寫入正式答案。",
                "task_id": real_task_id,
                "unmapped_slots": [str(i) for i in range(total_slots)],
            }), 200

        unmapped_slots = []
        status = "rule_match_complete"
        if mapped_slots < total_slots:
            unmapped_slots = [str(i) for i in range(total_slots) if str(i) not in block_chapter_map]
            status = "partial_rule_match_needs_teacher_review"

        concept_segment_map = build_concept_segment_map(block_chapter_map, chapters)

        ai_segment_map = {}
        for slot_str, ch_data in block_chapter_map.items():
            raw_start = float(ch_data.get("start") or 0.0)
            raw_end = float(ch_data.get("end") or 0.0)
            ai_segment_map[slot_str] = {
                "start": round(raw_start, 2),
                "end": round(raw_end, 2),
                "score": 1.0,
                "evidence": ch_data.get("concept") or "",
                "source": "concept_chapter",
                "concept": normalize_concept_name(ch_data.get("concept") or ""),
                "method": ch_data.get("method") or "ai",
                "chapter_source": align_source,
            }

        valid_ranges = [
            (float(v["start"]), float(v["end"]))
            for v in ai_segment_map.values()
            if float(v.get("end") or 0) > float(v.get("start") or 0)
        ]
        subtitle_range = {}
        if valid_ranges:
            subtitle_range = {
                "start": min(x[0] for x in valid_ranges),
                "end": max(x[1] for x in valid_ranges),
            }

        update = {
            "concept_chapters": chapters,
            "ai_segment_map": ai_segment_map,
            "concept_segment_map": concept_segment_map,
            "align_method": "concept_chapter",
            "chapter_source": align_source,
            "concept_align_updated_at": now_utc(),
            "concept_align_status": "teacher_confirmed",
            "concept_align_source": "rule_first_only" if status != "rule_match_complete" else align_source,
            "concept_align_unmapped_slots": unmapped_slots,
            "concept_align_version_key": current_version_key,
            "concept_chapters_formal": chapters,
            "chapter_recommendations": _build_chapter_ai_recommendations(chapters, effective_segments),
        }
        if subtitle_range:
            update["subtitle_range"] = subtitle_range

        db.parsons_tasks.update_one({"_id": task["_id"]}, {"$set": update})

        msg = f"概念章節對齊完成，共 {len(chapters)} 個章節，{len(ai_segment_map)} 個 slot 成功對齊。"
        if unmapped_slots:
            msg += f" (有 {len(unmapped_slots)} 個 slot 暫無對應)"

        return jsonify({
            "ok": True,
            "task_id": real_task_id,
            "chapters_count": len(chapters),
            "slots_aligned": len(ai_segment_map),
            "concept_chapters": chapters,
            "concept_chapters_draft": draft_chapters,
            "teacher_concept_chapters": validated_teacher_chapters,
            "concept_chapters_formal": chapters,
            "ai_segment_map": ai_segment_map,
            "concept_segment_map": concept_segment_map,
            "block_chapter_map": block_chapter_map,
            "block_chapter_code_map": block_chapter_code_map,
            "chapter_code_map": chapter_code_map,
            "chapter_source": align_source,
            "subtitle_version_key": current_version_key,
            "message": msg,
            "mode": "teacher_range_only" if teacher_range_only else (raw_mode or "legacy"),
            "teaching_range_mode": "teacher_range_only" if teacher_range_only else (raw_mode or "legacy"),
            "code_start_ts": code_start_ts,
            "teaching_range_start": teacher_video_range_start,
            "teaching_range_end": teacher_video_range_end,
            "teaching_range_start_subtitle": teaching_range_start,
            "teaching_range_end_subtitle": teaching_range_end,
            "subtitle_time_alignment": align_cfg,
            "teaching_range_recommended_range_video": recommended_video_range,
            "code_start_recommended_range_video": recommended_video_range,
            "subtitle_segments": subtitle_segments_safe,         # 全片
            "subtitle_segments_count": len(subtitle_segments_safe),
            "effective_segments": effective_segments,            # 區間
            "effective_segments_count": len(effective_segments),
            "teaching_range": _safe_bounds(_subtitle_bounds(effective_segments)) if effective_segments else None,
            "teaching_range_segments": effective_segments,
            "teaching_range_segments_count": len(effective_segments),
            "teaching_range_source": "teacher_range_only" if teacher_range_only else ("teaching_range" if effective_segments else "teaching_range_empty"),
            "teaching_range_warning": teaching_range_notice,
            "code_summary_range": _safe_bounds(_subtitle_bounds(effective_segments)) if effective_segments else None,
            "code_summary_segments": effective_segments,
            "code_summary_segments_count": len(effective_segments),
            "code_summary_range_source": "teacher_range_only" if teacher_range_only else ("teaching_range" if effective_segments else "teaching_range_empty"),
            "code_start_warning": teaching_range_notice,
            "ai_suggestions": ai_suggestions,
            "chapter_recommendations": _build_chapter_ai_recommendations(chapters, effective_segments),
            "warnings": [teaching_range_notice["code"]] if teaching_range_notice else [],
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "message": f"align_concept 失敗：{str(e)}",
            "error_type": e.__class__.__name__,
        }), 500