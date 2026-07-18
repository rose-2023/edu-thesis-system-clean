import os
import re
_re = re  # [新增] 統一使用 _re，避免未定義
import hashlib
import json
import math
import random
import statistics
import threading
import uuid
from datetime import datetime, timezone, timedelta
from functools import wraps
from typing import Any, Dict, Tuple, Optional

from flask import Blueprint, request, jsonify
from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError, OperationFailure

from ..db import db
from ..session_auth import (
    active_session_guard,
    current_participant_id,
    current_student_id,
)
from .learning_logs import (
    write_learning_log_safely,
    write_or_update_hint_learning_log_safely,
)

from . import parsons_ai  # [新增] 統一管理 OpenAI 呼叫（方案1）
# 負責概念標籤、字幕章節與 block 對齊。
from .parsons_concept_align import (
    align_task_by_concept,  # [新增] 概念章節對齊
    _WRONG_TYPE_TO_CONCEPT_TAG,
    _WRONG_TYPE_QUERY_TERMS,
    rerank_chapter_candidates_with_ai,
    concept_tag_to_label,
    infer_concept_tag_from_text,
    infer_surface_tag_from_text,
    infer_wrong_type_from_code,
    resolve_concept_tag_from_wrong_type,
    normalize_concept_name,
    normalize_surface_tag,
    infer_sub_concept_from_code,       # [新增] 程式碼 → 教學子概念
    get_query_terms_for_concept_tag,   # [新增] 子概念 → 字幕查詢詞
    get_context_rules_for_concept_tag, # [新增] 概念語境正負關鍵字
    _get_subtitle_for_task,
    _build_subtitle_index_from_text,
    infer_concept_window_from_subtitles,
    _chapter_conflicts_with_existing,
    _collect_chapter_warning_codes,
    _apply_semantic_constraint_to_chapters,
    _subtitle_segments_in_range,
    _subtitle_bounds,
    map_blocks_to_chapters,
    build_concept_segment_map,
)
# 負責從 SRT 找到最相關的字幕片段。
from .parsons_retrieval import (
    build_subtitle_index,
    retrieve_best_segment,
    retrieve_top_k_segments,
    merge_top_k_window,
    get_retrieval_mode,
)
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

# 學生才可以登入
_STUDENT_PARSONS_PATHS = {
    "/task",
    "/test/status",
    "/test/task",
    "/test/submit",
    "/submit",
    "/hint",
    "/hint_state",
}

# 老師與管理員才能管理題目
@parsons_bp.before_request
def enforce_parsons_session():
    subpath = request.path.removeprefix("/api/parsons")
    if subpath in _STUDENT_PARSONS_PATHS:
        return active_session_guard({"student"})
    if (
        subpath.startswith("/fixed_task/")
        or subpath.startswith("/test/cycle/")
        or subpath == "/test/export_csv"
        or subpath in {"/publish", "/regenerate", "/hint_library"}
    ):
        return active_session_guard({"teacher", "admin"})
    return None


_SUBMIT_GUARD_LOCK = threading.Lock()
_SUBMIT_ACTIVE = set()

# 會把這些資料做 SHA-256
def _submit_request_key(data):
    canonical_task_id = (
        data.get("task_id")
        or data.get("source_task_id")
        or data.get("test_task_id")
    )
    relevant = {
        "task_id": canonical_task_id,
        "test_role": data.get("test_role"),
        "answer_ids": data.get("answer_ids"),
        "submitted_order": data.get("submitted_order"),
        "submitted_indentation": data.get("submitted_indentation"),
    }
    serialized = json.dumps(relevant, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return current_student_id(), request.path, digest


def _blank_answer_id(value) -> bool:
    return value is None or str(value).strip() == ""


def _incomplete_answer_indices(answer_ids, expected_ids):
    submitted = answer_ids if isinstance(answer_ids, list) else []
    expected = expected_ids if isinstance(expected_ids, list) else []
    missing = []
    for idx in range(len(expected)):
        if idx >= len(submitted) or _blank_answer_id(submitted[idx]):
            missing.append(idx)
    return missing


def _incomplete_answer_json(missing_indices, expected_count):
    missing_count = len(missing_indices or [])
    all_blank = bool(expected_count > 0 and missing_count == expected_count)
    message = (
        "請先將所有程式片段放入作答區。"
        if all_blank
        else f"尚有 {missing_count} 個程式片段未放入作答區，請填答完整後再送出。"
    )
    return jsonify({
        "ok": False,
        "error": "incomplete_answer",
        "message": message,
        "missing_count": missing_count,
        "missing_indices": list(missing_indices or []),
        "all_blank": all_blank,
    }), 400


# 防止學生重複送出答案，避免 AI 生成提示被覆蓋
# 若同一學生短時間重複送出完全相同內容，會回傳 409 Conflict
def prevent_duplicate_submission(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        key = _submit_request_key(request.get_json(silent=True) or {})
        with _SUBMIT_GUARD_LOCK:
            if key in _SUBMIT_ACTIVE:
                return jsonify({
                    "ok": False,
                    "error": "duplicate_submission",
                    "message": "答案正在送出，請勿重複提交。",
                }), 409
            _SUBMIT_ACTIVE.add(key)

        try:
            return view(*args, **kwargs)
        finally:
            with _SUBMIT_GUARD_LOCK:
                _SUBMIT_ACTIVE.discard(key)

    return wrapped

# 學生只能呼叫學生 API
# 學生不能取得其他學生的 attempt
def _attempt_belongs_to_current_student(attempt):
    if not isinstance(attempt, dict):
        return False
    allowed = {current_student_id(), current_participant_id()}
    allowed.discard("")
    recorded = {
        str(attempt.get("student_id") or "").strip(),
        str(attempt.get("participant_id") or "").strip(),
    }
    recorded.discard("")
    return bool(allowed.intersection(recorded))

# =========================
# UTC time helper
# =========================
_TAIPEI_TZ = timezone(timedelta(hours=8))

# ========================
# MongoDB Date 使用 UTC。
def _utc_now():
    """
    Return current UTC datetime.
    Used by AI feedback / subtitle alignment / regenerate timestamps.
    """
    return datetime.now(timezone.utc)

# 額外保存台灣顯示時間。
def _taiwan_time_string(value):
    """Format an aware UTC datetime for display/audit fields without changing BSON Date storage."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _utc_iso_string(value):
    """Serialize a datetime as an explicit UTC ISO string for browser parsing."""
    if not isinstance(value, datetime):
        text = str(value or "").strip()
        return text or None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# 選擇字幕對齊模型與 AI 提示模型。


def _model_for_align() -> str:
    """Model used by subtitle/time alignment style calls."""
    return (
        os.getenv("OPENAI_MODEL_ALIGN")
        or os.getenv("OPENAI_MODEL")
        or "gpt-4.1-mini"
    ).strip()


def _model_for_feedback() -> str:
    """Model used by diagnosis/feedback generation calls."""
    return (
        os.getenv("OPENAI_MODEL_FEEDBACK")
        or os.getenv("OPENAI_MODEL")
        or "gpt-4o-mini"
    ).strip()

# =========================
#  (Pre/Post) Utils
# =========================
_TEST_INDEX_READY = False

# MongoDB Index
# 加快學生、題目、時間與作答紀錄查詢。
def ensure_test_indexes():
    """Keep legacy test-attempt indexes compatible with multi-question tests."""
    global _TEST_INDEX_READY
    if _TEST_INDEX_READY:
        return
    try:
        index_info = db.parsons_test_attempts.index_information()
        legacy = index_info.get("uniq_student_cycle_role")
        if legacy and legacy.get("unique"):
            db.parsons_test_attempts.drop_index("uniq_student_cycle_role")
        for index_name in ("student_cycle_role_task_1", "uniq_student_cycle_role_task"):
            index = index_info.get(index_name)
            index_keys = [item[0] for item in (index or {}).get("key", [])]
            if index and "test_task_id" in index_keys:
                db.parsons_test_attempts.drop_index(index_name)

        try:
            db.parsons_test_attempts.create_index(
                [
                    ("student_id", 1),
                    ("test_cycle_id", 1),
                    ("test_role", 1),
                    ("task_id", 1),
                ],
                name="uniq_student_cycle_role_task",
                unique=True,
            )
        except (DuplicateKeyError, OperationFailure) as e:
            print("[parsons_test_attempts] unique index not created; duplicate test attempts need manual review:", e)
            db.parsons_test_attempts.create_index(
                [
                    ("student_id", 1),
                    ("test_cycle_id", 1),
                    ("test_role", 1),
                    ("task_id", 1),
                ],
                name="student_cycle_role_task_1",
            )
        db.parsons_test_attempts.create_index(
            [("student_id", 1), ("test_cycle_id", 1), ("test_role", 1)],
            name="student_cycle_role_1",
        )
        _TEST_INDEX_READY = True
    except Exception as e:
        import traceback
        print("\n========== AI HINT AND SEGMENT ERROR ==========")
        print("error =", repr(e))
        traceback.print_exc()
        print("==============================================\n")


# Parsons attempts v2 standardized write helpers
_PARSONS_ATTEMPTS_V2_INDEX_READY = False
_PARSONS_HINT_RECORD_INDEX_READY = False
_PARSONS_HINT_LIBRARY_INDEX_READY = False
_PARSONS_ATTEMPTS_V2_TIMEZONE = "Asia/Taipei"
_PARSONS_TEST_STUDENT_ID = "11461127"
_PARSONS_HINT_PROMPT_VERSION = "structured_error_relation_library_v2"


# relation-aware hint library schema.  The six categories are intentionally
# finite so retrieval remains auditable and does not depend on an LLM guessing
# a student's misconception.
_HINT_LIBRARY_SCHEMA_VERSION = 2

_UNIT_CATEGORY_ALIASES = {
    "input_output": {
        "input_output", "io", "input", "output", "print", "輸入輸出", "輸入", "輸出",
    },
    "numeric_operation": {
        "numeric_operation", "numeric", "arithmetic", "calculation", "operator",
        "數值運算", "算術運算", "數值", "運算",
    },
    "conditional": {
        "conditional", "condition", "if", "if_else", "elif", "branch",
        "條件判斷", "條件", "分支",
    },
    "count_controlled_loop": {
        "count_controlled_loop", "for", "for_loop", "range", "loop_count_control",
        "定數迴圈", "計數迴圈", "巢狀迴圈",
    },
    "condition_controlled_loop": {
        "condition_controlled_loop", "while", "while_loop", "sentinel",
        "不定數迴圈", "條件控制迴圈",
    },
    "list_processing": {
        "list_processing", "list", "append", "traversal", "串列", "列表",
    },
}

# Deterministic role-to-relation rules.  Explicit relation_types_by_error on a
# task block always takes precedence; these rules are a backward-compatible
# fallback for existing questions that only contain code/role metadata.
_RELATION_TYPE_RULES = {
    "input_output": {
        "order": {
            "input_read": "input_before_use",
            "typed_input": "type_conversion_before_numeric_use",
            "initialization": "input_before_processing",
            "arithmetic_computation": "processing_before_output",
            "result_output": "processing_before_output",
        },
        "indentation": {
            "input_read": "input_output_scope_consistency",
            "typed_input": "input_output_scope_consistency",
            "result_output": "input_output_scope_consistency",
        },
    },
    "numeric_operation": {
        "order": {
            "input_read": "operands_before_computation",
            "typed_input": "operands_before_computation",
            "initialization": "initialization_before_accumulation",
            "arithmetic_computation": "operands_before_computation",
            "accumulator_update": "initialization_before_accumulation",
            "result_output": "computation_before_result_use",
        },
        "indentation": {
            "arithmetic_computation": "numeric_operation_scope_consistency",
            "accumulator_update": "numeric_operation_scope_consistency",
            "result_output": "numeric_operation_scope_consistency",
        },
    },
    "conditional": {
        "order": {
            "input_read": "value_before_condition_check",
            "typed_input": "value_before_condition_check",
            "initialization": "value_before_condition_check",
            "condition_header": "value_before_condition_check",
            "elif_header": "conditional_chain_order",
            "else_header": "conditional_chain_order",
            "branch_action": "condition_before_branch_action",
            "result_output": "condition_before_branch_action",
        },
        "indentation": {
            "branch_action": "branch_action_inside_condition",
            "result_output": "branch_action_inside_condition",
            "elif_header": "conditional_chain_scope",
            "else_header": "conditional_chain_scope",
        },
    },
    "count_controlled_loop": {
        "order": {
            "initialization": "initialization_before_loop",
            "for_header": "iterable_before_loop",
            "loop_body_action": "loop_header_before_body",
            "accumulator_update": "accumulator_update_inside_loop",
            "result_output": "result_output_after_loop",
        },
        "indentation": {
            "loop_body_action": "loop_body_inside_for",
            "accumulator_update": "accumulator_update_inside_loop",
            "result_output": "result_output_outside_completed_loop",
            "for_header": "nested_loop_scope_consistency",
        },
    },
    "condition_controlled_loop": {
        "order": {
            "initialization": "condition_state_before_loop",
            "input_read": "condition_state_before_loop",
            "typed_input": "condition_state_before_loop",
            "while_header": "condition_state_before_loop",
            "loop_body_action": "loop_header_before_body",
            "state_update": "state_update_before_next_condition_check",
            "break_action": "break_after_termination_check",
            "result_output": "result_output_after_loop",
        },
        "indentation": {
            "loop_body_action": "loop_body_inside_while",
            "state_update": "state_update_inside_loop",
            "break_action": "break_inside_loop",
            "result_output": "result_output_outside_completed_loop",
        },
    },
    "list_processing": {
        "order": {
            "list_initialization": "list_initialization_before_use",
            "input_read": "item_before_append",
            "typed_input": "item_before_append",
            "append_item": "item_before_append",
            "for_header": "traversal_after_list_ready",
            "loop_body_action": "traversal_after_list_ready",
            "aggregation_update": "aggregation_inside_traversal",
            "accumulator_update": "aggregation_inside_traversal",
            "result_output": "result_output_after_traversal",
        },
        "indentation": {
            "append_item": "append_inside_collection_loop",
            "loop_body_action": "loop_body_inside_traversal",
            "aggregation_update": "aggregation_inside_traversal",
            "accumulator_update": "aggregation_inside_traversal",
            "result_output": "result_output_outside_traversal",
        },
    },
}


def _normalize_unit_category(value):
    raw = str(value or "").strip().lower()
    if not raw:
        return "unknown"
    normalized = re.sub(r"[^a-z0-9_\u4e00-\u9fff]+", "_", raw).strip("_")
    for category, aliases in _UNIT_CATEGORY_ALIASES.items():
        if normalized == category or raw == category:
            return category
        if raw in aliases or normalized in aliases:
            return category
    return "unknown"


def _infer_unit_category(task, concept_tags=None):
    doc = task if isinstance(task, dict) else {}
    explicit = _normalize_unit_category(
        doc.get("unit_category") or doc.get("course_category") or doc.get("relation_category")
    )
    if explicit != "unknown":
        return explicit

    parts = [
        doc.get("unit_type"), doc.get("unit"), doc.get("target_concept"),
        doc.get("task_family"), doc.get("family"), doc.get("control_structure"),
        doc.get("question_text"), doc.get("question"),
    ]
    parts.extend(concept_tags or [])
    haystack = " ".join(str(item or "").lower() for item in parts)

    # Specific structures must be checked before generic condition/input words.
    ordered_checks = [
        ("list_processing", ("list", "append", "串列", "列表", "traversal")),
        ("condition_controlled_loop", ("while", "不定數迴圈", "條件控制迴圈", "sentinel")),
        ("count_controlled_loop", ("for_loop", "for ", "range", "定數迴圈", "計數迴圈", "巢狀迴圈")),
        ("conditional", ("if_else", "if_condition", "elif", "else", "條件判斷", "分支")),
        ("numeric_operation", ("numeric", "arithmetic", "calculation", "數值運算", "算術運算")),
        ("input_output", ("input_output", "輸入輸出", "print_separator", "input_int_cast")),
    ]
    for category, tokens in ordered_checks:
        if any(token in haystack for token in tokens):
            return category
    return "unknown"


def _infer_control_structure(task, unit_category=None):
    doc = task if isinstance(task, dict) else {}
    explicit = str(doc.get("control_structure") or "").strip().lower()
    if explicit:
        if "while" in explicit:
            return "while"
        if "for" in explicit or "range" in explicit:
            return "for"
        if explicit in {"if", "if_else", "conditional", "condition"} or "if" in explicit:
            return "conditional"
        if "list" in explicit:
            return "list"
        return explicit
    category = _normalize_unit_category(unit_category)
    return {
        "condition_controlled_loop": "while",
        "count_controlled_loop": "for",
        "conditional": "conditional",
        "list_processing": "list",
        "numeric_operation": "sequence",
        "input_output": "sequence",
    }.get(category, "unknown")


def _infer_block_role_for_relation(block, unit_category=None):
    doc = block if isinstance(block, dict) else {}
    explicit = str(doc.get("block_role") or doc.get("role") or "").strip()
    if explicit:
        return explicit

    text = str(doc.get("text") or "").strip()
    semantic = " ".join(
        str(doc.get(key) or "") for key in ("semantic_zh", "meaning_zh", "expected_meaning_zh")
    )
    low = text.lower()
    category = _normalize_unit_category(unit_category)

    if re.match(r"^\s*break\b", low):
        return "break_action"
    if re.match(r"^\s*elif\b", low):
        return "elif_header"
    if re.match(r"^\s*else\s*:", low):
        return "else_header"
    if re.match(r"^\s*if\b", low):
        return "condition_header"
    if re.match(r"^\s*for\b", low):
        return "for_header"
    if re.match(r"^\s*while\b", low):
        return "while_header"
    if ".append(" in low:
        return "append_item"
    if re.search(r"=\s*\[\s*\]", low) or "建立空串列" in semantic:
        return "list_initialization"
    if "input(" in low:
        if re.search(r"\b(?:int|float)\s*\(\s*input\s*\(", low):
            return "typed_input"
        return "input_read"
    if re.match(r"^\s*print\s*\(", low):
        return "result_output"
    if category == "condition_controlled_loop" and re.search(r"(?:\+=|-=|\*=|/=|//=|%=|\b=\b)", low):
        if any(token in semantic for token in ("更新", "遞增", "遞減", "重新輸入", "控制變數")) or re.search(r"(?:\+=|-=)", low):
            return "state_update"
    if category == "list_processing" and any(token in low for token in ("max(", "min(", "sum(", "len(")):
        return "aggregation_update"
    if re.search(r"(?:\+=|-=|\*=|/=|//=|%=)", low):
        return "accumulator_update"
    if re.search(r"^[A-Za-z_]\w*\s*=", low):
        if re.search(r"[+\-*/%]", low.split("=", 1)[-1]):
            return "arithmetic_computation"
        return "initialization"
    if category in {"count_controlled_loop", "condition_controlled_loop", "list_processing"}:
        return "loop_body_action"
    if category == "conditional":
        return "branch_action"
    return "unknown"


def _relation_type_from_rule(unit_category, block_role, error_type):
    category = _normalize_unit_category(unit_category)
    role = str(block_role or "").strip()
    normalized_error = _hint_library_error_type(error_type)
    if normalized_error not in {"order", "indentation"}:
        return None
    return (
        _RELATION_TYPE_RULES.get(category, {})
        .get(normalized_error, {})
        .get(role)
    )


def _structured_hint_fingerprint_key(*, unit_category, control_structure, concept_tag, error_type, relation_type, hint_level, scope):
    values = [
        "v2",
        _normalize_unit_category(unit_category),
        str(control_structure or "unknown").strip().lower() or "unknown",
        normalize_concept_name(concept_tag) or "unknown",
        _hint_library_error_type(error_type),
        str(relation_type or "unknown").strip() or "unknown",
        f"level_{_hint_library_level(hint_level)}",
        str(scope or "broad").strip().lower() or "broad",
    ]
    return "|".join(values)


def ensure_parsons_attempts_v2_indexes():
    """Create indexes for per-session Parsons attempt analysis."""
    global _PARSONS_ATTEMPTS_V2_INDEX_READY
    if _PARSONS_ATTEMPTS_V2_INDEX_READY:
        return

    try:
        try:
            if "parsons_attempts_v2" not in db.list_collection_names():
                db.create_collection("parsons_attempts_v2")
        except Exception:
            pass

        for field in [
            "student_id",
            "class_name",
            "group_type",
            "feedback_strategy",
            "activity_type",
            "test_role",
            "test_cycle_id",
            "task_id",
            "target_concept",
            "task_attempt_session",
            "attempt_no",
            "attempt_sequence_no",
            "submitted_at",
            "is_correct",
        ]:
            db.parsons_attempts_v2.create_index([(field, 1)], name=f"{field}_1")

        # 舊索引保留相容性；新索引明確把「第幾次完整作答場次」納入。
        db.parsons_attempts_v2.create_index(
            [
                ("student_id", 1),
                ("task_id", 1),
                ("activity_type", 1),
                ("test_role", 1),
                ("task_attempt_session", 1),
                ("attempt_no", 1),
            ],
            name="student_task_role_session_attempt_1",
        )
        db.parsons_attempts_v2.create_index(
            [
                ("student_id", 1),
                ("task_id", 1),
                ("attempt_sequence_no", 1),
            ],
            name="student_task_sequence_1",
        )
        _PARSONS_ATTEMPTS_V2_INDEX_READY = True
    except Exception as e:
        import traceback
        print("\n========== PARSONS ATTEMPTS V2 INDEX ERROR ==========")
        print("error =", repr(e))
        traceback.print_exc()
        print("=====================================================\n")
        raise

# 防止提示紀錄重複建立
def ensure_parsons_hint_record_indexes():
    """Create indexes for per-student per-task-per-round Parsons hint state."""
    global _PARSONS_HINT_RECORD_INDEX_READY
    if _PARSONS_HINT_RECORD_INDEX_READY:
        return
    try:
        try:
            info = db.parsons_hint_records.index_information()
            legacy_unique = info.get("student_task_unique") or {}
            if legacy_unique.get("unique"):
                db.parsons_hint_records.drop_index("student_task_unique")
        except Exception as e:
            print(f"[parsons_hint_records] legacy unique index drop skipped: {e}")
        db.parsons_hint_records.update_many(
            {"task_attempt_session": {"$exists": False}},
            {"$set": {"task_attempt_session": 1}},
        )
        db.parsons_hint_records.create_index(
            [("student_id", 1), ("task_id", 1), ("task_attempt_session", 1)],
            name="student_task_session_unique",
            unique=True,
        )
        db.parsons_hint_records.create_index([("student_id", 1)], name="student_id_1")
        db.parsons_hint_records.create_index([("task_id", 1)], name="task_id_1")
        db.parsons_hint_records.create_index([("task_attempt_session", 1)], name="task_attempt_session_1")
        db.parsons_hint_records.create_index([("hint_id", 1)], name="hint_id_1")
        db.parsons_hint_records.create_index([("updated_at", -1)], name="updated_at_-1")
        _PARSONS_HINT_RECORD_INDEX_READY = True
    except Exception as e:
        print(f"[parsons_hint_records] index ensure failed: {e}")

# ========================
# 保存可重用的 hint_library

def ensure_hint_library_indexes():
    """Create relation-aware indexes for reusable Parsons hint templates."""
    global _PARSONS_HINT_LIBRARY_INDEX_READY
    if _PARSONS_HINT_LIBRARY_INDEX_READY:
        return

    def _create(keys, *, name, **kwargs):
        try:
            db.hint_library.create_index(keys, name=name, **kwargs)
        except Exception as exc:
            # One failed optional index must not disable the whole hint system.
            print(f"[hint_library] index {name} skipped: {exc}")

    try:
        # Legacy indexes are retained so old documents remain inspectable.
        _create([("hint_key", 1), ("version", -1)], name="hint_key_version_1")
        _create(
            [("fingerprint_key", 1), ("version", -1)],
            name="structured_fingerprint_version_unique",
            unique=True,
            partialFilterExpression={
                "schema_version": {"$gte": 2},
                "fingerprint_key": {"$type": "string"},
            },
        )
        _create(
            [
                ("unit_category", 1),
                ("control_structure", 1),
                ("concept_tag", 1),
                ("error_type", 1),
                ("relation_type", 1),
                ("hint_level", 1),
                ("scope", 1),
                ("language", 1),
                ("is_active", 1),
                ("quality_status", 1),
                ("answer_leakage_check", 1),
            ],
            name="structured_relation_lookup_1",
        )
        _create(
            [
                ("unit_category", 1),
                ("relation_type", 1),
                ("error_type", 1),
                ("hint_level", 1),
                ("is_active", 1),
            ],
            name="category_relation_level_active_1",
        )
        _create(
            [("task_scope", 1), ("task_family", 1), ("relation_type", 1), ("is_active", 1)],
            name="task_scope_family_relation_active_1",
        )
        _create([("updated_at", -1)], name="updated_at_-1")
        _PARSONS_HINT_LIBRARY_INDEX_READY = True
    except Exception as exc:
        print(f"[hint_library] index ensure failed: {exc}")



def _clean_string(value):
    text = str(value or "").strip()
    return text or None


def _normalize_feedback_strategy(value):
    text = str(value or "").strip()
    if not text:
        return None
    upper = text.upper()
    if upper in {"A", "B", "C"}:
        return upper

    normalized = re.sub(r"[^A-Z0-9]+", "_", upper)
    for token in normalized.split("_"):
        if token in {"A", "B", "C"}:
            return token
    return None


def _feedback_strategy_from_user(user):
    data = user if isinstance(user, dict) else {}
    return (
        _normalize_feedback_strategy(data.get("feedback_strategy"))
        or _normalize_feedback_strategy(data.get("group_type"))
        or "B"
    )


def _student_feedback_profile(student_id):
    sid = str(student_id or "").strip()
    if not sid:
        return {"feedback_strategy": "B", "group_type": None}
    user = db.users.find_one(
        {"student_id": sid},
        {"group_type": 1, "feedback_strategy": 1},
    ) or {}
    return {
        "group_type": _clean_string(user.get("group_type")),
        "feedback_strategy": _feedback_strategy_from_user(user),
    }


def _int_list(value):
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        try:
            number = int(item)
        except Exception:
            continue
        if number >= 0:
            out.append(number)
    return sorted(set(out))


def _hint_error_types(v2_doc=None, attempt_doc=None, primary_error_type=None):
    v2_doc = v2_doc if isinstance(v2_doc, dict) else {}
    attempt_doc = attempt_doc if isinstance(attempt_doc, dict) else {}
    error_types = v2_doc.get("error_types")
    if isinstance(error_types, list) and error_types:
        return [str(item) for item in error_types if str(item or "").strip()]
    if _int_list(v2_doc.get("wrong_slots")) or _int_list(attempt_doc.get("wrong_indices")):
        guessed = ["sequence_error"]
        if _int_list(attempt_doc.get("indent_errors")):
            guessed.append("indentation_error")
        return guessed
    primary = str(primary_error_type or attempt_doc.get("primary_error_type") or attempt_doc.get("error_type") or "").strip()
    if primary == "indentation":
        return ["indentation_error"]
    if primary:
        return ["sequence_error"]
    return []


def _first_system_hint_text(wrong_slots, error_types):
    positions = _int_list(wrong_slots)
    position_text = "、".join([f"第 {idx + 1} 格" for idx in positions]) if positions else "紅色標記的位置"
    labels = []
    if "sequence_error" in (error_types or []):
        labels.append("結構順序錯誤")
    if "indentation_error" in (error_types or []):
        labels.append("縮排錯誤")
    label_text = "、".join(labels) if labels else "結構順序錯誤"
    return f"{position_text}有錯誤（{label_text}）。請先檢查紅色標記的位置，重新確認程式區塊的順序或結構。"


def _system_recheck_text(wrong_slots, error_types, attempt_no=None):
    """Build the system-only message used from the third wrong submission onward."""
    positions = _int_list(wrong_slots)
    position_text = "、".join([f"第 {idx + 1} 格" for idx in positions]) if positions else "紅色標記的位置"
    labels = []
    if "sequence_error" in (error_types or []):
        labels.append("結構順序錯誤")
    if "indentation_error" in (error_types or []):
        labels.append("縮排錯誤")
    label_text = "、".join(labels) if labels else "待調整"
    count = len(positions)
    # attempt_label = f"第 {int(attempt_no)} 次作答" if attempt_no else "本次作答"
    return (
        f"目前仍有 {count} 格需要調整。"
        f"錯誤位置為{position_text}（{label_text}）。"
        # "請依照紅色標記重新確認程式區塊的順序與縮排。"
    )

# parsons_hint_records
# 保存同一位學生、同一道題、目前作答場次的提示狀態
# 第一次系統提示
# 第一次 AI 提示
# 第二次 AI 提示
# 兩次提示各自的 metadata
# 提示產生次數
# 提示查看次數
def _empty_hint_record(student_id, task_id, group_type=None, task_attempt_session=1):
    now = now_utc()
    try:
        session_no = max(1, int(task_attempt_session or 1))
    except Exception:
        session_no = 1
    return {
        "hint_id": str(uuid.uuid4()),
        "student_id": student_id,
        "task_id": task_id,
        "task_attempt_session": session_no,
        "hint_prompt_version": _PARSONS_HINT_PROMPT_VERSION,
        "group_type": group_type,
        "first_hint_shown": False,
        "first_system_hint_text": None,
        "first_error_positions": [],
        "first_error_types": [],
        "second_error_positions": [],
        "second_error_types": [],
        "latest_error_positions": [],
        "latest_error_types": [],
        "latest_error_count": 0,
        "latest_attempt_v2_id": None,
        "ai_hint_1_text": None,
        "ai_hint_1_meta": {},
        "ai_hint_2_text": None,
        "ai_hint_2_meta": {},
        "hint_generation_count": 0,
        "hint_view_count": 0,
        "ai_hint_generation_count": 0,
        "ai_hint_view_count": 0,
        "latest_ai_hint_no": 0,
        "timezone": "Asia/Taipei",
        "created_at": now,
        "created_at_utc": now,
        "created_at_taiwan": _taiwan_time_string(now),
        "updated_at": now,
        "updated_at_utc": now,
        "updated_at_taiwan": _taiwan_time_string(now),
    }


def _hint_session_no(value, default=1):
    try:
        parsed = int(value)
        return parsed if parsed >= 1 else default
    except Exception:
        return default


def _get_hint_record(student_id, task_id, task_attempt_session=None):
    if not student_id or not task_id:
        return None
    ensure_parsons_hint_record_indexes()
    query = {
        "student_id": str(student_id),
        "task_id": str(task_id),
    }
    if task_attempt_session is not None:
        query["task_attempt_session"] = _hint_session_no(task_attempt_session)
        return db.parsons_hint_records.find_one(query)
    return db.parsons_hint_records.find_one(
        query,
        sort=[("task_attempt_session", -1), ("updated_at", -1), ("_id", -1)],
    )


def _set_hint_record(student_id, task_id, set_fields=None, set_on_insert=None, inc_fields=None):
    if not student_id or not task_id:
        return None
    ensure_parsons_hint_record_indexes()
    now = now_utc()
    set_fields = set_fields or {}
    set_on_insert = set_on_insert or {}
    session_no = _hint_session_no(
        set_fields.get("task_attempt_session")
        or set_on_insert.get("task_attempt_session")
        or 1
    )
    set_payload = {
        **(set_fields or {}),
        "task_attempt_session": session_no,
        "updated_at": now,
        "updated_at_utc": now,
        "updated_at_taiwan": _taiwan_time_string(now),
        "timezone": "Asia/Taipei",
    }
    insert_payload = {
        **_empty_hint_record(student_id, task_id, task_attempt_session=session_no),
        **(set_on_insert or {}),
    }
    for key in set_payload:
        insert_payload.pop(key, None)
    for key in (inc_fields or {}):
        insert_payload.pop(key, None)
    update = {
        "$set": set_payload,
        "$setOnInsert": insert_payload,
    }
    if inc_fields:
        update["$inc"] = inc_fields
    query = {
        "student_id": str(student_id),
        "task_id": str(task_id),
        "task_attempt_session": session_no,
    }
    try:
        db.parsons_hint_records.update_one(
            query,
            update,
            upsert=True,
        )
    except DuplicateKeyError:
        db.parsons_hint_records.update_one(
            query,
            update,
            upsert=True,
        )
    return _get_hint_record(student_id, task_id, session_no)


def _ensure_first_hint_record(
    student_id,
    task_id,
    *,
    group_type=None,
    wrong_slots=None,
    error_types=None,
    task_attempt_session=1,
):
    positions = _int_list(wrong_slots)
    types = [str(item) for item in (error_types or []) if str(item or "").strip()]
    first_text = _first_system_hint_text(positions, types)
    try:
        session_no = max(1, int(task_attempt_session or 1))
    except Exception:
        session_no = 1

    record = _get_hint_record(student_id, task_id, session_no)
    # 提示限制以 student_id + task_id 為單位。重新整理、退出或重新進入
    # 不能因 task_attempt_session 變動而清掉已產生的 AI 提示。
    if record and record.get("first_system_hint_text"):
        return record

    return _set_hint_record(
        student_id,
        task_id,
        set_fields={
            "task_attempt_session": session_no,
            "group_type": group_type,
            "first_hint_shown": True,
            "first_system_hint_text": first_text,
            "first_error_positions": positions,
            "first_error_types": types,
        },
    )

# AI 提示 metadata
# 提示紀錄的公開資訊組裝
def _hint_record_public(record):
    if not isinstance(record, dict):
        return None
    return {
        "hint_id": record.get("hint_id"),
        "student_id": record.get("student_id"),
        "task_id": record.get("task_id"),
        "task_attempt_session": int(record.get("task_attempt_session") or 1),
        "hint_prompt_version": record.get("hint_prompt_version") or "legacy",
        "group_type": record.get("group_type"),
        "first_hint_shown": bool(record.get("first_hint_shown")),
        "first_system_hint_text": record.get("first_system_hint_text"),
        "first_error_positions": record.get("first_error_positions") or [],
        "first_error_types": record.get("first_error_types") or [],
        "second_error_positions": record.get("second_error_positions") or [],
        "second_error_types": record.get("second_error_types") or [],
        "latest_error_positions": record.get("latest_error_positions") or [],
        "latest_error_types": record.get("latest_error_types") or [],
        "latest_error_count": int(record.get("latest_error_count") or 0),
        "latest_attempt_v2_id": record.get("latest_attempt_v2_id"),
        "ai_hint_1_text": record.get("ai_hint_1_text"),
        "ai_hint_1_meta": record.get("ai_hint_1_meta") if isinstance(record.get("ai_hint_1_meta"), dict) else {},
        "ai_hint_2_text": record.get("ai_hint_2_text"),
        "ai_hint_2_meta": record.get("ai_hint_2_meta") if isinstance(record.get("ai_hint_2_meta"), dict) else {},
        "hint_generation_count": int(record.get("hint_generation_count") or record.get("ai_hint_generation_count") or 0),
        "hint_view_count": int(record.get("hint_view_count") or record.get("ai_hint_view_count") or 0),
        "ai_hint_generation_count": int(record.get("ai_hint_generation_count") or record.get("hint_generation_count") or 0),
        "ai_hint_view_count": int(record.get("ai_hint_view_count") or record.get("hint_view_count") or 0),
        "latest_ai_hint_no": int(record.get("latest_ai_hint_no") or 0),
        "last_attempt_id": record.get("last_attempt_id"),
        "timezone": record.get("timezone") or "Asia/Taipei",
        "created_at_taiwan": record.get("created_at_taiwan"),
        "updated_at_taiwan": record.get("updated_at_taiwan"),
    }

# 提示紀錄的 metadata 組裝
def _hint_log_metadata(record, *, requested_hint_no=None, error_types=None, wrong_slots=None, repeated_error=None, extra=None):
    public = _hint_record_public(record) or {}
    hint_no = requested_hint_no if requested_hint_no in {1, 2} else public.get("latest_ai_hint_no")
    hint_text = public.get("ai_hint_2_text") if hint_no == 2 else public.get("ai_hint_1_text")
    hint_meta = public.get("ai_hint_2_meta") if hint_no == 2 else public.get("ai_hint_1_meta")
    if not isinstance(hint_meta, dict):
        hint_meta = {}
    subtitle_range = hint_meta.get("subtitle_range") if isinstance(hint_meta.get("subtitle_range"), dict) else {}
    metadata_wrong_slots = _int_list(wrong_slots or hint_meta.get("wrong_slots") or [])
    concept_tags = [
        normalize_concept_name(item)
        for item in (hint_meta.get("concept_tags") or [])
        if normalize_concept_name(item)
    ]
    if not concept_tags and hint_meta.get("concept_tag"):
        concept_tags = [normalize_concept_name(hint_meta.get("concept_tag"))]
    concept_scopes = [
        str(item or "").strip()
        for item in (hint_meta.get("concept_scopes") or [])
        if str(item or "").strip()
    ]
    if not concept_scopes and hint_meta.get("concept_scope"):
        concept_scopes = [str(hint_meta.get("concept_scope")).strip()]
    metadata = {
        "review_type": "ai_hint",
        "hint_type": "ai_hint",
        "hint_id": public.get("hint_id"),
        "task_attempt_session": int(public.get("task_attempt_session") or 1),
        "hint_prompt_version": public.get("hint_prompt_version") or "legacy",
        "requested_hint_no": hint_no,
        "hint_no": hint_no,
        "hint_click_no": hint_no,
        "max_hint_count": 2,
        "hint_generation_count": public.get("hint_generation_count", 0),
        "hint_view_count": public.get("hint_view_count", 0),
        "ai_hint_generation_count": public.get("ai_hint_generation_count", public.get("hint_generation_count", 0)),
        "ai_hint_view_count": public.get("ai_hint_view_count", public.get("hint_view_count", 0)),
        "first_system_hint_text": public.get("first_system_hint_text"),
        "ai_hint_1_text": public.get("ai_hint_1_text"),
        "ai_hint_2_text": public.get("ai_hint_2_text"),
        "ai_hint_1_meta": public.get("ai_hint_1_meta") or {},
        "ai_hint_2_meta": public.get("ai_hint_2_meta") or {},
        "hint_text": hint_text,
        "hint_content": hint_text,
        "hint_level": hint_meta.get("hint_level"),
        "scope": hint_meta.get("scope"),
        "aggregation_mode": hint_meta.get("aggregation_mode"),
        "concept_tag": hint_meta.get("concept_tag"),
        "concept_scope": hint_meta.get("concept_scope"),
        "concept_tags": concept_tags,
        "concept_scopes": concept_scopes,
        "concept_count": int(hint_meta.get("concept_count") or len(concept_tags) or 0),
        "wrong_index": hint_meta.get("wrong_index"),
        "wrong_slot_count": int(hint_meta.get("wrong_slot_count") or len(metadata_wrong_slots)),
        "subtitle_range": subtitle_range,
        "subtitle_range_available": _hint_subtitle_range_available(subtitle_range),
        "hint_source": hint_meta.get("hint_source") or "parsons_hint_records",
        "answer_leakage_check": hint_meta.get("answer_leakage_check"),
        "hint_quality_status": hint_meta.get("hint_quality_status"),
        "second_hint_similarity": hint_meta.get("second_hint_similarity"),
        "second_hint_similarity_threshold": hint_meta.get("second_hint_similarity_threshold"),
        "hint_loaded": bool(hint_text),
        "repeated_error": repeated_error if isinstance(repeated_error, bool) else None,
        "error_types": error_types if isinstance(error_types, list) else [],
        "wrong_slots": metadata_wrong_slots,
    }
    if isinstance(extra, dict):
        metadata.update(extra)
    return metadata


def _hint_float_or_none(value):
    try:
        number = float(value)
    except Exception:
        return None
    if not math.isfinite(number):
        return None
    return number


def _hint_range_payload(value, *, fallback_source=""):
    source = str(fallback_source or "").strip()
    if isinstance(value, dict):
        start = _hint_float_or_none(value.get("start"))
        end = _hint_float_or_none(value.get("end"))
        source = str(value.get("source") or source or "").strip()
    else:
        start = None
        end = None
    if start is None or end is None or start < 0 or end <= start:
        return {"start": None, "end": None, "source": source}
    return {
        "start": round(start, 2),
        "end": round(end, 2),
        "source": source,
    }


def _hint_subtitle_range_available(value):
    if not isinstance(value, dict):
        return False
    start = _hint_float_or_none(value.get("start"))
    end = _hint_float_or_none(value.get("end"))
    return bool(start is not None and end is not None and start >= 0 and end > start)


def _attempt_wrong_positions_for_hint(att):
    if not isinstance(att, dict):
        return []
    candidates = []
    for key in ("incorrect_slots", "wrong_indices", "wrong_slots"):
        values = att.get(key)
        if isinstance(values, list):
            candidates.extend(values)
        elif values is not None:
            candidates.append(values)
    if not candidates:
        for key in ("wrong_index", "primary_wrong_index"):
            if att.get(key) is not None:
                candidates.append(att.get(key))

    output = []
    seen = set()
    for item in candidates:
        try:
            number = int(item)
        except Exception:
            continue
        if number < 0 or number in seen:
            continue
        seen.add(number)
        output.append(number)
    return output

def _hint_library_error_type(value):
    raw = str(value or "").strip().lower()
    mapping = {
        "sequence_error": "order",
        "order_error": "order",
        "order": "order",
        "indentation_error": "indentation",
        "indentation": "indentation",
        "condition": "condition",
        "calculation": "calculation",
        "structure": "structure",
        "logic": "logic",
    }
    return mapping.get(raw, raw or "unknown")


def _hint_library_level(value=None, hint_key=None):
    key = str(hint_key or "").strip().lower()
    if "level_2" in key or key.endswith("|2"):
        return 2
    if "level_1" in key or key.endswith("|1"):
        return 1
    try:
        return 2 if int(value or 1) == 2 else 1
    except Exception:
        return 1



def _hint_library_context_from_detail(aggregate_detail, level):
    detail = aggregate_detail if isinstance(aggregate_detail, dict) else {}
    hint_level = _hint_library_level(level)
    scope = "narrow" if hint_level == 2 else "broad"

    task_context = detail.get("task_context") if isinstance(detail.get("task_context"), dict) else {}
    unit_category = _normalize_unit_category(
        detail.get("unit_category") or task_context.get("unit_category")
    )
    if unit_category == "unknown":
        unit_category = _infer_unit_category(task_context, detail.get("concept_tags") or [])
    control_structure = str(
        detail.get("control_structure")
        or task_context.get("control_structure")
        or _infer_control_structure(task_context, unit_category)
        or "unknown"
    ).strip().lower() or "unknown"

    concept_tags = []
    for item in (detail.get("concept_tags") or []):
        tag = normalize_concept_name(item)
        if tag and tag != "unknown" and tag not in concept_tags:
            concept_tags.append(tag)
    if not concept_tags:
        for item in (detail.get("error_concepts") or []):
            if not isinstance(item, dict):
                continue
            tag = normalize_concept_name(item.get("concept_tag") or "")
            if tag and tag != "unknown" and tag not in concept_tags:
                concept_tags.append(tag)
    concept_tag = concept_tags[0] if concept_tags else "unknown"

    error_types = []
    for concept in (detail.get("error_concepts") or []):
        if not isinstance(concept, dict):
            continue
        for candidate in (concept.get("error_types") or []):
            normalized = _hint_library_error_type(candidate)
            if normalized and normalized != "unknown" and normalized not in error_types:
                error_types.append(normalized)
    if not error_types:
        for slot in (detail.get("wrong_slot_details") or []):
            if not isinstance(slot, dict):
                continue
            for candidate in (slot.get("error_types") or []):
                normalized = _hint_library_error_type(candidate)
                if normalized and normalized != "unknown" and normalized not in error_types:
                    error_types.append(normalized)
    error_type = error_types[0] if error_types else "unknown"

    relation_types = []
    for value in (detail.get("relation_types") or []):
        relation = str(value or "").strip()
        if relation and relation != "unknown" and relation not in relation_types:
            relation_types.append(relation)
    if not relation_types:
        for concept in (detail.get("error_concepts") or []):
            if not isinstance(concept, dict):
                continue
            for value in (concept.get("relation_types") or []):
                relation = str(value or "").strip()
                if relation and relation != "unknown" and relation not in relation_types:
                    relation_types.append(relation)
    relation_type = relation_types[0] if len(relation_types) == 1 else None

    expected_roles = _hint_string_list(detail.get("expected_roles"))
    submitted_roles = _hint_string_list(detail.get("submitted_roles"))
    retrieval_eligible = bool(
        unit_category != "unknown"
        and concept_tag != "unknown"
        and len(error_types) == 1
        and relation_type
    )
    skip_reason = None
    if unit_category == "unknown":
        skip_reason = "unknown_unit_category"
    elif concept_tag == "unknown":
        skip_reason = "unknown_concept_tag"
    elif len(error_types) != 1:
        skip_reason = "multiple_or_unknown_error_types"
    elif not relation_type:
        skip_reason = "multiple_or_unknown_relation_types"

    fingerprint_key = _structured_hint_fingerprint_key(
        unit_category=unit_category,
        control_structure=control_structure,
        concept_tag=concept_tag,
        error_type=error_type,
        relation_type=relation_type or "unknown",
        hint_level=hint_level,
        scope=scope,
    )
    return {
        "schema_version": _HINT_LIBRARY_SCHEMA_VERSION,
        "unit_category": unit_category,
        "control_structure": control_structure,
        "concept_tag": concept_tag,
        "concept_tags": concept_tags,
        "error_type": error_type,
        "error_types": error_types,
        "relation_type": relation_type,
        "relation_types": relation_types,
        "expected_roles": expected_roles,
        "submitted_roles": submitted_roles,
        "hint_level": hint_level,
        "scope": scope,
        "fingerprint_key": fingerprint_key,
        "hint_key": fingerprint_key,
        "legacy_hint_key": f"{concept_tag}|{error_type}|level_{hint_level}",
        "retrieval_eligible": retrieval_eligible,
        "retrieval_skip_reason": skip_reason,
    }



def _hint_library_task_family(task):
    doc = task if isinstance(task, dict) else {}
    return _clean_string(
        doc.get("task_family")
        or doc.get("family")
        or doc.get("target_concept")
    )



def _hint_library_concept_aliases(concept_tag, unit_category=None):
    """Return conservative aliases without crossing for/while/list categories."""
    tag = str(concept_tag or "").strip()
    normalized = normalize_concept_name(tag)
    category = _normalize_unit_category(unit_category)
    aliases = [tag, normalized]

    if category == "count_controlled_loop":
        aliases.extend(["for_loop", "loop_count_control"])
    elif category == "condition_controlled_loop":
        aliases.extend(["while_loop", "condition_controlled_loop"])
    elif category == "conditional":
        aliases.extend(["if", "condition", "if_condition_logic"])
    elif category == "input_output":
        aliases.extend(["input_output", "io", "input_int_cast", "print_separator"])
    elif category == "numeric_operation":
        aliases.extend(["numeric_operation", "arithmetic", "calculation", "operator"])
    elif category == "list_processing":
        aliases.extend(["list", "list_processing", "append", "traversal"])

    return [
        item
        for item in dict.fromkeys(str(alias or "").strip() for alias in aliases)
        if item
    ]




def _find_hint_library_entry(aggregate_detail, task, level):
    """Retrieve a passed hint using a structured error fingerprint."""
    ensure_hint_library_indexes()
    ctx = _hint_library_context_from_detail(aggregate_detail, level)
    if not ctx.get("retrieval_eligible"):
        return None, ctx

    task_family = _hint_library_task_family(task)
    aliases = _hint_library_concept_aliases(
        ctx.get("concept_tag"),
        ctx.get("unit_category"),
    )
    concept_filter = {"$in": aliases} if aliases else ctx["concept_tag"]
    base = {
        "schema_version": {"$gte": 2},
        "retrieval_eligible": True,
        "is_active": True,
        "quality_status": "passed",
        "answer_leakage_check": "passed",
        "language": "zh-TW",
        "hint_level": ctx["hint_level"],
        "unit_category": ctx["unit_category"],
        "relation_type": ctx["relation_type"],
        "error_type": ctx["error_type"],
    }

    queries = [
        (
            "exact_fingerprint",
            1.0,
            {**base, "fingerprint_key": ctx["fingerprint_key"]},
        ),
        (
            "same_relation_and_control",
            0.95,
            {
                **base,
                "control_structure": ctx["control_structure"],
                "concept_tag": concept_filter,
                "scope": ctx["scope"],
            },
        ),
    ]
    if task_family:
        queries.append((
            "same_task_family_relation",
            0.93,
            {
                **base,
                "task_scope": "task_family",
                "task_family": task_family,
                "concept_tag": concept_filter,
            },
        ))
    queries.append((
        "same_category_relation",
        0.90,
        {
            **base,
            "concept_tag": concept_filter,
            "scope": ctx["scope"],
        },
    ))

    for match_mode, score, query in queries:
        try:
            doc = db.hint_library.find_one(
                query,
                sort=[("success_count", -1), ("usage_count", -1), ("version", -1), ("updated_at", -1), ("_id", -1)],
            )
        except Exception as exc:
            print("[hint_library] structured lookup failed:", repr(exc))
            return None, ctx
        if doc and str(doc.get("hint_template") or "").strip():
            return doc, {
                **ctx,
                "retrieval_match_mode": match_mode,
                "retrieval_score": score,
            }

    # Optional compatibility fallback.  It is disabled by default because old
    # concept-only hints can mix unrelated program relations.
    legacy_enabled = str(
        os.getenv("PARSONS_LEGACY_HINT_LIBRARY_ENABLED", "0")
    ).strip().lower() in {"1", "true", "yes", "on"}
    if legacy_enabled:
        try:
            legacy = db.hint_library.find_one(
                {
                    "is_active": True,
                    "quality_status": "passed",
                    "answer_leakage_check": "passed",
                    "language": "zh-TW",
                    "hint_level": ctx["hint_level"],
                    "hint_key": ctx["legacy_hint_key"],
                },
                sort=[("version", -1), ("updated_at", -1), ("_id", -1)],
            )
        except Exception as exc:
            print("[hint_library] legacy lookup failed:", repr(exc))
            legacy = None
        if legacy and str(legacy.get("hint_template") or "").strip():
            return legacy, {
                **ctx,
                "retrieval_match_mode": "legacy_concept_only",
                "retrieval_score": 0.50,
            }
    return None, ctx




def _mark_hint_library_used(doc):
    if not isinstance(doc, dict) or doc.get("_id") is None:
        return
    now = now_utc()
    try:
        db.hint_library.update_one(
            {"_id": doc["_id"]},
            {
                "$inc": {"usage_count": 1, "match_count": 1},
                "$set": {
                    "last_used_at": now,
                    "last_matched_at": now,
                    "updated_at": now,
                },
            },
        )
    except Exception as exc:
        print("[hint_library] usage update failed:", repr(exc))




def _hint_library_public_fields(doc, ctx):
    if not isinstance(doc, dict):
        return {}
    context = ctx if isinstance(ctx, dict) else {}
    try:
        version = int(doc.get("version") or 1)
    except Exception:
        version = 1
    return {
        "hint_library_id": str(doc.get("_id") or ""),
        "hint_library_key": str(doc.get("hint_key") or context.get("hint_key") or "").strip(),
        "hint_library_fingerprint_key": str(doc.get("fingerprint_key") or context.get("fingerprint_key") or "").strip() or None,
        "hint_library_version": version,
        "hint_library_schema_version": int(doc.get("schema_version") or context.get("schema_version") or 1),
        "hint_library_unit_category": str(doc.get("unit_category") or context.get("unit_category") or "").strip() or None,
        "hint_library_control_structure": str(doc.get("control_structure") or context.get("control_structure") or "").strip() or None,
        "hint_library_concept_tag": str(doc.get("concept_tag") or context.get("concept_tag") or "").strip(),
        "hint_library_error_type": str(doc.get("error_type") or context.get("error_type") or "").strip(),
        "hint_library_relation_type": str(doc.get("relation_type") or context.get("relation_type") or "").strip() or None,
        "hint_library_relation_types": _hint_string_list(doc.get("relation_types") or context.get("relation_types")),
        "hint_library_expected_roles": _hint_string_list(doc.get("expected_roles") or context.get("expected_roles")),
        "hint_library_task_scope": str(doc.get("task_scope") or "").strip(),
        "hint_library_task_family": doc.get("task_family"),
        "retrieval_match_mode": context.get("retrieval_match_mode"),
        "retrieval_score": _hint_float_or_none(context.get("retrieval_score")),
        "source_model": doc.get("source_model"),
        "source_prompt_version": doc.get("source_prompt_version"),
        "source_hint_id": doc.get("source_hint_id"),
        "source_task_id": doc.get("source_task_id"),
    }




def _hint_library_status_fields(ctx, status, reason=None):
    context = ctx if isinstance(ctx, dict) else {}
    resolved_reason = reason or context.get("retrieval_skip_reason")
    return {
        "hint_library_status": str(status or "").strip() or "unknown",
        "hint_library_skip_reason": str(resolved_reason or "").strip() or None,
        "hint_library_lookup_key": str(context.get("hint_key") or "").strip(),
        "hint_library_fingerprint_key": str(context.get("fingerprint_key") or "").strip() or None,
        "hint_library_schema_version": context.get("schema_version"),
        "hint_library_unit_category": str(context.get("unit_category") or "").strip() or None,
        "hint_library_control_structure": str(context.get("control_structure") or "").strip() or None,
        "hint_library_concept_tag": str(context.get("concept_tag") or "").strip() or None,
        "hint_library_error_type": str(context.get("error_type") or "").strip() or None,
        "hint_library_relation_type": str(context.get("relation_type") or "").strip() or None,
        "hint_library_relation_types": _hint_string_list(context.get("relation_types")),
        "hint_library_level": context.get("hint_level"),
        "hint_library_scope": str(context.get("scope") or "").strip() or None,
        "retrieval_match_mode": context.get("retrieval_match_mode"),
        "retrieval_score": _hint_float_or_none(context.get("retrieval_score")),
    }



def _hint_library_nonnegative_int(value, default=0):
    try:
        number = int(value)
    except Exception:
        return default
    return max(0, number)


def _hint_library_bool(value, default=True):
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text or text == "none":
        return default
    if text in {"1", "true", "yes", "y", "active"}:
        return True
    if text in {"0", "false", "no", "n", "inactive"}:
        return False
    return default


def _hint_library_date(value, default=None):
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text or text.lower() == "mongodb date":
        return default
    try:
        return _parse_attempt_v2_datetime(text) or default
    except Exception:
        return default



def _build_hint_library_upsert_doc(payload):
    data = payload if isinstance(payload, dict) else {}
    now = now_utc()
    hint_level = _hint_library_level(data.get("hint_level"), data.get("hint_key"))
    scope = str(data.get("scope") or ("narrow" if hint_level == 2 else "broad")).strip().lower()
    if scope not in {"broad", "narrow"}:
        scope = "narrow" if hint_level == 2 else "broad"

    concept_tag = normalize_concept_name(data.get("concept_tag") or "") or str(data.get("concept_tag") or "").strip()
    error_type = _hint_library_error_type(data.get("error_type") or "")
    unit_category = _normalize_unit_category(data.get("unit_category"))
    control_structure = str(
        data.get("control_structure") or _infer_control_structure(data, unit_category)
    ).strip().lower() or "unknown"
    relation_types = _hint_string_list(data.get("relation_types"))
    relation_type = str(data.get("relation_type") or "").strip()
    if relation_type and relation_type not in relation_types:
        relation_types.insert(0, relation_type)
    if not relation_type and len(relation_types) == 1:
        relation_type = relation_types[0]

    if not concept_tag:
        raise ValueError("concept_tag is required")
    if not error_type or error_type == "unknown":
        raise ValueError("error_type is required")
    hint_template = str(data.get("hint_template") or "").strip()
    if not hint_template:
        raise ValueError("hint_template is required")

    retrieval_eligible = bool(
        unit_category != "unknown"
        and relation_type
        and concept_tag != "unknown"
        and error_type != "unknown"
    )
    fingerprint_key = str(data.get("fingerprint_key") or "").strip()
    if retrieval_eligible and not fingerprint_key:
        fingerprint_key = _structured_hint_fingerprint_key(
            unit_category=unit_category,
            control_structure=control_structure,
            concept_tag=concept_tag,
            error_type=error_type,
            relation_type=relation_type,
            hint_level=hint_level,
            scope=scope,
        )
    hint_key = str(data.get("hint_key") or "").strip()
    if not hint_key:
        hint_key = fingerprint_key or f"{concept_tag}|{error_type}|level_{hint_level}"

    try:
        version = max(1, int(data.get("version") or 1))
    except Exception:
        version = 1
    schema_version = _HINT_LIBRARY_SCHEMA_VERSION if retrieval_eligible else max(1, int(data.get("schema_version") or 1))

    return {
        "schema_version": schema_version,
        "hint_key": hint_key,
        "fingerprint_key": fingerprint_key or None,
        "version": version,
        "unit_category": unit_category,
        "control_structure": control_structure,
        "concept_tag": concept_tag,
        "error_type": error_type,
        "relation_type": relation_type or None,
        "relation_types": relation_types,
        "expected_roles": _hint_string_list(data.get("expected_roles")),
        "submitted_roles": _hint_string_list(data.get("submitted_roles")),
        "retrieval_eligible": retrieval_eligible,
        "hint_level": hint_level,
        "scope": scope,
        "task_scope": str(data.get("task_scope") or "concept").strip() or "concept",
        "task_family": _clean_string(data.get("task_family")),
        "language": str(data.get("language") or "zh-TW").strip() or "zh-TW",
        "hint_template": hint_template,
        "hint_source": str(data.get("hint_source") or "ai_generated_structured").strip() or "ai_generated_structured",
        "quality_status": str(data.get("quality_status") or "passed").strip() or "passed",
        "answer_leakage_check": str(data.get("answer_leakage_check") or "passed").strip() or "passed",
        "usage_count": _hint_library_nonnegative_int(data.get("usage_count"), 0),
        "match_count": _hint_library_nonnegative_int(data.get("match_count"), 0),
        "evaluated_count": _hint_library_nonnegative_int(data.get("evaluated_count"), 0),
        "success_count": _hint_library_nonnegative_int(data.get("success_count"), 0),
        "last_used_at": _hint_library_date(data.get("last_used_at")),
        "last_matched_at": _hint_library_date(data.get("last_matched_at")),
        "source_prompt_version": str(data.get("source_prompt_version") or _PARSONS_HINT_PROMPT_VERSION).strip(),
        "source_model": str(data.get("source_model") or _model_for_feedback()).strip(),
        "source_hint_id": _clean_string(data.get("source_hint_id")),
        "source_task_id": _clean_string(data.get("source_task_id")),
        "is_active": _hint_library_bool(data.get("is_active", True), True),
        "created_at": _hint_library_date(data.get("created_at"), now),
        "updated_at": _hint_library_date(data.get("updated_at"), now),
    }



_HINT_LIBRARY_AUTO_SAVE_QUALITY_STATUSES = {
    "first_hint_accepted",
    "second_hint_depth_check_passed",
    "second_hint_without_first_hint",
}


def _auto_save_generated_hint_to_library(
    aggregate_detail,
    task,
    level,
    hint_text,
    *,
    source,
    quality_status,
    leakage_status,
):
    """Persist one safe, single-relation AI hint as a reusable template."""
    ctx = _hint_library_context_from_detail(aggregate_detail, level)
    status_context = _hint_library_status_fields(ctx, "skipped")

    if str(source or "").strip() != "ai_structured_error":
        status_context["hint_library_skip_reason"] = "not_structured_ai_generated"
        return status_context
    if str(leakage_status or "").strip() != "passed":
        status_context["hint_library_skip_reason"] = "answer_leakage_not_passed"
        return status_context
    if str(quality_status or "").strip() not in _HINT_LIBRARY_AUTO_SAVE_QUALITY_STATUSES:
        status_context["hint_library_skip_reason"] = "quality_status_not_auto_saved"
        return status_context
    if not ctx.get("retrieval_eligible"):
        status_context["hint_library_skip_reason"] = ctx.get("retrieval_skip_reason") or "structured_context_not_reusable"
        return status_context

    hint_template = str(hint_text or "").strip()
    if not hint_template:
        status_context["hint_library_skip_reason"] = "empty_hint_template"
        return status_context

    task_doc = task if isinstance(task, dict) else {}
    source_task_id = _clean_string(
        task_doc.get("task_id") or task_doc.get("id") or str(task_doc.get("_id") or "")
    )
    task_family = _hint_library_task_family(task_doc)
    now = now_utc()
    try:
        doc = _build_hint_library_upsert_doc({
            "schema_version": _HINT_LIBRARY_SCHEMA_VERSION,
            "hint_key": ctx.get("hint_key"),
            "fingerprint_key": ctx.get("fingerprint_key"),
            "version": 1,
            "unit_category": ctx.get("unit_category"),
            "control_structure": ctx.get("control_structure"),
            "concept_tag": ctx.get("concept_tag"),
            "error_type": ctx.get("error_type"),
            "relation_type": ctx.get("relation_type"),
            "relation_types": ctx.get("relation_types"),
            "expected_roles": ctx.get("expected_roles"),
            "submitted_roles": ctx.get("submitted_roles"),
            "hint_level": ctx.get("hint_level"),
            "scope": ctx.get("scope"),
            "task_scope": "task_family" if task_family else "concept",
            "task_family": task_family,
            "language": "zh-TW",
            "hint_template": hint_template,
            "hint_source": "ai_generated_structured",
            "quality_status": "passed",
            "answer_leakage_check": "passed",
            "usage_count": 1,
            "match_count": 0,
            "evaluated_count": 0,
            "success_count": 0,
            "last_used_at": now,
            "source_prompt_version": _PARSONS_HINT_PROMPT_VERSION,
            "source_model": _model_for_feedback(),
            "source_hint_id": None,
            "source_task_id": source_task_id,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        })
    except Exception as exc:
        print("[hint_library] auto-save payload failed:", repr(exc))
        status_context["hint_library_skip_reason"] = "payload_build_failed"
        return status_context

    try:
        ensure_hint_library_indexes()
        key = {"fingerprint_key": doc["fingerprint_key"], "version": doc["version"]}
        result = db.hint_library.update_one(key, {"$setOnInsert": doc}, upsert=True)
        if result.upserted_id:
            saved = db.hint_library.find_one({"_id": result.upserted_id})
            fields = _hint_library_public_fields(saved, ctx) if saved else {}
            fields.update(_hint_library_status_fields(ctx, "auto_saved"))
            return fields

        saved = db.hint_library.find_one(key)
        if saved:
            _mark_hint_library_used(saved)
            saved = db.hint_library.find_one(key)
        fields = _hint_library_public_fields(saved, ctx) if saved else {}
        fields.update(_hint_library_status_fields(ctx, "already_exists"))
        return fields
    except Exception as exc:
        print("[hint_library] auto-save write failed:", repr(exc))
        status_context["hint_library_skip_reason"] = "write_failed"
        return status_context



def _attempt_primary_wrong_index_for_hint(att):
    positions = _attempt_wrong_positions_for_hint(att)
    return positions[0] if positions else None


def _hint_string_list(value):
    """Normalize one string or a list-like value into unique non-empty strings."""
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    output = []
    seen = set()
    for item in values:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output



def _hint_relation_types_for_block(block, error_types, *, task=None, concept_tag=None):
    """Read explicit relations first, then apply deterministic six-category rules."""
    doc = block if isinstance(block, dict) else {}
    output = []
    relation_by_error = doc.get("relation_types_by_error")
    if not isinstance(relation_by_error, dict):
        relation_by_error = {}

    normalized_errors = []
    for error_type in (error_types or []):
        normalized = _hint_library_error_type(error_type)
        if normalized and normalized != "unknown" and normalized not in normalized_errors:
            normalized_errors.append(normalized)

    for error_type in normalized_errors:
        output.extend(_hint_string_list(relation_by_error.get(error_type)))
    output.extend(_hint_string_list(doc.get("relation_types")))
    output.extend(_hint_string_list(doc.get("relation_type")))

    if not output:
        category = _infer_unit_category(task or {}, [concept_tag] if concept_tag else [])
        role = _infer_block_role_for_relation(doc, category)
        for error_type in normalized_errors:
            relation = _relation_type_from_rule(category, role, error_type)
            if relation:
                output.append(relation)
    return list(dict.fromkeys(output))



def _collect_all_wrong_slot_contexts(att, task):
    """Collect safe, structured context for every currently wrong slot.

    Full submitted/expected code is retained only for backend leakage checks.
    It is not included in the LLM prompt.
    """
    if not isinstance(att, dict):
        att = {}
    if not isinstance(task, dict):
        task = {}

    wrong_slots = _attempt_wrong_positions_for_hint(att)
    sequence_slots = _int_list(att.get("sequence_slots") or att.get("wrong_indices") or att.get("wrong_slots") or [])
    indentation_slots = _int_list(att.get("indentation_slots") or att.get("indent_errors") or [])
    if not sequence_slots and not indentation_slots:
        sequence_slots = list(wrong_slots)

    try:
        parsed = t5doc_to_parsons_task(task)
    except Exception:
        parsed = {}

    pool = {
        str(block.get("id")): block
        for block in (parsed.get("pool") or [])
        if isinstance(block, dict)
    }
    # Raw task blocks preserve future structured fields such as block_role and
    # relation_types_by_error even when the student-facing normalized pool does not.
    raw_pool = {}
    for block in (task.get("solution_blocks") or []) + (task.get("distractor_blocks") or []):
        if not isinstance(block, dict):
            continue
        block_id = str(block.get("id") or block.get("_id") or "").strip()
        if block_id:
            raw_pool[block_id] = block

    template_slots = parsed.get("template_slots") or []
    expected_ids = [
        str(slot.get("expected_id"))
        for slot in template_slots
    ]
    answer_ids = [str(item) for item in (att.get("answer_ids") or att.get("submitted_order") or [])]

    try:
        slot_concept_map = _derive_slot_concept_map(task) or {}
    except Exception:
        slot_concept_map = {}

    attempt_detail_by_slot = {}
    for item in (att.get("error_details") or []):
        if not isinstance(item, dict):
            continue
        raw_slot = item.get("slot_index") if item.get("slot_index") is not None else item.get("slot")
        try:
            slot_index = int(raw_slot)
        except Exception:
            continue
        attempt_detail_by_slot.setdefault(slot_index, []).append(item)

    details = []
    for slot_index in wrong_slots:
        slot_errors = []
        if slot_index in sequence_slots:
            slot_errors.append("sequence_error")
        if slot_index in indentation_slots:
            slot_errors.append("indentation_error")
        if not slot_errors:
            slot_errors.append("sequence_error")

        expected_block_id = expected_ids[slot_index] if 0 <= slot_index < len(expected_ids) else ""
        submitted_block_id = answer_ids[slot_index] if 0 <= slot_index < len(answer_ids) else ""
        expected_block = raw_pool.get(expected_block_id) or pool.get(expected_block_id, {})
        submitted_block = raw_pool.get(submitted_block_id) or pool.get(submitted_block_id, {})
        expected_text = str((expected_block or {}).get("text") or "").strip()
        submitted_text = str((submitted_block or {}).get("text") or "").strip()

        concept_tag = normalize_concept_name(slot_concept_map.get(str(slot_index)) or "")
        if not concept_tag:
            try:
                concept_tag = normalize_concept_name(
                    infer_concept_tag_from_text("\n".join([expected_text, submitted_text]))
                )
            except Exception:
                concept_tag = ""

        concept = concept_tag or ("indentation_error" if "indentation_error" in slot_errors else "sequence_error")
        concept_scope = _progressive_hint_concept_label(concept, concept_tag)

        unit_category = _infer_unit_category(task, [concept_tag] if concept_tag else [])
        expected_role = str(
            (expected_block or {}).get("block_role")
            or (expected_block or {}).get("role")
            or _infer_block_role_for_relation(expected_block, unit_category)
            or ""
        ).strip()
        submitted_role = str(
            (submitted_block or {}).get("block_role")
            or (submitted_block or {}).get("role")
            or _infer_block_role_for_relation(submitted_block, unit_category)
            or ""
        ).strip()
        relation_types = _hint_relation_types_for_block(
            expected_block, slot_errors, task=task, concept_tag=concept_tag
        )

        # Attempt-level diagnostic data takes precedence when it already contains
        # a rule-derived relation type or role.
        for item in attempt_detail_by_slot.get(slot_index, []):
            relation_types.extend(_hint_string_list(item.get("relation_types")))
            relation_types.extend(_hint_string_list(item.get("relation_type")))
            expected_role = str(item.get("expected_role") or expected_role or "").strip()
            submitted_role = str(item.get("submitted_role") or submitted_role or "").strip()
        relation_types = list(dict.fromkeys(relation_types))

        details.append({
            "slot_index": slot_index,
            "slot_label": f"第{slot_index + 1}格",
            "error_types": slot_errors,
            "submitted_block_id": submitted_block_id,
            "expected_block_id": expected_block_id,
            # Backend-only fields for leakage checking; do not expose in prompts.
            "submitted_text": submitted_text,
            "expected_text": expected_text,
            # Safe structured evidence for prompt generation and future retrieval.
            "submitted_role": submitted_role,
            "expected_role": expected_role,
            "relation_types": relation_types,
            "concept": concept,
            "concept_tag": concept_tag,
            "concept_scope": concept_scope,
        })

    return details

def _aggregate_wrong_slot_contexts(slot_contexts):
    contexts = [item for item in (slot_contexts or []) if isinstance(item, dict)]
    concept_tags = []
    concept_scopes = []
    concept_error_types = {}
    concept_slots = {}
    relation_types = []
    expected_roles = []
    submitted_roles = []
    seen = set()
    for item in contexts:
        raw_tag = normalize_concept_name(item.get("concept_tag") or item.get("concept") or "")
        tag = raw_tag or "unknown"
        scope = str(item.get("concept_scope") or _progressive_hint_concept_label(tag, raw_tag) or "待釐清概念").strip()
        if tag not in seen:
            seen.add(tag)
            concept_tags.append(tag)
            concept_scopes.append(scope)
        concept_error_types.setdefault(tag, set()).update(
            str(x) for x in (item.get("error_types") or []) if str(x or "").strip()
        )
        concept_slots.setdefault(tag, []).append(int(item.get("slot_index")))
        relation_types.extend(_hint_string_list(item.get("relation_types")))
        expected_roles.extend(_hint_string_list(item.get("expected_role")))
        submitted_roles.extend(_hint_string_list(item.get("submitted_role")))

    return {
        "concept_tags": concept_tags,
        "concept_scopes": concept_scopes,
        "concept_error_types": {
            key: sorted(value)
            for key, value in concept_error_types.items()
        },
        "concept_slots": {
            key: sorted(set(value))
            for key, value in concept_slots.items()
        },
        "relation_types": list(dict.fromkeys(relation_types)),
        "expected_roles": list(dict.fromkeys(expected_roles)),
        "submitted_roles": list(dict.fromkeys(submitted_roles)),
    }

def _compact_subtitle_basis(value, fallback=""):
    text = " ".join(str(value or fallback or "").replace("\n", " ").split())
    if len(text) <= 180:
        return text
    return text[:180].rstrip("，。；;,.!?！？ ") + "..."

# 錯誤格的公開資訊整理
def _public_wrong_slot_details(slot_contexts):
    output = []
    for item in (slot_contexts or []):
        if not isinstance(item, dict):
            continue
        try:
            slot_index = int(item.get("slot_index"))
        except Exception:
            continue
        if slot_index < 0:
            continue
        output.append({
            "slot_index": slot_index,
            "slot_label": str(item.get("slot_label") or f"第{slot_index + 1}格"),
            "error_types": [str(x) for x in (item.get("error_types") or []) if str(x or "").strip()],
            "concept_tag": normalize_concept_name(item.get("concept_tag") or ""),
            "concept_scope": str(item.get("concept_scope") or "").strip(),
            "relation_types": _hint_string_list(item.get("relation_types")),
            "expected_role": str(item.get("expected_role") or "").strip() or None,
            "submitted_role": str(item.get("submitted_role") or "").strip() or None,
        })
    return sorted(output, key=lambda item: (int(item.get("slot_index") or 0), item.get("slot_label") or ""))

def _public_subtitle_ranges(ranges):
    output = []
    for item in (ranges or []):
        if not isinstance(item, dict):
            continue
        range_payload = _hint_range_payload(item)
        output.append({
            "concept_tag": normalize_concept_name(item.get("concept_tag") or ""),
            "concept_scope": str(item.get("concept_scope") or "").strip(),
            "start": range_payload.get("start"),
            "end": range_payload.get("end"),
            "source": range_payload.get("source") or str(item.get("source") or "").strip(),
        })
    return output


def _build_aggregated_hint_detail(att, task, requested_hint_no=1):
    """Build AI-hint evidence from deterministic answer comparison only.

    The legacy function name is retained because multiple hint-state paths call it.
    No subtitle/SRT retrieval is performed here.
    """
    level = 2 if int(requested_hint_no or 1) == 2 else 1
    scope_name = "narrow" if level == 2 else "broad"
    slot_contexts = _collect_all_wrong_slot_contexts(att, task)
    public_details = _public_wrong_slot_details(slot_contexts)
    aggregate = _aggregate_wrong_slot_contexts(slot_contexts)
    wrong_slots = sorted(set(int(item["slot_index"]) for item in public_details))

    error_concepts = []
    for item in public_details:
        error_concepts.append({
            "error_types": item.get("error_types") or [],
            "concept_tag": item.get("concept_tag") or "unknown",
            "concept_scope": item.get("concept_scope") or "程式流程與區塊關係",
            "relation_types": item.get("relation_types") or [],
            "expected_role": item.get("expected_role"),
            "submitted_role": item.get("submitted_role"),
        })

    if not error_concepts:
        error_concepts.append({
            "error_types": [],
            "concept_tag": "unknown",
            "concept_scope": "程式流程與區塊關係",
            "relation_types": [],
            "expected_role": None,
            "submitted_role": None,
        })

    task_doc = task if isinstance(task, dict) else {}
    question_text = str(task_doc.get("question_text") or task_doc.get("question") or "").strip()
    unit_category = _infer_unit_category(task_doc, aggregate.get("concept_tags") or [])
    control_structure = _infer_control_structure(task_doc, unit_category)
    task_context = {
        "unit": str(task_doc.get("unit") or "").strip() or None,
        "unit_type": str(task_doc.get("unit_type") or "").strip() or None,
        "unit_category": unit_category,
        "task_family": str(task_doc.get("task_family") or task_doc.get("family") or "").strip() or None,
        "target_concept": str(task_doc.get("target_concept") or "").strip() or None,
        "control_structure": control_structure,
    }

    evidence_parts = []
    if aggregate.get("concept_scopes"):
        evidence_parts.append("概念：" + "、".join(aggregate.get("concept_scopes")[:4]))
    if aggregate.get("relation_types"):
        evidence_parts.append("關係：" + "、".join(aggregate.get("relation_types")[:4]))
    evidence_summary = "；".join(evidence_parts) or "依據順序、縮排與概念標籤建立結構化錯誤摘要"

    return {
        "hint_level": level,
        "scope": scope_name,
        "aggregation_mode": "all_current_errors",
        "evidence_type": "structured_error",
        "question_text": question_text,
        "task_context": task_context,
        "unit_category": unit_category,
        "control_structure": control_structure,
        "wrong_slots": wrong_slots,
        "wrong_slot_count": len(wrong_slots),
        "wrong_slot_details": public_details,
        "concept_tags": aggregate.get("concept_tags") or ["unknown"],
        "concept_scopes": aggregate.get("concept_scopes") or ["程式流程與區塊關係"],
        "concept_count": len(aggregate.get("concept_tags") or ["unknown"]),
        "relation_types": aggregate.get("relation_types") or [],
        "expected_roles": aggregate.get("expected_roles") or [],
        "submitted_roles": aggregate.get("submitted_roles") or [],
        "covered_wrong_slots": wrong_slots if slot_contexts else [],
        "uncovered_wrong_slots": [] if slot_contexts else wrong_slots,
        "evidence_summary": evidence_summary,
        "error_concepts": error_concepts,
        # Compatibility fields kept empty so existing dashboards do not fail.
        "subtitle_ranges": [],
        "subtitle_range": {},
        "subtitle_broad_range": {},
        "subtitle_narrow_range": {},
        "subtitle_basis": "",
    }

def _aggregated_hint_fallback(detail, hint_level=1):
    scopes = [
        str(item or "").strip()
        for item in (detail.get("concept_scopes") or [])
        if str(item or "").strip()
    ]
    scope_text = "、".join(scopes[:4]) if scopes else "程式流程與區塊順序"
    level = 2 if int(hint_level or 1) == 2 else 1
    if level == 2:
        return f"再聚焦檢查{scope_text}之間的先後關係、作用範圍與資料流，想一想哪些步驟必須先成立，哪些步驟才適合接著執行。"
    return f"目前需要同時檢查{scope_text}。請先思考各程式區塊在整體流程中扮演的角色，以及彼此之間的先後依賴。"


def _build_ai_hint_meta_from_detail(detail, ctx, *, wrong_slots=None, requested_hint_no=1):
    detail = detail if isinstance(detail, dict) else {}
    ctx = ctx if isinstance(ctx, dict) else {}
    level = 2 if int(requested_hint_no or 1) == 2 else 1
    scope_name = "narrow" if level == 2 else "broad"

    broad_range = _hint_range_payload(detail.get("subtitle_broad_range"))
    narrow_range = _hint_range_payload(detail.get("subtitle_narrow_range"))
    selected_range = _hint_range_payload(detail.get("subtitle_range"))
    if not _hint_subtitle_range_available(selected_range):
        selected_range = narrow_range if level == 2 else broad_range

    positions = _int_list(wrong_slots or ctx.get("wrong_slots") or [])
    wrong_index = ctx.get("wrong_index")
    try:
        wrong_index = int(wrong_index) if wrong_index is not None else None
    except Exception:
        wrong_index = None
    if wrong_index is None and positions:
        wrong_index = positions[0]

    concept = str(detail.get("concept") or ctx.get("feedback_error_type") or "logic").strip() or "logic"
    concept_tag = str(detail.get("concept_tag") or ctx.get("concept_tag") or "").strip()
    concept_scope = str(
        detail.get("concept_scope")
        or detail.get("concept_explanation")
        or ctx.get("concept_scope")
        or _progressive_hint_concept_label(concept, concept_tag)
        or ""
    ).strip()
    wrong_slot_details = _public_wrong_slot_details(
        detail.get("wrong_slot_details") or ctx.get("wrong_slot_details") or []
    )
    if wrong_slot_details:
        positions = sorted(set([int(item["slot_index"]) for item in wrong_slot_details]))

    concept_tags = [
        normalize_concept_name(item)
        for item in (detail.get("concept_tags") or ctx.get("concept_tags") or [])
        if normalize_concept_name(item)
    ]
    concept_scopes = [
        str(item or "").strip()
        for item in (detail.get("concept_scopes") or ctx.get("concept_scopes") or [])
        if str(item or "").strip()
    ]
    if not concept_tags and concept_tag:
        concept_tags = [normalize_concept_name(concept_tag)]
    if not concept_scopes and concept_scope:
        concept_scopes = [concept_scope]

    subtitle_ranges = _public_subtitle_ranges(detail.get("subtitle_ranges") or [])
    covered_wrong_slots = _int_list(detail.get("covered_wrong_slots") or positions)
    uncovered_wrong_slots = _int_list(detail.get("uncovered_wrong_slots") or [])

    return {
        "hint_level": level,
        "scope": scope_name,
        "aggregation_mode": detail.get("aggregation_mode") or "all_current_errors",
        "evidence_type": str(detail.get("evidence_type") or "structured_error").strip() or "structured_error",
        "evidence_summary": str(detail.get("evidence_summary") or "").strip(),
        "unit_category": str(detail.get("unit_category") or (detail.get("task_context") or {}).get("unit_category") or "unknown").strip(),
        "control_structure": str(detail.get("control_structure") or (detail.get("task_context") or {}).get("control_structure") or "unknown").strip(),
        "relation_type": (
            _hint_string_list(detail.get("relation_types"))[0]
            if len(_hint_string_list(detail.get("relation_types"))) == 1
            else None
        ),
        "relation_types": _hint_string_list(detail.get("relation_types")),
        "expected_roles": _hint_string_list(detail.get("expected_roles")),
        "submitted_roles": _hint_string_list(detail.get("submitted_roles")),
        "concept": concept,
        "concept_tag": concept_tag,
        "concept_scope": concept_scope,
        "wrong_index": wrong_index,
        "wrong_slots": positions,
        "wrong_slot_count": len(positions),
        "wrong_slot_details": wrong_slot_details,
        "concept_tags": concept_tags,
        "concept_scopes": concept_scopes,
        "concept_count": len(concept_tags),
        "covered_wrong_slots": covered_wrong_slots,
        "uncovered_wrong_slots": uncovered_wrong_slots,
        "subtitle_ranges": subtitle_ranges,
        "subtitle_range": selected_range,
        "subtitle_broad_range": broad_range,
        "subtitle_narrow_range": narrow_range,
        "subtitle_basis": (
            ""
            if str(detail.get("evidence_type") or "").strip() == "structured_error"
            else _compact_subtitle_basis(detail.get("subtitle_basis"), concept_scope)
        ),
        "hint_source": str(detail.get("hint_source") or "system_fallback").strip() or "system_fallback",
        "answer_leakage_check": str(detail.get("answer_leakage_check") or "unknown").strip() or "unknown",
        "hint_quality_status": str(detail.get("hint_quality_status") or "not_checked").strip() or "not_checked",
        "hint_library_id": str(detail.get("hint_library_id") or "").strip() or None,
        "hint_library_key": str(detail.get("hint_library_key") or "").strip() or None,
        "hint_library_fingerprint_key": str(detail.get("hint_library_fingerprint_key") or "").strip() or None,
        "hint_library_unit_category": str(detail.get("hint_library_unit_category") or "").strip() or None,
        "hint_library_control_structure": str(detail.get("hint_library_control_structure") or "").strip() or None,
        "hint_library_relation_type": str(detail.get("hint_library_relation_type") or "").strip() or None,
        "retrieval_match_mode": str(detail.get("retrieval_match_mode") or "").strip() or None,
        "retrieval_score": _hint_float_or_none(detail.get("retrieval_score")),
        "hint_library_version": (
            int(detail.get("hint_library_version"))
            if str(detail.get("hint_library_version") or "").strip().isdigit()
            else None
        ),
        "hint_library_concept_tag": str(detail.get("hint_library_concept_tag") or "").strip() or None,
        "hint_library_error_type": str(detail.get("hint_library_error_type") or "").strip() or None,
        "hint_library_task_scope": str(detail.get("hint_library_task_scope") or "").strip() or None,
        "hint_library_task_family": detail.get("hint_library_task_family"),
        "hint_library_status": str(detail.get("hint_library_status") or "").strip() or None,
        "hint_library_skip_reason": str(detail.get("hint_library_skip_reason") or "").strip() or None,
        "hint_library_lookup_key": str(detail.get("hint_library_lookup_key") or "").strip() or None,
        "hint_library_level": (
            int(detail.get("hint_library_level"))
            if str(detail.get("hint_library_level") or "").strip().isdigit()
            else None
        ),
        "hint_library_scope": str(detail.get("hint_library_scope") or "").strip() or None,
        "source_model": str(detail.get("source_model") or "").strip() or None,
        "source_prompt_version": str(detail.get("source_prompt_version") or "").strip() or None,
        "source_hint_id": str(detail.get("source_hint_id") or "").strip() or None,
        "source_task_id": str(detail.get("source_task_id") or "").strip() or None,
        "second_hint_similarity": _hint_float_or_none(detail.get("second_hint_similarity")),
        "second_hint_similarity_threshold": (
            _hint_float_or_none(detail.get("second_hint_similarity_threshold"))
            if detail.get("second_hint_similarity_threshold") is not None
            else 0.72
        ),
        "generated_at_taiwan": _taiwan_time_string(now_utc()),
    }


def _build_ai_hint_meta_for_attempt(att, task, requested_hint_no, detail=None):
    detail = detail if isinstance(detail, dict) else {}
    ctx = _attempt_hint_context(att, task)
    aggregate_detail = _build_aggregated_hint_detail(att, task, requested_hint_no)
    detail = {
        **aggregate_detail,
        **detail,
        "aggregation_mode": detail.get("aggregation_mode") or aggregate_detail.get("aggregation_mode"),
        "wrong_slot_details": detail.get("wrong_slot_details") or aggregate_detail.get("wrong_slot_details"),
        "concept_tags": detail.get("concept_tags") or aggregate_detail.get("concept_tags"),
        "concept_scopes": detail.get("concept_scopes") or aggregate_detail.get("concept_scopes"),
        "subtitle_ranges": detail.get("subtitle_ranges") or aggregate_detail.get("subtitle_ranges"),
        "covered_wrong_slots": detail.get("covered_wrong_slots") or aggregate_detail.get("covered_wrong_slots"),
        "uncovered_wrong_slots": detail.get("uncovered_wrong_slots") or aggregate_detail.get("uncovered_wrong_slots"),
        "wrong_slots": detail.get("wrong_slots") or aggregate_detail.get("wrong_slots"),
        "wrong_slot_count": detail.get("wrong_slot_count") or aggregate_detail.get("wrong_slot_count"),
        "hint_source": detail.get("hint_source") or detail.get("source") or "metadata_reconstructed",
        "answer_leakage_check": detail.get("answer_leakage_check") or "unknown",
    }
    return _build_ai_hint_meta_from_detail(
        detail,
        ctx,
        wrong_slots=detail.get("wrong_slots") or ctx.get("wrong_slots"),
        requested_hint_no=requested_hint_no,
    )

def _resolve_attempt_v2_for_hint(att, explicit_attempt_v2_id=None, record=None):
    """Resolve the v2 attempt linked to one legacy attempt/hint request."""
    def _matches_context(doc):
        if not isinstance(doc, dict):
            return False
        expected_student = str((att or {}).get("student_id") or current_student_id() or "").strip()
        expected_task = str((att or {}).get("task_id") or "").strip()
        if expected_student and str(doc.get("student_id") or "").strip() != expected_student:
            return False
        if expected_task and str(doc.get("task_id") or "").strip() != expected_task:
            return False
        return True

    candidates = [
        explicit_attempt_v2_id,
        (att or {}).get("attempt_v2_id"),
        (record or {}).get("latest_attempt_v2_id"),
    ]
    for candidate in candidates:
        value = str(candidate or "").strip()
        if not value or not ObjectId.is_valid(value):
            continue
        doc = db.parsons_attempts_v2.find_one({"_id": ObjectId(value)})
        if doc and _matches_context(doc):
            return doc

    legacy_id = str((att or {}).get("_id") or "").strip()
    if legacy_id:
        doc = db.parsons_attempts_v2.find_one({"legacy_attempt_id": legacy_id})
        if doc and _matches_context(doc):
            return doc

    student_id = str((att or {}).get("student_id") or current_student_id() or "").strip()
    task_id = str((att or {}).get("task_id") or "").strip()
    if not student_id or not task_id:
        return None
    query = {"student_id": student_id, "task_id": task_id, "activity_type": "practice"}
    try:
        session_no = int(
            (att or {}).get("task_attempt_session")
            or (record or {}).get("task_attempt_session")
            or 0
        )
    except Exception:
        session_no = 0
    if session_no > 0:
        query["task_attempt_session"] = session_no
    return db.parsons_attempts_v2.find_one(
        query,
        sort=[("attempt_no", -1), ("submitted_at", -1), ("_id", -1)],
    )


def _merge_attempt_v2_hint_context(att, attempt_v2):
    """Use the latest v2 attempt fields as the authoritative hint context."""
    merged = dict(att or {})
    if not isinstance(attempt_v2, dict) or not attempt_v2:
        return merged

    if attempt_v2.get("_id") is not None:
        merged["attempt_v2_id"] = str(attempt_v2.get("_id"))

    for key in (
        "task_attempt_session",
        "attempt_no",
        "attempt_sequence_no",
        "answer_ids",
        "submitted_order",
        "submitted_indentation",
        "wrong_slots",
        "incorrect_slots",
        "sequence_slots",
        "indentation_slots",
        "error_count",
        "error_types",
        "repeated_error",
        "block_results",
        "target_concept",
        "submitted_answer_raw",
    ):
        if attempt_v2.get(key) is not None:
            merged[key] = attempt_v2.get(key)

    if isinstance(attempt_v2.get("incorrect_slots"), list):
        merged["wrong_indices"] = attempt_v2.get("incorrect_slots")
    elif isinstance(attempt_v2.get("wrong_slots"), list):
        merged["wrong_indices"] = attempt_v2.get("wrong_slots")

    return merged


def _update_attempt_v2_ai_hint_event(
    att,
    record,
    *,
    requested_hint_no,
    explicit_attempt_v2_id=None,
    hint_text_override="",
    hint_meta_override=None,
    event_type="ai_hint_view",
    generated=False,
    trigger_method="",
    button_name="",
):
    """
    Denormalize AI hint text/click summary into parsons_attempts_v2.

    learning_logs remains the detailed event stream. This copy makes statistical
    joins and exports possible without reconstructing every click from logs.
    """
    attempt_v2 = _resolve_attempt_v2_for_hint(att, explicit_attempt_v2_id, record)
    if not attempt_v2:
        return None

    public = _hint_record_public(record) or {}
    hint_no = 2 if int(requested_hint_no or 1) == 2 else 1
    hint_text = str(
        hint_text_override
        or public.get("ai_hint_2_text" if hint_no == 2 else "ai_hint_1_text")
        or ""
    ).strip()
    hint_meta = hint_meta_override if isinstance(hint_meta_override, dict) else {}
    if not hint_meta:
        hint_meta = public.get("ai_hint_2_meta" if hint_no == 2 else "ai_hint_1_meta") or {}
    if not isinstance(hint_meta, dict):
        hint_meta = {}

    hint_1_text = str(public.get("ai_hint_1_text") or attempt_v2.get("ai_hint_1_text") or "").strip()
    hint_2_text = str(public.get("ai_hint_2_text") or attempt_v2.get("ai_hint_2_text") or "").strip()
    hint_1_meta = public.get("ai_hint_1_meta") if isinstance(public.get("ai_hint_1_meta"), dict) else (attempt_v2.get("ai_hint_1_meta") or {})
    hint_2_meta = public.get("ai_hint_2_meta") if isinstance(public.get("ai_hint_2_meta"), dict) else (attempt_v2.get("ai_hint_2_meta") or {})
    if hint_no == 1 and hint_text:
        hint_1_text, hint_1_meta = hint_text, hint_meta
    if hint_no == 2 and hint_text:
        hint_2_text, hint_2_meta = hint_text, hint_meta

    hint_texts = []
    if hint_1_text:
        hint_texts.append({"hint_no": 1, "text": hint_1_text, "meta": hint_1_meta})
    if hint_2_text:
        hint_texts.append({"hint_no": 2, "text": hint_2_text, "meta": hint_2_meta})

    aggregation_meta = hint_meta if isinstance(hint_meta, dict) else {}
    if not aggregation_meta.get("aggregation_mode"):
        aggregation_meta = hint_2_meta if isinstance(hint_2_meta, dict) and hint_2_meta.get("aggregation_mode") else aggregation_meta
    if not aggregation_meta.get("aggregation_mode"):
        aggregation_meta = hint_1_meta if isinstance(hint_1_meta, dict) and hint_1_meta.get("aggregation_mode") else aggregation_meta

    aggregation_mode = str(aggregation_meta.get("aggregation_mode") or "").strip() or None
    concept_tags = [
        normalize_concept_name(item)
        for item in (aggregation_meta.get("concept_tags") or [])
        if normalize_concept_name(item)
    ]
    if not concept_tags and aggregation_meta.get("concept_tag"):
        concept_tags = [normalize_concept_name(aggregation_meta.get("concept_tag"))]
    concept_scopes = [
        str(item or "").strip()
        for item in (aggregation_meta.get("concept_scopes") or [])
        if str(item or "").strip()
    ]
    if not concept_scopes and aggregation_meta.get("concept_scope"):
        concept_scopes = [str(aggregation_meta.get("concept_scope")).strip()]
    hint_wrong_slots = _int_list(aggregation_meta.get("wrong_slots") or [])

    now = now_utc()
    click_event = {
        "hint_no": hint_no,
        "hint_text": hint_text,
        "hint_meta": hint_meta,
        "event_type": str(event_type or "ai_hint_view"),
        "generated": bool(generated),
        "trigger_method": str(trigger_method or ""),
        "button_name": str(button_name or ""),
        "viewed_at": now,
        "viewed_at_taiwan": _taiwan_time_string(now),
    }

    set_fields = {
        "ai_hint_prompt_version": public.get("hint_prompt_version") or _PARSONS_HINT_PROMPT_VERSION,
        "ai_hint_1_text": hint_1_text or None,
        "ai_hint_1_meta": hint_1_meta if isinstance(hint_1_meta, dict) else {},
        "ai_hint_2_text": hint_2_text or None,
        "ai_hint_2_meta": hint_2_meta if isinstance(hint_2_meta, dict) else {},
        "ai_hint_texts": hint_texts,
        "ai_hint_generation_count": int(public.get("ai_hint_generation_count") or public.get("hint_generation_count") or 0),
        "ai_hint_view_count": int(public.get("ai_hint_view_count") or public.get("hint_view_count") or 0),
        "ai_hint_clicked": True,
        "ai_hint_last_viewed_no": hint_no,
        "ai_hint_last_viewed_at": now,
        "ai_hint_last_viewed_at_taiwan": _taiwan_time_string(now),
        "source_hint_id": public.get("hint_id") or attempt_v2.get("source_hint_id"),
        "ai_hint_aggregation_mode": aggregation_mode,
        "ai_hint_concept_tags": concept_tags,
        "ai_hint_concept_scopes": concept_scopes,
        "ai_hint_wrong_slots": hint_wrong_slots,
        "updated_at": now,
        "updated_at_utc": now,
        "updated_at_taiwan": _taiwan_time_string(now),
    }

    db.parsons_attempts_v2.update_one(
        {"_id": attempt_v2["_id"]},
        {
            "$set": set_fields,
            "$addToSet": {"ai_hint_viewed_numbers": hint_no},
            "$push": {"ai_hint_clicks": {"$each": [click_event], "$slice": -50}},
        },
    )
    return str(attempt_v2["_id"])


# 作答時間處理
# 包括:計算學生此次作答秒數
# 排除負數
# 排除超過一小時的長時間開頁
# 標記 duration outlier
def _parse_attempt_v2_datetime(value):
    """Normalize a submitted timestamp to an aware UTC datetime for BSON Date storage."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        try:
            ts = float(value)
            if ts > 1000000000000:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
    return None

# 標準答案與錯誤判斷
def _build_attempt_v2_duration_fields(started_at, submitted_at) -> dict:
    result = {
        "elapsed_sec_raw": None,
        "duration_sec": None,
        "duration_seconds": None,
        "duration_outlier": False,
        "duration_outlier_reason": None,
    }
    if not started_at or not submitted_at:
        return result
    try:
        sec = int(round((submitted_at - started_at).total_seconds()))
        result["elapsed_sec_raw"] = sec
        if sec < 0:
            result["duration_outlier"] = True
            result["duration_outlier_reason"] = "invalid_time_order"
            return result
        if sec > 3600:
            result["duration_outlier"] = True
            result["duration_outlier_reason"] = "long_page_open"
            return result
        result["duration_sec"] = sec
        result["duration_seconds"] = sec
        return result
    except Exception:
        return result


def _normalize_attempt_v2_duration(started_at, submitted_at):
    fields = _build_attempt_v2_duration_fields(started_at, submitted_at)
    return fields["duration_sec"], fields["duration_outlier"]


def _safe_duration_seconds(value):
    try:
        if value is None or value == "":
            return None
        seconds = int(round(float(value)))
        if 0 <= seconds <= 3600:
            return seconds
    except Exception:
        return None
    return None


def _lookup_attempt_v2_user_profile(student_id: str, participant_id: str = "") -> dict:
    user = None
    sid = (student_id or "").strip()
    pid = (participant_id or "").strip()

    try:
        if sid:
            user = db.users.find_one({"student_id": sid})
        if not user and pid and ObjectId.is_valid(pid):
            user = db.users.find_one({"_id": ObjectId(pid)})
    except Exception:
        user = None

    user = user or {}
    profile_student_id = sid or str(user.get("student_id") or "").strip()
    return {
        "class_name": user.get("class_name") or None,
        "group_type": user.get("group_type") or None,
        "feedback_strategy": _feedback_strategy_from_user(user),
        "is_test_data": bool(
            profile_student_id == _PARSONS_TEST_STUDENT_ID
            or user.get("is_test_data") is True
            or (
                user.get("role") == "student"
                and not str(user.get("group_type") or "").strip()
            )
        ),
    }


def _normalize_attempt_v2_concept_value(value):
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, (list, tuple, set)):
        for item in value:
            normalized = _normalize_attempt_v2_concept_value(item)
            if normalized:
                return normalized
        return None
    if isinstance(value, dict):
        for key in ("concept", "target_concept", "tag", "name", "label"):
            normalized = _normalize_attempt_v2_concept_value(value.get(key))
            if normalized:
                return normalized
    return None


def _infer_attempt_v2_target_concept_from_title(task_title) -> str:
    title = str(task_title or "").strip().lower()
    if not title:
        return "unknown"

    if _re.search(r"\b(?:if|else|elif)\b", title):
        return "if_elif_else"
    if _re.search(r"\bfor\b", title):
        return "for_loop"
    if _re.search(r"\bwhile\b", title):
        return "while_loop"
    if "巢狀" in title or _re.search(r"\bnested\b", title):
        return "nested_loop"
    if "函式" in title or _re.search(r"\bfunction\b", title):
        return "function"
    if "串列" in title or _re.search(r"\blist\b", title):
        return "list_processing"
    return "unknown"


def _extract_attempt_v2_target_concept(task_doc: dict):
    task = task_doc if isinstance(task_doc, dict) else {}
    for key in ("concept", "target_concept", "tags", "concept_tag"):
        normalized = _normalize_attempt_v2_concept_value(task.get(key))
        if normalized:
            return normalized
    return _infer_attempt_v2_target_concept_from_title(
        _extract_attempt_v2_task_title(task)
    )


def _extract_attempt_v2_task_title(task_doc: dict):
    if not isinstance(task_doc, dict):
        return None
    for key in ("task_title", "title", "question_text"):
        value = task_doc.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _submitted_indentation_from_lines(answer_lines):
    if not isinstance(answer_lines, list):
        return None
    out = []
    for line in answer_lines:
        if line is None:
            out.append(None)
            continue
        text = str(line).replace("\t", "    ")
        out.append(len(text) - len(text.lstrip(" ")))
    return out


def _submitted_indentation_by_block(submitted_order, submitted_indentation) -> dict:
    if not isinstance(submitted_order, list):
        return {}
    indentation = submitted_indentation if isinstance(submitted_indentation, list) else []
    normalized = {}
    for index, block_id in enumerate(submitted_order):
        key = str(block_id or "").strip()
        if not key:
            continue
        normalized[key] = indentation[index] if index < len(indentation) else None
    return normalized


def _infer_attempt_v2_indents_from_blocks(blocks: list) -> list:
    out = []
    level = 0
    for block in blocks or []:
        if not isinstance(block, dict):
            out.append(0)
            continue
        if "indent" in block:
            try:
                out.append(int(block.get("indent") or 0))
                continue
            except Exception:
                pass

        raw = str(block.get("text") or "")
        stripped = raw.strip()
        low = stripped.lower()
        if low.startswith(("elif ", "else:", "except", "finally:")):
            level = max(0, level - 1)
        leading = len(raw) - len(raw.lstrip(" "))
        out.append(leading if leading > 0 else level * 4)
        if stripped.endswith(":"):
            level += 1
    return out


def _correct_answer_from_task(task_doc: dict):
    if not isinstance(task_doc, dict):
        return None
    try:
        parsed = t5doc_to_parsons_task(task_doc)
        slots = parsed.get("template_slots") or []
        solution_blocks = parsed.get("solution_blocks") or []
        pool_by_id = {
            str((block or {}).get("id")): block
            for block in (parsed.get("pool") or [])
            if isinstance(block, dict) and (block or {}).get("id") is not None
        }

        order = []
        for slot in slots:
            if not isinstance(slot, dict):
                continue
            expected_id = slot.get("expected_id")
            if expected_id is not None and str(expected_id).strip():
                order.append(str(expected_id).strip())

        if not order:
            order = [
                str((block or {}).get("id")).strip()
                for block in solution_blocks
                if isinstance(block, dict) and (block or {}).get("id") is not None and str((block or {}).get("id")).strip()
            ]

        lines = []
        blocks_in_order = []
        for idx, block_id in enumerate(order):
            block = pool_by_id.get(str(block_id))
            if not isinstance(block, dict) and idx < len(solution_blocks):
                block = solution_blocks[idx]
            if not isinstance(block, dict):
                block = {}
            blocks_in_order.append(block)
            lines.append(str(block.get("text") or ""))

        return {
            "order": order,
            "lines": lines,
            "indentation": _infer_attempt_v2_indents_from_blocks(blocks_in_order),
        }
    except Exception:
        return None


def _build_attempt_v2_error_analysis(
    *,
    is_correct: bool,
    submitted_order,
    submitted_indentation,
    correct_answer,
    target_concept,
    previous_error_types=None,
) -> dict:
    repeated_basis = "same_task_same_error_type"
    repeated_rule_version = 1
    concept = str(target_concept or "").strip() or "unknown"

    if bool(is_correct):
        return {
            "error_count": 0,
            "error_types": [],
            "wrong_slots": [],
            "incorrect_slots": [],
            "sequence_slots": [],
            "indentation_slots": [],
            "error_details": [],
            "error_concept": None,
            "repeated_error": False,
            "repeated_error_types": [],
            "repeated_error_count": 0,
            "repeated_error_basis": repeated_basis,
            "repeated_error_rule_version": repeated_rule_version,
        }

    correct = correct_answer if isinstance(correct_answer, dict) else {}
    expected_order = correct.get("order") if isinstance(correct.get("order"), list) else []
    actual_order = submitted_order if isinstance(submitted_order, list) else []
    expected_indentation = (
        correct.get("indentation")
        if isinstance(correct.get("indentation"), list)
        else []
    )
    actual_indentation = (
        submitted_indentation
        if isinstance(submitted_indentation, list)
        else []
    )

    sequence_slots = []
    order_error_details = []
    if expected_order:
        expected_position_by_block = {
            str(block_id): index
            for index, block_id in enumerate(expected_order)
            if block_id is not None
        }
        for index in range(max(len(actual_order), len(expected_order))):
            actual = actual_order[index] if index < len(actual_order) else None
            expected = expected_order[index] if index < len(expected_order) else None
            if str(actual) != str(expected):
                sequence_slots.append(index)
                order_error_details.append({
                    "slot": index,
                    "block_id": str(actual) if actual is not None else None,
                    "error_type": "order",
                    "expected_block_id": str(expected) if expected is not None else None,
                    "submitted_position": index if actual is not None else None,
                    "expected_position": expected_position_by_block.get(str(actual)) if actual is not None else None,
                    "concept_tag": concept,
                })

    indentation_slots = []
    indentation_error_details = []
    if actual_indentation and expected_indentation:
        for index in range(max(len(actual_indentation), len(expected_indentation))):
            actual = actual_indentation[index] if index < len(actual_indentation) else None
            expected = expected_indentation[index] if index < len(expected_indentation) else None
            if actual != expected:
                indentation_slots.append(index)
                block_id = actual_order[index] if index < len(actual_order) else None
                indentation_error_details.append({
                    "slot": index,
                    "block_id": str(block_id) if block_id is not None else None,
                    "error_type": "indentation",
                    "submitted_indent": actual,
                    "expected_indent": expected,
                    "concept_tag": concept,
                })

    error_types = []
    if sequence_slots:
        error_types.append("sequence_error")
    if indentation_slots:
        error_types.append("indentation_error")

    # wrong_slots stores only block-order mismatches (0-based positions).
    wrong_slots = sorted(set(sequence_slots))
    # error_count counts distinct slots with either a sequence or indentation error.
    incorrect_slots = sorted(set(sequence_slots + indentation_slots))
    error_details = order_error_details + indentation_error_details
    detail_error_types = sorted({
        str(item.get("error_type") or "").strip()
        for item in error_details
        if str(item.get("error_type") or "").strip()
    })
    previous_types = {
        "order" if str(item) == "sequence_error" else "indentation" if str(item) == "indentation_error" else str(item)
        for item in (previous_error_types or [])
        if str(item or "").strip()
    }
    repeated_error_types = sorted(set(detail_error_types).intersection(previous_types))
    repeated_error_count = sum(
        1
        for item in error_details
        if item.get("error_type") in repeated_error_types
    )
    repeated_error = bool(repeated_error_types)

    return {
        "error_count": len(incorrect_slots),
        "error_types": error_types,
        # wrong_slots 保留相容性：只代表順序錯誤位置。
        "wrong_slots": wrong_slots,
        # incorrect_slots 才是畫面紅色標記應使用的完整位置：
        # 順序錯誤與縮排錯誤的聯集。
        "incorrect_slots": incorrect_slots,
        "sequence_slots": sorted(set(sequence_slots)),
        "indentation_slots": sorted(set(indentation_slots)),
        "error_details": error_details,
        "error_concept": concept,
        "repeated_error": repeated_error,
        "repeated_error_types": repeated_error_types,
        "repeated_error_count": repeated_error_count,
        "repeated_error_basis": repeated_basis,
        "repeated_error_rule_version": repeated_rule_version,
    }



def _normalize_attempt_v2_block_text(value) -> str:
    return str(value or "").replace("\t", "    ").strip()


def _attempt_v2_block_lookup(task_doc: dict) -> dict:
    """Return block_id -> normalized block metadata for exact audit trails."""
    lookup = {}
    try:
        parsed = t5doc_to_parsons_task(task_doc if isinstance(task_doc, dict) else {})
    except Exception:
        parsed = {}

    collections = [
        parsed.get("pool") or [],
        parsed.get("solution_blocks") or [],
        parsed.get("distractor_blocks") or [],
    ]
    for blocks in collections:
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_id = str(block.get("id") or block.get("_id") or "").strip()
            if not block_id:
                continue
            if block_id not in lookup:
                lookup[block_id] = {
                    "id": block_id,
                    "text": str(block.get("text") or block.get("code") or ""),
                    "indent": block.get("indent", block.get("indent_level")),
                    "type": block.get("type"),
                }
    return lookup


def _attempt_v2_base_query(student_id: str, task_id: str, activity_type: str, test_role):
    return {
        "student_id": str(student_id or "").strip(),
        "task_id": str(task_id or "").strip(),
        "activity_type": str(activity_type or "").strip() or None,
        "test_role": test_role,
    }


def _latest_attempt_v2_doc(student_id: str, task_id: str, activity_type: str, test_role):
    query = _attempt_v2_base_query(student_id, task_id, activity_type, test_role)
    if not query["student_id"] or not query["task_id"]:
        return None
    return db.parsons_attempts_v2.find_one(
        query,
        sort=[("submitted_at", -1), ("created_at", -1), ("_id", -1)],
    )

# 作答場次管理
def _resolve_task_attempt_session(student_id: str, task_id: str, activity_type: str, test_role):
    """
    Return (task_attempt_session, attempt_no_in_session, attempt_sequence_no, previous_in_session).

    - attempt_no：同一完整作答場次內的第幾次送出。
    - task_attempt_session：答對後重新進入同題時加 1。
    - attempt_sequence_no：跨場次的全域送出順序，方便排序。
    """
    query = _attempt_v2_base_query(student_id, task_id, activity_type, test_role)
    if not query["student_id"] or not query["task_id"]:
        return 1, 1, 1, None

    latest = _latest_attempt_v2_doc(student_id, task_id, activity_type, test_role)
    total_existing = db.parsons_attempts_v2.count_documents(query)
    sequence_no = int(total_existing) + 1

    if not latest:
        return 1, 1, sequence_no, None

    try:
        latest_session = max(1, int(latest.get("task_attempt_session") or 1))
    except Exception:
        latest_session = 1

    if bool(latest.get("is_correct")):
        return latest_session + 1, 1, sequence_no, None

    try:
        next_attempt_no = max(1, int(latest.get("attempt_no") or 0) + 1)
    except Exception:
        next_attempt_no = 1
    return latest_session, next_attempt_no, sequence_no, latest


def _previous_attempt_v2_doc(
    student_id: str,
    task_id: str,
    activity_type: str,
    test_role,
    task_attempt_session=None,
):
    query = _attempt_v2_base_query(student_id, task_id, activity_type, test_role)
    if not query["student_id"] or not query["task_id"]:
        return None
    if task_attempt_session is not None:
        query["task_attempt_session"] = int(task_attempt_session)
    return db.parsons_attempts_v2.find_one(
        query,
        sort=[("attempt_no", -1), ("submitted_at", -1), ("_id", -1)],
    )

# 會逐格比較這次與前一次作答
# 包括:這一格是否正確
# 原本錯誤是否修正
# 原本正確是否被改錯
# 是否持續錯誤
def _build_attempt_v2_block_results(
    *,
    task_doc: dict,
    submitted_order,
    submitted_indentation,
    correct_answer,
    previous_attempt=None,
):
    """
    Build per-slot/per-block audit rows.

    Each row preserves both submitted and expected block IDs, so the teacher
    dashboard can determine exactly which block was corrected after an AI hint.
    """
    actual_order = list(submitted_order) if isinstance(submitted_order, list) else []
    actual_indentation = (
        list(submitted_indentation)
        if isinstance(submitted_indentation, list)
        else []
    )
    correct = correct_answer if isinstance(correct_answer, dict) else {}
    expected_order = (
        list(correct.get("order"))
        if isinstance(correct.get("order"), list)
        else []
    )
    expected_indentation = (
        list(correct.get("indentation"))
        if isinstance(correct.get("indentation"), list)
        else []
    )
    block_lookup = _attempt_v2_block_lookup(task_doc)

    previous = previous_attempt if isinstance(previous_attempt, dict) else {}
    previous_order = (
        list(previous.get("submitted_order"))
        if isinstance(previous.get("submitted_order"), list)
        else []
    )
    previous_indentation = (
        list(previous.get("submitted_indentation"))
        if isinstance(previous.get("submitted_indentation"), list)
        else []
    )

    total_slots = max(
        len(expected_order),
        len(actual_order),
        len(expected_indentation),
        len(actual_indentation),
    )

    def _state_at(order, indentation, index):
        submitted_id = (
            str(order[index]).strip()
            if index < len(order) and order[index] is not None
            else ""
        )
        expected_id = (
            str(expected_order[index]).strip()
            if index < len(expected_order) and expected_order[index] is not None
            else ""
        )
        submitted_block = block_lookup.get(submitted_id) or {}
        expected_block = block_lookup.get(expected_id) or {}
        submitted_text = str(submitted_block.get("text") or "")
        expected_text = str(expected_block.get("text") or "")

        block_id_match = bool(
            submitted_id
            and expected_id
            and submitted_id == expected_id
        )
        content_equivalent = bool(
            submitted_id
            and expected_id
            and _normalize_attempt_v2_block_text(submitted_text)
            and _normalize_attempt_v2_block_text(submitted_text)
            == _normalize_attempt_v2_block_text(expected_text)
        )
        sequence_correct = bool(block_id_match or content_equivalent)

        submitted_indent = (
            indentation[index]
            if index < len(indentation)
            else None
        )
        expected_indent = (
            expected_indentation[index]
            if index < len(expected_indentation)
            else None
        )
        if expected_indent is None:
            indentation_correct = bool(submitted_id)
        else:
            indentation_correct = bool(
                submitted_id
                and submitted_indent is not None
                and submitted_indent == expected_indent
            )

        slot_correct = bool(sequence_correct and indentation_correct)
        return {
            "slot_index": index,
            "slot_label": f"第{index + 1}格",
            "submitted_block_id": submitted_id or None,
            "expected_block_id": expected_id or None,
            "submitted_text": submitted_text or None,
            "expected_text": expected_text or None,
            "block_id_match": block_id_match,
            "content_equivalent": content_equivalent,
            "submitted_indentation": submitted_indent,
            "expected_indentation": expected_indent,
            "sequence_correct": sequence_correct,
            "indentation_correct": indentation_correct,
            "slot_correct": slot_correct,
        }

    current_rows = [_state_at(actual_order, actual_indentation, i) for i in range(total_slots)]
    previous_rows = (
        [_state_at(previous_order, previous_indentation, i) for i in range(total_slots)]
        if previous
        else []
    )

    corrected_block_ids = []
    remaining_wrong_block_ids = []
    remaining_wrong_submitted_block_ids = []
    newly_wrong_block_ids = []
    corrected_slot_indices = []
    remaining_wrong_slot_indices = []
    newly_wrong_slot_indices = []

    for index, row in enumerate(current_rows):
        previous_row = previous_rows[index] if index < len(previous_rows) else None
        previous_slot_correct = (
            bool(previous_row.get("slot_correct"))
            if isinstance(previous_row, dict)
            else None
        )
        current_slot_correct = bool(row.get("slot_correct"))

        corrected = bool(
            previous_slot_correct is False
            and current_slot_correct is True
        )
        regressed = bool(
            previous_slot_correct is True
            and current_slot_correct is False
        )
        remained_wrong = bool(
            previous_slot_correct is False
            and current_slot_correct is False
        )

        if previous_slot_correct is None:
            status_change = "first_attempt_correct" if current_slot_correct else "first_attempt_wrong"
        elif corrected:
            status_change = "corrected"
        elif regressed:
            status_change = "regressed"
        elif remained_wrong:
            status_change = "still_wrong"
        else:
            status_change = "still_correct"

        row.update({
            "previous_slot_correct": previous_slot_correct,
            "corrected_since_previous": corrected,
            "regressed_since_previous": regressed,
            "remained_wrong": remained_wrong,
            "status_change": status_change,
        })

        expected_id = row.get("expected_block_id")
        submitted_id = row.get("submitted_block_id")
        if corrected:
            corrected_slot_indices.append(index)
            if expected_id:
                corrected_block_ids.append(expected_id)
        if not current_slot_correct:
            remaining_wrong_slot_indices.append(index)
            if expected_id:
                remaining_wrong_block_ids.append(expected_id)
            if submitted_id:
                remaining_wrong_submitted_block_ids.append(submitted_id)
        if regressed:
            newly_wrong_slot_indices.append(index)
            if expected_id:
                newly_wrong_block_ids.append(expected_id)

    def _unique(values):
        return list(dict.fromkeys([value for value in values if value]))

    summary = {
        "previous_attempt_id": str(previous.get("_id")) if previous.get("_id") is not None else None,
        "previous_attempt_no": previous.get("attempt_no"),
        "corrected_block_ids": _unique(corrected_block_ids),
        "remaining_wrong_block_ids": _unique(remaining_wrong_block_ids),
        "remaining_wrong_submitted_block_ids": _unique(remaining_wrong_submitted_block_ids),
        "newly_wrong_block_ids": _unique(newly_wrong_block_ids),
        "corrected_slot_indices": sorted(set(corrected_slot_indices)),
        "remaining_wrong_slot_indices": sorted(set(remaining_wrong_slot_indices)),
        "newly_wrong_slot_indices": sorted(set(newly_wrong_slot_indices)),
        "corrected_block_count": len(set(corrected_slot_indices)),
        "remaining_wrong_block_count": len(set(remaining_wrong_slot_indices)),
        "newly_wrong_block_count": len(set(newly_wrong_slot_indices)),
    }
    return current_rows, summary


def _calculate_attempt_v2_score(
    *,
    is_correct: bool,
    error_count: int,
    correct_answer,
    submitted_order,
) -> float:
    # score is the proportion of slots without a sequence or indentation error.
    if bool(is_correct):
        return 1.0
    correct = correct_answer if isinstance(correct_answer, dict) else {}
    expected_order = correct.get("order") if isinstance(correct.get("order"), list) else []
    actual_order = submitted_order if isinstance(submitted_order, list) else []
    total_slots = len(expected_order) or len(actual_order)
    if total_slots <= 0:
        return 0.0
    incorrect_slots = min(max(int(error_count or 0), 0), total_slots)
    return round((total_slots - incorrect_slots) / total_slots, 6)


def _previous_attempt_v2_error_types(
    student_id: str,
    task_id: str,
    task_attempt_session=None,
) -> list:
    sid = str(student_id or "").strip()
    tid = str(task_id or "").strip()
    if not sid or not tid:
        return []
    query = {"student_id": sid, "task_id": tid}
    if task_attempt_session is not None:
        query["task_attempt_session"] = int(task_attempt_session)
    previous = db.parsons_attempts_v2.find_one(
        query,
        {"error_types": 1, "error_details": 1},
        sort=[("submitted_at", -1), ("created_at", -1), ("_id", -1)],
    )
    error_details = (previous or {}).get("error_details")
    if isinstance(error_details, list) and error_details:
        detail_types = []
        for item in error_details:
            if not isinstance(item, dict):
                continue
            error_type = str(item.get("error_type") or "").strip()
            if error_type:
                detail_types.append(error_type)
        if detail_types:
            return sorted(set(detail_types))
    error_types = (previous or {}).get("error_types")
    return error_types if isinstance(error_types, list) else []


def _normalize_attempt_v2_test_role(test_role):
    role = str(test_role or "").strip().lower()
    if role in ("pre", "pretest"):
        return "pretest"
    if role in ("post", "posttest"):
        return "posttest"
    return None


def _normalize_attempt_v2_test_cycle_id(test_cycle_id):
    cycle_id = str(test_cycle_id or "").strip()
    if not cycle_id or cycle_id == "default":
        return "cycle_01"
    return cycle_id


def _next_attempt_v2_no(
    student_id: str,
    task_id: str,
    activity_type: str,
    test_role,
    task_attempt_session=None,
):
    if task_attempt_session is None:
        _, attempt_no, _, _ = _resolve_task_attempt_session(
            student_id,
            task_id,
            activity_type,
            test_role,
        )
        return attempt_no
    previous = _previous_attempt_v2_doc(
        student_id,
        task_id,
        activity_type,
        test_role,
        task_attempt_session=task_attempt_session,
    )
    try:
        return int((previous or {}).get("attempt_no") or 0) + 1
    except Exception:
        return 1


def _validate_attempt_v2_doc(doc: dict) -> list:
    missing = []
    for key in [
        "student_id",
        "task_id",
        "task_attempt_session",
        "attempt_no",
        "attempt_sequence_no",
        "is_correct",
        "is_test_data",
        "submitted_at",
        "created_at",
        "updated_at",
    ]:
        if key not in doc:
            missing.append(key)
            continue
        value = doc.get(key)
        if key in ("is_correct", "is_test_data"):
            if not isinstance(value, bool):
                missing.append(key)
        elif key in ("task_attempt_session", "attempt_no", "attempt_sequence_no"):
            try:
                if int(value) < 1:
                    missing.append(key)
            except Exception:
                missing.append(key)
        elif value is None or str(value).strip() == "":
            missing.append(key)

    for key in ("started_at", "submitted_at", "created_at", "updated_at"):
        value = doc.get(key)
        if value is not None and not isinstance(value, datetime):
            missing.append(key)
    if not isinstance(doc.get("error_count"), int) or doc.get("error_count", -1) < 0:
        missing.append("error_count")
    for key in ("error_types", "wrong_slots", "incorrect_slots"):
        if not isinstance(doc.get(key), list):
            missing.append(key)
    if not isinstance(doc.get("repeated_error"), bool):
        missing.append("repeated_error")
    if not doc.get("is_correct") and not str(doc.get("error_concept") or "").strip():
        missing.append("error_concept")
    score = doc.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1:
        missing.append("score")
    if isinstance(doc.get("wrong_slots"), list) and isinstance(doc.get("error_count"), int):
        if doc.get("error_count") < len(doc.get("wrong_slots")):
            missing.append("error_count")
    if not isinstance(doc.get("submitted_indentation_by_block"), dict):
        missing.append("submitted_indentation_by_block")
    if not isinstance(doc.get("block_results"), list):
        missing.append("block_results")
    for key in (
        "corrected_block_ids",
        "remaining_wrong_block_ids",
        "remaining_wrong_submitted_block_ids",
        "newly_wrong_block_ids",
        "corrected_slot_indices",
        "remaining_wrong_slot_indices",
        "newly_wrong_slot_indices",
        "ai_hint_texts",
        "ai_hint_viewed_numbers",
        "ai_hint_clicks",
    ):
        if not isinstance(doc.get(key), list):
            missing.append(key)
    for key in (
        "submitted_after_ai_hint",
        "ai_hint_generated_before_submit",
        "ai_hint_viewed_before_submit",
        "ai_hint_clicked",
    ):
        if not isinstance(doc.get(key), bool):
            missing.append(key)
    for key in ("ai_hint_1_meta", "ai_hint_2_meta"):
        if not isinstance(doc.get(key), dict):
            missing.append(key)
    for key in ("ai_hint_generation_count", "ai_hint_view_count"):
        if not isinstance(doc.get(key), int) or doc.get(key) < 0:
            missing.append(key)
    if not isinstance(doc.get("needs_review"), bool):
        missing.append("needs_review")
    if not isinstance(doc.get("review_reason"), list):
        missing.append("review_reason")
    elapsed_sec_raw = doc.get("elapsed_sec_raw")
    if elapsed_sec_raw is not None and (
        isinstance(elapsed_sec_raw, bool) or not isinstance(elapsed_sec_raw, (int, float))
    ):
        missing.append("elapsed_sec_raw")
    for duration_key in ("duration_sec", "duration_seconds"):
        duration_value = doc.get(duration_key)
        if duration_value is not None and (
            isinstance(duration_value, bool)
            or not isinstance(duration_value, (int, float))
            or not 0 <= duration_value <= 3600
        ):
            missing.append(duration_key)
    if not isinstance(doc.get("duration_outlier"), bool):
        missing.append("duration_outlier")
    duration_outlier_reason = doc.get("duration_outlier_reason")
    if duration_outlier_reason is not None and not isinstance(duration_outlier_reason, str):
        missing.append("duration_outlier_reason")
    return missing

# 負責把一次送出的所有資料組成 MongoDB 文件(重要)
def _build_parsons_attempt_v2_doc(
    *,
    data: dict,
    task_doc: dict,
    student_id: str,
    participant_id: str,
    activity_type: str,
    test_role,
    test_cycle_id,
    task_id: str,
    video_id,
    answer_ids,
    answer_lines,
    is_correct: bool,
    score,
):
    del score

    sid = (student_id or "").strip()
    tid = (task_id or "").strip()
    activity = str(activity_type or "").strip() or None
    v2_test_role = _normalize_attempt_v2_test_role(test_role) if activity == "test" else None
    v2_test_cycle_id = _normalize_attempt_v2_test_cycle_id(test_cycle_id) if activity == "test" else None
    submitted_at_input = _parse_attempt_v2_datetime((data or {}).get("submitted_at"))
    submitted_at = submitted_at_input or now_utc()
    started_at = _parse_attempt_v2_datetime((data or {}).get("started_at"))
    duration_fields = _build_attempt_v2_duration_fields(started_at, submitted_at)
    profile = _lookup_attempt_v2_user_profile(sid, participant_id)

    task_attempt_session, attempt_no, attempt_sequence_no, previous_attempt = _resolve_task_attempt_session(
        sid,
        tid,
        activity,
        v2_test_role,
    ) if sid and tid and activity else (1, 1, 1, None)

    submitted_order = list(answer_ids) if isinstance(answer_ids, list) else None
    submitted_indentation = _submitted_indentation_from_lines(answer_lines)
    submitted_indentation_by_block = _submitted_indentation_by_block(
        submitted_order,
        submitted_indentation,
    )
    correct_answer = _correct_answer_from_task(task_doc)
    target_concept = _extract_attempt_v2_target_concept(task_doc)
    block_results, block_change_summary = _build_attempt_v2_block_results(
        task_doc=task_doc,
        submitted_order=submitted_order,
        submitted_indentation=submitted_indentation,
        correct_answer=correct_answer,
        previous_attempt=previous_attempt,
    )

    hint_record_before_submit = None
    if activity == "practice" and sid and tid:
        hint_record_before_submit = _get_hint_record(sid, tid, task_attempt_session)

    ai_hint_generation_count_before_submit = int(
        (hint_record_before_submit or {}).get("ai_hint_generation_count")
        or (hint_record_before_submit or {}).get("hint_generation_count")
        or 0
    )
    ai_hint_view_count_before_submit = int(
        (hint_record_before_submit or {}).get("ai_hint_view_count")
        or (hint_record_before_submit or {}).get("hint_view_count")
        or 0
    )
    ai_hint_1_text = str((hint_record_before_submit or {}).get("ai_hint_1_text") or "").strip()
    ai_hint_2_text = str((hint_record_before_submit or {}).get("ai_hint_2_text") or "").strip()
    ai_hint_1_meta = (
        (hint_record_before_submit or {}).get("ai_hint_1_meta")
        if isinstance((hint_record_before_submit or {}).get("ai_hint_1_meta"), dict)
        else {}
    )
    ai_hint_2_meta = (
        (hint_record_before_submit or {}).get("ai_hint_2_meta")
        if isinstance((hint_record_before_submit or {}).get("ai_hint_2_meta"), dict)
        else {}
    )
    ai_hint_generated_before_submit = bool(
        ai_hint_generation_count_before_submit > 0 or ai_hint_1_text or ai_hint_2_text
    )
    ai_hint_viewed_before_submit = bool(ai_hint_view_count_before_submit > 0)
    ai_hint_texts = []
    if ai_hint_1_text:
        ai_hint_texts.append({"hint_no": 1, "text": ai_hint_1_text, "meta": ai_hint_1_meta})
    if ai_hint_2_text:
        ai_hint_texts.append({"hint_no": 2, "text": ai_hint_2_text, "meta": ai_hint_2_meta})

    previous_error_details = (
        previous_attempt.get("error_details")
        if isinstance((previous_attempt or {}).get("error_details"), list)
        else []
    )
    previous_error_types = []
    for item in previous_error_details:
        if not isinstance(item, dict):
            continue
        error_type = str(item.get("error_type") or "").strip()
        if error_type:
            previous_error_types.append(error_type)
    if not previous_error_types:
        previous_error_types = (
            previous_attempt.get("error_types")
            if isinstance((previous_attempt or {}).get("error_types"), list)
            else []
        )
    error_analysis = _build_attempt_v2_error_analysis(
        is_correct=bool(is_correct),
        submitted_order=submitted_order,
        submitted_indentation=submitted_indentation,
        correct_answer=correct_answer,
        target_concept=target_concept,
        previous_error_types=previous_error_types,
    )
    normalized_score = _calculate_attempt_v2_score(
        is_correct=bool(is_correct),
        error_count=error_analysis.get("error_count", 0),
        correct_answer=correct_answer,
        submitted_order=submitted_order,
    )
    is_test_data = bool(profile.get("is_test_data", False))
    review_reason = []
    if duration_fields.get("duration_outlier"):
        review_reason.append("invalid_duration_sec")
    if not is_test_data and not profile.get("group_type"):
        review_reason.append("missing_group_type")
    if not is_test_data and str(target_concept or "").strip().lower() == "unknown":
        review_reason.append("unknown_target_concept")
    now = now_utc()

    doc = {
        "schema_version": 2,
        "student_id": sid or None,
        "class_name": profile.get("class_name"),
        "group_type": profile.get("group_type"),
        "feedback_strategy": profile.get("feedback_strategy") or "B",
        "is_test_data": is_test_data,
        "activity_type": activity,
        "test_role": v2_test_role,
        "test_cycle_id": v2_test_cycle_id,
        "task_id": tid or None,
        "video_id": normalize_video_id(video_id) or None,
        "task_title": _extract_attempt_v2_task_title(task_doc),
        "target_concept": target_concept,
        "task_attempt_session": int(task_attempt_session),
        "attempt_no": int(attempt_no),
        "attempt_sequence_no": int(attempt_sequence_no),
        "is_correct": bool(is_correct),
        "score": normalized_score,
        "submitted_order": submitted_order,
        "submitted_indentation": submitted_indentation,
        "submitted_indentation_by_block": submitted_indentation_by_block,
        "correct_answer": correct_answer,
        "block_results": block_results,
        **block_change_summary,

        # 方便直接從 parsons_attempts_v2 分析 AI 提示內容與點擊摘要。
        "ai_hint_prompt_version": str((hint_record_before_submit or {}).get("hint_prompt_version") or _PARSONS_HINT_PROMPT_VERSION),
        "ai_hint_1_text": ai_hint_1_text or None,
        "ai_hint_1_meta": ai_hint_1_meta,
        "ai_hint_2_text": ai_hint_2_text or None,
        "ai_hint_2_meta": ai_hint_2_meta,
        "ai_hint_texts": ai_hint_texts,
        "ai_hint_generation_count": ai_hint_generation_count_before_submit,
        "ai_hint_view_count": ai_hint_view_count_before_submit,
        "ai_hint_clicked": ai_hint_viewed_before_submit,
        "ai_hint_viewed_numbers": [],
        "ai_hint_clicks": [],
        "ai_hint_last_viewed_no": int((hint_record_before_submit or {}).get("latest_ai_hint_no") or 0),
        "ai_hint_last_viewed_at": None,
        "ai_hint_last_viewed_at_taiwan": None,

        "submitted_after_ai_hint": ai_hint_viewed_before_submit,
        "ai_hint_generated_before_submit": ai_hint_generated_before_submit,
        "ai_hint_viewed_before_submit": ai_hint_viewed_before_submit,
        "ai_hint_generation_count_before_submit": ai_hint_generation_count_before_submit,
        "ai_hint_view_count_before_submit": ai_hint_view_count_before_submit,
        "latest_ai_hint_no_before_submit": int(
            (hint_record_before_submit or {}).get("latest_ai_hint_no") or 0
        ),
        "source_hint_id": (
            (hint_record_before_submit or {}).get("hint_id")
            if hint_record_before_submit
            else None
        ),
        **error_analysis,
        "started_at": started_at,
        "started_at_utc": started_at,
        "submitted_at": submitted_at,
        "submitted_at_utc": submitted_at,
        "submitted_at_taiwan": _taiwan_time_string(submitted_at),
        **duration_fields,
        "needs_review": bool(review_reason),
        "review_reason": review_reason,
        "created_at": now,
        "created_at_utc": now,
        "created_at_taiwan": _taiwan_time_string(now),
        "updated_at": now,
        "updated_at_utc": now,
        "updated_at_taiwan": _taiwan_time_string(now),
        "timezone": _PARSONS_ATTEMPTS_V2_TIMEZONE,
    }

    missing = _validate_attempt_v2_doc(doc)
    if missing:
        raise ValueError("missing required parsons_attempts_v2 fields: " + ", ".join(missing))
    return doc


def _insert_parsons_attempt_v2(**kwargs):
    doc = _build_parsons_attempt_v2_doc(**kwargs)
    ins = db.parsons_attempts_v2.insert_one(doc)
    return str(ins.inserted_id), doc


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


# ========================

def get_default_test_cycle_id() -> str:
    """預設測驗批次。v1.8 統一使用 test_control，不再依賴 parsons_test_cycles。"""
    return "default"

def is_posttest_open(test_cycle_id: str) -> bool:
    """是否開放後測（統一讀 test_control）。"""
    test_cycle_id = (test_cycle_id or "default").strip() or "default"
    doc = db.test_control.find_one({"_id": f"post_open:{test_cycle_id}"}) or {}
    return bool(doc.get("post_open", False))


def _normalize_dt_for_sort(v):
    """Best-effort datetime normalization for created_at sorting."""
    if isinstance(v, datetime):
        return v
    if isinstance(v, str) and v.strip():
        s = v.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def _refresh_first_wrong_flag_for_group(
    student_id: str,
    task_id: str,
    task_attempt_session=None,
) -> str:
    """Mark the earliest wrong attempt inside one task-attempt session."""
    sid = (student_id or "").strip()
    tid = (task_id or "").strip()
    if not sid or sid.lower() == "unknown" or not tid:
        return ""

    query = {"student_id": sid, "task_id": tid}
    if task_attempt_session is not None:
        query["task_attempt_session"] = int(task_attempt_session)

    docs = list(db.parsons_attempts.find(
        query,
        {"_id": 1, "is_correct": 1, "created_at": 1}
    ))
    if not docs:
        return ""

    docs.sort(key=lambda d: (_normalize_dt_for_sort(d.get("created_at")), str(d.get("_id") or "")))

    first_wrong_id = None
    for doc in docs:
        if bool(doc.get("is_correct", False)):
            continue
        first_wrong_id = doc.get("_id")
        break

    db.parsons_attempts.update_many(query, {"$set": {"is_first_wrong": False}})

    if first_wrong_id is not None:
        db.parsons_attempts.update_one(
            {"_id": first_wrong_id},
            {"$set": {"is_first_wrong": True}},
        )
        return str(first_wrong_id)
    return ""


# ========================
# [新增] 將資料庫的 task doc 轉成前端 Parsons.vue 期待的格式
def t5doc_to_parsons_task(doc: dict) -> dict:
    """
    將 parsons_tasks / parsons_test_tasks 來源任務（doc）轉成前端 Parsons.vue 期待的格式。
    - 支援 template_slots 可能是 dict list 或 str list（舊資料/手動匯入）。
    """
    question_text = doc.get("question_text") or doc.get("question") or ""
    solution_blocks = doc.get("solution_blocks") or []
    distractor_blocks = doc.get("distractor_blocks") or [] #移除停用的干擾 block。
    template_slots = doc.get("template_slots") or [] #打亂右側 block 池順序。

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
                    "indent": b.get("indent") if b.get("indent") is not None else b.get("indent_level", 0),
                    "semantic_zh": b.get("semantic_zh") if b.get("semantic_zh") is not None else b.get("meaning_zh", ""),
                    "meaning_zh": b.get("meaning_zh") if b.get("meaning_zh") is not None else b.get("semantic_zh", ""),
                    "zh": b.get("zh", ""),
                })
            else:
                out.append({
                    "id": f"b{i+1}",
                    "text": str(b),
                    "type": "solution",
                    "semantic_zh": "",
                    "meaning_zh": "",
                    "zh": "",
                })
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

    # [新增] 老師端若標記 enabled=false，學生端不顯示該干擾區塊
    try:
        distractor_blocks = [
            b for b in (distractor_blocks or [])
            if not (isinstance(b, dict) and b.get("enabled") is False)
        ]
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

    # 統一 slot 數量：以正解 block 數為準，避免出現「13 解答但只顯示 10 格」。
    expected_len = len(solution_blocks)
    if not template_slots:
        template_slots = []

    # 不足則補齊；過多則截斷
    if len(template_slots) < expected_len:
        for i in range(len(template_slots), expected_len):
            template_slots.append({"slot": f"s{i+1}", "label": f"第{i+1}格"})
    elif len(template_slots) > expected_len:
        template_slots = template_slots[:expected_len]

    # 重新對齊 expected_id：固定以 solution_blocks 順序為準，避免舊資料錯位。
    for i in range(expected_len):
        if not isinstance(template_slots[i], dict):
            template_slots[i] = {"slot": f"s{i+1}", "label": f"第{i+1}格"}
        template_slots[i]["slot"] = str(template_slots[i].get("slot") or f"s{i+1}")
        template_slots[i]["label"] = str(template_slots[i].get("label") or f"第{i+1}格")
        template_slots[i]["expected_id"] = solution_blocks[i]["id"]

    pool = doc.get("pool")
    if not pool:
        pool = solution_blocks + distractor_blocks
    else:
        # [新增] 舊資料若 pool 仍含已隱藏干擾，回傳前再過濾一次
        hidden_ids = {
            str(b.get("id") or b.get("_id") or "")
            for b in (doc.get("distractor_blocks") or [])
            if isinstance(b, dict) and b.get("enabled") is False
        }
        if hidden_ids:
            _pool = []
            for p in (pool or []):
                pid = str((p or {}).get("id") or (p or {}).get("_id") or "") if isinstance(p, dict) else ""
                if pid and pid in hidden_ids:
                    continue
                _pool.append(p)
            pool = _pool

    # 學生端片段池需要亂序，避免不同題型或不同資料來源暴露原始順序。
    if isinstance(pool, list) and len(pool) > 1:
        pool = list(pool)
        random.shuffle(pool)

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
        "hide_semantic_zh": bool(doc.get("hide_semantic_zh", False)),
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
    incoming_task_code = (data.get("task_code") or "").strip()
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
        "source_type": "fixed",
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
        "ai_slot_hints": {}, # AI 推估的 slot -> 中文提示
        "subtitle_range": subtitle_range,
        "version": "fixed",
    }
    print("data keys =", list(data.keys()))
    print("source_subtitle =", data.get("source_subtitle"))
    print("subtitle_range =", data.get("subtitle_range"))

    if vid_oid:
        doc_set["video_id"] = vid_oid

    # upsert：同影片只保留一筆 fixed（相容舊欄位 source_type）
    q = {"$and": [{"$or": [{"gen_source": "fixed"}, {"source_type": "fixed"}]}]}
    if vid_oid:
        q["$and"].append({"video_id": vid_oid})
    else:
        q["$and"].append({"video_id_str": video_id})

    existing = db.parsons_tasks.find_one(q)
    if existing:
        if not (existing.get("task_code") or "").strip():
            doc_set["task_code"] = incoming_task_code or "FIXED-01"
        if not existing.get("created_at"):
            existing_id = existing.get("_id")
            doc_set["created_at"] = (
                existing.get("created_at_utc")
                or (existing_id.generation_time if isinstance(existing_id, ObjectId) else now)
            )
        db.parsons_tasks.update_one({"_id": existing["_id"]}, {"$set": doc_set})
        task_id = str(existing["_id"])
    else:
        doc_set["task_code"] = incoming_task_code or "FIXED-01"
        doc_set["created_at"] = now
        result = db.parsons_tasks.insert_one(doc_set)
        task_id = str(result.inserted_id)

    return jsonify({"ok": True, "task_id": task_id})

# ========================
# GET /fixed_task/get 供前端載入已儲存的固定題
@parsons_bp.get("/fixed_task/get")
def get_fixed_task():
    """取得指定影片的固定題；影片跳轉與字幕對齊欄位已移除。"""
    video_id = (request.args.get("video_id") or "").strip()
    if not video_id:
        return jsonify({"ok": False, "message": "missing video_id"}), 400

    vid_oid = maybe_oid(video_id)
    q = {
        "$and": [
            {"$or": [{"gen_source": "fixed"}, {"source_type": "fixed"}]},
            ({"$or": [{"video_id": vid_oid}, {"video_id_str": video_id}]} if vid_oid else {"video_id_str": video_id}),
        ]
    }
    task = db.parsons_tasks.find_one(q)
    if not task:
        return jsonify({"ok": True, "found": False})

    return jsonify({
        "ok": True,
        "found": True,
        "task_id": str(task["_id"]),
        "question_text": task.get("question_text") or "",
        "solution_lines": [b.get("text", "") for b in (task.get("solution_blocks") or [])],
        "distractor_lines": [b.get("text", "") for b in (task.get("distractor_blocks") or [])],
        "enabled": bool(task.get("enabled", False)),
        "status": task.get("status") or "pending",
    })


# ========================


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
    task_id_str = str(task.get("_id"))

    previous_completion = None
    previous_completion_source = None
    student_id = current_student_id()
    feedback_profile = _student_feedback_profile(student_id)
    if student_id and task_id_str:
        projection = {
            "_id": 1,
            "submitted_at": 1,
            "submitted_at_taiwan": 1,
            "created_at": 1,
            "task_attempt_session": 1,
            "attempt_no": 1,
            "attempt_sequence_no": 1,
        }
        previous_completion = db.parsons_attempts_v2.find_one(
            {
                "student_id": str(student_id),
                "task_id": task_id_str,
                "activity_type": "practice",
                "test_role": None,
                "is_correct": True,
            },
            projection,
            sort=[("submitted_at", -1), ("created_at", -1), ("_id", -1)],
        )
        previous_completion_source = "parsons_attempts_v2" if previous_completion else None

        if not previous_completion:
            previous_completion = db.parsons_attempts.find_one(
                {
                    "student_id": str(student_id),
                    "task_id": task_id_str,
                    "is_correct": True,
                },
                projection,
                sort=[("submitted_at", -1), ("created_at", -1), ("_id", -1)],
            )
            previous_completion_source = "parsons_attempts" if previous_completion else None

    previous_completed_at = None
    if previous_completion:
        previous_completed_at = (
            previous_completion.get("submitted_at")
            or previous_completion.get("created_at")
        )

    return jsonify({
        "ok": True,
        "noTask": False,
        "task_id": task_id_str,
        "video_id": normalize_video_id(task.get("video_id")),
        "level": task.get("level"),
        "feedback_strategy": feedback_profile.get("feedback_strategy") or "B",
        "group_type": feedback_profile.get("group_type"),
        "question_text": parsed.get("question_text", ""),
        "hide_semantic_zh": bool(parsed.get("hide_semantic_zh", False)),
        "pool": parsed.get("pool", []),
        "template_slots": parsed.get("template_slots", []),
        "solution_blocks": parsed.get("solution_blocks", []),
        "distractor_blocks": parsed.get("distractor_blocks", []),
        "ai_feedback": parsed.get("ai_feedback", {}),
        "version": task.get("version", "v1.AI"),
        "has_previous_completion": bool(previous_completion),
        "previous_completion_data_source": previous_completion_source,
        "previous_completed_at": _utc_iso_string(previous_completed_at),
        "previous_completed_at_taiwan": (
            previous_completion.get("submitted_at_taiwan")
            if previous_completion and previous_completion.get("submitted_at_taiwan")
            else _taiwan_time_string(previous_completed_at)
        ),
        "previous_task_attempt_session": (
            previous_completion.get("task_attempt_session")
            if previous_completion
            else None
        ),
    })


# =========================
# (A) POST /publish  老師端：發布題目（同影片同 level 只允許一題 enabled）
# =========================
@parsons_bp.post("/publish")
def publish_task():
    """發布題目；不再建立影片跳轉所需的字幕 IR 快取。"""
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
        {"$set": {"enabled": False, "review_status": "draft"}},
    )
    if isinstance(task_video_id, str):
        video_oid = maybe_oid(task_video_id)
        if video_oid:
            db.parsons_tasks.update_many(
                {"video_id": video_oid, "level": task_level, "_id": {"$ne": oid}},
                {"$set": {"enabled": False, "review_status": "draft"}},
            )

    result = db.parsons_tasks.update_one(
        {"_id": oid},
        {"$set": {
            "enabled": True,
            "review_status": "published",
            "published_at": now_utc(),
        }},
    )
    return jsonify({
        "ok": True,
        "matched": result.matched_count,
        "modified": result.modified_count,
    }), 200



# =========================
# Parsons 作答、錯誤診斷與提示流程
# =========================


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

# 載入前測、後測題目
@parsons_bp.get("/test/status")
def test_status():
    ensure_test_indexes()
    student_id = current_student_id()
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
        return False
    return bool(doc.get("pre_open", False))

# 前後測開放判斷
def _test_question_collection_name(test_role: str):
    role = str(test_role or "").strip().lower()
    if role == "pre":
        return "pre_parsons_questions"
    if role == "post":
        return "post_parsons_questions"
    return None


def _test_question_task_id(question_doc: dict) -> str:
    if not isinstance(question_doc, dict):
        return ""
    for key in ("task_id", "question_id", "test_task_id"):
        value = question_doc.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    if question_doc.get("_id") is not None:
        return str(question_doc.get("_id"))
    return ""


def _test_question_sort_key():
    return [("order", 1), ("question_id", 1), ("_id", 1)]


def _active_test_question_query(question_types=None):
    types = list(question_types or ["parsons", None])
    type_conditions = []
    if types:
        type_conditions.append({"question_type": {"$in": types}})
        type_conditions.append({"type": {"$in": [item for item in types if item is not None]}})

    return {
        "$and": [
            {"$or": type_conditions or [{"question_type": {"$in": ["parsons", None]}}]},
            {"is_active": {"$ne": False}},
            {"active": {"$ne": False}},
        ]
    }


def _choice_test_question_types():
    return ["choice", "choices", "mcq", "multiple_choice", "single_choice"]


def _is_choice_test_question(question_doc: dict) -> bool:
    if not isinstance(question_doc, dict):
        return False
    qtype = str(
        question_doc.get("question_type")
        or question_doc.get("type")
        or ""
    ).strip().lower()
    if qtype in set(_choice_test_question_types()):
        return True
    return isinstance(question_doc.get("options"), list) and bool(
        question_doc.get("answer_key")
        or question_doc.get("correct_answer")
        or question_doc.get("answer")
    )


def _normalize_choice_options(question_doc: dict) -> list:
    raw_options = question_doc.get("options") if isinstance(question_doc, dict) else []
    if not isinstance(raw_options, list):
        return []

    labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    output = []
    for index, option in enumerate(raw_options):
        default_key = labels[index] if index < len(labels) else str(index + 1)
        if isinstance(option, dict):
            key = str(
                option.get("key")
                or option.get("label")
                or option.get("value")
                or default_key
            ).strip()
            text = str(
                option.get("text")
                or option.get("label_text")
                or option.get("content")
                or option.get("value")
                or ""
            ).strip()
        else:
            key = default_key
            text = str(option or "").strip()
            match = re.match(r"^\s*([A-Za-z])[\.\)、)]\s*(.+)$", text)
            if match:
                key = match.group(1).upper()
                text = match.group(2).strip()

        if not key:
            key = default_key
        output.append({
            "key": str(key).strip().upper(),
            "text": text,
        })
    return output


def _choice_answer_key(question_doc: dict) -> str:
    raw = ""
    if isinstance(question_doc, dict):
        raw = (
            question_doc.get("answer_key")
            or question_doc.get("correct_answer")
            or question_doc.get("answer")
            or question_doc.get("correct_option")
            or ""
        )
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.isdigit():
        idx = int(text)
        options = _normalize_choice_options(question_doc)
        if 0 <= idx < len(options):
            return str(options[idx].get("key") or "").strip().upper()
        if 1 <= idx <= len(options):
            return str(options[idx - 1].get("key") or "").strip().upper()
    return text.upper()


def _choice_question_text(question_doc: dict) -> str:
    if not isinstance(question_doc, dict):
        return ""
    return str(
        question_doc.get("stem")
        or question_doc.get("instruction")
        or question_doc.get("question_text")
        or question_doc.get("question")
        or question_doc.get("title")
        or ""
    ).strip()


def _find_pretest_choice_question_by_ref(task_ref: str):
    ref = str(task_ref or "").strip()
    if not ref:
        return None

    query = _active_test_question_query(_choice_test_question_types())
    candidates = [{"question_id": ref}, {"task_id": ref}, {"test_task_id": ref}]
    oid_value = maybe_oid(ref)
    if oid_value is not None:
        candidates.append({"_id": oid_value})

    doc = db.pre_parsons_questions.find_one({"$and": [query, {"$or": candidates}]})
    if doc:
        return doc

    question_query = {
        "$and": [
            {"type": {"$in": ["mcq", "choice", "multiple_choice", "single_choice"]}},
            {"active": {"$ne": False}},
            {"is_active": {"$ne": False}},
            {"$or": candidates},
        ]
    }
    return db.questions.find_one(question_query)


def _find_pretest_choice_question_by_index(index: int):
    current_index = max(1, int(index or 1))
    query = _active_test_question_query(_choice_test_question_types())
    total = db.pre_parsons_questions.count_documents(query)
    if total > 0:
        if current_index > total:
            current_index = total
        docs = list(
            db.pre_parsons_questions
            .find(query)
            .sort(_test_question_sort_key())
            .skip(current_index - 1)
            .limit(1)
        )
        return (docs[0] if docs else None), total, current_index

    question_query = {
        "$and": [
            {"type": {"$in": ["mcq", "choice", "multiple_choice", "single_choice"]}},
            {"active": {"$ne": False}},
            {"is_active": {"$ne": False}},
        ]
    }
    total = db.questions.count_documents(question_query)
    if total <= 0:
        return None, 0, current_index
    if current_index > total:
        current_index = total
    docs = list(
        db.questions
        .find(question_query)
        .sort(_test_question_sort_key())
        .skip(current_index - 1)
        .limit(1)
    )
    return (docs[0] if docs else None), total, current_index


def _find_test_question_by_ref(test_role: str, task_ref: str):
    collection_name = _test_question_collection_name(test_role)
    ref = str(task_ref or "").strip()
    if not collection_name or not ref:
        return None

    if str(test_role or "").strip().lower() == "pre":
        choice_doc = _find_pretest_choice_question_by_ref(ref)
        if choice_doc:
            return choice_doc

    query = _active_test_question_query()
    candidates = [{"question_id": ref}, {"task_id": ref}, {"test_task_id": ref}]
    oid_value = maybe_oid(ref)
    if oid_value is not None:
        candidates.append({"_id": oid_value})
    return db[collection_name].find_one({"$and": [query, {"$or": candidates}]})


def _find_test_question_by_index(test_role: str, index: int):
    collection_name = _test_question_collection_name(test_role)
    if not collection_name:
        return None, 0, 1

    if str(test_role or "").strip().lower() == "pre":
        choice_doc, choice_total, choice_index = _find_pretest_choice_question_by_index(index)
        if choice_doc:
            return choice_doc, choice_total, choice_index

    current_index = max(1, int(index or 1))
    query = _active_test_question_query()
    total = db[collection_name].count_documents(query)
    if total <= 0:
        return None, 0, current_index

    if current_index > total:
        current_index = total
    docs = list(
        db[collection_name]
        .find(query)
        .sort(_test_question_sort_key())
        .skip(current_index - 1)
        .limit(1)
    )
    return (docs[0] if docs else None), total, current_index


def _parsons_question_to_task_doc(question_doc: dict) -> dict:
    question = question_doc if isinstance(question_doc, dict) else {}
    raw_blocks = question.get("blocks") if isinstance(question.get("blocks"), list) else []
    raw_solution = question.get("solution") if isinstance(question.get("solution"), list) else []
    blocks_by_id = {}

    for index, block in enumerate(raw_blocks):
        if not isinstance(block, dict):
            continue
        block_id = str(block.get("block_id") or block.get("id") or f"b{index + 1}").strip()
        if not block_id:
            continue
        blocks_by_id[block_id] = {
            "id": block_id,
            "text": str(block.get("code") if block.get("code") is not None else block.get("text", "")),
            "type": block.get("type") or "solution",
            "indent": int(block.get("indent_level") or block.get("indent") or 0),
            "semantic_zh": block.get("semantic_zh") or block.get("meaning_zh") or "",
            "meaning_zh": block.get("meaning_zh") or block.get("semantic_zh") or "",
            "zh": block.get("zh") or "",
        }

    solution_blocks = []
    template_slots = []
    for index, slot in enumerate(raw_solution):
        if isinstance(slot, dict):
            block_id = str(slot.get("block_id") or slot.get("id") or "").strip()
            indent_level = int(slot.get("indent_level") or slot.get("indent") or 0)
        else:
            block_id = str(slot or "").strip()
            indent_level = 0
        if not block_id:
            continue
        block = dict(blocks_by_id.get(block_id) or {"id": block_id, "text": "", "type": "solution"})
        block["indent"] = indent_level
        solution_blocks.append(block)
        template_slots.append({
            "slot": f"s{index + 1}",
            "label": f"第{index + 1}格",
            "expected_id": block_id,
        })

    if not solution_blocks and blocks_by_id:
        for index, block in enumerate(blocks_by_id.values()):
            solution_blocks.append(dict(block))
            template_slots.append({
                "slot": f"s{index + 1}",
                "label": f"第{index + 1}格",
                "expected_id": block["id"],
            })

# 回傳的MongoDB前後測題目格式
    task_id = _test_question_task_id(question)
    return {
        "_id": question.get("_id"),
        "question_id": task_id,
        "task_id": task_id,
        "task_title": question.get("title"),
        "title": question.get("title"),
        "question_text": question.get("instruction") or question.get("question_text") or question.get("title") or "",
        "concept_tag": question.get("concept_tag") or question.get("target_concept") or question.get("concept"),
        "target_concept": question.get("target_concept") or question.get("concept_tag") or question.get("concept"),
        "difficulty": question.get("difficulty"),
        "solution_blocks": solution_blocks,
        "distractor_blocks": [],
        "pool": list(blocks_by_id.values()),
        "template_slots": template_slots,
        "hide_semantic_zh": bool(question.get("hide_semantic_zh", True)),
    }


def _build_test_question_response(question_doc: dict, total: int, current_index: int):
    if _is_choice_test_question(question_doc):
        task_id = _test_question_task_id(question_doc)
        return {
            "ok": True,
            "task_id": task_id,
            "question_id": task_id,
            "question_type": "choice",
            "question_text": _choice_question_text(question_doc),
            "options": _normalize_choice_options(question_doc),
            "total": int(total or 1),
            "current_index": int(current_index or 1),
            "data_source": "pretest_choice_questions",
        }

    task_doc = _parsons_question_to_task_doc(question_doc)
    parsed = t5doc_to_parsons_task(task_doc)
    task_id = _test_question_task_id(question_doc)
    return {
        "ok": True,
        "task_id": task_id,
        "question_text": parsed.get("question_text") or "",
        "solution_blocks": parsed.get("solution_blocks") or [],
        "template_slots": parsed.get("template_slots") or [],
        "pool": parsed.get("pool") or [],
        "total": int(total or 1),
        "current_index": int(current_index or 1),
        "question_type": "parsons",
        "data_source": "pre_post_parsons_questions",
    }

# 紀錄前後測結果
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
    if test_role == "pre" and not is_pretest_open(test_cycle_id):
        return jsonify({"ok": False, "message": "pretest not open"}), 403
    if test_role == "post" and not is_posttest_open(test_cycle_id):
        return jsonify({"ok": False, "message": "posttest not open"}), 403

    requested_index = request.args.get("next_index") or request.args.get("index") or 1
    try:
        requested_index = int(requested_index)
    except Exception:
        requested_index = 1

    question_doc, total, current_index = _find_test_question_by_index(test_role, requested_index)
    if question_doc:
        return jsonify(_build_test_question_response(question_doc, total, current_index))

    tt = db.parsons_test_tasks.find_one({"test_cycle_id": test_cycle_id, "test_role": test_role})
    if not tt:
        return jsonify({"ok": False, "message": "test task not configured"}), 404

    # 取得原始題目
    raw_source = tt.get("task_id") or tt.get("source_task_id")
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
        "task_id": str(task_doc.get("_id")),
        "question_text": task_doc.get("question_text") or "",
        "template_slots": parsed.get("template_slots") or [],
        "pool": parsed.get("pool") or [],
        "total": 1, # 目前設計為一人一題
        "current_index": 1
    })


def _submit_choice_test_answer(
    *,
    data,
    task,
    task_identity,
    student_id,
    participant_id,
    test_cycle_id,
    test_role,
    payload_duration_sec,
):
    selected_answer = str(
        data.get("selected_answer")
        or data.get("answer")
        or data.get("choice")
        or data.get("selected_choice")
        or ""
    ).strip().upper()
    if not selected_answer:
        return jsonify({
            "ok": False,
            "error": "incomplete_answer",
            "message": "請先選擇一個答案後再送出。",
            "missing_count": 1,
            "missing_indices": [0],
            "all_blank": True,
        }), 400

    options = _normalize_choice_options(task)
    option_keys = {str(option.get("key") or "").strip().upper() for option in options}
    if option_keys and selected_answer not in option_keys:
        return jsonify({
            "ok": False,
            "message": "invalid selected_answer",
        }), 400

    correct_key = _choice_answer_key(task)
    is_correct = bool(correct_key and selected_answer == correct_key)
    score = 1.0 if is_correct else 0.0

    submitted_at_input = _parse_attempt_v2_datetime(data.get("submitted_at"))
    submitted_at = submitted_at_input or now_utc()
    started_at = _parse_attempt_v2_datetime(data.get("started_at"))
    duration_fields = _build_attempt_v2_duration_fields(started_at, submitted_at)
    duration_seconds = duration_fields.get("duration_seconds")
    if duration_seconds is None and not duration_fields.get("duration_outlier"):
        duration_seconds = payload_duration_sec
        duration_fields["duration_sec"] = payload_duration_sec
        duration_fields["duration_seconds"] = payload_duration_sec
    created_at = now_utc()

    selected_text = ""
    for option in options:
        if str(option.get("key") or "").strip().upper() == selected_answer:
            selected_text = str(option.get("text") or "").strip()
            break

    attempt_doc = {
        "student_id": student_id,
        "participant_id": participant_id or None,
        "test_cycle_id": test_cycle_id,
        "test_role": test_role,
        "task_id": task_identity,
        "question_id": task_identity,
        "question_type": "choice",
        "task_title": task.get("title") or task.get("task_title") or _choice_question_text(task),
        "target_concept": task.get("target_concept") or task.get("concept_tag") or task.get("unit"),
        "attempt_no": 1,
        "selected_answer": selected_answer,
        "selected_answer_text": selected_text,
        "answer": selected_answer,
        "answer_text": selected_text,
        "answer_ids": [],
        "answer_lines": [],
        "answer_block_ids": [],
        "submitted_order": [],
        "submitted_indentation": [],
        "submitted_indentation_by_block": {},
        "submitted_blocks": [],
        "choice_options": options,
        "correct_answer": correct_key,
        "expected_answer": correct_key,
        "is_correct": is_correct,
        "score": score,
        "duration_sec": duration_seconds,
        "duration_seconds": duration_seconds,
        "elapsed_sec_raw": duration_fields.get("elapsed_sec_raw"),
        "duration_outlier": bool(duration_fields.get("duration_outlier")),
        "duration_outlier_reason": duration_fields.get("duration_outlier_reason"),
        "wrong_indices": [] if is_correct else [0],
        "wrong_indices_all": [] if is_correct else [0],
        "id_mismatch_indices": [],
        "indent_errors": [],
        "wrong_slots": [] if is_correct else [0],
        "error_count": 0 if is_correct else 1,
        "error_types": [] if is_correct else ["choice_error"],
        "error_details": [],
        "error_concept": task.get("concept_tag") or task.get("unit"),
        "repeated_error": False,
        "repeated_error_types": [],
        "repeated_error_count": 0,
        "repeated_error_basis": None,
        "repeated_error_rule_version": None,
        "extra_wrong_count": 0,
        "total_slots": 1,
        "started_at": started_at,
        "started_at_utc": started_at,
        "submitted_at": submitted_at,
        "submitted_at_utc": submitted_at,
        "submitted_at_taiwan": _taiwan_time_string(submitted_at),
        "created_at": created_at,
        "created_at_utc": created_at,
        "created_at_taiwan": _taiwan_time_string(created_at),
        "updated_at": created_at,
        "updated_at_utc": created_at,
        "updated_at_taiwan": _taiwan_time_string(created_at),
        "timezone": _PARSONS_ATTEMPTS_V2_TIMEZONE,
        "data_source": "pretest_choice_questions",
    }

    try:
        ins = db.parsons_test_attempts.insert_one(attempt_doc)
        attempt_id = str(ins.inserted_id)
    except DuplicateKeyError:
        existing_attempt = db.parsons_test_attempts.find_one(
            {
                "student_id": student_id,
                "test_cycle_id": test_cycle_id,
                "test_role": test_role,
                "task_id": task_identity,
            },
            {"_id": 1, "is_correct": 1, "score": 1},
        )
        return jsonify({
            "ok": True,
            "already_submitted": True,
            "attempt_id": str((existing_attempt or {}).get("_id") or ""),
            "is_correct": (existing_attempt or {}).get("is_correct"),
            "score": (existing_attempt or {}).get("score"),
            "message": "此題已提交，不能重複作答。",
        })
    except Exception as e:
        return jsonify({"ok": False, "message": "choice test attempt write failed", "detail": str(e)}), 500

    write_learning_log_safely({
        "session_id": data.get("session_id"),
        "student_id": student_id,
        "event_type": "answer_submit",
        "page": data.get("page") or "pretest_choice",
        "activity_type": "test",
        "test_role": test_role,
        "task_id": task_identity,
        "attempt_id": attempt_id,
        "attempt_no": 1,
        "target_concept": attempt_doc.get("target_concept"),
        "event_at": submitted_at,
        "metadata": {
            "question_type": "choice",
            "selected_answer": selected_answer,
            "is_correct": is_correct,
            "score": score,
            "error_count": attempt_doc.get("error_count"),
            "error_types": attempt_doc.get("error_types", []),
            "wrong_slots": attempt_doc.get("wrong_slots", []),
            "repeated_error": False,
        },
    })

    return jsonify({
        "ok": True,
        "already_submitted": False,
        "attempt_id": attempt_id,
        "attempt_v2_id": None,
        "attempt_no": 1,
        "question_type": "choice",
        "is_correct": is_correct,
        "score": score,
    })


# 提交前後測答案
@parsons_bp.post("/test/submit")
@prevent_duplicate_submission
def submit_test_answer():
    '''
    payload:
      - student_id
      - test_cycle_id
      - test_role: pre/post
      - task_id
      - answer_ids: [block_id,...]
      - duration_sec
    '''
    ensure_test_indexes()
    data = request.get_json(silent=True) or {}

    student_id = current_student_id()
    participant_id = current_participant_id()
    test_cycle_id = (data.get("test_cycle_id") or get_default_test_cycle_id()).strip() or get_default_test_cycle_id()
    test_role = (data.get("test_role") or "").strip().lower()
    request_task_id = (
        data.get("task_id")
        or data.get("source_task_id")
        or data.get("test_task_id")
        or ""
    ).strip()
    answer_ids = data.get("answer_ids") or []
    answer_lines = data.get("answer_lines") or []
    payload_duration_sec = _safe_duration_seconds(data.get("duration_sec"))

    if not student_id:
        return jsonify({"ok": False, "message": "missing student_id"}), 400
    if test_role not in ("pre", "post"):
        return jsonify({"ok": False, "message": "invalid test_role"}), 400

    if test_role == "pre" and not is_pretest_open(test_cycle_id):
        return jsonify({"ok": False, "message": "pretest not open"}), 403
    if test_role == "post" and not is_posttest_open(test_cycle_id):
        return jsonify({"ok": False, "message": "posttest not open"}), 403

    # 取得題目
    task_identity = request_task_id
    try:
        task = db.parsons_tasks.find_one({"_id": ObjectId(request_task_id)})
    except Exception:
        task = None
    if not task:
        question_doc = _find_test_question_by_ref(test_role, request_task_id)
        if question_doc:
            task = question_doc if _is_choice_test_question(question_doc) else _parsons_question_to_task_doc(question_doc)
            task_identity = _test_question_task_id(question_doc)
    if not task:
        return jsonify({"ok": False, "message": "task not found"}), 404
    if not task_identity:
        task_identity = str(task.get("question_id") or task.get("task_id") or task.get("_id") or "")
    existing_attempt = db.parsons_test_attempts.find_one(
        {
            "student_id": student_id,
            "test_cycle_id": test_cycle_id,
            "test_role": test_role,
            "task_id": task_identity,
        },
        {"_id": 1, "is_correct": 1, "score": 1, "submitted_at": 1},
    )
    if existing_attempt:
        return jsonify({
            "ok": True,
            "already_submitted": True,
            "attempt_id": str(existing_attempt.get("_id")),
            "is_correct": existing_attempt.get("is_correct"),
            "score": existing_attempt.get("score"),
            "message": "此題已提交，不能重複作答。",
        })

    if _is_choice_test_question(task):
        return _submit_choice_test_answer(
            data=data,
            task=task,
            task_identity=task_identity,
            student_id=student_id,
            participant_id=participant_id,
            test_cycle_id=test_cycle_id,
            test_role=test_role,
            payload_duration_sec=payload_duration_sec,
        )

    parsed = t5doc_to_parsons_task(task)
    expected_ids = [str(s.get("expected_id")) for s in (parsed.get("template_slots") or [])]
    missing_answer_indices = _incomplete_answer_indices(answer_ids, expected_ids)
    if missing_answer_indices:
        return _incomplete_answer_json(missing_answer_indices, len(expected_ids))

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
    expected_lines = [(b.get("text") or "") for b in expected_blocks]
    if len(expected_lines) < len(expected_ids):
        expected_lines += [""] * (len(expected_ids) - len(expected_lines))

    wrong_indices = []
    id_mismatch_indices = []
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
        id_mismatch_indices.append(i)

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
    submitted_order = list(answer_ids) if isinstance(answer_ids, list) else []
    submitted_indentation = _submitted_indentation_from_lines(answer_lines)
    submitted_indentation_by_block = _submitted_indentation_by_block(
        submitted_order,
        submitted_indentation,
    )
    correct_answer = _correct_answer_from_task(task) or {
        "order": expected_ids,
        "lines": expected_lines,
        "indentation": expected_indent_list,
    }
    test_error_analysis = _build_attempt_v2_error_analysis(
        is_correct=bool(is_correct),
        submitted_order=submitted_order,
        submitted_indentation=submitted_indentation,
        correct_answer=correct_answer,
        target_concept=_extract_attempt_v2_target_concept(task),
        previous_error_types=[],
    )
    submitted_blocks = []
    for index, block_id in enumerate(submitted_order):
        submitted_blocks.append({
            "index": index,
            "block_id": block_id,
            "line": answer_lines[index] if index < len(answer_lines) else None,
            "indentation": submitted_indentation[index] if index < len(submitted_indentation or []) else None,
            "expected_block_id": expected_ids[index] if index < len(expected_ids) else None,
            "expected_line": expected_lines[index] if index < len(expected_lines) else None,
            "expected_indentation": expected_indent_list[index] if index < len(expected_indent_list) else None,
            "is_sequence_correct": (
                index < len(expected_ids)
                and str(block_id) == str(expected_ids[index])
            ),
            "is_indentation_correct": (
                index < len(expected_indent_list)
                and index < len(submitted_indentation or [])
                and submitted_indentation[index] == expected_indent_list[index]
            ),
        })
    submitted_at_input = _parse_attempt_v2_datetime(data.get("submitted_at"))
    submitted_at = submitted_at_input or now_utc()
    started_at = _parse_attempt_v2_datetime(data.get("started_at"))
    duration_fields = _build_attempt_v2_duration_fields(started_at, submitted_at)
    duration_seconds = duration_fields.get("duration_seconds")
    if duration_seconds is None and not duration_fields.get("duration_outlier"):
        duration_seconds = payload_duration_sec
        duration_fields["duration_sec"] = payload_duration_sec
        duration_fields["duration_seconds"] = payload_duration_sec
    created_at = now_utc()

    attempt_doc = {
        "student_id": student_id,
        "test_cycle_id": test_cycle_id,
        "test_role": test_role,
        "task_id": task_identity,
        "task_title": _extract_attempt_v2_task_title(task),
        "target_concept": _extract_attempt_v2_target_concept(task),
        "attempt_no": 1,
        "answer_ids": answer_ids,
        "answer_lines": answer_lines,
        "answer_block_ids": answer_ids,
        "submitted_order": submitted_order,
        "submitted_indentation": submitted_indentation,
        "submitted_indentation_by_block": submitted_indentation_by_block,
        "submitted_blocks": submitted_blocks,
        "correct_answer": correct_answer,
        "expected_order": expected_ids,
        "expected_lines": expected_lines,
        "expected_indentation": expected_indent_list,
        "is_correct": is_correct,
        "score": score,
        "duration_sec": duration_seconds,
        "duration_seconds": duration_seconds,
        "elapsed_sec_raw": duration_fields.get("elapsed_sec_raw"),
        "duration_outlier": bool(duration_fields.get("duration_outlier")),
        "duration_outlier_reason": duration_fields.get("duration_outlier_reason"),
        "wrong_indices": wrong_indices,
        "wrong_indices_all": wrong_indices,
        "id_mismatch_indices": id_mismatch_indices,
        "indent_errors": indent_errors,
        "wrong_slots": test_error_analysis.get("wrong_slots", []),
        "error_count": test_error_analysis.get("error_count", 0),
        "error_types": test_error_analysis.get("error_types", []),
        "error_details": test_error_analysis.get("error_details", []),
        "error_concept": test_error_analysis.get("error_concept"),
        "repeated_error": False,
        "repeated_error_types": test_error_analysis.get("repeated_error_types", []),
        "repeated_error_count": test_error_analysis.get("repeated_error_count", 0),
        "repeated_error_basis": test_error_analysis.get("repeated_error_basis"),
        "repeated_error_rule_version": test_error_analysis.get("repeated_error_rule_version"),
        "extra_wrong_count": extra_wrong,
        "total_slots": total_slots,
        "started_at": started_at,
        "started_at_utc": started_at,
        "submitted_at": submitted_at,
        "submitted_at_utc": submitted_at,
        "submitted_at_taiwan": _taiwan_time_string(submitted_at),
        "created_at": created_at,
        "created_at_utc": created_at,
        "created_at_taiwan": _taiwan_time_string(created_at),
        "updated_at": created_at,
        "updated_at_utc": created_at,
        "updated_at_taiwan": _taiwan_time_string(created_at),
        "timezone": _PARSONS_ATTEMPTS_V2_TIMEZONE,
    }

    try:
        ins = db.parsons_test_attempts.insert_one(attempt_doc)
        attempt_id = str(ins.inserted_id)
    except DuplicateKeyError:
        existing_attempt = db.parsons_test_attempts.find_one(
            {
                "student_id": student_id,
                "test_cycle_id": test_cycle_id,
                "test_role": test_role,
                "task_id": task_identity,
            },
            {"_id": 1, "is_correct": 1, "score": 1},
        )
        return jsonify({
            "ok": True,
            "already_submitted": True,
            "attempt_id": str((existing_attempt or {}).get("_id") or ""),
            "is_correct": (existing_attempt or {}).get("is_correct"),
            "score": (existing_attempt or {}).get("score"),
            "message": "此題已提交，不能重複作答。",
        })
    except Exception as e:
        return jsonify({"ok": False, "message": "parsons_test_attempts write failed", "detail": str(e)}), 500

    write_learning_log_safely({
        "session_id": data.get("session_id"),
        "student_id": student_id,
        "event_type": "answer_submit",
        "page": data.get("page") or "parsons",
        "activity_type": "test",
        "test_role": test_role,
        "task_id": task_identity,
        "attempt_id": attempt_id,
        "attempt_no": 1,
        "target_concept": attempt_doc.get("target_concept"),
        "event_at": submitted_at,
        "metadata": {
            "is_correct": attempt_doc.get("is_correct"),
            "score": attempt_doc.get("score"),
            "error_count": attempt_doc.get("error_count"),
            "error_types": attempt_doc.get("error_types", []),
            "wrong_slots": attempt_doc.get("wrong_slots", []),
            "repeated_error": False,
        },
    })

    return jsonify({
        "ok": True,
        "already_submitted": False,
        "attempt_id": attempt_id,
        "attempt_v2_id": None,
        "attempt_no": 1,
        "is_correct": is_correct,
        "score": score,
        "wrong_indices": wrong_indices,
        "indent_errors": indent_errors,
    })


# 控制後測開放
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
# 取得目前後測開放狀態，給前端判斷是否顯示「後測區塊」
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

# 匯出 CSV
@parsons_bp.get("/test/export_csv")
def export_test_csv():
    ensure_test_indexes()
    test_cycle_id = (request.args.get("test_cycle_id") or get_default_test_cycle_id()).strip() or get_default_test_cycle_id()

    cur = db.parsons_test_attempts.find({"test_cycle_id": test_cycle_id}).sort("submitted_at", 1)

    headers = [
        "student_id",
        "test_cycle_id",
        "test_role",
        "task_id",
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
            "task_id": d.get("task_id") or d.get("test_task_id") or d.get("source_task_id") or "",
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














def _derive_slot_concept_map(task_doc: dict) -> dict:
    """Build slot->concept mapping from stored map or infer from solution blocks."""
    def _normalize_slot_key_to_index_str(k) -> str:
        ks = str(k or "").strip()
        if not ks:
            return ""
        if ks.isdigit():
            return str(int(ks))
        m = _re.match(r"^s(\d+)$", ks, flags=_re.I)
        if m:
            return str(max(0, int(m.group(1)) - 1))
        m = _re.match(r"^第\s*(\d+)\s*格$", ks)
        if m:
            return str(max(0, int(m.group(1)) - 1))
        return ks

    def _rule_concept_from_text(text: str) -> str:
        return infer_concept_tag_from_text(text)


    def _prefer_more_specific_concept(existing: str, text: str, inferred: str) -> str:
        current = normalize_concept_name(existing)
        candidate = normalize_concept_name(inferred)
        low = str(text or "").lower()

        # 明確看到 input/int(input) 時，優先保留輸入轉整數，避免被舊的 if/condition 標記覆蓋。
        if "input(" in low or "int(input" in low or "讀取輸入" in low:
            return "input_int_cast"

        if current:
            return current
        return candidate

    out = {}

    stored = task_doc.get("slot_concept_map") or {}
    if isinstance(stored, dict):
        for k, v in stored.items():
            ks = _normalize_slot_key_to_index_str(k)
            vv = normalize_concept_name(v)
            if ks and vv:
                out[ks] = vv

    hints_concept = task_doc.get("ai_slot_hints_concept") or {}
    if isinstance(hints_concept, dict):
        for k, v in hints_concept.items():
            ks = _normalize_slot_key_to_index_str(k)
            vv = normalize_concept_name(v)
            if ks and vv and not out.get(ks):
                out[ks] = vv

    blocks = task_doc.get("solution_blocks") or []
    for i, b in enumerate(blocks):
        txt = (
            str((b or {}).get("text") or "")
            + " "
            + str((b or {}).get("semantic_zh") or (b or {}).get("meaning_zh") or "")
        ).strip()
        c = _rule_concept_from_text(txt)
        refined = _prefer_more_specific_concept(out.get(str(i)) or "", txt, c)
        if refined:
            out[str(i)] = refined

    return out






























#  重要:
'''
執行順序:
驗證學生
取得題目
接收 answer_ids 與 answer_lines
系統判斷正誤
建立 legacy attempt
建立 parsons_attempts_v2
寫入 learning_logs
判斷第幾次錯誤
回傳 hint_flow
第1次錯誤 → first_system_hint
第2次錯誤 → ai_hint
第3次以上 → system_recheck
答對 → 不顯示錯誤提示
'''
@parsons_bp.post("/submit")
@prevent_duplicate_submission
def submit_answer():
    data = request.get_json(silent=True) or {}
    task_id = (data.get("task_id") or "").strip()
    answer_ids = data.get("answer_ids") or []
    answer_lines = data.get("answer_lines") or []
    student_id = current_student_id()
    participant_id = current_participant_id()
    # Submit 只做系統判斷；AI 提示延後到學生按「查看提示」時才呼叫。
    request_ai_feedback = False

    if not task_id:
        return jsonify({"ok": False, "message": "missing task_id"}), 400

    try:
        task = db.parsons_tasks.find_one({"_id": ObjectId(task_id)})
    except Exception:
        return jsonify({"ok": False, "message": "invalid task_id"}), 400
    if not task:
        return jsonify({"ok": False, "message": "task not found"}), 404

    if not student_id:
        return jsonify({"ok": False, "message": "missing student_id"}), 400

    parsed = t5doc_to_parsons_task(task)

    # 取得難度/等級資訊（優先 body 的 level，其次為 task 本身的設定）
    level = (data.get("level") or task.get("level") or "").strip()

    expected_ids = [
        str(s.get("expected_id"))
        for s in (parsed.get("template_slots") or [])
    ]
    missing_answer_indices = _incomplete_answer_indices(answer_ids, expected_ids)
    if missing_answer_indices:
        return _incomplete_answer_json(missing_answer_indices, len(expected_ids))


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

    expected_lines = [(b.get("text") or "") for b in expected_blocks_all]
    if len(expected_lines) < len(expected_ids):
        expected_lines += [""] * (len(expected_ids) - len(expected_lines))

    wrong_indices = []
    id_mismatch_indices = []
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
        id_mismatch_indices.append(i)

    # [修正] 縮排檢查僅針對「該格有實際作答」的情況。
    # 空白格（未拖拉）應視為未完成/位置錯誤，不應誤判為縮排錯誤。
    for i in range(min(len(expected_ids), len(answer_lines), len(expected_indent_list))):
        has_answer_id = (i < len(aligned) and aligned[i] is not None and str(aligned[i]).strip() != "")
        user_line = str(answer_lines[i] or "")
        if (not has_answer_id) or (not user_line.strip()):
            continue

        expected_indent = int(expected_indent_list[i] or 0)
        user_indent = len(user_line) - len(user_line.lstrip(" "))
        if user_indent != expected_indent:
            indent_errors.append(i)
            if i not in wrong_indices:
                wrong_indices.append(i)

    # 額外多填的答案也算錯（不新增不存在的格 index，只影響 is_correct / score）
    extra_wrong = max(0, len(answer_ids) - len(expected_ids))

    def _line_kind(s: str) -> str:
        t = str(s or "").strip().lower()
        if not t:
            return "main"
        if t.startswith(("if ", "elif ", "else:")):
            return "control"
        if any(op in t for op in [" + ", " - ", " * ", " / ", "==", "!=", "<=", ">=", "<", ">", "+", "-", "*", "/"]):
            return "semantic"
        if t.startswith("print(") or " return " in (" " + t + " "):
            return "semantic"
        return "main"

    # 四層優先：indentation > control > semantic > main
    control_errors = []
    semantic_errors = []
    main_order_errors = []
    for i in id_mismatch_indices:
        exp_line = str(expected_lines[i] or "") if i < len(expected_lines) else ""
        act_id = str(aligned[i]) if i < len(aligned) and aligned[i] is not None else ""
        act_line = str(pool_by_id_for_compare.get(act_id, {}).get("text", "") or "")
        k = _line_kind(exp_line)
        if k == "control":
            control_errors.append(i)
        elif k == "semantic":
            semantic_errors.append(i)
        else:
            # 若 expected 看不出來，actual 是控制/語意也要納入
            k2 = _line_kind(act_line)
            if k2 == "control":
                control_errors.append(i)
            elif k2 == "semantic":
                semantic_errors.append(i)
            else:
                main_order_errors.append(i)

    # 主錯誤格固定採「最前面出錯的格」，避免第1格錯卻回饋第2格。
    wrong_index = min(wrong_indices) if wrong_indices else None

    if wrong_index is None:
        primary_error_type = None
    elif wrong_index in indent_errors:
        primary_error_type = "indentation"
    elif wrong_index in control_errors:
        primary_error_type = "condition"
    elif wrong_index in semantic_errors:
        primary_error_type = "calculation"
    elif wrong_index in main_order_errors:
        primary_error_type = "structure"
    else:
        primary_error_type = "structure"

    # 關鍵規則：只回報第一個主錯誤，忽略後續衍生錯誤。
    reported_wrong_indices = [wrong_index] if wrong_index is not None else []
    is_correct = (len(reported_wrong_indices) == 0 and extra_wrong == 0)

    # [新增] 分數：以格數正確率計算
    total_slots = max(1, len(expected_ids))
    score = (total_slots - len(reported_wrong_indices)) / total_slots

    # [新增] 產生回饋需要的欄位（slot_label / actual_text / expected_text）
    slot_label = f"第{(wrong_index + 1)}格" if wrong_index is not None else ""
    pool_by_id = {str(b.get("id")): b for b in (parsed.get("pool") or [])}

    actual_id = str(aligned[wrong_index]) if (wrong_index is not None and aligned[wrong_index] is not None) else ""
    expected_id = str(expected_ids[wrong_index]) if wrong_index is not None else ""

    actual_text = pool_by_id.get(actual_id, {}).get("text", "") if actual_id else ""
    expected_text = pool_by_id.get(expected_id, {}).get("text", "") if expected_id else ""

    try:
        print("DEBUG_WRONG_SLOT =", {
            "wrong_index": wrong_index,
            "slot_label": slot_label,
            "expected_id": expected_id,
            "actual_id": actual_id,
            "expected_text": expected_text,
            "actual_text": actual_text,
            "expected_semantic": (pool_by_id.get(expected_id, {}) or {}).get("semantic_zh", "") if expected_id else "",
        })
    except Exception:
        pass

    # 組建回饋字串（與舊版本邏輯一致）
    feedback = "✅ 完全正確！" if is_correct else ((task.get("ai_feedback") or {}).get("general") or f"❌ 目前正確率 {score:.0%}，建議先確認「輸入 → 計算 → 輸出」的順序。")

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
        "feedback_strategy": _student_feedback_profile(student_id).get("feedback_strategy") or "B",
        "answer_ids": answer_ids,
        "answer_lines": answer_lines,
        "answer_block_ids": answer_ids,
        "answer_core_ids": answer_core,
        "is_correct": is_correct,
        "score": score,
        "feedback": feedback,
        "wrong_index": wrong_index,
        "wrong_indices": reported_wrong_indices,
        "wrong_indices_all": wrong_indices,
        "indent_errors": indent_errors,  # [紋緒] 縮排錯誤格數
        "slot_label": slot_label,
        "actual_text": actual_text,
        "expected_text": expected_text,
        "primary_error_type": primary_error_type,
        "diagnosis_mode": "system",
        "is_first_wrong": False,
        "created_at": now_utc(),
    }

    try:
        v2_doc = _build_parsons_attempt_v2_doc(
            data=data,
            task_doc=task, #題目資料
            student_id=student_id, #學生學號
            participant_id=participant_id,
            activity_type="practice", #區分資料類型
            test_role=None, #測驗類別
            test_cycle_id=None,  #前後測的編號
            task_id=task_id, #題目id
            video_id=data.get("video_id") or video_id_str or task.get("video_id"),
            answer_ids=answer_ids, #作答紀錄
            answer_lines=answer_lines, #
            is_correct=is_correct, #是否為測試資料
            score=score, #作答時間
        )
    except ValueError as e:
        return jsonify({"ok": False, "message": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "message": "parsons_attempts_v2 prepare failed", "detail": str(e)}), 500

    # Keep the legacy attempt collection auditable too, while parsons_attempts_v2
    # remains the primary analysis source.
    for _field in (
        "task_attempt_session",
        "attempt_no",
        "attempt_sequence_no",
        "block_results",
        "previous_attempt_id",
        "previous_attempt_no",
        "corrected_block_ids",
        "remaining_wrong_block_ids",
        "remaining_wrong_submitted_block_ids",
        "newly_wrong_block_ids",
        "corrected_slot_indices",
        "remaining_wrong_slot_indices",
        "newly_wrong_slot_indices",
        "corrected_block_count",
        "remaining_wrong_block_count",
        "newly_wrong_block_count",
        "submitted_after_ai_hint",
        "ai_hint_generated_before_submit",
        "ai_hint_viewed_before_submit",
        "latest_ai_hint_no_before_submit",
        "source_hint_id",
    ):
        attempt_doc[_field] = v2_doc.get(_field)

    legacy_attempt_oid = attempt_doc.get("_id")
    if not isinstance(legacy_attempt_oid, ObjectId):
        legacy_attempt_oid = ObjectId()
    attempt_doc["_id"] = legacy_attempt_oid
    attempt_id = str(legacy_attempt_oid)
    v2_ins = None
    try:
        v2_doc["legacy_attempt_id"] = attempt_id
        v2_ins = db.parsons_attempts_v2.insert_one(v2_doc)
        v2_attempt_id = str(v2_ins.inserted_id)
    except Exception as e:
        return jsonify({"ok": False, "message": "parsons_attempts_v2 write failed", "detail": str(e)}), 500

    attempt_doc["attempt_v2_id"] = v2_attempt_id
    attempt_doc["task_attempt_session"] = v2_doc.get("task_attempt_session")
    attempt_doc["attempt_sequence_no"] = v2_doc.get("attempt_sequence_no")
    try:
        db.parsons_attempts.insert_one(attempt_doc)
    except Exception as e:
        try:
            if v2_ins is not None:
                db.parsons_attempts_v2.delete_one({"_id": v2_ins.inserted_id})
        except Exception as rollback_error:
            print("[parsons submit] v2 rollback after legacy write failure failed:", repr(rollback_error))
        return jsonify({"ok": False, "message": "parsons_attempts legacy write failed", "detail": str(e)}), 500

    write_learning_log_safely({
        "session_id": data.get("session_id"),
        "student_id": student_id,
        "event_type": "answer_submit",
        "page": data.get("page") or "parsons",
        "activity_type": "practice",
        "test_role": None,
        "task_id": v2_doc.get("task_id"),
        "attempt_id": v2_attempt_id,
        "attempt_no": v2_doc.get("attempt_no"),
        "target_concept": v2_doc.get("target_concept"),
        "event_at": v2_doc.get("submitted_at"),
        "metadata": {
            "task_attempt_session": v2_doc.get("task_attempt_session"),
            "attempt_sequence_no": v2_doc.get("attempt_sequence_no"),
            "is_correct": v2_doc.get("is_correct"),
            "score": v2_doc.get("score"),
            "error_count": v2_doc.get("error_count"),
            "error_types": v2_doc.get("error_types", []),
            "wrong_slots": v2_doc.get("wrong_slots", []),
            "incorrect_slots": v2_doc.get("incorrect_slots", []),
            "block_results": v2_doc.get("block_results", []),
            "corrected_block_ids": v2_doc.get("corrected_block_ids", []),
            "remaining_wrong_block_ids": v2_doc.get("remaining_wrong_block_ids", []),
            "newly_wrong_block_ids": v2_doc.get("newly_wrong_block_ids", []),
            "submitted_after_ai_hint": v2_doc.get("submitted_after_ai_hint", False),
            "latest_ai_hint_no_before_submit": v2_doc.get("latest_ai_hint_no_before_submit", 0),
            "repeated_error": v2_doc.get("repeated_error"),
        },
    })

    # 依同一學生、同一題的作答序列，標記第一次錯誤的嘗試
    _refresh_first_wrong_flag_for_group(
        student_id,
        task_id,
        v2_doc.get("task_attempt_session"),
    )



    feedback_profile_for_submit = {
        "group_type": _clean_string(v2_doc.get("group_type")),
        "feedback_strategy": _normalize_feedback_strategy(v2_doc.get("feedback_strategy")) or "B",
    }
    feedback_strategy = (
        _normalize_feedback_strategy(v2_doc.get("feedback_strategy"))
        or feedback_profile_for_submit.get("feedback_strategy")
        or "B"
    )

    resp = {
        "ok": True,
        "attempt_id": attempt_id,
        "attempt_v2_id": v2_attempt_id,
        "task_attempt_session": v2_doc.get("task_attempt_session"),
        "attempt_no": v2_doc.get("attempt_no"),
        "attempt_sequence_no": v2_doc.get("attempt_sequence_no"),
        "feedback_strategy": feedback_strategy,
        "submitted_at": _utc_iso_string(v2_doc.get("submitted_at")),
        "submitted_at_taiwan": _taiwan_time_string(v2_doc.get("submitted_at")),
        "completed_at": _utc_iso_string(v2_doc.get("submitted_at")) if is_correct else None,
        "completed_at_taiwan": _taiwan_time_string(v2_doc.get("submitted_at")) if is_correct else None,
        "last_completed_at": _utc_iso_string(v2_doc.get("submitted_at")) if is_correct else None,
        "last_completed_at_taiwan": _taiwan_time_string(v2_doc.get("submitted_at")) if is_correct else None,
        "is_correct": is_correct,
        "score": v2_doc.get("score", score),
        "feedback": feedback,
        "wrong_index": wrong_index,
        "wrong_indices": reported_wrong_indices,
        "wrong_indices_all": wrong_indices,
        "indent_errors": indent_errors,  # [新增] 縮排錯誤的格數

        # [新增] V1.4：送出後回傳欄位（前端顯示以後端為準）
        "slot_label": slot_label,
        "actual_text": actual_text,
        "expected_text": expected_text,
        "hint": "",
        "error_types": v2_doc.get("error_types", []),
        "wrong_slots": v2_doc.get("wrong_slots", []),
        "incorrect_slots": v2_doc.get("incorrect_slots", []),
        "current_error_positions": v2_doc.get("incorrect_slots", []),
        "current_error_count": v2_doc.get("error_count", 0),
        "current_error_types": v2_doc.get("error_types", []),
        "sequence_slots": v2_doc.get("sequence_slots", []),
        "indentation_slots": v2_doc.get("indentation_slots", []),
        "error_count": v2_doc.get("error_count", 0),
        "block_results": v2_doc.get("block_results", []),
        "corrected_block_ids": v2_doc.get("corrected_block_ids", []),
        "remaining_wrong_block_ids": v2_doc.get("remaining_wrong_block_ids", []),
        "remaining_wrong_submitted_block_ids": v2_doc.get("remaining_wrong_submitted_block_ids", []),
        "newly_wrong_block_ids": v2_doc.get("newly_wrong_block_ids", []),
        "corrected_slot_indices": v2_doc.get("corrected_slot_indices", []),
        "remaining_wrong_slot_indices": v2_doc.get("remaining_wrong_slot_indices", []),
        "newly_wrong_slot_indices": v2_doc.get("newly_wrong_slot_indices", []),
        "submitted_after_ai_hint": v2_doc.get("submitted_after_ai_hint", False),
        "ai_hint_generated_before_submit": v2_doc.get("ai_hint_generated_before_submit", False),
        "ai_hint_viewed_before_submit": v2_doc.get("ai_hint_viewed_before_submit", False),
        "latest_ai_hint_no_before_submit": v2_doc.get("latest_ai_hint_no_before_submit", 0),
        "repeated_error": v2_doc.get("repeated_error"),
    }

    slot_wrong_type = ""
    slot_concept_tag = ""
    try:
        if wrong_index is not None and 0 <= int(wrong_index) < len(expected_blocks_all):
            wrong_block = expected_blocks_all[int(wrong_index)] or {}
            wrong_block_text = str(wrong_block.get("text") or "")
            wrong_block_sem = str(wrong_block.get("semantic_zh") or wrong_block.get("meaning_zh") or "")
            slot_wrong_type = str(infer_wrong_type_from_code(wrong_block_text, wrong_block_sem) or "").strip()
            slot_concept_tag = str(resolve_concept_tag_from_wrong_type(slot_wrong_type, wrong_block_text, wrong_block_sem) or "").strip()
    except Exception:
        slot_wrong_type = ""
        slot_concept_tag = ""

    resp["wrong_type"] = slot_wrong_type
    resp["concept_tag"] = slot_concept_tag

    if primary_error_type == "indentation":
        system_error_code = "indentation_mismatch"
        system_error_type = "syntax"
        system_misconception = "學生尚未掌握 Python 區塊縮排與控制流程關係"
    elif primary_error_type == "structure":
        system_error_code = "main_order_error"
        system_error_type = "structure"
        system_misconception = "學生對主程式流程與區塊順序的關係尚未穩定"
    else:
        system_error_code = "generic_slot_misplacement"
        system_error_type = "logic"
        system_misconception = "學生對程式區塊角色與先後順序尚未建立穩定心智模型"

    resp["error_code"] = system_error_code
    resp["error_type"] = system_error_type
    resp["misconception"] = system_misconception
    resp["diagnosis_mode"] = "system"
    resp["ai_feedback_pending"] = bool(not is_correct)

    hint_flow = None
    wrong_attempt_count = 0
    attempt_for_hint = dict(attempt_doc)
    attempt_for_hint["_id"] = legacy_attempt_oid
    attempt_for_hint["attempt_v2_id"] = v2_attempt_id
    attempt_for_hint["task_attempt_session"] = v2_doc.get("task_attempt_session")
    repeated_error = bool(v2_doc.get("repeated_error"))
    v2_error_types = v2_doc.get("error_types") if isinstance(v2_doc.get("error_types"), list) else []
    # 提示與前端標色都應使用本次完整錯誤位置：
    # 順序錯誤 + 縮排錯誤，而不是只沿用第一次錯誤位置。
    v2_wrong_slots = (
        v2_doc.get("incorrect_slots")
        if isinstance(v2_doc.get("incorrect_slots"), list)
        else (
            v2_doc.get("wrong_slots")
            if isinstance(v2_doc.get("wrong_slots"), list)
            else reported_wrong_indices
        )
    )
    user_profile_for_hint = {
        "group_type": feedback_profile_for_submit.get("group_type"),
        "feedback_strategy": feedback_strategy,
    }

    if not is_correct:
        first_record = _ensure_first_hint_record(
            student_id,
            task_id,
            group_type=_clean_string(user_profile_for_hint.get("group_type")),
            wrong_slots=v2_wrong_slots,
            error_types=v2_error_types,
            task_attempt_session=v2_doc.get("task_attempt_session"),
        )
        wrong_attempt_query = {
            "student_id": student_id,
            "task_id": task_id,
            "is_correct": False,
        }
        activity_for_count = v2_doc.get("activity_type") or "practice"
        if activity_for_count:
            wrong_attempt_query["activity_type"] = activity_for_count
        if activity_for_count == "test":
            wrong_attempt_query["test_role"] = v2_doc.get("test_role")
        wrong_attempt_count = db.parsons_attempts_v2.count_documents(wrong_attempt_query)
        try:
            attempt_no_for_hint_flow = int(v2_doc.get("attempt_no") or 0)
        except Exception:
            attempt_no_for_hint_flow = 0
        is_first_wrong_for_hint_flow = (
            wrong_attempt_count <= 1
            and attempt_no_for_hint_flow <= 1
        )
        is_second_wrong_for_hint_flow = (
            wrong_attempt_count == 2
            or attempt_no_for_hint_flow == 2
        )

        # Always preserve the latest system evaluation separately from the
        # immutable first/second snapshots used for research.
        current_record = _set_hint_record(
            student_id,
            task_id,
            set_fields={
                "group_type": _clean_string(user_profile_for_hint.get("group_type")),
                "task_attempt_session": v2_doc.get("task_attempt_session"),
                "latest_error_positions": _int_list(v2_wrong_slots),
                "latest_error_types": v2_error_types,
                "latest_error_count": int(v2_doc.get("error_count") or len(_int_list(v2_wrong_slots))),
                "last_attempt_id": str(legacy_attempt_oid),
                "latest_attempt_v2_id": v2_attempt_id,
            },
        ) or first_record

        if is_first_wrong_for_hint_flow:
            hint_flow = {
                "type": "first_system_hint",
                "feedback_strategy": feedback_strategy,
                "task_attempt_session": v2_doc.get("task_attempt_session"),
                "auto_open_ai": False,
                "wrong_attempt_count": int(wrong_attempt_count),
                "hint_record": _hint_record_public(current_record),
                "first_system_hint_text": (current_record or {}).get("first_system_hint_text"),
                "first_error_positions": (current_record or {}).get("first_error_positions") or _int_list(v2_wrong_slots),
                "first_error_types": (current_record or {}).get("first_error_types") or v2_error_types,
            }
            write_learning_log_safely({
                "session_id": data.get("session_id"),
                "student_id": student_id,
                "event_type": "first_error_hint_shown",
                "page": data.get("page") or "parsons",
                "activity_type": "practice",
                "test_role": None,
                "task_id": task_id,
                "attempt_id": v2_attempt_id,
                "attempt_no": v2_doc.get("attempt_no"),
                "target_concept": v2_doc.get("target_concept"),
                "event_at": v2_doc.get("submitted_at"),
                "metadata": _hint_log_metadata(
                    current_record,
                    requested_hint_no=None,
                    error_types=v2_error_types,
                    wrong_slots=v2_wrong_slots,
                    repeated_error=False,
                    extra={"hint_source": "system_first_error"},
                ),
            })

        elif feedback_strategy == "C":
            current_positions = _int_list(v2_wrong_slots)
            current_error_count = int(v2_doc.get("error_count") or len(current_positions))
            try:
                c_record = _set_hint_record(
                    student_id,
                    task_id,
                    set_fields={
                        "group_type": _clean_string(user_profile_for_hint.get("group_type")),
                        "task_attempt_session": v2_doc.get("task_attempt_session"),
                        "second_error_positions": current_positions,
                        "second_error_types": v2_error_types,
                        "latest_error_positions": current_positions,
                        "latest_error_types": v2_error_types,
                        "latest_error_count": current_error_count,
                        "last_attempt_id": str(legacy_attempt_oid),
                        "latest_attempt_v2_id": v2_attempt_id,
                    },
                ) or current_record
            except Exception as hint_state_error:
                print("[parsons submit] C fixed feedback state update failed:", repr(hint_state_error))
                c_record = current_record

            hint_flow = {
                "type": "fixed_semantic_feedback",
                "feedback_strategy": "C",
                "task_attempt_session": v2_doc.get("task_attempt_session"),
                "auto_open_ai": False,
                "wrong_attempt_count": int(wrong_attempt_count),
                "current_error_positions": current_positions,
                "current_error_count": current_error_count,
                "current_error_types": v2_error_types,
                "second_error_positions": current_positions,
                "second_error_types": v2_error_types,
                "hint_record": _hint_record_public(c_record),
                "source": "pre_generated_task_semantic",
            }
            write_learning_log_safely({
                "session_id": data.get("session_id"),
                "student_id": student_id,
                "event_type": "fixed_semantic_feedback_shown",
                "page": data.get("page") or "parsons",
                "activity_type": "practice",
                "test_role": None,
                "task_id": task_id,
                "attempt_id": v2_attempt_id,
                "attempt_no": v2_doc.get("attempt_no"),
                "target_concept": v2_doc.get("target_concept"),
                "event_at": v2_doc.get("submitted_at"),
                "metadata": {
                    "feedback_strategy": "C",
                    "task_attempt_session": v2_doc.get("task_attempt_session"),
                    "current_error_positions": current_positions,
                    "current_error_count": current_error_count,
                    "current_error_types": v2_error_types,
                    "hint_source": "pre_generated_task_semantic",
                },
            })

        elif is_second_wrong_for_hint_flow:
            # 這裡只負責通知前端開啟 AI 提示視窗。
            # 不在 /submit 內直接呼叫 AI，避免提示生成、SRT 檢索或 MongoDB
            # 提示寫入的例外使整次作答送出變成 HTTP 500。
            # 前端收到 auto_open_ai=True 後，會再呼叫 /api/parsons/hint。
            try:
                pending_record = _set_hint_record(
                    student_id,
                    task_id,
                    set_fields={
                        "group_type": _clean_string(user_profile_for_hint.get("group_type")),
                        "task_attempt_session": v2_doc.get("task_attempt_session"),
                        "second_error_positions": _int_list(v2_wrong_slots),
                        "second_error_types": v2_error_types,
                        "latest_error_positions": _int_list(v2_wrong_slots),
                        "latest_error_types": v2_error_types,
                        "latest_error_count": int(v2_doc.get("error_count") or len(_int_list(v2_wrong_slots))),
                        "last_attempt_id": str(legacy_attempt_oid),
                        "latest_attempt_v2_id": v2_attempt_id,
                    },
                ) or current_record
            except Exception as hint_state_error:
                print("[parsons submit] hint state update failed:", repr(hint_state_error))
                pending_record = current_record

            existing_hint_1 = str(
                (pending_record or {}).get("ai_hint_1_text") or ""
            ).strip()
            existing_hint_1_meta = (
                (pending_record or {}).get("ai_hint_1_meta")
                if isinstance((pending_record or {}).get("ai_hint_1_meta"), dict)
                else {}
            )
            ai_hint_recovery_triggered = False

            hint_flow = {
                "type": "ai_hint",
                "feedback_strategy": feedback_strategy,
                "task_attempt_session": v2_doc.get("task_attempt_session"),
                "auto_open_ai": True,
                "requested_hint_no": 1,
                "wrong_attempt_count": int(wrong_attempt_count),
                "ai_hint_recovery_triggered": ai_hint_recovery_triggered,
                "second_error_positions": _int_list(v2_wrong_slots),
                "second_error_types": v2_error_types,
                "hint_record": _hint_record_public(pending_record),
                "hint": existing_hint_1,
                "hint_meta": existing_hint_1_meta,
                "source": "parsons_hint_records" if existing_hint_1 else "hint_api_pending",
                "ai_feedback_detail": {},
                "ai_diagnosis_summary": "",
                "hint_pending": not bool(existing_hint_1),
            }

        else:
            current_positions = _int_list(v2_wrong_slots)
            current_error_count = int(v2_doc.get("error_count") or len(current_positions))
            hint_flow = {
                "type": "system_recheck",
                "feedback_strategy": feedback_strategy,
                "task_attempt_session": v2_doc.get("task_attempt_session"),
                "auto_open_ai": False,
                "wrong_attempt_count": int(wrong_attempt_count),
                "current_error_positions": current_positions,
                "current_error_count": current_error_count,
                "current_error_types": v2_error_types,
                "current_system_feedback_text": _system_recheck_text(
                    current_positions,
                    v2_error_types,
                    v2_doc.get("attempt_no"),
                ),
                "hint_record": _hint_record_public(current_record),
                "existing_ai_hint_available": bool(
                    (current_record or {}).get("ai_hint_1_text")
                ),
                "corrected_block_ids": v2_doc.get("corrected_block_ids", []),
                "remaining_wrong_block_ids": v2_doc.get("remaining_wrong_block_ids", []),
                "newly_wrong_block_ids": v2_doc.get("newly_wrong_block_ids", []),
            }
            write_learning_log_safely({
                "session_id": data.get("session_id"),
                "student_id": student_id,
                "event_type": "system_recheck_shown",
                "page": data.get("page") or "parsons",
                "activity_type": "practice",
                "test_role": None,
                "task_id": task_id,
                "attempt_id": v2_attempt_id,
                "attempt_no": v2_doc.get("attempt_no"),
                "target_concept": v2_doc.get("target_concept"),
                "event_at": v2_doc.get("submitted_at"),
                "metadata": {
                    "task_attempt_session": v2_doc.get("task_attempt_session"),
                    "current_error_positions": current_positions,
                    "current_error_count": current_error_count,
                    "current_error_types": v2_error_types,
                    "corrected_block_ids": v2_doc.get("corrected_block_ids", []),
                    "remaining_wrong_block_ids": v2_doc.get("remaining_wrong_block_ids", []),
                    "newly_wrong_block_ids": v2_doc.get("newly_wrong_block_ids", []),
                    "submitted_after_ai_hint": v2_doc.get("submitted_after_ai_hint", False),
                    "existing_ai_hint_available": bool(
                        (current_record or {}).get("ai_hint_1_text")
                    ),
                },
            })
    else:
        existing_hint_record = _get_hint_record(
            student_id,
            task_id,
            v2_doc.get("task_attempt_session"),
        )
        if existing_hint_record and int(existing_hint_record.get("ai_hint_view_count") or 0) > 0:
            write_learning_log_safely({
                "session_id": data.get("session_id"),
                "student_id": student_id,
                "event_type": "submit_after_hint",
                "page": data.get("page") or "parsons",
                "activity_type": "practice",
                "test_role": None,
                "task_id": task_id,
                "attempt_id": v2_attempt_id,
                "attempt_no": v2_doc.get("attempt_no"),
                "target_concept": v2_doc.get("target_concept"),
                "event_at": v2_doc.get("submitted_at"),
                "metadata": _hint_log_metadata(
                    existing_hint_record,
                    requested_hint_no=existing_hint_record.get("latest_ai_hint_no"),
                    error_types=[],
                    wrong_slots=[],
                    repeated_error=False,
                    extra={"is_correct": True, "score": v2_doc.get("score")},
                ),
            })
    if not is_correct:
        resp["wrong_attempt_count"] = int(wrong_attempt_count or 0)
    resp["hint_flow"] = hint_flow

    # 影片回看／跳轉功能已移除；僅保留系統錯誤診斷與提示流程。
    if not is_correct:
        try:
            db.parsons_attempts.update_one(
                {"_id": legacy_attempt_oid},
                {"$set": {
                    "wrong_type": slot_wrong_type,
                    "concept_tag": slot_concept_tag,
                    "error_code": system_error_code,
                    "error_type": system_error_type,
                    "misconception": system_misconception,
                    "ai_feedback_pending": True,
                }},
            )
        except Exception as persist_error:
            print("[parsons submit] diagnosis persistence skipped:", repr(persist_error))

    return jsonify(resp)




# =========================
# POST /hint  學生按「查看提示」後才產生 AI 提示
# 找主要錯誤格
# =========================
def _attempt_hint_context(att, task):
    """Collect the backend-selected wrong-slot context for hint generation."""
    wrong_index = _attempt_primary_wrong_index_for_hint(att)
    wrong_slots = _attempt_wrong_positions_for_hint(att)

    slot_label = str(att.get("slot_label") or "").strip()
    if not slot_label and wrong_index is not None:
        slot_label = f"第 {wrong_index + 1} 格"
    if not slot_label:
        slot_label = "錯誤位置"

    expected_text = str(att.get("expected_text") or "").strip()
    actual_text = str(att.get("actual_text") or "").strip()
    if (not expected_text or not actual_text) and wrong_index is not None:
        try:
            parsed = t5doc_to_parsons_task(task)
            pool = {
                str(block.get("id")): block
                for block in (parsed.get("pool") or [])
                if isinstance(block, dict)
            }
            expected_ids = [
                str(slot.get("expected_id"))
                for slot in (parsed.get("template_slots") or [])
            ]
            answer_ids = list(att.get("answer_ids") or [])

            if not expected_text and 0 <= wrong_index < len(expected_ids):
                expected_text = str(
                    pool.get(str(expected_ids[wrong_index]), {}).get("text") or ""
                ).strip()

            if not actual_text and 0 <= wrong_index < len(answer_ids):
                actual_text = str(
                    pool.get(str(answer_ids[wrong_index]), {}).get("text") or ""
                ).strip()
        except Exception:
            pass

    primary_error_type = str(
        att.get("primary_error_type")
        or att.get("error_type")
        or "logic"
    ).strip().lower()

    if primary_error_type == "condition" and (
        str(actual_text).strip().lower().startswith("else")
        or str(expected_text).strip().lower().startswith("else")
    ):
        feedback_error_type = "if_else"
    elif primary_error_type in {
        "indentation",
        "calculation",
        "structure",
        "sequence_error",
        "indentation_error",
    }:
        feedback_error_type = primary_error_type
    else:
        feedback_error_type = "logic"

    task_concept_tag = ""
    try:
        slot_concept_map = _derive_slot_concept_map(task) or {}
        if wrong_index is not None:
            task_concept_tag = normalize_concept_name(slot_concept_map.get(str(wrong_index)) or "")
        if not task_concept_tag:
            task_concept_tag = normalize_concept_name(_extract_attempt_v2_target_concept(task) or "")
    except Exception:
        task_concept_tag = ""

    return {
        "wrong_index": wrong_index,
        "wrong_slots": wrong_slots,
        "slot_label": slot_label,
        "expected_text": expected_text,
        "actual_text": actual_text,
        "feedback_error_type": feedback_error_type,
        "concept_tag": task_concept_tag,
        "concept_scope": _progressive_hint_concept_label(feedback_error_type, task_concept_tag),
    }


def _progressive_hint_concept_label(concept: str, concept_tag: str = "") -> str:
    """Return a student-facing Traditional Chinese concept label."""
    normalized_tag = normalize_concept_name(concept_tag or "")
    if normalized_tag:
        try:
            label = str(concept_tag_to_label(normalized_tag) or "").strip()
            if label and label != normalized_tag:
                return label
        except Exception:
            pass

    labels = {
        "input": "資料輸入與準備",
        "print": "輸出內容與輸出時機",
        "condition": "條件判斷與分支流程",
        "if_else": "條件分支與互斥關係",
        "indentation": "縮排與控制範圍",
        "calculation": "運算步驟與資料關係",
        "loop": "迴圈控制與重複範圍",
        "assignment": "變數設定與資料保存",
        "sequence_error": "程式流程與區塊順序",
        "indentation_error": "縮排與控制範圍",
        "structure": "程式結構與執行流程",
        "logic": "程式流程與資料關係",
    }
    return labels.get(str(concept or "").strip().lower(), "程式流程與資料關係")















# 找 SRT 時間




def _progressive_hint_sensitive_tokens(expected_text: str) -> list:
    expected = str(expected_text or "")
    identifiers = _re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", expected)
    allowed = {
        "if", "elif", "else", "for", "while", "in", "and", "or", "not",
        "def", "return", "print", "input", "range", "int", "float", "str",
        "list", "len", "true", "false", "none",
    }

    tokens = []
    for identifier in identifiers:
        low = identifier.lower()
        if low in allowed or len(identifier) < 2:
            continue
        tokens.append(identifier)

    for quoted in _re.findall(r"['\"]([^'\"]{2,})['\"]", expected):
        tokens.append(quoted)

    return list(dict.fromkeys(tokens))


def _progressive_hint_has_leakage(hint: str, expected_text: str) -> bool:
    text = str(hint or "").strip()
    expected = str(expected_text or "").strip()
    if not text:
        return True

    low = text.lower()
    forbidden_phrases = [
        "正確答案",
        "正確寫法",
        "應改成",
        "請改成",
        "放到第",
        "移到第",
        "排在前面",
        "排在後面",
    ]
    if any(phrase in low for phrase in forbidden_phrases):
        return True

    if _re.search(r"```|`[^`]+`", text):
        return True

    if _re.search(r"\b(?:def|return|print|input|range)\s*\([^\n]{0,80}\)", text):
        return True

    if expected:
        if expected.lower() in low:
            return True
        if _lexical_overlap_ratio(text, expected) >= 0.35:
            return True
        for token in _progressive_hint_sensitive_tokens(expected):
            if token and token.lower() in low:
                return True

    return False

# 分成 broad / narrow

# 保存第一次與第二次提示
def _generate_ai_hint_payload(att, task, requested_hint_no):
    """Generate one progressive hint from deterministic structured errors."""
    level = 2 if int(requested_hint_no or 1) == 2 else 1
    aggregate_detail = _build_aggregated_hint_detail(att, task, level)
    fallback_hint = _aggregated_hint_fallback(aggregate_detail, level)

    first_hint = ""
    if level == 2:
        try:
            student_id = str(att.get("student_id") or current_student_id() or "").strip()
            task_id = str(att.get("task_id") or "").strip()
            record = (
                _get_hint_record(student_id, task_id, att.get("task_attempt_session"))
                if student_id and task_id else None
            )
            first_hint = str((record or {}).get("ai_hint_1_text") or "").strip()
        except Exception:
            first_hint = ""

    error_concepts = aggregate_detail.get("error_concepts") or []
    expected_for_leakage = "\n".join(
        str(item.get("expected_text") or "")
        for item in _collect_all_wrong_slot_contexts(att, task)
        if isinstance(item, dict)
    )
    hint = fallback_hint
    guiding_question = ""
    evidence_summary = str(aggregate_detail.get("evidence_summary") or "").strip()
    leakage_status = "fallback_safe"
    source = "system_fallback"
    quality_status = "ai_disabled_fallback"
    second_hint_similarity = None
    second_hint_similarity_threshold = 0.72
    hint_library_fields = {}

    relation_library_enabled = str(
        os.getenv("PARSONS_RELATION_HINT_LIBRARY_ENABLED", "1")
    ).strip().lower() not in {"0", "false", "no", "off"}
    hint_library_doc = None
    hint_library_ctx = _hint_library_context_from_detail(aggregate_detail, level)
    if relation_library_enabled:
        hint_library_doc, hint_library_ctx = _find_hint_library_entry(
            aggregate_detail, task, level
        )
        if not hint_library_doc:
            hint_library_fields.update(
                _hint_library_status_fields(
                    hint_library_ctx,
                    "miss" if hint_library_ctx.get("retrieval_eligible") else "skipped",
                )
            )
    else:
        hint_library_fields.update(
            _hint_library_status_fields(
                hint_library_ctx, "skipped", "relation_library_disabled"
            )
        )

    if hint_library_doc:
        hint = str(hint_library_doc.get("hint_template") or "").strip() or fallback_hint
        hint_library_fields = _hint_library_public_fields(
            hint_library_doc,
            hint_library_ctx,
        )
        hint_library_fields.update(
            _hint_library_status_fields(hint_library_ctx, "hit")
        )
        source = "hint_library_structured"
        quality_status = str(
            hint_library_doc.get("quality_status") or "passed"
        ).strip() or "passed"
        leakage_status = str(
            hint_library_doc.get("answer_leakage_check") or "passed"
        ).strip() or "passed"
        if level == 2:
            guiding_question = hint
        _mark_hint_library_used(hint_library_doc)

    if (not hint_library_doc) and ai_enabled():
        prompt_payload = {
            "question_text": aggregate_detail.get("question_text") or "",
            "task_context": aggregate_detail.get("task_context") or {},
            "hint_level": level,
            "scope": "narrow" if level == 2 else "broad",
            "wrong_slot_count": aggregate_detail.get("wrong_slot_count") or 0,
            "error_concepts": error_concepts,
            "relation_types": aggregate_detail.get("relation_types") or [],
            "first_hint": first_hint if level == 2 else "",
        }
        prompt = f"""
你是 Python Parsons 題的錯誤導向教學提示產生器。

系統已使用標準答案完成正誤判斷，並整理出順序錯誤、縮排錯誤、
程式概念、程式角色與程式關係。你不負責重新判分，也不可自行猜測
未出現在結構化資料中的錯誤原因。

【提示層級】
level = {level}

【層級規則】
- level 1：統整主要錯誤概念，提供較廣的流程、作用範圍或資料依賴檢查方向；一句繁體中文，30～70字。
- level 2：根據第一次提示，縮小到更具體的概念關係或提出一個引導問題；一到兩句繁體中文，40～90字。
- level 2 必須比 level 1 更聚焦，但仍不可直接提供修正答案。

【禁止事項】
1. 不可輸出完整或局部正確程式碼。
2. 不可輸出 block ID、正確排列、正確格位或正確縮排數值。
3. 不可說哪一行要移到哪一格，也不可指示移到前面、後面、上一格或下一格。
4. 不可使用「正確答案是」、「正確寫法是」、「應改成」、「請改成」。
5. 不可提供逐步移動操作。
6. 不可把 expected_role 當成正確排列清單逐項複述。
7. 不可輸出 Markdown、程式碼區塊或額外說明。
8. 若 relation_types 或角色資料不足，只能提供一般概念檢查方向，不可猜測。

【後端整理的結構化錯誤資料】
{json.dumps(prompt_payload, ensure_ascii=False)}

請只輸出合法 JSON：
{{
  "hint_text": "給學生的概念提示",
  "concept_scope": "本次提示涵蓋的概念範圍",
  "guiding_question": "level 2 的引導問題；level 1 請輸出空字串",
  "evidence_summary": "簡述提示依據的錯誤類型與概念，不揭露正確排列"
}}
""".strip()

        try:
            data = parsons_ai.call_openai_json(
                model=_model_for_feedback(),
                system=(
                    "你是教學鷹架提示產生器，不是解題器。"
                    "只能依據後端已確認的結構化錯誤資料產生由廣到窄的提示。"
                    "不得重新判分，不得揭露正確程式碼、區塊排列、格位、縮排值或直接修改步驟。"
                    "只輸出合法 JSON。"
                ),
                user=prompt,
            ) or {}
            candidate = str(data.get("hint_text") or "").strip()

            if not candidate:
                hint = fallback_hint
                leakage_status = "empty_candidate_fallback"
                quality_status = "empty_candidate_fallback"
                source = "system_fallback"
            elif _progressive_hint_has_leakage(candidate, expected_for_leakage):
                hint = fallback_hint
                leakage_status = "rejected_fallback_used"
                quality_status = "answer_leakage_fallback"
                source = "system_fallback"
            elif level == 2 and first_hint:
                second_hint_similarity = _lexical_overlap_ratio(candidate, first_hint)
                if second_hint_similarity >= second_hint_similarity_threshold:
                    hint = _aggregated_hint_fallback(aggregate_detail, hint_level=2)
                    leakage_status = "passed"
                    quality_status = "second_hint_too_similar_fallback"
                    source = "system_fallback"
                else:
                    hint = candidate
                    leakage_status = "passed"
                    quality_status = "second_hint_depth_check_passed"
                    source = "ai_structured_error"
            else:
                hint = candidate
                leakage_status = "passed"
                quality_status = (
                    "first_hint_accepted"
                    if level == 1
                    else "second_hint_without_first_hint"
                )
                source = "ai_structured_error"

            guiding_question = str(data.get("guiding_question") or "").strip()
            if level == 1 or _progressive_hint_has_leakage(guiding_question, expected_for_leakage):
                guiding_question = ""
            evidence_summary = _compact_subtitle_basis(
                data.get("evidence_summary"),
                evidence_summary,
            )
        except Exception:
            hint = fallback_hint
            leakage_status = "ai_error_fallback_safe"
            quality_status = "ai_error_fallback_safe"
            source = "system_fallback"

    if not hint_library_doc:
        auto_saved_fields = _auto_save_generated_hint_to_library(
            aggregate_detail,
            task,
            level,
            hint,
            source=source,
            quality_status=quality_status,
            leakage_status=leakage_status,
        )
        if auto_saved_fields:
            hint_library_fields.update(auto_saved_fields)

    concept_scope = "、".join(aggregate_detail.get("concept_scopes") or []) or "程式流程與區塊順序"
    ai_feedback_detail = {
        "feedback_type": "progressive_structured_error_hint",
        "evidence_type": "structured_error",
        "evidence_summary": evidence_summary,
        "hint_level": level,
        "hint_source": source,
        "hint_quality_status": quality_status,
        "second_hint_similarity": (
            round(float(second_hint_similarity), 4)
            if second_hint_similarity is not None
            else None
        ),
        "second_hint_similarity_threshold": second_hint_similarity_threshold,
        "concept": (aggregate_detail.get("concept_tags") or ["unknown"])[0],
        "concept_tag": (aggregate_detail.get("concept_tags") or ["unknown"])[0],
        "concept_scope": concept_scope,
        "concept_explanation": concept_scope,
        "concept_hint": concept_scope,
        "possible_causes": [],
        "impact": "請先依照目前的概念範圍修正，再重新送出讓系統判斷。",
        "guiding_question": guiding_question,
        "reflection_questions": [guiding_question] if guiding_question else [],
        "first_hint": hint if level == 1 else first_hint,
        "second_hint": hint if level == 2 else "",
        "subtitle_basis": "",
        "answer_leakage_check": leakage_status,
        **hint_library_fields,
        **aggregate_detail,
    }
    ai_diagnosis_summary = (
        f"結構化錯誤概念：{concept_scope}；"
        f"錯誤格數：{aggregate_detail.get('wrong_slot_count') or 0}"
    )

    hint_meta = _build_ai_hint_meta_for_attempt(
        att,
        task,
        level,
        detail=ai_feedback_detail,
    )

    return {
        "hint": str(hint or "").strip(),
        "ai_feedback_detail": ai_feedback_detail,
        "ai_diagnosis_summary": ai_diagnosis_summary,
        "hint_meta": hint_meta,
        "source": str(ai_feedback_detail.get("hint_source") or "system_fallback"),
    }

def _prepare_ai_hint_record(att, task, *, requested_hint_no=1, force=False, count_view=True, explicit_attempt_v2_id=None):
    attempt_v2_for_context = _resolve_attempt_v2_for_hint(att, explicit_attempt_v2_id)
    if attempt_v2_for_context:
        att = _merge_attempt_v2_hint_context(att, attempt_v2_for_context)

    student_id = str(att.get("student_id") or current_student_id() or "").strip()
    task_id = str(att.get("task_id") or "").strip()
    if not student_id or not task_id:
        return None, {"hint": "", "source": "system"}

    user = db.users.find_one({"student_id": student_id}, {"group_type": 1}) or {}
    try:
        task_attempt_session = max(1, int(att.get("task_attempt_session") or 1))
    except Exception:
        task_attempt_session = 1
    wrong_slots = _attempt_wrong_positions_for_hint(att)
    error_types = _hint_error_types(attempt_doc=att, primary_error_type=att.get("primary_error_type"))
    record = _ensure_first_hint_record(
        student_id,
        task_id,
        group_type=_clean_string(user.get("group_type")),
        wrong_slots=wrong_slots,
        error_types=error_types,
        task_attempt_session=task_attempt_session,
    )

    # 舊 Prompt 快取可能來自 SRT 流程或使用不同的提示限制。
    # 版本不一致時清空本場次 AI 快取，讓結構化錯誤 Prompt 重新生成。
    if record and str(record.get("hint_prompt_version") or "legacy") != _PARSONS_HINT_PROMPT_VERSION:
        record = _set_hint_record(
            student_id,
            task_id,
            set_fields={
                "task_attempt_session": task_attempt_session,
                "hint_prompt_version": _PARSONS_HINT_PROMPT_VERSION,
                "ai_hint_1_text": None,
                "ai_hint_1_meta": {},
                "ai_hint_2_text": None,
                "ai_hint_2_meta": {},
                "hint_generation_count": 0,
                "ai_hint_generation_count": 0,
                "hint_view_count": 0,
                "ai_hint_view_count": 0,
                "latest_ai_hint_no": 0,
                "last_ai_feedback_detail": {},
                "last_ai_diagnosis_summary": "",
            },
        )

    try:
        requested_hint_no = 2 if int(requested_hint_no or 1) == 2 else 1
    except Exception:
        requested_hint_no = 1
    field = "ai_hint_2_text" if requested_hint_no == 2 else "ai_hint_1_text"
    meta_field = "ai_hint_2_meta" if requested_hint_no == 2 else "ai_hint_1_meta"
    existing_hint = str((record or {}).get(field) or "").strip()
    existing_meta = (record or {}).get(meta_field) if isinstance((record or {}).get(meta_field), dict) else {}
    stored_generation_count = int(
        (record or {}).get("hint_generation_count")
        or (record or {}).get("ai_hint_generation_count")
        or 0
    )
    actual_generation_count = (
        1 if str((record or {}).get("ai_hint_1_text") or "").strip() else 0
    ) + (
        1 if str((record or {}).get("ai_hint_2_text") or "").strip() else 0
    )
    first_existing_hint = str((record or {}).get("ai_hint_1_text") or "").strip()
    if requested_hint_no == 2 and existing_hint and first_existing_hint and existing_hint == first_existing_hint:
        # Older cached records could accidentally store the first AI hint in the
        # second slot. Treat that as missing so the second hint can be regenerated.
        existing_hint = ""
        existing_meta = {}
        actual_generation_count = 1
    current_view_count = int(
        (record or {}).get("hint_view_count")
        or (record or {}).get("ai_hint_view_count")
        or 0
    )
    payload = {
        "hint": existing_hint,
        "ai_feedback_detail": (att.get("ai_feedback_detail") if isinstance(att.get("ai_feedback_detail"), dict) else {}),
        "ai_diagnosis_summary": str(att.get("ai_diagnosis_summary") or ""),
        "hint_meta": existing_meta,
        "source": "cache" if existing_hint else "system",
    }
    if existing_hint and not existing_meta.get("hint_library_status"):
        cached_detail = (
            (record or {}).get("last_ai_feedback_detail")
            if isinstance((record or {}).get("last_ai_feedback_detail"), dict)
            else {}
        )
        cached_source = str(
            cached_detail.get("hint_source")
            or existing_meta.get("hint_source")
            or payload.get("source")
            or "cache"
        ).strip()
        cached_quality_status = str(
            cached_detail.get("hint_quality_status")
            or existing_meta.get("hint_quality_status")
            or "cached_hint_without_quality_status"
        ).strip()
        cached_leakage_status = str(
            cached_detail.get("answer_leakage_check")
            or existing_meta.get("answer_leakage_check")
            or "unknown"
        ).strip()
        cached_aggregate = _build_aggregated_hint_detail(
            att,
            task,
            requested_hint_no,
        )
        cached_library_fields = _auto_save_generated_hint_to_library(
            cached_aggregate,
            task,
            requested_hint_no,
            existing_hint,
            source=cached_source,
            quality_status=cached_quality_status,
            leakage_status=cached_leakage_status,
        )
        if cached_library_fields:
            payload["ai_feedback_detail"] = {
                **cached_aggregate,
                **cached_detail,
                **cached_library_fields,
                "hint_source": cached_source,
                "hint_quality_status": cached_quality_status,
                "answer_leakage_check": cached_leakage_status,
            }
            payload["hint_meta"] = _build_ai_hint_meta_for_attempt(
                att,
                task,
                requested_hint_no,
                detail=payload["ai_feedback_detail"],
            )

    generated = False
    if force or not existing_hint:
        if actual_generation_count < 2:
            payload = _generate_ai_hint_payload(att, task, requested_hint_no)
            existing_hint = payload.get("hint") or existing_hint
            generated = bool(existing_hint)
            if requested_hint_no == 2 and first_existing_hint and existing_hint.strip() == first_existing_hint.strip():
                detail = (
                    payload.get("ai_feedback_detail")
                    if isinstance(payload.get("ai_feedback_detail"), dict)
                    else _build_aggregated_hint_detail(att, task, requested_hint_no)
                )
                existing_hint = _aggregated_hint_fallback(detail, hint_level=2)
                payload["hint"] = existing_hint
                payload["source"] = "system_fallback_second_hint_deduped"

    hint_meta = payload.get("hint_meta") if isinstance(payload.get("hint_meta"), dict) else {}
    if not hint_meta:
        hint_meta = existing_meta or _build_ai_hint_meta_for_attempt(
            att,
            task,
            requested_hint_no,
            detail=payload.get("ai_feedback_detail") if isinstance(payload.get("ai_feedback_detail"), dict) else {},
        )
    payload["hint_meta"] = hint_meta

    final_generation_count = min(
        2,
        max(
            stored_generation_count,
            actual_generation_count + (1 if generated and existing_hint else 0),
        ),
    )
    set_fields = {
        "task_attempt_session": task_attempt_session,
        "hint_prompt_version": _PARSONS_HINT_PROMPT_VERSION,
        "latest_ai_hint_no": requested_hint_no,
        "second_error_positions": wrong_slots,
        "second_error_types": error_types,
        field: existing_hint or None,
        meta_field: hint_meta or {},
        "hint_generation_count": final_generation_count,
        "ai_hint_generation_count": final_generation_count,
        "last_attempt_id": str(att.get("_id") or ""),
        "latest_attempt_v2_id": str(att.get("attempt_v2_id") or "") or None,
        "last_ai_feedback_detail": payload.get("ai_feedback_detail") or {},
        "last_ai_diagnosis_summary": payload.get("ai_diagnosis_summary") or "",
    }
    inc_fields = {}
    if count_view:
        inc_fields["hint_view_count"] = 1
        inc_fields["ai_hint_view_count"] = 1
    record = _set_hint_record(
        student_id,
        task_id,
        set_fields=set_fields,
        inc_fields=inc_fields or None,
    )

    try:
        update_fields = {
            "ai_feedback_pending": False,
            "ai_feedback_generated_at": now_utc(),
            "ai_hint_requested_no": requested_hint_no,
            "hint_id": (record or {}).get("hint_id"),
        }
        if requested_hint_no == 1:
            update_fields["hint"] = existing_hint
            update_fields["ai_hint_1_text"] = existing_hint
            update_fields["ai_hint_1_meta"] = hint_meta or {}
        else:
            update_fields["ai_hint_2_text"] = existing_hint
            update_fields["ai_hint_2_meta"] = hint_meta or {}
        if payload.get("ai_feedback_detail"):
            update_fields["ai_feedback_detail"] = payload.get("ai_feedback_detail")
        if payload.get("ai_diagnosis_summary"):
            update_fields["ai_diagnosis_summary"] = payload.get("ai_diagnosis_summary")
        db.parsons_attempts.update_one({"_id": att.get("_id")}, {"$set": update_fields})
    except Exception:
        pass

    payload["hint"] = existing_hint
    payload["hint_meta"] = hint_meta or {}
    payload["generated"] = bool(generated)
    return record, payload

# 重新整理或重新進入題目時，還原已產生的兩次提示與 metadata
@parsons_bp.route("/hint_state", methods=["GET", "OPTIONS"])
def get_parsons_hint_state():
    if request.method == "OPTIONS":
        return ("", 204)
    student_id = current_student_id()
    task_id = str(request.args.get("task_id") or "").strip()
    if not student_id:
        return jsonify({"ok": False, "message": "missing student_id"}), 401
    if not task_id:
        return jsonify({"ok": False, "message": "missing task_id"}), 400

    latest = _latest_attempt_v2_doc(student_id, task_id, "practice", None)
    if latest:
        try:
            latest_session = max(1, int(latest.get("task_attempt_session") or 1))
        except Exception:
            latest_session = 1
        # 已答對後再次進入同題時，不得還原上一輪提示。
        if bool(latest.get("is_correct")):
            return jsonify({
                "ok": True,
                "task_attempt_session": latest_session + 1,
                "hint_record": None,
                "completed_previous_session": True,
            })
        current_session = latest_session
    else:
        current_session = 1

    record = _get_hint_record(student_id, task_id, current_session)

    return jsonify({
        "ok": True,
        "task_attempt_session": current_session,
        "hint_record": _hint_record_public(record),
        "completed_previous_session": False,
    })

# 產生或取得指定的第一次／第二次 AI 提示
@parsons_bp.route("/hint_library", methods=["POST", "OPTIONS"])
def upsert_hint_library_entry():
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(silent=True) or {}
    try:
        doc = _build_hint_library_upsert_doc(data)
    except ValueError as exc:
        return jsonify({
            "ok": False,
            "message": str(exc),
        }), 400
    except Exception as exc:
        return jsonify({
            "ok": False,
            "message": "hint_library payload invalid",
            "detail": str(exc),
        }), 400

    ensure_hint_library_indexes()
    key = (
        {"fingerprint_key": doc["fingerprint_key"], "version": doc["version"]}
        if doc.get("retrieval_eligible") and doc.get("fingerprint_key")
        else {"hint_key": doc["hint_key"], "version": doc["version"]}
    )
    set_fields = dict(doc)
    created_at = set_fields.pop("created_at", now_utc())

    try:
        result = db.hint_library.update_one(
            key,
            {
                "$set": set_fields,
                "$setOnInsert": {"created_at": created_at},
            },
            upsert=True,
        )
        saved = db.hint_library.find_one(key, {"_id": 1})
    except Exception as exc:
        return jsonify({
            "ok": False,
            "message": "hint_library write failed",
            "detail": str(exc),
        }), 500

    return jsonify({
        "ok": True,
        "data_source": "hint_library",
        "hint_key": doc["hint_key"],
        "version": doc["version"],
        "upserted": bool(result.upserted_id),
        "matched_count": int(result.matched_count or 0),
        "modified_count": int(result.modified_count or 0),
        "hint_library_id": str((saved or {}).get("_id") or result.upserted_id or ""),
    })


@parsons_bp.route("/hint", methods=["POST", "OPTIONS"])
def generate_parsons_hint():
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(silent=True) or {}
    attempt_id = str(data.get("attempt_id") or "").strip()
    force = bool(data.get("force"))
    try:
        requested_hint_no = 2 if int(data.get("requested_hint_no") or 1) == 2 else 1
    except Exception:
        requested_hint_no = 1

    if not attempt_id:
        return jsonify({"ok": False, "message": "missing attempt_id"}), 400

    try:
        att = db.parsons_attempts.find_one({"_id": ObjectId(attempt_id)})
    except Exception:
        return jsonify({"ok": False, "message": "invalid attempt_id"}), 400

    if not att:
        return jsonify({"ok": False, "message": "attempt not found"}), 404

    if not _attempt_belongs_to_current_student(att):
        return jsonify({
            "ok": False,
            "error": "attempt_access_denied",
            "message": "不得存取其他學生的作答提示。",
        }), 403

    task_id_for_record = str(att.get("task_id") or "").strip()
    if not task_id_for_record:
        return jsonify({"ok": False, "message": "attempt missing task_id"}), 400
    try:
        task_for_record = db.parsons_tasks.find_one({"_id": ObjectId(task_id_for_record)})
    except Exception:
        task_for_record = None
    if not task_for_record:
        return jsonify({"ok": False, "message": "task not found"}), 404

    try:
        record, payload = _prepare_ai_hint_record(
            att,
            task_for_record,
            requested_hint_no=requested_hint_no,
            force=force,
            count_view=True,
            explicit_attempt_v2_id=data.get("attempt_v2_id"),
        )
    except Exception as hint_prepare_error:
        # AI、SRT 檢索或提示資料寫入失敗時，不能讓學生看到 HTTP 500。
        # 改用依全部目前錯誤概念建立的安全 fallback，並把錯誤留在後端主控台。
        import traceback
        print("\n========== PARSONS HINT PREPARE ERROR ==========")
        print("error =", repr(hint_prepare_error))
        traceback.print_exc()
        print("================================================\n")

        aggregate_detail = _build_aggregated_hint_detail(
            att,
            task_for_record,
            requested_hint_no,
        )
        fallback_hint = _aggregated_hint_fallback(
            aggregate_detail,
            hint_level=requested_hint_no,
        )
        fallback_detail = {
            **aggregate_detail,
            "feedback_type": "progressive_srt_aggregated_hint",
            "hint_level": requested_hint_no,
            "hint_source": "system_fallback",
            "hint_quality_status": "hint_prepare_exception_fallback",
            "answer_leakage_check": "fallback_safe",
        }
        fallback_meta = _build_ai_hint_meta_for_attempt(
            att,
            task_for_record,
            requested_hint_no,
            detail=fallback_detail,
        )
        fallback_student_id = str(
            att.get("student_id")
            or current_student_id()
            or ""
        ).strip()
        existing_fallback_record = _get_hint_record(
            fallback_student_id,
            task_id_for_record,
            att.get("task_attempt_session"),
        )
        fallback_text_field = (
            "ai_hint_2_text"
            if requested_hint_no == 2
            else "ai_hint_1_text"
        )
        fallback_meta_field = (
            "ai_hint_2_meta"
            if requested_hint_no == 2
            else "ai_hint_1_meta"
        )
        existing_hint_count = (
            1
            if str(
                (existing_fallback_record or {}).get("ai_hint_1_text")
                or ""
            ).strip()
            else 0
        ) + (
            1
            if str(
                (existing_fallback_record or {}).get("ai_hint_2_text")
                or ""
            ).strip()
            else 0
        )
        fallback_generation_count = min(
            2,
            max(
                existing_hint_count,
                requested_hint_no,
            ),
        )
        try:
            record = _set_hint_record(
                fallback_student_id,
                task_id_for_record,
                set_fields={
                    "task_attempt_session": int(
                        att.get("task_attempt_session")
                        or (existing_fallback_record or {}).get(
                            "task_attempt_session"
                        )
                        or 1
                    ),
                    "hint_prompt_version": _PARSONS_HINT_PROMPT_VERSION,
                    "latest_ai_hint_no": requested_hint_no,
                    fallback_text_field: fallback_hint,
                    fallback_meta_field: fallback_meta,
                    "hint_generation_count": fallback_generation_count,
                    "ai_hint_generation_count": fallback_generation_count,
                    "last_attempt_id": str(att.get("_id") or ""),
                    "latest_attempt_v2_id": str(
                        data.get("attempt_v2_id")
                        or att.get("attempt_v2_id")
                        or ""
                    ) or None,
                    "last_ai_feedback_detail": fallback_detail,
                    "last_ai_diagnosis_summary": (
                        "提示生成發生例外，已改用安全概念提示。"
                    ),
                },
                inc_fields={
                    "hint_view_count": 1,
                    "ai_hint_view_count": 1,
                },
            )
        except Exception as fallback_save_error:
            print(
                "[parsons hint] fallback save failed:",
                repr(fallback_save_error),
            )
            record = existing_fallback_record

        payload = {
            "hint": fallback_hint,
            "hint_meta": fallback_meta,
            "ai_feedback_detail": fallback_detail,
            "ai_diagnosis_summary": "提示生成發生例外，已改用安全概念提示。",
            "source": "system_fallback",
            "generated": True,
            "fallback_reason": "hint_prepare_exception",
        }

    try:
        updated_attempt_v2_id = _update_attempt_v2_ai_hint_event(
            att,
            record,
            requested_hint_no=requested_hint_no,
            explicit_attempt_v2_id=data.get("attempt_v2_id"),
            hint_text_override=payload.get("hint") or "",
            hint_meta_override=payload.get("hint_meta") or {},
            event_type="ai_hint_api_view",
            generated=bool(payload.get("generated")),
            trigger_method=data.get("trigger_method") or ("generate_second_ai_hint" if requested_hint_no == 2 else "view_ai_hint"),
            button_name=data.get("button_name") or ("產生第二次 AI 提示" if requested_hint_no == 2 else "查看 AI 提示"),
        )
    except Exception as hint_event_error:
        print("[parsons hint] attempt v2 hint event update failed:", repr(hint_event_error))
        updated_attempt_v2_id = data.get("attempt_v2_id") or att.get("attempt_v2_id")

    write_learning_log_safely({
        "session_id": data.get("session_id"),
        "student_id": current_student_id(),
        "event_type": "ai_hint_view",
        "page": data.get("page") or "parsons",
        "activity_type": data.get("activity_type") or "practice",
        "test_role": data.get("test_role"),
        "task_id": task_id_for_record,
        "attempt_id": data.get("attempt_v2_id") or data.get("learning_attempt_id"),
        "attempt_no": data.get("attempt_no"),
        "target_concept": data.get("target_concept"),
        "metadata": _hint_log_metadata(
            record,
            requested_hint_no=requested_hint_no,
            error_types=_hint_error_types(attempt_doc=att),
            wrong_slots=_attempt_wrong_positions_for_hint(att),
            repeated_error=True,
            extra={
                "task_attempt_session": int((record or {}).get("task_attempt_session") or att.get("task_attempt_session") or 1),
                "attempt_v2_id": updated_attempt_v2_id or data.get("attempt_v2_id"),
                "trigger_method": data.get("trigger_method") or ("generate_second_ai_hint" if requested_hint_no == 2 else "view_ai_hint"),
                "button_name": data.get("button_name") or ("產生第二次 AI 提示" if requested_hint_no == 2 else "查看 AI 提示"),
            },
        ),
    })
    return jsonify({
        "ok": True,
        "attempt_id": attempt_id,
        "attempt_v2_id": updated_attempt_v2_id or data.get("attempt_v2_id"),
        "task_attempt_session": int((record or {}).get("task_attempt_session") or att.get("task_attempt_session") or 1),
        "hint_prompt_version": (record or {}).get("hint_prompt_version") or _PARSONS_HINT_PROMPT_VERSION,
        "requested_hint_no": requested_hint_no,
        "source": payload.get("source"),
        "hint": payload.get("hint") or "",
        "hint_meta": payload.get("hint_meta") or {},
        "ai_feedback_detail": payload.get("ai_feedback_detail") or {},
        "ai_diagnosis_summary": payload.get("ai_diagnosis_summary") or "",
        "hint_record": _hint_record_public(record),
    })



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

        # 5) 寫回 DB（確保不是只在記憶體）
        try:
            db.parsons_tasks.update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "solution_blocks": doc.get("solution_blocks", []),
                    "distractor_blocks": doc.get("distractor_blocks", []),
                    "pool": doc.get("pool", []),

                    "ai_slot_hints": doc.get("ai_slot_hints", {}),
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


# 提示文字相似度工具
def _lexical_overlap_ratio(a: str, b: str) -> float:
    ta = [x for x in _re.split(r"[^a-zA-Z0-9_\u4e00-\u9fff]+", str(a or "").lower()) if x]
    tb = [x for x in _re.split(r"[^a-zA-Z0-9_\u4e00-\u9fff]+", str(b or "").lower()) if x]
    if not ta or not tb:
        return 0.0
    sa = set(ta)
    sb = set(tb)
    inter = len(sa.intersection(sb))
    denom = max(1, len(sb))
    return float(inter) / float(denom)
