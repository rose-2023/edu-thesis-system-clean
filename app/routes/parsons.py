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
    "/review_choice",
    "/review_watch",
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

# 控制影片跳轉功能是否啟用。
def _video_review_enabled() -> bool:
    """Return whether video review and subtitle alignment features are enabled."""
    return str(os.getenv("PARSONS_VIDEO_REVIEW_ENABLED", "0")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

# 選擇字幕對齊模型與 AI 提示模型。
def _video_review_disabled_response():
    return jsonify({
        "ok": False,
        "disabled": True,
        "feature": "parsons_video_review",
        "message": "Parsons video review is temporarily disabled.",
    }), 503


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
_PARSONS_HINT_PROMPT_VERSION = "srt_aggregated_all_errors_v2_similarity_checked"


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
    """Create indexes for reusable Parsons hint templates."""
    global _PARSONS_HINT_LIBRARY_INDEX_READY
    if _PARSONS_HINT_LIBRARY_INDEX_READY:
        return
    try:
        db.hint_library.create_index(
            [("hint_key", 1), ("version", -1)],
            name="hint_key_version_1",
        )
        db.hint_library.create_index(
            [
                ("concept_tag", 1),
                ("error_type", 1),
                ("hint_level", 1),
                ("scope", 1),
                ("language", 1),
                ("is_active", 1),
            ],
            name="concept_error_level_scope_lang_active_1",
        )
        db.hint_library.create_index(
            [("task_scope", 1), ("task_family", 1), ("is_active", 1)],
            name="task_scope_family_active_1",
        )
        db.hint_library.create_index([("updated_at", -1)], name="updated_at_-1")
        _PARSONS_HINT_LIBRARY_INDEX_READY = True
    except Exception as e:
        print(f"[hint_library] index ensure failed: {e}")


def _clean_string(value):
    text = str(value or "").strip()
    return text or None


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

    concept_tag = ""
    for item in (detail.get("concept_tags") or []):
        concept_tag = normalize_concept_name(item)
        if concept_tag:
            break
    if not concept_tag:
        for item in (detail.get("error_concepts") or []):
            if not isinstance(item, dict):
                continue
            concept_tag = normalize_concept_name(item.get("concept_tag") or "")
            if concept_tag:
                break
    concept_tag = concept_tag or "unknown"

    error_type = ""
    for concept in (detail.get("error_concepts") or []):
        if not isinstance(concept, dict):
            continue
        for candidate in (concept.get("error_types") or []):
            error_type = _hint_library_error_type(candidate)
            if error_type and error_type != "unknown":
                break
        if error_type and error_type != "unknown":
            break
    if not error_type or error_type == "unknown":
        for slot in (detail.get("wrong_slot_details") or []):
            if not isinstance(slot, dict):
                continue
            for candidate in (slot.get("error_types") or []):
                error_type = _hint_library_error_type(candidate)
                if error_type and error_type != "unknown":
                    break
            if error_type and error_type != "unknown":
                break
    error_type = error_type or "unknown"

    return {
        "concept_tag": concept_tag,
        "error_type": error_type,
        "hint_level": hint_level,
        "scope": scope,
        "hint_key": f"{concept_tag}|{error_type}|level_{hint_level}",
    }


def _hint_library_task_family(task):
    doc = task if isinstance(task, dict) else {}
    return _clean_string(
        doc.get("task_family")
        or doc.get("family")
        or doc.get("target_concept")
    )


def _hint_library_concept_aliases(concept_tag):
    tag = str(concept_tag or "").strip()
    normalized = normalize_concept_name(tag)
    aliases = [tag, normalized]
    loop_tags = {
        "for_loop",
        "while_loop",
        "loop",
        "loops",
        "loop_count_control",
        "loop_reverse_range",
        "nested_loop_structure",
        "star_formula_2i_minus_1",
        "space_formula_n_minus_i",
    }
    branch_tags = {
        "if",
        "if_else",
        "condition",
        "branch",
        "if_condition_logic",
        "if_branch_order",
        "edge_case_condition",
    }
    io_tags = {
        "input",
        "output",
        "print",
        "input_int_cast",
        "print_separator",
    }
    syntax_tags = {"syntax", "python_syntax", "def", "return", "call", "operator"}
    if normalized in loop_tags or tag in loop_tags:
        aliases.extend(["for_loop", "loop", "loop_count_control"])
    elif normalized in branch_tags or tag in branch_tags:
        aliases.extend(["if", "condition", "if_condition_logic"])
    elif normalized in io_tags or tag in io_tags:
        aliases.extend(["input_output", "io", "input_int_cast", "print_separator"])
    elif normalized in syntax_tags or tag in syntax_tags:
        aliases.extend(["syntax", "python_syntax"])
    return [
        item
        for item in dict.fromkeys(str(alias or "").strip() for alias in aliases)
        if item
    ]


def _find_hint_library_entry(aggregate_detail, task, level):
    ensure_hint_library_indexes()
    ctx = _hint_library_context_from_detail(aggregate_detail, level)
    task_family = _hint_library_task_family(task)
    concept_aliases = _hint_library_concept_aliases(ctx.get("concept_tag"))
    concept_filter = {"$in": concept_aliases} if concept_aliases else ctx["concept_tag"]
    hint_keys = [
        f"{alias}|{ctx['error_type']}|level_{ctx['hint_level']}"
        for alias in concept_aliases
    ] or [ctx["hint_key"]]
    base = {
        "is_active": True,
        "quality_status": "passed",
        "answer_leakage_check": "passed",
        "language": "zh-TW",
        "hint_level": ctx["hint_level"],
    }
    queries = [{**base, "hint_key": {"$in": hint_keys}}]
    if task_family:
        queries.append({
            **base,
            "concept_tag": concept_filter,
            "error_type": ctx["error_type"],
            "task_scope": "task_family",
            "task_family": task_family,
        })
    queries.extend([
        {
            **base,
            "concept_tag": concept_filter,
            "error_type": ctx["error_type"],
            "scope": ctx["scope"],
            "task_scope": "concept",
        },
        {
            **base,
            "concept_tag": concept_filter,
            "error_type": ctx["error_type"],
            "scope": ctx["scope"],
        },
    ])

    for query in queries:
        try:
            doc = db.hint_library.find_one(
                query,
                sort=[("version", -1), ("updated_at", -1), ("_id", -1)],
            )
        except Exception as exc:
            print("[hint_library] lookup failed:", repr(exc))
            return None, ctx
        if doc and str(doc.get("hint_template") or "").strip():
            return doc, ctx
    return None, ctx


def _mark_hint_library_used(doc):
    if not isinstance(doc, dict) or doc.get("_id") is None:
        return
    now = now_utc()
    try:
        db.hint_library.update_one(
            {"_id": doc["_id"]},
            {
                "$inc": {"usage_count": 1},
                "$set": {"last_used_at": now, "updated_at": now},
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
        "hint_library_version": version,
        "hint_library_concept_tag": str(doc.get("concept_tag") or context.get("concept_tag") or "").strip(),
        "hint_library_error_type": str(doc.get("error_type") or context.get("error_type") or "").strip(),
        "hint_library_task_scope": str(doc.get("task_scope") or "").strip(),
        "hint_library_task_family": doc.get("task_family"),
        "source_model": doc.get("source_model"),
        "source_prompt_version": doc.get("source_prompt_version"),
        "source_hint_id": doc.get("source_hint_id"),
        "source_task_id": doc.get("source_task_id"),
    }


def _hint_library_status_fields(ctx, status, reason=None):
    context = ctx if isinstance(ctx, dict) else {}
    return {
        "hint_library_status": str(status or "").strip() or "unknown",
        "hint_library_skip_reason": (
            str(reason or "").strip()
            if reason
            else None
        ),
        "hint_library_lookup_key": str(context.get("hint_key") or "").strip(),
        "hint_library_concept_tag": str(context.get("concept_tag") or "").strip() or None,
        "hint_library_error_type": str(context.get("error_type") or "").strip() or None,
        "hint_library_level": context.get("hint_level"),
        "hint_library_scope": str(context.get("scope") or "").strip() or None,
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
    hint_level = _hint_library_level(
        data.get("hint_level"),
        data.get("hint_key"),
    )

    concept_tag = str(data.get("concept_tag") or "").strip()
    normalized_concept_tag = normalize_concept_name(concept_tag)
    error_type = _hint_library_error_type(data.get("error_type") or "")
    scope = str(
        data.get("scope") or ("narrow" if hint_level == 2 else "broad")
    ).strip().lower()
    if scope not in {"broad", "narrow"}:
        scope = "narrow" if hint_level == 2 else "broad"

    hint_key = str(data.get("hint_key") or "").strip()
    if not hint_key:
        hint_key = f"{concept_tag or 'unknown'}|{error_type}|level_{hint_level}"

    hint_template = str(data.get("hint_template") or "").strip()
    if not concept_tag:
        raise ValueError("concept_tag is required")
    if not error_type or error_type == "unknown":
        raise ValueError("error_type is required")
    if not hint_template:
        raise ValueError("hint_template is required")
    if not normalized_concept_tag:
        normalized_concept_tag = concept_tag

    try:
        version = max(1, int(data.get("version") or 1))
    except Exception:
        version = 1
    try:
        schema_version = max(1, int(data.get("schema_version") or 1))
    except Exception:
        schema_version = 1

    return {
        "schema_version": schema_version,
        "hint_key": hint_key,
        "version": version,
        "concept_tag": concept_tag,
        "error_type": error_type,
        "hint_level": hint_level,
        "scope": scope,
        "task_scope": str(data.get("task_scope") or "concept").strip() or "concept",
        "task_family": _clean_string(data.get("task_family")),
        "language": str(data.get("language") or "zh-TW").strip() or "zh-TW",
        "hint_template": hint_template,
        "hint_source": str(data.get("hint_source") or "ai_generated").strip() or "ai_generated",
        "quality_status": str(data.get("quality_status") or "passed").strip() or "passed",
        "answer_leakage_check": str(data.get("answer_leakage_check") or "passed").strip() or "passed",
        "usage_count": _hint_library_nonnegative_int(data.get("usage_count"), 0),
        "evaluated_count": _hint_library_nonnegative_int(data.get("evaluated_count"), 0),
        "success_count": _hint_library_nonnegative_int(data.get("success_count"), 0),
        "last_used_at": _hint_library_date(data.get("last_used_at")),
        "source_prompt_version": str(
            data.get("source_prompt_version") or _PARSONS_HINT_PROMPT_VERSION
        ).strip(),
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
    """Persist a safe AI-generated hint as a reusable concept/error template."""
    ctx = _hint_library_context_from_detail(aggregate_detail, level)
    status_context = _hint_library_status_fields(ctx, "skipped")

    if str(source or "").strip() != "ai_srt_aggregated":
        status_context["hint_library_skip_reason"] = "not_ai_generated"
        return status_context
    if str(leakage_status or "").strip() != "passed":
        status_context["hint_library_skip_reason"] = "answer_leakage_not_passed"
        return status_context
    if str(quality_status or "").strip() not in _HINT_LIBRARY_AUTO_SAVE_QUALITY_STATUSES:
        status_context["hint_library_skip_reason"] = "quality_status_not_auto_saved"
        return status_context

    hint_template = str(hint_text or "").strip()
    if not hint_template:
        status_context["hint_library_skip_reason"] = "empty_hint_template"
        return status_context

    concept_tag = str(ctx.get("concept_tag") or "").strip()
    error_type = str(ctx.get("error_type") or "").strip()
    if not concept_tag or concept_tag == "unknown":
        status_context["hint_library_skip_reason"] = "unknown_concept_tag"
        return status_context
    if not error_type or error_type == "unknown":
        status_context["hint_library_skip_reason"] = "unknown_error_type"
        return status_context

    task_doc = task if isinstance(task, dict) else {}
    source_task_id = _clean_string(
        task_doc.get("task_id")
        or task_doc.get("id")
        or str(task_doc.get("_id") or "")
    )
    now = now_utc()
    try:
        doc = _build_hint_library_upsert_doc({
            "schema_version": 1,
            "hint_key": ctx.get("hint_key"),
            "version": 1,
            "concept_tag": concept_tag,
            "error_type": error_type,
            "hint_level": ctx.get("hint_level"),
            "scope": ctx.get("scope"),
            "task_scope": "concept",
            "task_family": None,
            "language": "zh-TW",
            "hint_template": hint_template,
            "hint_source": "ai_generated",
            "quality_status": "passed",
            "answer_leakage_check": "passed",
            "usage_count": 1,
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
        result = db.hint_library.update_one(
            {
                "hint_key": doc["hint_key"],
                "version": doc["version"],
            },
            {"$setOnInsert": doc},
            upsert=True,
        )
        if result.upserted_id:
            saved = db.hint_library.find_one({"_id": result.upserted_id})
            fields = _hint_library_public_fields(saved, ctx) if saved else {}
            fields.update(_hint_library_status_fields(ctx, "auto_saved"))
            return fields
        saved = db.hint_library.find_one(
            {
                "hint_key": doc["hint_key"],
                "version": doc["version"],
            }
        )
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


def _collect_all_wrong_slot_contexts(att, task):
    """Collect per-slot hint context for all current wrong slots."""
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
        expected_text = str(pool.get(expected_block_id, {}).get("text") or "").strip()
        submitted_text = str(pool.get(submitted_block_id, {}).get("text") or "").strip()

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

        details.append({
            "slot_index": slot_index,
            "slot_label": f"第{slot_index + 1}格",
            "error_types": slot_errors,
            "submitted_block_id": submitted_block_id,
            "expected_block_id": expected_block_id,
            "submitted_text": submitted_text,
            "expected_text": expected_text,
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
    seen = set()
    for item in contexts:
        raw_tag = normalize_concept_name(item.get("concept_tag") or item.get("concept") or "")
        tag = raw_tag or "unknown"
        scope = str(item.get("concept_scope") or _progressive_hint_concept_label(tag, raw_tag) or "待釐清概念").strip()
        if tag not in seen:
            seen.add(tag)
            concept_tags.append(tag)
            concept_scopes.append(scope)
        concept_error_types.setdefault(tag, set()).update(str(x) for x in (item.get("error_types") or []) if str(x or "").strip())
        concept_slots.setdefault(tag, []).append(int(item.get("slot_index")))

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
    level = 2 if int(requested_hint_no or 1) == 2 else 1
    scope_name = "narrow" if level == 2 else "broad"
    slot_contexts = _collect_all_wrong_slot_contexts(att, task)
    public_details = _public_wrong_slot_details(slot_contexts)
    aggregate = _aggregate_wrong_slot_contexts(slot_contexts)
    wrong_slots = sorted(set([int(item["slot_index"]) for item in public_details]))

    subtitle_ranges = []
    error_concepts = []
    first_selected_range = {}
    first_broad_range = {}
    first_narrow_range = {}
    first_basis = ""

    for concept_tag, concept_scope in zip(aggregate.get("concept_tags") or [], aggregate.get("concept_scopes") or []):
        concept_slots = aggregate.get("concept_slots", {}).get(concept_tag) or wrong_slots
        slot_index = concept_slots[0] if concept_slots else (wrong_slots[0] if wrong_slots else None)
        try:
            scope = _build_progressive_subtitle_scope(
                task,
                slot_index,
                concept_tag or "logic",
                level,
            )
        except Exception:
            scope = {}

        selected = _hint_range_payload({
            "start": scope.get("start"),
            "end": scope.get("end"),
            "source": scope.get("source"),
        })
        broad = _hint_range_payload(scope.get("broad_range") or {})
        narrow = _hint_range_payload(scope.get("narrow_range") or {})
        basis = _compact_subtitle_basis(scope.get("subtitle_excerpt"), concept_scope)

        subtitle_ranges.append({
            "concept_tag": concept_tag,
            "concept_scope": concept_scope,
            "start": selected.get("start"),
            "end": selected.get("end"),
            "source": selected.get("source"),
        })
        concept_error_types = aggregate.get("concept_error_types", {}).get(concept_tag) or []
        error_concepts.append({
            "concept_tag": concept_tag,
            "concept_scope": concept_scope,
            "error_types": concept_error_types,
            "subtitle_basis": basis,
        })

        if not first_selected_range:
            first_selected_range = selected
            first_broad_range = broad
            first_narrow_range = narrow
            first_basis = basis

    if not error_concepts:
        fallback_scope = "程式流程與區塊順序"
        error_concepts.append({
            "concept_tag": "unknown",
            "concept_scope": fallback_scope,
            "error_types": [],
            "subtitle_basis": fallback_scope,
        })

    covered_wrong_slots = wrong_slots if aggregate.get("concept_tags") else []
    uncovered_wrong_slots = [] if aggregate.get("concept_tags") else wrong_slots

    return {
        "hint_level": level,
        "scope": scope_name,
        "aggregation_mode": "all_current_errors",
        "wrong_slots": wrong_slots,
        "wrong_slot_count": len(wrong_slots),
        "wrong_slot_details": public_details,
        "concept_tags": aggregate.get("concept_tags") or ["unknown"],
        "concept_scopes": aggregate.get("concept_scopes") or ["程式流程與區塊順序"],
        "concept_count": len(aggregate.get("concept_tags") or ["unknown"]),
        "covered_wrong_slots": covered_wrong_slots,
        "uncovered_wrong_slots": uncovered_wrong_slots,
        "subtitle_ranges": subtitle_ranges,
        "subtitle_range": first_selected_range,
        "subtitle_broad_range": first_broad_range,
        "subtitle_narrow_range": first_narrow_range,
        "subtitle_basis": first_basis,
        "error_concepts": error_concepts,
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
        "subtitle_basis": _compact_subtitle_basis(detail.get("subtitle_basis"), concept_scope),
        "hint_source": str(detail.get("hint_source") or "system_fallback").strip() or "system_fallback",
        "answer_leakage_check": str(detail.get("answer_leakage_check") or "unknown").strip() or "unknown",
        "hint_quality_status": str(detail.get("hint_quality_status") or "not_checked").strip() or "not_checked",
        "hint_library_id": str(detail.get("hint_library_id") or "").strip() or None,
        "hint_library_key": str(detail.get("hint_library_key") or "").strip() or None,
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
        "ai_segment_map": {}, # AI 推估的 slot -> segment（start/end/evidence）
        "teacher_segment_map": {}, # 老師手動修正的 slot -> segment（submit 優先）
        "teacher_concept_segment_map": {}, #老師確認的概念章節
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

    # upsert：同影片只保留一筆 fixed（相容舊欄位 source_type）
    q = {"$and": [{"$or": [{"gen_source": "fixed"}, {"source_type": "fixed"}]}]}
    if vid_oid:
        q["$and"].append({"video_id": vid_oid})
    else:
        q["$and"].append({"video_id_str": video_id})

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
    q = {
        "$and": [
            {"$or": [{"gen_source": "fixed"}, {"source_type": "fixed"}]},
            ({"$or": [{"video_id": vid_oid}, {"video_id_str": video_id}]} if vid_oid else {"video_id_str": video_id}),
        ]
    }

    task = db.parsons_tasks.find_one(q)
    if not task:
        return jsonify({"ok": True, "found": False})

    sol_lines = [b.get("text", "") for b in (task.get("solution_blocks") or [])]
    dis_lines = [b.get("text", "") for b in (task.get("distractor_blocks") or [])]
    video_review_enabled = _video_review_enabled()

    return jsonify({
        "ok": True,
        "found": True,
        "task_id": str(task["_id"]),
        "question_text": task.get("question_text") or "",
        "solution_lines": sol_lines,
        "distractor_lines": dis_lines,
        "ai_segment_map": (task.get("ai_segment_map") or {}) if video_review_enabled else {},
        "teacher_segment_map": (task.get("teacher_segment_map") or {}) if video_review_enabled else {},
        "concept_chapters_draft": (task.get("concept_chapters_draft") or []) if video_review_enabled else [],
        "concept_chapters_formal": (
            task.get("concept_chapters_formal") or task.get("teacher_concept_chapters") or []
        ) if video_review_enabled else [],
        "teacher_concept_chapters": (task.get("teacher_concept_chapters") or []) if video_review_enabled else [],
        "concept_align_status": (task.get("concept_align_status") or "") if video_review_enabled else "",
        "teacher_concept_version_key": task.get("teacher_concept_version_key") or "",
        "teacher_concept_updated_at": task.get("teacher_concept_updated_at"),
        "enabled": bool(task.get("enabled", False)),
        "status": task.get("status") or "pending",
    })

# ========================


# ========================
# [新增] POST /fixed_task/save_teacher_concept_chapters
# 老師手動儲存/確認概念章節草稿(不使用)
# ========================
@parsons_bp.post("/fixed_task/save_teacher_concept_chapters")
def save_teacher_concept_chapters():
    data = request.get_json(silent=True) or {}
    task_id_str = (data.get("task_id") or "").strip()
    chapters = data.get("chapters") or []

    if not task_id_str:
        return jsonify({"ok": False, "message": "missing task_id"}), 400

    try:
        task = db.parsons_tasks.find_one({"_id": ObjectId(task_id_str)})
    except Exception:
        task = None
        
    if not task:
        return jsonify({"ok": False, "message": "找不到任務"}), 404

    from .parsons_concept_align import validate_chapters
    from .parsons_concept_align import derive_concept_version_key

    subtitle_raw = _get_subtitle_for_task(task)
    if not subtitle_raw:
        return jsonify({"ok": False, "message": "找不到字幕內容，無法推估缺漏章節時間"}), 400

    subtitle_index = _build_subtitle_index_from_text(subtitle_raw)
    subtitle_segments = subtitle_index.get("segments") or []
    subtitle_version = str(data.get("subtitle_version") or "").strip()
    version_key = derive_concept_version_key(task, subtitle_version)
    teaching_range_start = data.get("teaching_range_start")
    teaching_range_end = data.get("teaching_range_end")
    code_start_ts = data.get("code_start_ts")
    if code_start_ts is None or str(code_start_ts).strip() == "":
        code_start_ts = teaching_range_start or task.get("teacher_code_start_ts") or task.get("concept_code_start_ts")
    if teaching_range_start is None or str(teaching_range_start).strip() == "":
        teaching_range_start = code_start_ts

    def _to_float_or_none(value):
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        parts = text.split(":")
        try:
            return float(text)
        except Exception:
            pass
        try:
            if len(parts) == 2:
                return (float(parts[0]) * 60.0) + float(parts[1])
            if len(parts) == 3:
                return (float(parts[0]) * 3600.0) + (float(parts[1]) * 60.0) + float(parts[2])
        except Exception:
            return None
        return None

    teaching_range_start_sec = _to_float_or_none(teaching_range_start)
    teaching_range_end_sec = _to_float_or_none(teaching_range_end)
    effective_segments = subtitle_segments
    if teaching_range_start_sec is not None and teaching_range_end_sec is not None and teaching_range_end_sec > teaching_range_start_sec:
        effective_segments = _subtitle_segments_in_range(subtitle_segments, teaching_range_start_sec, teaching_range_end_sec)
    if (data.get("teaching_range_start") is not None or data.get("teaching_range_end") is not None) and not effective_segments:
        return jsonify({
            "ok": True,
            "message": "教學區間內沒有足夠字幕可儲存章節，請放大教學區間。",
            "warnings": ["teaching_range_empty"],
            "teaching_range_warning": {
                "code": "teaching_range_empty",
                "message": "教學區間內沒有足夠字幕可儲存章節，請放大教學區間。",
                "recommended_range": _subtitle_bounds(subtitle_segments),
            },
            "subtitle_segments": subtitle_segments,
            "subtitle_segments_count": len(subtitle_segments),
            "effective_segments": effective_segments,
            "effective_segments_count": len(effective_segments),
        }), 200

    prepared_chapters = []
    for idx, ch in enumerate(chapters or []):
        if not isinstance(ch, dict):
            continue

        item = dict(ch)
        concept_tag = normalize_concept_name(
            item.get("concept_tag")
            or item.get("concept")
            or item.get("wrong_type")
            or item.get("label")
            or ""
        )
        if not concept_tag:
            continue

        item["concept_tag"] = concept_tag
        item["concept"] = concept_tag
        item["concept_label"] = str(item.get("concept_label") or item.get("chapter_label") or concept_tag_to_label(concept_tag) or concept_tag).strip()
        item["chapter_label"] = item["concept_label"]
        item["source"] = str(item.get("source") or item.get("chapter_source") or "").strip()
        item["chapter_source"] = str(item.get("chapter_source") or item.get("source") or "").strip()

        start_sec = _to_float_or_none(item.get("start"))
        end_sec = _to_float_or_none(item.get("end"))

        if start_sec is None or end_sec is None:
            inferred = infer_concept_window_from_subtitles(
                concept_tag,
                subtitle_segments,
                existing_chapters=prepared_chapters,
                code_start_ts=teaching_range_start or code_start_ts,
            )
            if inferred:
                start_sec = inferred.get("start")
                end_sec = inferred.get("end")

        if start_sec is None or end_sec is None:
            return jsonify({
                "ok": False,
                "message": f'章節「{item["concept_label"]}」找不到合理字幕區間，請手動輸入開始與結束時間。',
                "chapter_errors": [
                    {
                        "index": idx,
                        "concept_tag": concept_tag,
                        "concept_label": item["concept_label"],
                        "reason": "no_reasonable_window_found",
                    }
                ],
            }), 400

        if end_sec <= start_sec:
            return jsonify({
                "ok": False,
                "message": f'章節「{item["concept_label"]}」的時間區段無效。',
                "chapter_errors": [
                    {
                        "index": idx,
                        "concept_tag": concept_tag,
                        "concept_label": item["concept_label"],
                        "reason": "invalid_time_range",
                    }
                ],
            }), 400

        item["start"] = round(float(start_sec), 2)
        item["end"] = round(float(end_sec), 2)

        if _chapter_conflicts_with_existing(item["start"], item["end"], prepared_chapters, threshold=0.98):
            return jsonify({
                "ok": False,
                "message": f'章節「{item["concept_label"]}」與既有章節時間重疊過高，請調整後再儲存。',
                "chapter_errors": [
                    {
                        "index": idx,
                        "concept_tag": concept_tag,
                        "concept_label": item["concept_label"],
                        "reason": "overlaps_existing_chapter",
                    }
                ],
            }), 400

        prepared_chapters.append(item)

    semantic_result = _apply_semantic_constraint_to_chapters(prepared_chapters, task, subtitle_segments=effective_segments, code_start_ts=code_start_ts)
    valid_chapters = semantic_result.get("chapters") or []
    if not valid_chapters:
        return jsonify({"ok": False, "message": "無效的章節格式"}), 400

    chapter_warnings = list(dict.fromkeys((semantic_result.get("warnings") or []) + _collect_chapter_warning_codes(valid_chapters)))

    # 同步老師確認章節到 slot 時間軸，避免學生端仍讀到舊 ai_segment_map。
    solution_blocks = task.get("solution_blocks") or []
    block_chapter_map = map_blocks_to_chapters(solution_blocks, valid_chapters)
    slot_concept_map = _derive_slot_concept_map(task)

    chapter_by_concept = {}
    for ch in (valid_chapters or []):
        if not isinstance(ch, dict):
            continue
        tag = normalize_concept_name(ch.get("concept_tag") or ch.get("concept") or ch.get("wrong_type"))
        if tag and tag not in chapter_by_concept:
            chapter_by_concept[tag] = ch

    for i, _ in enumerate(solution_blocks or []):
        slot_key = str(i)
        if slot_key in block_chapter_map:
            continue
        ctag = normalize_concept_name((slot_concept_map or {}).get(slot_key))
        ch = chapter_by_concept.get(ctag)
        if not isinstance(ch, dict):
            continue
        try:
            s = float(ch.get("start"))
            e = float(ch.get("end"))
        except Exception:
            continue
        if e <= s:
            continue
        block_chapter_map[slot_key] = {
            "concept": ctag,
            "concept_tag": ctag,
            "surface_tag": normalize_surface_tag(ch.get("surface_tag") or ch.get("wrong_type")),
            "concept_label": str(ch.get("concept_label") or ch.get("chapter_label") or concept_tag_to_label(ctag) or ctag),
            "start": round(s, 2),
            "end": round(e, 2),
            "chapter_index": int(ch.get("chapter_index", 0)) if str(ch.get("chapter_index", "")).strip().isdigit() else None,
            "method": "teacher_concept_fallback",
        }

    teacher_segment_map = {}
    for slot_key, row in (block_chapter_map or {}).items():
        if not isinstance(row, dict):
            continue
        try:
            s = float(row.get("start"))
            e = float(row.get("end"))
        except Exception:
            continue
        if e <= s:
            continue
        teacher_segment_map[str(slot_key)] = {
            "start": round(s, 2),
            "end": round(e, 2),
            "text": str(row.get("concept_label") or row.get("concept") or row.get("concept_tag") or "").strip(),
            "source": "teacher_concept_chapter",
            "evidence": f"concept={row.get('concept_tag') or row.get('concept') or ''}",
            "score": 1.0,
        }

    teacher_concept_segment_map = build_concept_segment_map(block_chapter_map, valid_chapters)

    subtitle_range = {}
    if valid_chapters:
        starts = []
        ends = []
        for ch in valid_chapters:
            if not isinstance(ch, dict):
                continue
            try:
                s = float(ch.get("start"))
                e = float(ch.get("end"))
            except Exception:
                continue
            if e <= s:
                continue
            starts.append(s)
            ends.append(e)
        if starts and ends:
            subtitle_range = {
                "start": round(min(starts), 2),
                "end": round(max(ends), 2),
            }

    # 存為老師確認版，並把狀態改成已更新
    db.parsons_tasks.update_one(
        {"_id": task["_id"]},
        {"$set": {
            "concept_chapters_formal": valid_chapters,
            "teacher_concept_chapters": valid_chapters,
            "concept_chapters_warnings": chapter_warnings,
            "concept_align_status": "teacher_confirmed",
            "teacher_concept_version_key": version_key,
            "teacher_concept_updated_at": now_utc(),
            "concept_align_version_key": version_key,
            "teacher_code_start_ts": code_start_ts,
            "teaching_range_start": teaching_range_start,
            "teaching_range_end": teaching_range_end,
            "effective_segments_count": len(effective_segments),
            "block_chapter_map": block_chapter_map,
            "block_chapter_code_map": block_chapter_map,
            "teacher_segment_map": teacher_segment_map, #老師針對某一格設定的時間
            "teacher_concept_segment_map": teacher_concept_segment_map,
            "concept_segment_map": teacher_concept_segment_map,#系統整理的概念範圍
            "ai_segment_map": teacher_segment_map, #先前建立的自動對齊結果
            "subtitle_range": subtitle_range or (task.get("subtitle_range") or {}),
        }}
    )

    return jsonify({"ok": True, "message": "章節儲存成功，可再次進行概念對齊！", "warnings": chapter_warnings, "overlap_warning": "time_overlap" in chapter_warnings, "effective_segments_count": len(effective_segments)})


# ========================
# [新增] POST /fixed_task/align_subtitle  手動觸發固定題字幕對齊
# 直接從 prompt_source.subtitle_preview 或 ai_segments_compact 取字幕，不需要找檔案路徑
# ========================
@parsons_bp.post("/fixed_task/align_subtitle")
def align_fixed_task_subtitle():
    if not _video_review_enabled():
        return _video_review_disabled_response()

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
        q = {
            "$and": [
                # 允許 fixed, openai, fallback
                {"$or": [{"gen_source": "fixed"}, {"source_type": "fixed"}, {"gen_source": "openai"}, {"gen_source": "fallback"}]},
                ({"$or": [{"video_id": vid_oid}, {"video_id_str": video_id}]} if vid_oid else {"video_id_str": video_id}),
            ]
        }
        task = db.parsons_tasks.find_one(q)

    if not task:
        return jsonify({"ok": False, "message": "找不到題目（需 gen_source 可為 fixed/openai/fallback）"}), 404

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

    # ② 如果還是空的，嘗試從影片欄位/字幕檔路徑抓取（相容手動匯入 fixed 題）
    if not subtitle_raw:
        raw_vid = task.get("video_id")
        vid_oid = raw_vid if isinstance(raw_vid, ObjectId) else maybe_oid(str(raw_vid or ""))
        vid_str = task.get("video_id_str") or str(raw_vid or "")

        video_doc = {}
        try:
            if vid_oid:
                video_doc = db.videos.find_one({"_id": vid_oid}) or {}
            elif vid_str:
                video_doc = db.videos.find_one({"_id": maybe_oid(vid_str)}) or {}
        except Exception:
            video_doc = {}

        subtitle_raw = (
            str(video_doc.get("subtitle_preview") or "")
            or str(video_doc.get("subtitle_text") or "")
            or ""
        ).strip()

        if not subtitle_raw:
            subtitle_path = (
                str(prompt_source.get("subtitle_path") or "").strip()
                or str(task.get("subtitle_path") or "").strip()
                or pick_latest_subtitle_path(video_doc or {}, vid_str)
            )
            if subtitle_path:
                subtitle_raw = (read_subtitle_text(subtitle_path) or "").strip()

    if not subtitle_raw:
        return jsonify({
            "ok": False,
            "message": "找不到字幕內容，請確認此固定題的影片有字幕資料",
            "task_id": real_task_id,
            "prompt_source_keys": list(prompt_source.keys()),
        }), 404

    # ③ 解析字幕成時間段
    if "-->" in subtitle_raw:
        segs = parse_srt_segments(subtitle_raw)
        segs_compact = compact_segments_for_prompt(segs, max_chars=12000)
    else:
        segs_compact = subtitle_raw[:12000]
        segs = []

    if not segs_compact:
        return jsonify({"ok": False, "message": "字幕解析結果為空"}), 400

    solution_blocks = task.get("solution_blocks") or []
    if not solution_blocks:
        return jsonify({"ok": False, "message": "此固定題沒有 solution_blocks"}), 400

    # 若輸入是 compact 文字，轉成可檢索的 segment 列表
    if not segs and segs_compact:
        compact_segs = []
        for ln in str(segs_compact or "").splitlines():
            m = _re.match(r"\s*\[(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\]\s*(.*)$", ln.strip())
            if not m:
                continue
            try:
                ss = float(m.group(1))
                ee = float(m.group(2))
            except Exception:
                continue
            if ee <= ss:
                continue
            compact_segs.append({"start": ss, "end": ee, "text": (m.group(3) or "").strip()})
        segs = compact_segs

    if not segs:
        return jsonify({"ok": False, "message": "字幕解析結果為空（無法建立 IR index）"}), 400

    # =========================
    # 新增：更細的 block role / concept
    # =========================
    def _line_role_for_block(text: str) -> str:
        t = str(text or "").strip().lower()
        if not t:
            return "any"

        if t.startswith("def "):
            return "function_def"
        if t.startswith(("if ", "elif ", "else")):
            return "branch_condition"
        if "input(" in t or "eval(input(" in t:
            return "input_read"
        if t.startswith("print("):
            return "output_print"
        if t.startswith("return "):
            return "branch_return"
        if ("operator" in t and ("==" in t or "!=" in t)):
            return "branch_condition"
        if _re.search(r"\b[a-z_]\w*\s*\(", t) and not t.startswith(("print(", "input(")):
            return "function_call"
        return "any"

    def _concept_for_block(text: str, block_sem: str = "") -> str:
        q = (str(text or "") + " " + str(block_sem or "")).strip()
        if not q:
            return "python_syntax"
        return infer_concept_tag_from_text(q)

    def _concept_terms(concept: str) -> list:
        m = {
            "python_syntax": ["函式", "定義", "def", "return", "語法", "syntax"],
            "input_int_cast": ["輸入", "讀入", "讀取", "input", "eval", "整數", "cast"],
            "if_condition_logic": ["判斷", "如果", "否則", "if", "條件", "是不是"],
            "if_branch_order": ["elif", "else if", "分支", "順序"],
            "edge_case_condition": ["邊界", "特殊", "例外", "空值", "沒有", "最後一筆"],
            "loop_count_control": ["for", "while", "range", "迴圈", "次數"],
            "loop_reverse_range": ["reverse", "倒著", "反向", "遞減"],
            "nested_loop_structure": ["巢狀", "雙層", "多層", "nested"],
            "star_formula_2i_minus_1": ["2i-1", "星號", "奇數列"],
            "space_formula_n_minus_i": ["n-i", "空白", "space"],
            "print_separator": ["輸出", "印出", "顯示", "答案", "結果", "print", "sep", "end"],
        }
        return m.get(str(concept or ""), [])

    def _role_score(text: str, role: str) -> int:
        low = str(text or "").lower()
        if not low:
            return 0

        score = 0
        if role == "function_def":
            for kw in ["函式", "定義", "def", "參數"]:
                if kw in low:
                    score += 2

        elif role == "branch_condition":
            for kw in ["判斷", "如果", "if", "elif", "符號", "是不是"]:
                if kw in low:
                    score += 2
            for bad in ["回傳", "輸出", "print", "答案"]:
                if bad in low:
                    score -= 1

        elif role == "branch_return":
            for kw in ["回傳", "傳回", "return", "結果"]:
                if kw in low:
                    score += 2
            for bad in ["輸入", "讀入", "運算符號"]:
                if bad in low:
                    score -= 1

        elif role == "input_read":
            for kw in ["輸入", "讀入", "讀取", "存進", "第一個數字", "第二個數字", "運算符號"]:
                if kw in low:
                    score += 2
            for bad in ["輸出", "答案", "結果"]:
                if bad in low:
                    score -= 1

        elif role == "output_print":
            for kw in ["輸出", "印出", "顯示", "答案", "結果", "print"]:
                if kw in low:
                    score += 2
            for bad in ["輸入", "讀入"]:
                if bad in low:
                    score -= 1

        elif role == "function_call":
            for kw in ["呼叫", "使用", "傳入", "執行", "calc"]:
                if kw in low:
                    score += 2

        return score

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

    def _is_intro_instruction_text(text: str) -> bool:
        low = str(text or "").strip().lower()
        if not low:
            return False
        intro_terms = [
            "請設計", "題目", "這一題", "請你", "我們要來", "這裡", "示範", "接著", "再來", "同步編輯器",
            "請輸入", "請完成", "根據題目", "根據以下", "請先", "請直接", "今天我們", "現在我們",
        ]
        return any(term in low for term in intro_terms)

    def _looks_like_problem_statement(text: str, concept: str = "", role: str = "") -> bool:
        low = str(text or "").strip().lower()
        if not low:
            return False

        if not _is_intro_instruction_text(low):
            return False

        code_signals = ["=", "input(", "print(", "range(", "return", "if ", "elif ", "else:", "+", "-", "*", "/"]
        if any(sig in low for sig in code_signals):
            return False

        concept_terms = _concept_terms(concept)
        role_terms = []
        if role == "input_read":
            role_terms = ["輸入", "input", "讀入", "讀取"]
        elif role == "output_print":
            role_terms = ["輸出", "印出", "print", "顯示"]
        elif role == "branch_condition":
            role_terms = ["判斷", "如果", "條件", "if", "elif"]
        elif role == "branch_return":
            role_terms = ["回傳", "return", "結果"]
        elif role == "function_def":
            role_terms = ["函式", "定義", "def", "參數"]

        has_concept_signal = any(str(term).lower() in low for term in concept_terms + role_terms if str(term or "").strip())
        return not has_concept_signal

    def _function_def_expand_window(seg: dict, all_segs: list) -> dict:
        if not isinstance(seg, dict):
            return seg

        ss = float(seg.get("start", 0.0))
        ee = float(seg.get("end", 0.0))
        if ee <= ss:
            return seg

        idx = -1
        for j, s in enumerate(all_segs or []):
            try:
                js = float((s or {}).get("start", 0.0))
                je = float((s or {}).get("end", 0.0))
            except Exception:
                continue
            if abs(js - ss) <= 0.25 and abs(je - ee) <= 0.25:
                idx = j
                break
        if idx < 0:
            return seg

        keep_terms = ["函式", "定義", "參數", "x", "y", "op", "運算符號"]
        stop_terms = ["輸入", "讀取", "第一個數字", "第二個數字", "回傳", "輸出", "答案", "print"]

        start = ss
        end = ee
        pieces = [str(seg.get("text") or "").strip()]

        for k in range(idx + 1, len(all_segs or [])):
            nxt = all_segs[k] or {}
            try:
                ns = float(nxt.get("start", 0.0))
                ne = float(nxt.get("end", 0.0))
            except Exception:
                continue
            if ne <= ns:
                continue
            txt = str(nxt.get("text") or "").strip()
            low = txt.lower()
            if any(term in low for term in stop_terms):
                break
            if any(term in low for term in keep_terms):
                end = ne
                pieces.append(txt)
                continue
            # 允許一小句過渡，但不要無限擴張
            if (ns - end) <= 0.8 and len(pieces) <= 3:
                end = ne
                pieces.append(txt)
                continue
            break

        return {
            "start": float(start),
            "end": float(end),
            "text": " ".join([p for p in pieces if p]).strip() or str(seg.get("text") or ""),
        }

    def _term_hit_score(text: str, terms: list) -> int:
        low = str(text or "").lower()
        if not low:
            return 0
        score = 0
        for term in (terms or []):
            if str(term).lower() in low:
                score += 1
        return score

    def _find_rule_segment_for_block(block_text: str, block_sem: str):
        query_text = (str(block_text or "") + " " + str(block_sem or "")).strip()
        concept = _concept_for_block(block_text, block_sem)
        terms = _concept_terms(concept)
        role = _line_role_for_block(block_text)

        if not terms and role == "any":
            return None

        best = None
        best_key = None

        for seg in segs:
            st = float(seg.get("start", 0.0))
            ed = float(seg.get("end", 0.0))
            if ed <= st:
                continue

            txt = str(seg.get("text") or "")
            if _looks_like_problem_statement(txt, concept, role):
                continue
            term_score = _term_hit_score(txt, terms)
            role_score = _role_score(txt, role)

            # 至少要有 concept 或 role 的其中一種證據
            if term_score <= 0 and role_score <= 0:
                continue

            # 越強越好，若同分則取較早出現的
            key = (-int(term_score), -int(role_score), float(st))
            if best is None or key < best_key:
                best = {
                    "start": st,
                    "end": ed,
                    "text": txt,
                    "concept": concept,
                    "role": role,
                    "term_score": int(term_score),
                    "role_score": int(role_score),
                }
                best_key = key

        return best
    

    def _structural_query(text: str) -> str:
        """
        Normalize variable names/literals so fixed tasks still align when symbols change.
        """
        t = str(text or "")
        if not t:
            return ""

        keep = {
            "if", "elif", "else", "return", "print", "input",
            "int", "float", "str", "for", "while", "in", "range",
            "def", "and", "or", "not", "eval"
        }

        def _id_repl(m):
            tok = str(m.group(0) or "")
            low = tok.lower()
            return tok if low in keep else "VAR"

        t = _re.sub(r"\b\d+(?:\.\d+)?\b", " NUM ", t)
        t = _re.sub(r"\b[A-Za-z_][A-Za-z0-9_]*\b", _id_repl, t)
        return " ".join(t.split())


    def _anchor_positive_terms(concept: str, role: str, block_text: str = "", block_sem: str = "") -> list:
        concept_terms = list(_concept_terms(concept) or [])
        role_terms = []
        if role == "input_read":
            role_terms = ["輸入", "讀入", "讀取", "input", "eval", "姓名", "名字"]
        elif role == "output_print":
            role_terms = ["輸出", "印出", "顯示", "結果", "print", "你好", "hello"]
        elif role == "branch_condition":
            role_terms = ["判斷", "如果", "條件", "if", "elif", "符號", "是否"]
        elif role == "branch_return":
            role_terms = ["回傳", "傳回", "return", "結果"]
        elif role == "function_def":
            role_terms = ["函式", "定義", "def", "參數"]
        extra = []
        low = (str(block_text or "") + " " + str(block_sem or "")).lower()
        if "hello" in low or "你好" in low:
            extra.extend(["hello", "你好", "打招呼"])
        if "name" in low or "姓名" in low or "名字" in low:
            extra.extend(["姓名", "名字", "name"])
        seen = set()
        out = []
        for term in concept_terms + role_terms + extra:
            t = str(term or "").strip().lower()
            if not t or t in seen:
                continue
            seen.add(t)
            out.append(t)
        return out

    def _anchor_negative_terms(concept: str, role: str) -> list:
        common = ["題目", "我們來看", "這一題", "接下來", "再來", "示範", "小題", "說明"]
        if normalize_concept_name(concept) == "input_int_cast" or role == "input_read":
            return common + ["輸出", "印出", "結果", "print"]
        if normalize_concept_name(concept) == "print_separator" or role == "output_print":
            return common + ["輸入", "讀入", "讀取", "input", "eval"]
        if role == "function_def":
            return common + ["輸入第一個", "讀取", "印出"]
        return common

    def _select_anchor_candidate(candidates, concept: str, role: str, block_text: str = "", block_sem: str = ""):
        if not candidates:
            return None

        pos_terms = _anchor_positive_terms(concept, role, block_text, block_sem)
        neg_terms = _anchor_negative_terms(concept, role)
        best = None
        best_adj = -1e9

        for cand in (candidates or []):
            if not isinstance(cand, dict):
                continue
            cs = float(cand.get("start", 0.0))
            ce = float(cand.get("end", 0.0))
            if ce <= cs:
                continue
            txt = str(cand.get("text") or "")
            low = txt.lower()

            if _looks_like_problem_statement(txt, concept, role):
                # 題目說明不是有效錨點；只有在沒有其他候選時才可能保留。
                continue

            base = float(cand.get("score") or 0.0)
            pos_score = 0.10 * sum(1 for t in pos_terms if t and t in low)
            neg_score = 0.14 * sum(1 for t in neg_terms if t and t.lower() in low)
            concept_bonus = 0.06 * _term_hit_score(txt, _concept_terms(concept))
            role_bonus = 0.04 * max(0, _role_score(txt, role))
            intro_penalty = 0.28 if _is_intro_instruction_text(txt) else 0.0

            adj = base + pos_score + concept_bonus + role_bonus - neg_score - intro_penalty
            if best is None or adj > best_adj:
                best = {
                    "start": cs,
                    "end": ce,
                    "text": txt,
                    "score": float(cand.get("score") or 0.0),
                    "source": str(cand.get("source") or "anchor"),
                    "anchor_adj": float(adj),
                }
                best_adj = adj

        return best

    def _build_anchor_window(anchor: dict, all_segs: list, concept: str, role: str):
        if not isinstance(anchor, dict):
            return None

        try:
            ss = float(anchor.get("start", 0.0))
            ee = float(anchor.get("end", 0.0))
        except Exception:
            return None
        if ee <= ss:
            return None

        idx = -1
        for j, seg in enumerate(all_segs or []):
            try:
                s0 = float((seg or {}).get("start", 0.0))
                e0 = float((seg or {}).get("end", 0.0))
            except Exception:
                continue
            if abs(s0 - ss) <= 0.35 and abs(e0 - ee) <= 0.6:
                idx = j
                break

        if idx < 0:
            return {
                "start": ss,
                "end": ee,
                "text": str(anchor.get("text") or "").strip(),
                "source": "anchor_exact",
            }

        # 若錨點本身只是題目說明，向右找第一個真正的教學/程式段。
        anchor_txt = str(anchor.get("text") or "").strip()
        if _looks_like_problem_statement(anchor_txt, concept, role):
            for k in range(idx + 1, len(all_segs or [])):
                seg = all_segs[k] or {}
                txt = str(seg.get("text") or "").strip()
                if not _looks_like_problem_statement(txt, concept, role):
                    try:
                        ns = float(seg.get("start", 0.0))
                        ne = float(seg.get("end", 0.0))
                    except Exception:
                        continue
                    if ne > ns:
                        return {
                            "start": float(ns),
                            "end": float(ne),
                            "text": txt,
                            "source": "anchor_shifted_from_intro",
                        }
            return {
                "start": ss,
                "end": ee,
                "text": anchor_txt,
                "source": "anchor_intro_only",
            }

        if normalize_concept_name(concept) in {"input_int_cast", "print_separator"} or role in {"input_read", "output_print"}:
            expand_before = 0
            expand_after = 1
        elif role in {"branch_condition", "branch_return"}:
            expand_before = 1
            expand_after = 1
        else:
            expand_before = 1
            expand_after = 2

        stop_terms = ["接下來", "再來", "題目", "我們來看", "示範", "下一題"]
        start = ss
        end = ee
        pieces = []

        if anchor_txt and not _looks_like_problem_statement(anchor_txt, concept, role):
            pieces.append(anchor_txt)

        for k in range(idx + 1, min(len(all_segs or []), idx + 1 + expand_after)):
            seg = all_segs[k] or {}
            txt = str(seg.get("text") or "").strip()
            low = txt.lower()
            if any(term.lower() in low for term in stop_terms):
                break
            if _looks_like_problem_statement(txt, concept, role):
                continue
            try:
                ns = float(seg.get("start", 0.0))
                ne = float(seg.get("end", 0.0))
            except Exception:
                continue
            if ne <= ns:
                continue
            if normalize_concept_name(concept) == "input_int_cast" and any(bad in low for bad in ["輸出", "print", "結果"]):
                break
            if normalize_concept_name(concept) == "print_separator" and any(bad in low for bad in ["輸入", "讀入", "input", "eval"]):
                break
            end = ne
            pieces.append(txt)

        for k in range(idx - 1, max(-1, idx - 1 - expand_before), -1):
            seg = all_segs[k] or {}
            txt = str(seg.get("text") or "").strip()
            low = txt.lower()
            if any(term.lower() in low for term in stop_terms):
                break
            if _looks_like_problem_statement(txt, concept, role):
                continue
            try:
                ns = float(seg.get("start", 0.0))
                ne = float(seg.get("end", 0.0))
            except Exception:
                continue
            if ne <= ns:
                continue
            if normalize_concept_name(concept) == "input_int_cast" and any(bad in low for bad in ["輸出", "print", "結果"]):
                break
            if normalize_concept_name(concept) == "print_separator" and any(bad in low for bad in ["輸入", "讀入", "input", "eval"]):
                break
            start = ns
            pieces.insert(0, txt)

        if end <= start:
            end = start + 1.0

        return {
            "start": round(max(0.0, start), 2),
            "end": round(max(start + 0.8, end), 2),
            "text": " ".join([p for p in pieces if p]).strip(),
            "source": "anchor_window",
        }

    subtitle_index = build_subtitle_index(segs)
    retrieval_mode = str((subtitle_index or {}).get("mode") or get_retrieval_mode())

    ai_segment_map = {}
    prev_end = 0.0
    used_range_counts = {}

    slot_concept_map = {}
    concept_segment_map = {}
    
    # 優先從概念章節對齊結果中取出每個 slot 對應的章節區間
    block_chapter_map = task.get("block_chapter_map") or {}
    # 有些舊資料可能存在 tasks 內的不同欄位
    if not block_chapter_map and "concept_chapters" in task:
        # 如果曾經跑過概念對齊但沒寫好 block_chapter_map，嘗試重建或取用
        pass

    for i, block in enumerate(solution_blocks):
        q_text = str((block or {}).get("text") or "").strip()
        q_sem = str((block or {}).get("semantic_zh") or "").strip()

        concept = _concept_for_block(q_text, q_sem)
        role = _line_role_for_block(q_text)
        slot_concept_map[str(i)] = normalize_concept_name(concept)
        
        # [核心改動] 取得該 slot 所屬的 concept chapter 範圍限制
        chapter_limit = None
        slot_str = str(i)
        if slot_str in block_chapter_map:
            ch_data = block_chapter_map[slot_str]
            if "start" in ch_data and "end" in ch_data:
                chapter_limit = {"start": float(ch_data["start"]), "end": float(ch_data["end"])}

        query = (q_text + " " + q_sem + " " + _structural_query(q_text)).strip()

        best, score = retrieve_best_segment(query, subtitle_index)
        top_hits = retrieve_top_k_segments(query, subtitle_index, k=6)
        rule_best = _find_rule_segment_for_block(q_text, q_sem)

        candidates = []

        if isinstance(best, dict):
            candidates.append({
                "start": float(best.get("start", 0.0)),
                "end": float(best.get("end", 0.0)),
                "text": str(best.get("text") or ""),
                "score": float(score or 0.0),
                "source": "ir_top1",
            })

        for hit in (top_hits or []):
            if not isinstance(hit, dict):
                continue
            hs = float(hit.get("start", 0.0))
            he = float(hit.get("end", 0.0))
            if he <= hs:
                continue
            candidates.append({
                "start": hs,
                "end": he,
                "text": str(hit.get("text") or ""),
                "score": float(hit.get("score") or 0.0),
                "source": "ir_topk",
            })

        if rule_best:
            candidates.append({
                "start": float(rule_best.get("start", 0.0)),
                "end": float(rule_best.get("end", 0.0)),
                "text": str(rule_best.get("text") or ""),
                "score": 0.80,  # rule 命中時給較高基礎分
                "source": "rule",
            })

        chosen = None
        chosen_adj = -1e9
        filtered_candidates = []

        for cand in candidates:
            cs = float(cand.get("start", 0.0))
            ce = float(cand.get("end", 0.0))
            if ce <= cs:
                continue

            # [核心改動] 過濾不在 concept chapter 內的候選片段
            # 容許一點點誤差（例如前後 -5 ~ +5 秒，避免擷取太過剛好）
            if chapter_limit:
                c_start = chapter_limit["start"] - 5.0
                c_end = chapter_limit["end"] + 5.0
                # 如果候選片段完全在 chapter 範圍之外，就過濾掉
                if ce < c_start or cs > c_end:
                    continue

            key = (round(cs, 1), round(ce, 1))
            txt = str(cand.get("text") or "")
            base = float(cand.get("score") or 0.0)

            # concept / role 證據
            concept_bonus = 0.08 * _term_hit_score(txt, _concept_terms(concept))
            role_bonus = 0.06 * max(0, _role_score(txt, role))

            # function_def 不要吃到題目前導說明句。
            intro_penalty = 0.0
            if role == "function_def" and _is_intro_instruction_text(txt):
                intro_penalty = 0.45

            # 避免同一段一直被重用
            count = used_range_counts.get(key, 0)
            if count >= 2:
                dup_penalty = 1.0  # 強烈懲罰，幾乎等於排斥
            elif count == 1:
                dup_penalty = 0.35
            else:
                dup_penalty = 0.0

            # 往後推進，但只給小幅獎勵，避免弱匹配被硬推走
            forward_bonus = 0.0
            backward_penalty = 0.0

            if cs >= (prev_end - 0.5):
                forward_bonus = 0.04
            elif cs + 0.15 < prev_end:
                backward_penalty = min(0.20, (prev_end - cs) / 40.0)

            adj = base + concept_bonus + role_bonus + forward_bonus - dup_penalty - backward_penalty - intro_penalty
            cand2 = dict(cand)
            cand2["adj_score"] = float(adj)
            filtered_candidates.append(cand2)

            if chosen is None or adj > chosen_adj:
                chosen = cand2
                chosen_adj = adj

        anchor = _select_anchor_candidate(filtered_candidates, concept, role, q_text, q_sem)
        anchor_window = _build_anchor_window(anchor, segs, concept, role)

        if anchor_window is not None:
            best = {
                "start": float(anchor_window.get("start", 0.0)),
                "end": float(anchor_window.get("end", 0.0)),
                "text": str(anchor_window.get("text") or ""),
            }
            if role == "function_def":
                best = _function_def_expand_window(best, segs)
            # 沿用 anchor 原本候選分數，避免破壞原有 strong_evidence 判準
            score = float((anchor or {}).get("score") or 0.0)
            chosen_source = str((anchor or {}).get("source") or "anchor_window")
        elif chosen is not None:
            best = {
                "start": float(chosen.get("start", 0.0)),
                "end": float(chosen.get("end", 0.0)),
                "text": str(chosen.get("text") or ""),
            }
            if role == "function_def":
                best = _function_def_expand_window(best, segs)
            score = float(chosen.get("score") or 0.0)
            chosen_source = str(chosen.get("source") or "candidate")
        else:
            chosen_source = "none"

        if best is not None and float(best.get("end", 0.0)) > float(best.get("start", 0.0)):
            rng_key = (
                round(float(best.get("start", 0.0)), 1),
                round(float(best.get("end", 0.0)), 1),
            )
            used_range_counts[rng_key] = used_range_counts.get(rng_key, 0) + 1

            # 只有在「有足夠證據」時才更新 prev_end，避免 unknown / 弱匹配整串往後漂
            txt = str(best.get("text") or "")
            strong_evidence = (
                score >= 0.55
                or _term_hit_score(txt, _concept_terms(concept)) >= 1
                or _role_score(txt, role) >= 2
            )
            if strong_evidence:
                prev_end = max(prev_end, float(best.get("end", 0.0)))

            ai_segment_map[str(i)] = {
                "start": float(best.get("start", 0.0)),
                "end": float(best.get("end", 0.0)),
                "score": float(score),
                "evidence": str(best.get("text") or ""),
                "source": chosen_source,
                "concept": normalize_concept_name(concept),
                "concept_tag": normalize_concept_name(concept),
                "surface_tag": normalize_surface_tag(infer_surface_tag_from_text(q_text + " " + q_sem)),
            }

            # concept_segment_map 聚合
            s = float(best.get("start", 0.0))
            e = float(best.get("end", 0.0))
            if e > s:
                concept_key = normalize_concept_name(concept)
                cur = concept_segment_map.get(concept_key)
                if not cur:
                    concept_segment_map[concept_key] = {
                        "start": s,
                        "end": e,
                        "concept": concept_key,
                        "concept_tag": concept_key,
                        "concept_label": concept_tag_to_label(concept_key),
                    }
                else:
                    concept_segment_map[concept_key]["start"] = min(float(cur.get("start", s)), s)
                    concept_segment_map[concept_key]["end"] = max(float(cur.get("end", e)), e)

        else:
            ai_segment_map[str(i)] = {
                "start": 0.0,
                "end": 0.0,
                "score": -1.0,  # [修改] 將找不到對齊的 slot 的分數標記為負分作為 fallback/錯誤訊號
                "evidence": "fallback",
                "source": "fallback"
            }

    # 修正固定題中 condition -> return 成對結構的漂移問題。
    ai_segment_map = _repair_fixed_task_branch_pairs(
        solution_blocks=solution_blocks,
        segs=segs,
        ai_segment_map=ai_segment_map,
        slot_concept_map=slot_concept_map,
    )

    update = {
        "ai_segment_map": ai_segment_map,
        "ai_segments_compact": compact_segments_for_prompt(segs, max_chars=12000),
        "subtitle_ir_cache": subtitle_index,
        "subtitle_ir_cache_updated_at": now_utc(),
        "slot_concept_map": slot_concept_map,
        "concept_segment_map": concept_segment_map,
    }

    valid_ranges = [
        (float(v.get("start", 0.0)), float(v.get("end", 0.0)))
        for v in ai_segment_map.values()
        if isinstance(v, dict) and float(v.get("end", 0.0)) > float(v.get("start", 0.0))
    ]
    if valid_ranges:
        update["subtitle_range"] = {
            "start": min(x[0] for x in valid_ranges),
            "end": max(x[1] for x in valid_ranges),
        }

    db.parsons_tasks.update_one({"_id": task["_id"]}, {"$set": update})

    return jsonify({
        "ok": True,
        "task_id": real_task_id,
        "retrieval_mode": retrieval_mode,
        "subtitle_chars": len(update.get("ai_segments_compact", "")),
        "slots_aligned": list(ai_segment_map.keys()),
        "ai_segment_map": ai_segment_map,
        "slot_concept_map": slot_concept_map,
        "concept_segment_map": concept_segment_map,
        "message": "IR 對齊完成，共 %d 個 slot" % len(ai_segment_map),
    })



# ========================
# [新增] POST /fixed_task/align_concept  概念章節對齊（YouTube 章節風格）
# 適用固定題與 AI 題，準確度優於逐行精準對齊
# ========================
@parsons_bp.post("/fixed_task/align_concept")
def align_fixed_task_concept():
    if not _video_review_enabled():
        return _video_review_disabled_response()

    data = request.get_json(silent=True) or {}
    return align_task_by_concept(data)


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
        q = {
            "$and": [
                # 允許 fixed, openai, fallback
                {"$or": [{"gen_source": "fixed"}, {"source_type": "fixed"}, {"gen_source": "openai"}, {"gen_source": "fallback"}]},
                ({"$or": [{"video_id": vid_oid}, {"video_id_str": video_id}]} if vid_oid else {"video_id_str": video_id}),
            ]
        }
        task = db.parsons_tasks.find_one(q)
    if not task:
        return jsonify({"ok": False, "message": "找不到題目（需 gen_source 可為 fixed/openai/fallback）"})

    def _parse_segments_from_task_doc(task_doc: dict, max_segments: int = 5000) -> list:
        # 優先讀完整字幕來源（與 TeacherSubtitles 同步），避免只用 compact 造成段落缺漏。
        raw = str(_read_subtitle_text_for_task(task_doc) or "").strip()
        if not raw:
            prompt_source_doc = task_doc.get("prompt_source") or {}
            subtitle_raw_text = (
                prompt_source_doc.get("subtitle_preview")
                or prompt_source_doc.get("subtitle_text")
                or task_doc.get("subtitle_text_used")
                or task_doc.get("ai_segments_compact")
                or ""
            )
            raw = str(subtitle_raw_text or "").strip()
        if not raw:
            return []

        out = []
        if "-->" in raw:
            segs = parse_srt_segments(raw)
            for i, seg in enumerate(segs[:max_segments]):
                try:
                    s = float(seg.get("start", 0.0))
                    e = float(seg.get("end", 0.0))
                except Exception:
                    continue
                if e <= s:
                    continue
                out.append({
                    "idx": i,
                    "id": seg.get("id", i + 1),
                    "start": s,
                    "end": e,
                    "text": str(seg.get("text") or "").strip(),
                })
            return out

        # compact format: [12.3-15.4] some text
        for i, ln in enumerate(raw.splitlines()):
            m = _re.match(r"\s*\[(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\]\s*(.*)$", str(ln or "").strip())
            if not m:
                continue
            try:
                s = float(m.group(1))
                e = float(m.group(2))
            except Exception:
                continue
            if e <= s:
                continue
            out.append({
                "idx": len(out),
                "id": len(out) + 1,
                "start": s,
                "end": e,
                "text": str(m.group(3) or "").strip(),
            })
            if len(out) >= max_segments:
                break
        return out

    prompt_source = task.get("prompt_source") or {}
    subtitle_segments = _parse_segments_from_task_doc(task)

    latest_attempt_debug = {}
    try:
        latest_attempt = db.parsons_attempts.find_one(
            {"task_id": str(task.get("_id"))},
            sort=[("created_at", -1)],
            projection={
                "_id": 1,
                "created_at": 1,
                "wrong_index": 1,
                "segment_source": 1,
                "jump_start": 1,
                "jump_end": 1,
                "alignment_trace": 1,
                "alignment_debug": 1,
            },
        )
        if latest_attempt:
            trace = latest_attempt.get("alignment_trace") or []
            merged_step = None
            if isinstance(trace, list):
                for st in trace:
                    if not isinstance(st, dict):
                        continue
                    if str(st.get("step") or "") == "adjacent_same_concept_merged":
                        merged_step = st
                        break

            latest_attempt_debug = {
                "attempt_id": str(latest_attempt.get("_id")),
                "created_at": latest_attempt.get("created_at"),
                "wrong_index": latest_attempt.get("wrong_index"),
                "segment_source": str(latest_attempt.get("segment_source") or ""),
                "jump_start": latest_attempt.get("jump_start"),
                "jump_end": latest_attempt.get("jump_end"),
                "merged_adjacent_same_concept": bool(merged_step is not None),
                "merged_meta": (merged_step or {}).get("meta") if isinstance(merged_step, dict) else {},
                "trace_tail": trace[-6:] if isinstance(trace, list) else [],
                "alignment_debug": latest_attempt.get("alignment_debug") or {},
            }
    except Exception:
        latest_attempt_debug = {}

    derived_slot_concept_map = _derive_slot_concept_map(task)

    def _has_slot_entry(seg_map: dict, idx: int) -> bool:
        if not isinstance(seg_map, dict):
            return False
        for k in [str(idx), f"s{idx + 1}", f"第{idx + 1}格"]:
            seg = seg_map.get(k)
            if not isinstance(seg, dict):
                continue
            s = seg.get("start", seg.get("start_ts"))
            e = seg.get("end", seg.get("end_ts"))
            if s is None or e is None:
                continue
            try:
                sf = float(s)
                ef = float(e)
            except Exception:
                continue
            if ef > sf:
                return True
        return False

    solution_blocks = task.get("solution_blocks") or []
    ai_seg_map = task.get("ai_segment_map") or {}
    missing_concept_slots = [i for i in range(len(solution_blocks)) if not str(derived_slot_concept_map.get(str(i)) or "").strip()]
    missing_ai_segment_slots = [i for i in range(len(solution_blocks)) if not _has_slot_entry(ai_seg_map, i)]
    subtitle_health = _build_subtitle_health_report(task)

    return jsonify({
        "ok": True,
        "task_id": str(task["_id"]),
        "prompt_source_keys": list(prompt_source.keys()),
        "has_subtitle_preview": bool(prompt_source.get("subtitle_preview")),
        "subtitle_preview_len": len(prompt_source.get("subtitle_preview") or ""),
        "ai_segments_compact_len": len(task.get("ai_segments_compact") or ""),
        "subtitle_range": task.get("subtitle_range") or {},
        "slot_concept_map": derived_slot_concept_map,
        "ai_slot_hints_concept": task.get("ai_slot_hints_concept") or {},
        "concept_segment_map": task.get("concept_segment_map") or {},
        "ai_segment_map": task.get("ai_segment_map") or {},
        "teacher_segment_map": task.get("teacher_segment_map") or {},
        "teacher_concept_segment_map": task.get("teacher_concept_segment_map") or {},
        "concept_chapters_draft": task.get("concept_chapters_draft") or [],
        "concept_chapters_formal": task.get("concept_chapters_formal") or task.get("teacher_concept_chapters") or task.get("concept_chapters_draft") or [],
        "teacher_concept_chapters": task.get("teacher_concept_chapters") or [],
        "subtitle_segments": subtitle_segments,
        "subtitle_segments_count": len(subtitle_segments),
        "subtitle_health": subtitle_health,
        "solution_blocks": [b.get("text") for b in solution_blocks],
        "slot_key_alignment": {
            "solution_slots": len(solution_blocks),
            "derived_slot_concept_keys": sorted([str(k) for k in derived_slot_concept_map.keys()]),
            "ai_segment_keys": sorted([str(k) for k in (ai_seg_map.keys() if isinstance(ai_seg_map, dict) else [])]),
            "missing_concept_slots": missing_concept_slots,
            "missing_ai_segment_slots": missing_ai_segment_slots,
        },
        "latest_attempt_debug": latest_attempt_debug,
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
        "hide_semantic_zh": bool(parsed.get("hide_semantic_zh", False)),
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

    ir_cache_status = "skipped"
    ir_cache_mode = ""
    try:
        ir_cache = _build_or_get_task_subtitle_ir_cache(task, force_rebuild=False)
        if ir_cache and (ir_cache.get("segments") or []):
            ir_cache_status = "ready"
            ir_cache_mode = str(ir_cache.get("mode") or "")
        else:
            ir_cache_status = "missing"
    except Exception:
        ir_cache_status = "error"

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

    return jsonify({
        "ok": True,
        "matched": r.matched_count,
        "modified": r.modified_count,
        "subtitle_ir_cache_status": ir_cache_status,
        "subtitle_ir_cache_mode": ir_cache_mode,
    }), 200


# =========================
# AI: build hint + jump segment for wrong slot
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


def _active_test_question_query():
    return {
        "$and": [
            {"question_type": {"$in": ["parsons", None]}},
            {"is_active": {"$ne": False}},
        ]
    }


def _find_test_question_by_ref(test_role: str, task_ref: str):
    collection_name = _test_question_collection_name(test_role)
    ref = str(task_ref or "").strip()
    if not collection_name or not ref:
        return None

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
            task = _parsons_question_to_task_doc(question_doc)
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


def _read_subtitle_text_for_task(task_doc: dict) -> str:
    """Best-effort subtitle text loader for IR retrieval."""
    prompt_source = task_doc.get("prompt_source") or {}

    raw = (
        str(prompt_source.get("subtitle_preview") or "")
        or str(prompt_source.get("subtitle_text") or "")
        or str((task_doc.get("source_subtitle") or {}).get("text_used") or "")
        or str(task_doc.get("subtitle_text_used") or "")
    ).strip()
    if raw:
        return raw

    subtitle_path = (
        str(prompt_source.get("subtitle_path") or "").strip()
        or str(task_doc.get("subtitle_path") or "").strip()
    )
    if subtitle_path:
        return str(read_subtitle_text(subtitle_path) or "").strip()

    # AI 題常見只存 video_id，未把完整字幕放進 task；這裡回查影片與字幕檔。
    try:
        raw_vid = task_doc.get("video_id")
        vid_str = str(task_doc.get("video_id_str") or raw_vid or "").strip()
        vid_oid = raw_vid if isinstance(raw_vid, ObjectId) else maybe_oid(vid_str)

        video_doc = {}
        if vid_oid:
            video_doc = db.videos.find_one({"_id": vid_oid}) or {}
        elif vid_str:
            maybe = maybe_oid(vid_str)
            if maybe:
                video_doc = db.videos.find_one({"_id": maybe}) or {}

        raw_video_sub = (
            str(video_doc.get("subtitle_preview") or "")
            or str(video_doc.get("subtitle_text") or "")
        ).strip()
        if raw_video_sub:
            return raw_video_sub

        subtitle_path2 = pick_latest_subtitle_path(video_doc or {}, vid_str)
        if subtitle_path2:
            txt = str(read_subtitle_text(subtitle_path2) or "").strip()
            if txt:
                return txt
    except Exception:
        pass

    compact = str(task_doc.get("ai_segments_compact") or "").strip()
    if compact:
        return compact

    return ""


def _build_or_get_task_subtitle_ir_cache(task_doc: dict, force_rebuild: bool = False):
    """Build subtitle IR cache at publish/align stage, or return existing cache."""
    try:
        cached = task_doc.get("subtitle_ir_cache") or {}
        if (not force_rebuild) and isinstance(cached, dict) and (cached.get("segments") or []):
            return cached

        subtitle_raw = _read_subtitle_text_for_task(task_doc)
        segs = parse_srt_segments(subtitle_raw) if "-->" in subtitle_raw else []
        if not segs and subtitle_raw:
            compact_segs = []
            for ln in str(subtitle_raw or "").splitlines():
                m = _re.match(r"\s*\[(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\]\s*(.*)$", ln.strip())
                if not m:
                    continue
                try:
                    ss = float(m.group(1))
                    ee = float(m.group(2))
                except Exception:
                    continue
                if ee <= ss:
                    continue
                compact_segs.append({"start": ss, "end": ee, "text": (m.group(3) or "").strip()})
            segs = compact_segs
        if not segs:
            return {}

        ir_index = build_subtitle_index(segs)
        if not isinstance(ir_index, dict) or not (ir_index.get("segments") or []):
            return {}

        db.parsons_tasks.update_one(
            {"_id": task_doc.get("_id")},
            {"$set": {
                "subtitle_ir_cache": ir_index,
                "subtitle_ir_cache_updated_at": now_utc(),
            }}
        )
        return ir_index
    except Exception:
        return {}


def _extract_operation_from_code_text(text: str) -> str:
    t = str(text or "").strip().lower()
    if not t:
        return ""
    if "/" in t or "除" in t:
        return "division"
    if "*" in t or "乘" in t:
        return "multiplication"
    if "+" in t or "加" in t:
        return "addition"
    # treat '-' carefully to avoid conflict with negative signs in plain text.
    if " - " in f" {t} " or "減" in t:
        return "subtraction"
    return ""


def _operation_query_boost_terms(op: str) -> str:
    m = {
        "division": "除 除法 除號 除以 / division operator",
        "multiplication": "乘 乘法 乘號 乘以 * multiplication operator",
        "addition": "加 加法 加號 + addition operator",
        "subtraction": "減 減法 減號 - subtraction operator",
    }
    return m.get(str(op or ""), "")


def _operation_filter_keywords(op: str) -> list:
    m = {
        "division": ["除", "除號", "除以", "/", "division"],
        "multiplication": ["乘", "乘號", "乘以", "*", "multiplication"],
        "addition": ["加", "加號", "加上", "+", "addition"],
        "subtraction": ["減", "減號", "減去", "-", "subtraction"],
    }
    return m.get(str(op or ""), [])


def _build_filtered_subtitle_index(subtitle_index: dict, keep_indices: list):
    try:
        idxs = sorted(set(int(i) for i in (keep_indices or []) if int(i) >= 0))
    except Exception:
        idxs = []
    if not idxs:
        return {}

    out = {
        "mode": str((subtitle_index or {}).get("mode") or "local"),
        "embed_model": str((subtitle_index or {}).get("embed_model") or ""),
        "segments": [],
    }

    segs = (subtitle_index or {}).get("segments") or []
    for i in idxs:
        if i < len(segs):
            out["segments"].append(segs[i])

    if not out["segments"]:
        return {}

    if out["mode"] == "openai":
        embs = (subtitle_index or {}).get("embeddings") or []
        out["embeddings"] = [embs[i] for i in idxs if i < len(embs)]
        if not out.get("embeddings"):
            return {}
    else:
        out["idf"] = (subtitle_index or {}).get("idf") or {}
        vecs = (subtitle_index or {}).get("vectors") or []
        out["vectors"] = [vecs[i] for i in idxs if i < len(vecs)]
        if not out.get("vectors"):
            return {}

    return out


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


def _derive_concept_segment_map(task_doc: dict, slot_concept_map: dict = None) -> dict:
    """Build concept->segment mapping from stored map or aggregate ai_segment_map by slot concept."""
    stored = task_doc.get("concept_segment_map") or {}
    if isinstance(stored, dict) and stored:
        return stored

    teacher_stored = task_doc.get("teacher_concept_segment_map") or {}
    if isinstance(teacher_stored, dict) and teacher_stored:
        return teacher_stored

    seg_map = task_doc.get("teacher_segment_map") or task_doc.get("ai_segment_map") or {}
    if not isinstance(seg_map, dict) or not seg_map:
        return {}

    scm = slot_concept_map or _derive_slot_concept_map(task_doc)
    acc = {}
    for k, concept in (scm or {}).items():
        seg = seg_map.get(str(k))
        if not isinstance(seg, dict):
            continue
        s = seg.get("start", seg.get("start_ts"))
        e = seg.get("end", seg.get("end_ts"))
        if s is None or e is None:
            continue
        try:
            sf = float(s)
            ef = float(e)
        except Exception:
            continue
        if ef <= sf:
            continue
        c = normalize_concept_name(concept)
        if not c:
            continue
        cur = acc.get(c)
        if not cur:
            acc[c] = {
                "start": sf,
                "end": ef,
                "concept": c,
                "concept_tag": c,
                "concept_label": concept_tag_to_label(c),
            }
        else:
            acc[c]["start"] = min(float(cur.get("start", sf)), sf)
            acc[c]["end"] = max(float(cur.get("end", ef)), ef)

    return acc


def _get_slot_segment_map(task_doc: dict) -> dict:
    return task_doc.get("teacher_segment_map") or task_doc.get("ai_segment_map") or {}

def _get_slot_segment_from_maps(task_doc: dict, slot_idx: int):
    seg_map = task_doc.get("teacher_segment_map") or task_doc.get("ai_segment_map") or {}
    if not isinstance(seg_map, dict):
        return None

    candidates = [str(slot_idx), f"s{slot_idx + 1}", f"第{slot_idx + 1}格"]
    for k in candidates:
        seg = seg_map.get(k)
        if not isinstance(seg, dict):
            continue

        s = seg.get("start", seg.get("start_ts"))
        e = seg.get("end", seg.get("end_ts"))
        if s is None or e is None:
            continue

        try:
            sf = float(s)
            ef = float(e)
        except Exception:
            continue

        if ef > sf:
            return {"start": sf, "end": ef}

    return None


def _maybe_merge_adjacent_wrong_segments(task_doc: dict, wrong_indices: list, primary_wrong_idx: int,
                                         current_start: float, current_end: float):
    """
    若 primary_wrong_idx 與相鄰錯格屬於同 concept，且時間相連/重疊，則合併回看片段。
    只做 very small post-process，不改既有判錯與主流程。
    """
    if current_start is None or current_end is None or current_end <= current_start:
        return None, None, {}

    if not wrong_indices or primary_wrong_idx is None:
        return None, None, {}

    slot_concept_map = _derive_slot_concept_map(task_doc)
    if not isinstance(slot_concept_map, dict) or not slot_concept_map:
        return None, None, {}

    wrong_sorted = sorted([int(x) for x in wrong_indices if isinstance(x, int)])
    if primary_wrong_idx not in wrong_sorted:
        return None, None, {}

    primary_concept = str(slot_concept_map.get(str(primary_wrong_idx)) or "").strip().lower()
    if not primary_concept:
        return None, None, {}

    group = [primary_wrong_idx]

    # 往左收
    i = primary_wrong_idx - 1
    while i in wrong_sorted:
        c = str(slot_concept_map.get(str(i)) or "").strip().lower()
        if c != primary_concept:
            break
        group.insert(0, i)
        i -= 1

    # 往右收
    i = primary_wrong_idx + 1
    while i in wrong_sorted:
        c = str(slot_concept_map.get(str(i)) or "").strip().lower()
        if c != primary_concept:
            break
        group.append(i)
        i += 1

    if len(group) <= 1:
        return None, None, {}

    ranges = []
    for idx in group:
        seg = _get_slot_segment_from_maps(task_doc, idx)
        if not seg:
            continue
        ranges.append((float(seg["start"]), float(seg["end"]), idx))

    if len(ranges) <= 1:
        return None, None, {}

    ranges.sort(key=lambda x: x[0])

    # 檢查是否真的相連/近鄰；相差太遠就不要亂合
    max_gap_sec = 3.0
    for j in range(1, len(ranges)):
        prev_end = float(ranges[j - 1][1])
        cur_start = float(ranges[j][0])
        if cur_start - prev_end > max_gap_sec:
            return None, None, {
                "merged": False,
                "reason": "gap_too_large",
                "group": group,
            }

    merged_start = min(x[0] for x in ranges)
    merged_end = max(x[1] for x in ranges)

    if merged_end <= merged_start:
        return None, None, {}

    return float(merged_start), float(merged_end), {
        "merged": True,
        "concept": primary_concept,
        "group": group,
        "start": float(merged_start),
        "end": float(merged_end),
    }

def _fixed_block_role(text: str) -> str:
    t = str(text or "").strip().lower()
    if not t:
        return "any"
    if t.startswith("def "):
        return "function_def"
    if t.startswith(("if ", "elif ", "else")) or ("operator" in t and ("==" in t or "!=" in t)):
        return "branch_condition"
    if t.startswith("return "):
        return "branch_return"
    if "input(" in t or "eval(input(" in t:
        return "input_read"
    if t.startswith("print("):
        return "output_print"
    return "any"


def _concept_terms_for_repair(concept: str) -> list:
    m = {
        "addition": ["加", "加號", "加法", "+", "add"],
        "subtraction": ["減", "減號", "減法", "-", "sub"],
        "multiplication": ["乘", "乘號", "乘法", "*", "mul"],
        "division": ["除", "除號", "除法", "/", "div"],
        "input": ["輸入", "讀入", "讀取", "第一個數字", "第二個數字", "第三個輸入", "運算符號"],
        "output": ["輸出", "印出", "答案", "結果", "顯示"],
        "function_def": ["函式", "def", "參數", "函式f", "函式 f"],
    }
    return m.get(str(concept or "").strip().lower(), [])


def _text_has_any(text: str, kws: list) -> bool:
    low = str(text or "").lower()
    return any(str(k).lower() in low for k in (kws or []) if str(k).strip())


def _pick_next_slot_start(seg_map: dict, from_idx: int) -> float:
    cands = []
    if not isinstance(seg_map, dict):
        return 0.0
    for j in range(from_idx + 1, from_idx + 4):
        for k in [str(j), f"s{j+1}", f"第{j+1}格"]:
            seg = seg_map.get(k)
            if not isinstance(seg, dict):
                continue
            s = seg.get("start", seg.get("start_ts"))
            if s is None:
                continue
            try:
                cands.append(float(s))
            except Exception:
                pass
    return min(cands) if cands else 0.0


def _repair_fixed_task_branch_pairs(solution_blocks: list, segs: list, ai_segment_map: dict, slot_concept_map: dict) -> dict:
    """
    修正固定題自動對齊：
    若 current 是 branch_return，前一格是同 concept 的 branch_condition，
    則 return 不可漂到下一個 input/output 段。
    """
    repaired = dict(ai_segment_map or {})
    if not solution_blocks or not segs or not repaired:
        return repaired

    for i in range(1, len(solution_blocks)):
        prev_block = solution_blocks[i - 1] or {}
        cur_block = solution_blocks[i] or {}

        prev_role = _fixed_block_role(prev_block.get("text"))
        cur_role = _fixed_block_role(cur_block.get("text"))

        prev_concept = str((slot_concept_map or {}).get(str(i - 1)) or "").strip().lower()
        cur_concept = str((slot_concept_map or {}).get(str(i)) or "").strip().lower()

        # 只修「condition -> return」且 concept 相同的成對結構
        if prev_role != "branch_condition" or cur_role != "branch_return":
            continue
        if not prev_concept or prev_concept != cur_concept:
            continue

        prev_seg = repaired.get(str(i - 1)) or {}
        cur_seg = repaired.get(str(i)) or {}
        if not isinstance(prev_seg, dict) or not isinstance(cur_seg, dict):
            continue

        try:
            ps = float(prev_seg.get("start", 0.0))
            pe = float(prev_seg.get("end", 0.0))
            cs = float(cur_seg.get("start", 0.0))
            ce = float(cur_seg.get("end", 0.0))
        except Exception:
            continue
        if pe <= ps or ce <= cs:
            continue

        cur_text = str(cur_seg.get("evidence") or cur_seg.get("text") or "").strip()
        suspicious = False

        # 若 return 已漂到 input / output / topic shift，標記為可疑
        if _text_has_any(cur_text, ["輸入", "讀入", "讀取", "第一個數字", "第二個數字", "第三個輸入", "答案印出", "印出", "輸出"]):
            suspicious = True
        if cs > pe + 3.5:
            suspicious = True

        if not suspicious:
            continue

        # 搜尋視窗：從前一格開始，到下一格起點前
        next_start = _pick_next_slot_start(repaired, i)
        window_start = max(ps, pe - 1.0)
        window_end = next_start - 0.2 if next_start and next_start > pe else (pe + 10.0)

        wanted_terms = _concept_terms_for_repair(cur_concept)
        best = None
        best_score = -999

        for seg in (segs or []):
            try:
                ss = float(seg.get("start", 0.0))
                ee = float(seg.get("end", 0.0))
            except Exception:
                continue
            if ee <= ss:
                continue
            if ee < window_start or ss > window_end:
                continue

            txt = str(seg.get("text") or "").strip().lower()
            score = 0

            # return 類型偏好
            for kw in ["回傳", "return", "結果", "值"]:
                if kw.lower() in txt:
                    score += 3

            # concept 偏好
            for kw in wanted_terms:
                if str(kw).lower() in txt:
                    score += 2

            # 避免漂到下一個 input/output
            for bad in ["輸入", "讀入", "讀取", "第一個數字", "第二個數字", "第三個輸入", "印出", "輸出"]:
                if bad.lower() in txt:
                    score -= 4

            # 越靠近前一格之後越好
            if ss >= pe - 0.5:
                score += 1

            if score > best_score:
                best_score = score
                best = {"start": ss, "end": ee, "text": str(seg.get("text") or "")}

        if best and best["end"] > best["start"]:
            repaired[str(i)] = {
                "start": float(best["start"]),
                "end": float(best["end"]),
                "score": float(cur_seg.get("score", 0.0)),
                "evidence": str(best.get("text") or ""),
            }

    return repaired


def _expand_review_window_for_learning_shared(
    task_doc: dict,
    start_sec: float,
    end_sec: float,
    segment_source: str = "",
    segment_concept: str = "",
    wrong_index: Optional[int] = None,
    min_span_default: float = 10.0,
):
    """Shared learning-window expansion used by teacher preview and submit-like prediction."""
    try:
        s = float(start_sec or 0.0)
        e = float(end_sec or 0.0)
        if e <= s:
            e = s + float(min_span_default)

        concept = str(segment_concept or "").strip().lower()

        if concept == "input_int_cast":
            min_span = 12.0
        elif concept == "print_separator":
            min_span = 10.0
        elif concept in {"addition", "subtraction", "multiplication", "division"}:
            min_span = 8.0
        elif concept in {"python_syntax", "if_condition_logic", "if_branch_order", "edge_case_condition", "return"}:
            min_span = 8.0
        else:
            min_span = float(min_span_default)

        if (e - s) < min_span:
            e = s + min_span

        try:
            seg_map = task_doc.get("teacher_segment_map") or task_doc.get("ai_segment_map") or {}
            if isinstance(seg_map, dict) and isinstance(wrong_index, int) and wrong_index >= 0:
                next_candidates = []
                for k in [str(wrong_index + 1), f"s{wrong_index + 2}", f"第{wrong_index + 2}格"]:
                    seg = seg_map.get(k)
                    if not isinstance(seg, dict):
                        continue
                    ns = seg.get("start", seg.get("start_ts"))
                    if ns is None:
                        continue
                    try:
                        nsf = float(ns)
                    except Exception:
                        continue
                    if nsf > s:
                        next_candidates.append(nsf)
                if next_candidates:
                    next_start = min(next_candidates)
                    if next_start > s + 1.0:
                        e = min(max(e, next_start - 0.2), s + 18.0)
        except Exception:
            pass

        try:
            segs = _load_task_subtitle_segments(task_doc)
            nearest_end = None
            best_dist = 10**9
            for seg in segs or []:
                txt = str(seg.get("text") or "")
                if _looks_like_problem_statement(txt, concept, ""):
                    continue
                ee = seg.get("end_sec")
                if ee is None:
                    ee = seg.get("end")
                if ee is None:
                    continue
                try:
                    eef = float(ee)
                except Exception:
                    continue
                if eef < s + 0.8:
                    continue
                d = abs(eef - e)
                if d < best_dist:
                    best_dist = d
                    nearest_end = eef
            if nearest_end is not None:
                e = nearest_end
        except Exception:
            pass

        if e <= s:
            e = s + 1.2

        return (round(max(0.0, s), 2), round(max(s + 1.0, e), 2))

    except Exception:
        s = float(start_sec or 0.0)
        e = float(end_sec or 0.0)
        if e <= s:
            e = s + float(min_span_default)
        return (round(max(0.0, s), 2), round(max(s + 1.0, e), 2))


def _enforce_monotonic_solution_order_windows(windows: list, min_gap_sec: float = 0.2) -> list:
    """Keep teacher preview windows non-decreasing in solution-slot order."""
    if not isinstance(windows, list) or not windows:
        return windows

    adjusted = []
    prev_end = None
    gap = max(0.0, float(min_gap_sec or 0.0))

    for item in windows:
        if not isinstance(item, dict):
            adjusted.append(item)
            continue

        current = dict(item)
        try:
            start = float(current.get("start", 0.0))
            end = float(current.get("end", 0.0))
        except Exception:
            adjusted.append(current)
            continue

        if prev_end is not None and start < prev_end - gap:
            before_start = start
            before_end = end
            start = float(prev_end + gap)
            span = max(1.0, float(before_end - before_start))
            end = max(start + span, start + 1.0)
            current["order_adjusted"] = True
            current["order_adjust_reason"] = "non_monotonic_solution_order"
            current["order_adjust_before"] = {
                "start": round(before_start, 2),
                "end": round(before_end, 2),
            }
            current["start"] = round(start, 2)
            current["end"] = round(end, 2)
        else:
            current["order_adjusted"] = bool(current.get("order_adjusted", False))

        prev_end = float(current.get("end", end))
        adjusted.append(current)

    return adjusted


def _build_review_groups_for_predicted_windows(predicted_rows: list) -> list:
    """Group predicted rows into teacher-readable review blocks."""
    if not isinstance(predicted_rows, list) or not predicted_rows:
        return predicted_rows

    def _slot_code_text(row: dict) -> str:
        return str(row.get("code") or row.get("codeText") or "").strip().lower()

    def _slot_concept(row: dict) -> str:
        return str(row.get("concept_tag") or row.get("conceptTag") or row.get("concept") or "").strip().lower()

    def _slot_wrong_type(row: dict) -> str:
        return str(row.get("wrong_type") or row.get("wrongType") or "").strip().lower()

    def _slot_family(row: dict) -> str:
        text = _slot_code_text(row)
        concept = _slot_concept(row)
        wrong_type = _slot_wrong_type(row)

        has_condition = any(term in text for term in ["if ", "elif ", "else:", "else "]) or "condition" in concept or "if_condition_logic" in wrong_type
        has_print = "print(" in text or "sep=" in text or "end=" in text
        if has_condition and has_print:
            return "condition"
        if concept == "print_separator" or has_print:
            return "output"
        if any(term in concept for term in ["loop_count_control", "loop_reverse_range", "nested_loop_structure"]) or \
            any(term in wrong_type for term in ["loop_count", "loop_reverse_range", "nested_loop_structure"]) or \
            any(term in text for term in ["for ", "while ", "range("]):
            return "loop"
        if any(term in concept for term in ["star_formula_2i_minus_1", "space_formula_n_minus_i"]) or \
            any(term in wrong_type for term in ["star_count", "space_count"]) or \
            any(term in text for term in ["2*i-1", "2i-1", "n-i"]):
            return "pattern"
        return "other"

    def _has_print_end(text: str) -> bool:
        return "end=" in text

    def _has_print_newline(text: str) -> bool:
        return "print()" in text or "print ( )" in text or ("print(" in text and "end=" not in text)

    grouped = []
    current = None
    max_gap_sec = 4.0

    for row in predicted_rows:
        if not isinstance(row, dict):
            continue
        start = row.get("start")
        end = row.get("end")
        try:
            start_f = float(start)
            end_f = float(end)
        except Exception:
            continue
        family = _slot_family(row)
        slot = int(row.get("slot") if str(row.get("slot") or "").strip().isdigit() else len(grouped))
        code_text = str(row.get("code") or "").strip()

        if current is None:
            current = {
                "items": [dict(row, group_family=family)],
                "families": [family],
                "start": start_f,
                "end": end_f,
                "slotStart": slot,
                "slotEnd": slot,
                "hasLoop": family == "loop",
                "hasOutput": family == "output",
                "hasPattern": family == "pattern",
                "hasPrintEnd": _has_print_end(code_text),
                "hasPrintNewline": _has_print_newline(code_text),
            }
            continue

        prev_end = float(current.get("end") or 0.0)
        prev_slot = int(current.get("slotEnd") or 0)
        can_extend = (
            slot <= prev_slot + 1
            and start_f <= prev_end + max_gap_sec
            and family in {"loop", "output", "pattern"}
            and (current.get("hasLoop") or current.get("hasOutput") or current.get("hasPattern"))
        )

        if not can_extend:
            grouped.append(current)
            current = {
                "items": [dict(row, group_family=family)],
                "families": [family],
                "start": start_f,
                "end": end_f,
                "slotStart": slot,
                "slotEnd": slot,
                "hasLoop": family == "loop",
                "hasOutput": family == "output",
                "hasPattern": family == "pattern",
                "hasPrintEnd": _has_print_end(code_text),
                "hasPrintNewline": _has_print_newline(code_text),
            }
            continue

        current["items"].append(dict(row, group_family=family))
        current["families"].append(family)
        current["start"] = min(float(current.get("start") or start_f), start_f)
        current["end"] = max(float(current.get("end") or end_f), end_f)
        current["slotEnd"] = slot
        current["hasLoop"] = bool(current.get("hasLoop")) or family == "loop"
        current["hasOutput"] = bool(current.get("hasOutput")) or family == "output"
        current["hasPattern"] = bool(current.get("hasPattern")) or family == "pattern"
        current["hasPrintEnd"] = bool(current.get("hasPrintEnd")) or _has_print_end(code_text)
        current["hasPrintNewline"] = bool(current.get("hasPrintNewline")) or _has_print_newline(code_text)

    if current is not None:
        grouped.append(current)

    def _group_label(group: dict) -> str:
        items = group.get("items") or []
        families = set(group.get("families") or [])
        has_loop = bool(group.get("hasLoop")) or "loop" in families
        has_output = bool(group.get("hasOutput")) or "output" in families
        has_pattern = bool(group.get("hasPattern")) or "pattern" in families
        has_print_end = bool(group.get("hasPrintEnd"))
        has_print_newline = bool(group.get("hasPrintNewline"))

        if has_loop and has_output:
            return "nested_output_structure" if (has_pattern or len(items) >= 2) else "continuous_output_structure"
        if has_output and has_print_end and has_print_newline:
            return "print_end_newline_pair"
        if has_output:
            return "continuous_output_structure"
        if has_loop:
            return "loop_structure"
        if has_pattern:
            return "pattern_structure"
        return str((items[0] or {}).get("concept_tag") or (items[0] or {}).get("concept") or "review_group")

    out = []
    for group in grouped:
        items = group.get("items") or []
        label = _group_label(group)
        slot_start = int(group.get("slotStart") or 0)
        slot_end = int(group.get("slotEnd") or slot_start)
        out.append({
            "group_key": f"{label}:{slot_start}-{slot_end}",
            "group_label": label,
            "slot_start": slot_start,
            "slot_end": slot_end,
            "group_slots": list(range(slot_start, slot_end + 1)) if slot_end >= slot_start else [slot_start],
            "start": round(float(group.get("start") or 0.0), 2),
            "end": round(float(group.get("end") or 0.0), 2),
            "items": items,
        })

    return out


@parsons_bp.get("/fixed_task/predicted_windows")
def fixed_task_predicted_windows():
    """Teacher-side preview of submit-like final playback windows per slot."""
    if not _video_review_enabled():
        return _video_review_disabled_response()

    task_id_str = (request.args.get("task_id") or "").strip()
    if not task_id_str:
        return jsonify({"ok": False, "message": "missing task_id"}), 400

    try:
        task = db.parsons_tasks.find_one({"_id": ObjectId(task_id_str)})
    except Exception:
        return jsonify({"ok": False, "message": "invalid task_id"}), 400
    if not task:
        return jsonify({"ok": False, "message": "task not found"}), 404

    blocks = task.get("solution_blocks") or []
    if not blocks:
        return jsonify({"ok": True, "task_id": task_id_str, "predicted_windows": []})

    subtitle_index = task.get("subtitle_ir_cache") or {}
    subtitle_ready = isinstance(subtitle_index, dict) and bool((subtitle_index or {}).get("segments"))
    if not subtitle_ready:
        try:
            subtitle_index = _build_or_get_task_subtitle_ir_cache(task, force_rebuild=False) or {}
            subtitle_ready = isinstance(subtitle_index, dict) and bool((subtitle_index or {}).get("segments"))
        except Exception:
            subtitle_index = {}
            subtitle_ready = False

    slot_map = _derive_slot_concept_map(task)
    predicted = []

    for i, b in enumerate(blocks):
        code_text = str((b or {}).get("text") or "")
        sem_text = str((b or {}).get("semantic_zh") or (b or {}).get("meaning_zh") or "")
        expected_text = code_text
        actual_text = code_text
        wrong_type = infer_wrong_type_from_code(code_text, sem_text)
        concept_tag = resolve_concept_tag_from_wrong_type(wrong_type, code_text, sem_text)

        t_start = None
        t_end = None
        source = "none"
        trace = []

        # 1) slot/concept mapping path
        ms, me, _, _ = _segment_from_concept_mapping(task, i)
        if ms is not None and me is not None and me > ms:
            t_start, t_end = float(ms), float(me)
            source = "slot_mapping"
            trace.append("slot_mapping")

        # 2) IR path
        if (t_start is None or t_end is None or t_end <= t_start) and subtitle_ready:
            try:
                ir_seg, ir_score = retrieve_segment_for_wrong_slot(task, int(i), subtitle_index)
                if ir_seg and float(ir_score or 0.0) >= 0.08:
                    t_start = float(ir_seg.get("start", 0.0))
                    t_end = float(ir_seg.get("end", 0.0))
                    source = "ir_slot_retrieval"
                    trace.append("ir_slot_retrieval")
            except Exception:
                pass

        # 3) map fallback
        if t_start is None or t_end is None or t_end <= t_start:
            seg = _get_slot_segment_from_maps(task, i)
            if isinstance(seg, dict):
                try:
                    fs = float(seg.get("start", 0.0))
                    fe = float(seg.get("end", 0.0))
                    if fe > fs:
                        t_start, t_end = fs, fe
                        source = "task_map_fallback"
                        trace.append("task_map_fallback")
                except Exception:
                    pass

        if t_start is None or t_end is None or t_end <= t_start:
            t_start, t_end = 0.0, 0.0
            source = "unresolved"
            trace.append("unresolved")

        concept = str(slot_map.get(str(i)) or "").strip().lower()
        raw_start = float(t_start)
        raw_end = float(t_end)
        raw_source = source
        anchor_id = None
        if subtitle_index and (subtitle_index.get("segments") or []):
            anchor_seg = _resolve_subtitle_anchor_for_window(subtitle_index, raw_start, raw_end)
            anchor_id = anchor_seg.get("id") if isinstance(anchor_seg, dict) else None
            if anchor_id is None:
                try:
                    anchor_hit, _ = retrieve_segment_for_wrong_slot(task, int(i), subtitle_index)
                except Exception:
                    anchor_hit = None
                if isinstance(anchor_hit, dict):
                    anchor_id = anchor_hit.get("anchor_id") or anchor_hit.get("id") or anchor_hit.get("index")
        if source != "unresolved":
            t_start, t_end = _expand_review_window_for_learning_shared(
                task_doc=task,
                start_sec=t_start,
                end_sec=t_end,
                segment_source=source,
                segment_concept=concept,
                wrong_index=int(i),
                min_span_default=10.0,
            )
            trace.append("expand_review_window_for_learning")

        predicted.append({
            "slot": int(i),
            "code": code_text,
            "semantic_zh": sem_text,
            "concept": concept,
            "wrong_type": wrong_type,
            "concept_tag": concept_tag,
            "raw_start": float(raw_start),
            "raw_end": float(raw_end),
            "raw_source": raw_source,
            "start": float(t_start),
            "end": float(t_end),
            "segment_source": source,
            "trace": trace,
            "anchor_id": anchor_id,
        })

    # 保留原始字幕命中順序；slot 時間不一定要單調遞增，硬推會把早出的片段挪到後面。
    review_groups = _build_review_groups_for_predicted_windows(predicted)

    return jsonify({
        "ok": True,
        "task_id": task_id_str,
        "predicted_windows": predicted,
        "review_groups": review_groups,
        "count": len(predicted),
    })

def _segment_from_concept_mapping(task_doc: dict, wrong_idx: int):
    """
    Structured mapping path.
    僅允許 strict slot mapping / repair slot mapping。
    不再使用 concept aggregate fallback。
    """
    try:
        wi = int(wrong_idx)

        def _block_role(text: str) -> str:
            t = str(text or "").strip().lower()
            if not t:
                return "any"
            if t.startswith(("if ", "elif ", "else")) or ("operator" in t and ("==" in t or "!=" in t)):
                return "condition"
            if ("return" in t) or t.startswith("print(") or ("回傳" in t) or ("輸出" in t):
                return "compute"
            return "any"

        def _repair_slot_segment() -> tuple:
            blocks = task_doc.get("solution_blocks") or []
            if wi < 0 or wi >= len(blocks):
                return (None, None, "", "")

            block = blocks[wi] or {}
            query = (
                str(block.get("text") or "").strip()
                + "\n"
                + str(block.get("semantic_zh") or block.get("meaning_zh") or "").strip()
            ).strip()

            subtitle_index = task_doc.get("subtitle_ir_cache") or {}
            if not (subtitle_index.get("segments") or []):
                return (None, None, "", "")

            try:
                seg, score = retrieve_segment_for_wrong_slot(task_doc, wi, subtitle_index)
                if seg and float(score or 0.0) >= 0.08:
                    s = float(seg.get("start", 0.0))
                    e = float(seg.get("end", 0.0))
                    if e > s:
                        slot_map = _derive_slot_concept_map(task_doc)
                        concept = str(slot_map.get(str(wi)) or "").strip().lower()
                        return (s, e, concept, "repair_slot_segment")
            except Exception:
                pass

            return (None, None, "", "")

        def _repair_function_def_segment() -> tuple:
            subtitle_index = task_doc.get("subtitle_ir_cache") or {}
            if not (subtitle_index.get("segments") or []):
                return (None, None, "", "")

            try:
                fake_task = dict(task_doc or {})
                fake_blocks = list(fake_task.get("solution_blocks") or [])
                if wi < 0 or wi >= len(fake_blocks):
                    return (None, None, "", "")
                fake_blocks[wi] = {
                    **(fake_blocks[wi] or {}),
                    "text": "def function_name(...):",
                    "semantic_zh": "函式定義",
                }
                fake_task["solution_blocks"] = fake_blocks

                seg, score = retrieve_segment_for_wrong_slot(fake_task, wi, subtitle_index)
                if seg and float(score or 0.0) >= 0.08:
                    s = float(seg.get("start", 0.0))
                    e = float(seg.get("end", 0.0))
                    if e > s:
                        return (s, e, "python_syntax", "repair_function_def_segment")
            except Exception:
                pass

            return (None, None, "", "")

        def _context_from_cache(start_sec: float, end_sec: float) -> str:
            cache = task_doc.get("subtitle_ir_cache") or {}
            texts = []
            for seg in (cache.get("segments") or []):
                try:
                    ss = float(seg.get("start"))
                    ee = float(seg.get("end"))
                except Exception:
                    continue
                if ee <= ss:
                    continue
                if ee < start_sec or ss > end_sec:
                    continue
                texts.append(str(seg.get("text") or ""))
            return " ".join(texts).strip()

        def _role_match_score(text: str, slot_role: str) -> int:
            low = str(text or "").lower()
            score = 0
            if slot_role == "condition":
                for good in ["if", "elif", "else", "條件", "判斷", "運算符號"]:
                    if good in low:
                        score += 1
                for bad in ["print", "return", "輸出", "回傳"]:
                    if bad in low:
                        score -= 1
            elif slot_role == "compute":
                for good in ["print", "return", "輸出", "回傳", "計算", "結果"]:
                    if good in low:
                        score += 1
                for bad in ["if", "elif", "條件"]:
                    if bad in low:
                        score -= 1
            return int(score)

        seg_map = _get_slot_segment_map(task_doc)
        slot_map = _derive_slot_concept_map(task_doc)

        # 1) strict slot mapping only
        if isinstance(seg_map, dict) and seg_map:
            for k, v in seg_map.items():
                try:
                    ki = int(str(k))
                except Exception:
                    continue
                if ki != wi:
                    continue
                if not isinstance(v, dict):
                    continue

                s = v.get("start", v.get("start_ts"))
                e = v.get("end", v.get("end_ts"))
                if s is None or e is None:
                    continue

                sf = float(s)
                ef = float(e)
                if ef <= sf:
                    continue

                concept = str(v.get("concept") or slot_map.get(str(wi)) or "").strip().lower()
                slot_role = _block_role(str((task_doc.get("solution_blocks") or [{}])[wi].get("text") or ""))

                reject_current_slot_segment = False

                try:
                    current_ctx = _context_from_cache(sf, ef).lower()
                    need_repair_semantic = False

                    if concept in {"division", "multiplication", "addition", "subtraction"}:
                        op_score0 = _calc_operator_score(current_ctx, _operation_filter_keywords(concept))
                        if op_score0 <= 0:
                            need_repair_semantic = True

                    if (not need_repair_semantic) and slot_role in {"condition", "compute"}:
                        if _role_match_score(current_ctx, slot_role) <= 0:
                            need_repair_semantic = True

                    if need_repair_semantic:
                        rs, re, rc, rr = _repair_slot_segment()
                        if rs is not None and re is not None and re > rs:
                            return (float(rs), float(re), str(rc or concept), rr)
                        reject_current_slot_segment = True
                except Exception:
                    pass

                try:
                    total_slots = max(1, len(task_doc.get("template_slots") or task_doc.get("solution_blocks") or []))
                    if total_slots > 1:
                        all_ranges = []
                        for _, vv in (seg_map or {}).items():
                            if not isinstance(vv, dict):
                                continue
                            ss = vv.get("start", vv.get("start_ts"))
                            ee = vv.get("end", vv.get("end_ts"))
                            if ss is None or ee is None:
                                continue
                            ssf = float(ss)
                            eef = float(ee)
                            if eef <= ssf:
                                continue
                            all_ranges.append((ssf, eef))

                        if len(all_ranges) >= 3:
                            g_start = min(x[0] for x in all_ranges)
                            g_end = max(x[1] for x in all_ranges)
                            g_span = max(1e-6, g_end - g_start)
                            center_ratio = (((sf + ef) / 2.0) - g_start) / g_span
                            expected_ratio = float(wi) / float(max(1, total_slots - 1))
                            gap = abs(center_ratio - expected_ratio)
                            gap_th = float(os.getenv("PARSONS_SLOT_RATIO_GAP_THRESHOLD") or "0.20")
                            if gap > gap_th:
                                rs, re, rc, rr = _repair_slot_segment()
                                if rs is not None and re is not None and re > rs:
                                    return (float(rs), float(re), str(rc or concept), rr)
                                reject_current_slot_segment = True
                except Exception:
                    pass

                try:
                    total_slots = max(1, len(task_doc.get("template_slots") or task_doc.get("solution_blocks") or []))
                    if total_slots > 1:
                        cache = task_doc.get("subtitle_ir_cache") or {}
                        cache_ranges = []
                        for seg0 in (cache.get("segments") or []):
                            try:
                                ss0 = float(seg0.get("start"))
                                ee0 = float(seg0.get("end"))
                            except Exception:
                                continue
                            if ee0 <= ss0:
                                continue
                            cache_ranges.append((ss0, ee0))

                        if len(cache_ranges) >= 3:
                            g_start2 = min(x[0] for x in cache_ranges)
                            g_end2 = max(x[1] for x in cache_ranges)
                            g_span2 = max(1e-6, g_end2 - g_start2)
                            center_ratio2 = (((sf + ef) / 2.0) - g_start2) / g_span2
                            expected_ratio2 = float(wi) / float(max(1, total_slots - 1))
                            gap2 = abs(center_ratio2 - expected_ratio2)
                            gap2_th = float(os.getenv("PARSONS_SLOT_RATIO_GAP2_THRESHOLD") or "0.22")
                            if gap2 > gap2_th:
                                rs, re, rc, rr = _repair_slot_segment()
                                if rs is not None and re is not None and re > rs:
                                    return (float(rs), float(re), str(rc or concept), rr)
                                reject_current_slot_segment = True
                except Exception:
                    pass

                if reject_current_slot_segment:
                    continue

                if (normalize_concept_name(concept) == "python_syntax" or normalize_concept_name(slot_map.get(str(wi)) or "") == "python_syntax"):
                    try:
                        current_ctx = _context_from_cache(sf, ef).lower()
                    except Exception:
                        current_ctx = ""
                    looks_intro = any(t in current_ctx for t in ["請設計", "題目", "這一題", "請你"]) and ("參數" not in current_ctx)
                    weak_function_cue = ("參數" not in current_ctx) and ("運算符號" not in current_ctx)
                    if looks_intro or weak_function_cue:
                        rs, re, rc, rr = _repair_function_def_segment()
                        if rs is not None and re is not None and re > rs:
                            return (float(rs), float(re), str(rc or concept), rr)

                if wi > 0:
                    rs, re, rc, rr = _repair_slot_segment()
                    if rs is not None and re is not None and re > rs:
                        return (float(rs), float(re), str(rc or concept), rr)
                    continue

                return (sf, ef, concept, f"slot:{wi}->slot_segment:{k}")

        # 2) concept aggregate fallback 已完全停用
        return (None, None, "", "")

    except Exception:
        return (None, None, "", "")


def retrieve_segment_for_wrong_slot(task_doc: dict, wrong_idx: int, subtitle_index: dict):
    solution_blocks = task_doc.get("solution_blocks") or []
    if not isinstance(wrong_idx, int) or wrong_idx < 0 or wrong_idx >= len(solution_blocks):
        return None, 0.0

    block = solution_blocks[wrong_idx] or {}
    block_text = str(block.get("text") or "").strip()
    block_sem  = str(block.get("semantic_zh") or block.get("meaning_zh") or "").strip()
    query = (block_text + " " + block_sem).strip()
    if not query:
        return None, 0.0

    # ── [新增] wrong_type → concept_tag → 查詢詞 ──
    wrong_type = infer_wrong_type_from_code(block_text, block_sem)
    sub_concept = resolve_concept_tag_from_wrong_type(wrong_type, block_text, block_sem)
    if not sub_concept:
        sub_concept = infer_sub_concept_from_code(block_text, block_sem)

    query_terms = []
    if wrong_type:
        mapped_tag = normalize_concept_name(_WRONG_TYPE_TO_CONCEPT_TAG.get(wrong_type) or "")
        if mapped_tag:
            query_terms.extend(get_query_terms_for_concept_tag(mapped_tag))
        query_terms.extend(_WRONG_TYPE_QUERY_TERMS.get(wrong_type, []))
    if sub_concept:
        query_terms.extend(get_query_terms_for_concept_tag(sub_concept))

    seen_terms = set()
    merged_terms = []
    for term in query_terms:
        t = str(term or "").strip()
        if not t:
            continue
        key = t.lower()
        if key in seen_terms:
            continue
        seen_terms.add(key)
        merged_terms.append(t)

    if merged_terms:
        boosted_query = (" ".join(merged_terms) + " " + query).strip()
    else:
        boosted_query = query
    # ─────────────────────────────────────────────

    def _slot_role(text: str) -> str:
        t = str(text or "").strip().lower()
        if not t:
            return "any"
        if t.startswith(("if ", "elif ", "else")) or ("operator" in t and ("==" in t or "!=" in t)):
            return "condition"
        if ("return" in t) or t.startswith("print(") or ("回傳" in t) or ("輸出" in t):
            return "compute"
        return "any"

    def _segment_role_score(text: str, role: str) -> int:
        low = str(text or "").lower()
        if not low or role == "any":
            return 0
        score = 0
        if role == "condition":
            for kw in ["判斷", "符號", "是不是", "if", "elif", "operator", "==", "!="]:
                if kw in low:
                    score += 2
            for bad in ["回傳", "return", "print", "值", "結果"]:
                if bad in low:
                    score -= 1
        elif role == "compute":
            for kw in ["回傳", "return", "print", "值", "結果", "除以", "乘以", "加", "減"]:
                if kw in low:
                    score += 2
            for bad in ["判斷", "if", "elif", "條件"]:
                if bad in low:
                    score -= 1
        return int(score)

    def _segment_is_opening_explanation(text: str) -> bool:
        return _looks_like_intro_explanation_text(text)

    op = _extract_operation_from_code_text(query)
    # 在子概念查詢詞基礎上疊加運算子 boost（不覆蓋）
    if op:
        boosted_query = (
            boosted_query
            + " "
            + _operation_query_boost_terms(op)
            + " condition 判斷"
        ).strip()

    slot_role = _slot_role(block_text)  # 用 block_text，不含 semantic_zh 避免干擾
    slot_map = _derive_slot_concept_map(task_doc)
    concept_map = _derive_concept_segment_map(task_doc, slot_map)
    slot_concept = str(slot_map.get(str(wrong_idx)) or "").strip().lower()

    concept_window = None
    if slot_concept:
        seg = concept_map.get(slot_concept)
        if isinstance(seg, dict):
            try:
                cs = float(seg.get("start"))
                ce = float(seg.get("end"))
                if ce > cs:
                    concept_window = (cs, ce)
            except Exception:
                concept_window = None

    active_index = subtitle_index
    keep = []
    segs = (subtitle_index or {}).get("segments") or []
    op_code = {
        "addition": "add",
        "subtraction": "sub",
        "multiplication": "mul",
        "division": "div",
    }.get(str(op or ""), "")
    op_kws = _operation_filter_keywords(op) if op else []

    for i, seg in enumerate(segs):
        txt = str((seg or {}).get("text") or "")
        low = txt.lower()
        try:
            ss = float((seg or {}).get("start"))
            ee = float((seg or {}).get("end"))
        except Exception:
            continue
        if ee <= ss:
            continue

        # Hard gate 1: concept segment window.
        if concept_window is not None:
            cws, cwe = concept_window
            if ee < cws or ss > cwe:
                continue

        # Hard gate 2: operation concept.
        if op:
            op_hit = False
            op_score_pos = 0
            if op_code:
                op_score_pos = _calc_operator_score(txt, [op_code])
                op_hit = op_score_pos > 0
            if (not op_hit) and op_kws:
                op_hit = any(str(k).lower() in low for k in op_kws)
            if not op_hit:
                continue

            # negative guard: prevent division<->multiplication confusion.
            if op == "division":
                neg_mul = _calc_operator_score(txt, ["mul"])
                if neg_mul > op_score_pos:
                    continue
            elif op == "multiplication":
                neg_div = _calc_operator_score(txt, ["div"])
                if neg_div > op_score_pos:
                    continue

        # Hard gate 3: slot role (condition/compute).
        if slot_role in {"condition", "compute"}:
            if _segment_role_score(txt, slot_role) <= 0:
                continue

        # 開頭說明區通常是教學口白，不鎖成回看片段。
        # 只有在沒有更像程式碼的字幕時才會被保留在更後面的 fallback 裡。
        if _segment_is_opening_explanation(txt):
            continue

        keep.append(i)

    # 當 gate 啟用卻沒有候選時，寧可回傳無命中，避免誤配到錯概念。
    if (op or slot_role in {"condition", "compute"} or concept_window is not None) and not keep:
        return None, 0.0

    if keep:
        filtered = _build_filtered_subtitle_index(subtitle_index, keep)
        if filtered:
            active_index = filtered

    top_k = max(1, int(os.getenv("PARSONS_RETRIEVAL_TOP_K") or "3"))
    if top_k <= 1:
        best, score = retrieve_best_segment(boosted_query, active_index)
        if isinstance(best, dict):
            best["op_concept"] = op
            best["slot_role"] = slot_role
            best["slot_concept"] = slot_concept
            best["anchor_id"] = best.get("id", best.get("index", None))
        return best, score

    top_hits = retrieve_top_k_segments(boosted_query, active_index, k=top_k)

    if top_hits:
        total_slots = max(1, len(task_doc.get("template_slots") or task_doc.get("solution_blocks") or []))
        slot_ratio = float(wrong_idx) / float(max(1, total_slots - 1)) if total_slots > 1 else 0.0

        g_start = None
        g_end = None
        try:
            all_segs = (active_index or {}).get("segments") or []
            if all_segs:
                g_start = min(float(x.get("start")) for x in all_segs)
                g_end = max(float(x.get("end")) for x in all_segs)
        except Exception:
            g_start, g_end = None, None

        reranked = []
        context_rules = get_context_rules_for_concept_tag(slot_concept)
        context_positive = [str(x or "").strip().lower() for x in (context_rules.get("positive") or []) if str(x or "").strip()]
        context_negative = [str(x or "").strip().lower() for x in (context_rules.get("negative") or []) if str(x or "").strip()]
        for hit in top_hits:
            txt = str(hit.get("text") or "")
            low_txt = txt.lower()
            base = float(hit.get("score") or 0.0)
            op_bonus = 0.0
            if op_code:
                op_bonus = min(0.22, max(0.0, float(_calc_operator_score(txt, [op_code])) * 0.03))

            role_bonus = 0.0
            role_score = _segment_role_score(txt, slot_role)
            if slot_role in {"condition", "compute"} and role_score > 0:
                role_bonus = min(0.18, float(role_score) * 0.03)

            concept_bonus = 0.0
            if slot_concept and concept_window is not None:
                concept_bonus = 0.08

            context_bonus = 0.0
            context_penalty = 0.0
            positive_hits = 0
            negative_hits = 0
            for kw in context_positive:
                if kw and kw in low_txt:
                    positive_hits += 1
            for kw in context_negative:
                if kw and kw in low_txt:
                    negative_hits += 1
            if positive_hits:
                context_bonus = min(0.20, positive_hits * 0.05)
            if negative_hits:
                context_penalty = min(0.24, negative_hits * 0.06)

            timeline_bonus = 0.0
            if g_start is not None and g_end is not None and g_end > g_start:
                hs = float(hit.get("start") or 0.0)
                he = float(hit.get("end") or hs)
                center = (hs + he) / 2.0
                ratio = (center - g_start) / max(1e-6, (g_end - g_start))
                timeline_bonus = max(0.0, 0.12 - abs(ratio - slot_ratio) * 0.24)

            rerank_score = min(1.0, max(0.0, base + op_bonus + role_bonus + concept_bonus + context_bonus + timeline_bonus - context_penalty))
            reranked.append((rerank_score, base, hit, {
                "base": round(base, 4),
                "op_bonus": round(op_bonus, 4),
                "role_bonus": round(role_bonus, 4),
                "concept_bonus": round(concept_bonus, 4),
                "context_bonus": round(context_bonus, 4),
                "context_penalty": round(context_penalty, 4),
                "positive_hits": int(positive_hits),
                "negative_hits": int(negative_hits),
                "timeline_bonus": round(timeline_bonus, 4),
                "rerank_score": round(rerank_score, 4),
            }))

        reranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
        _, final_base, best_hit, detail = reranked[0]
        best = {
            "start": float(best_hit.get("start", 0.0)),
            "end": float(best_hit.get("end", 0.0)),
            "text": str(best_hit.get("text") or ""),
            "index": int(best_hit.get("index", 0)),
            "id": best_hit.get("id", best_hit.get("index", 0)),
            "top_k_count": len(top_hits),
            "top_k_scores": [float(x.get("score", 0.0)) for x in top_hits],
            "op_concept": op,
            "slot_role": slot_role,
            "slot_concept": slot_concept,
            "wrong_type": wrong_type,
            "sub_concept": sub_concept,
            "sub_concept_terms": merged_terms,
            "rerank_strategy": "rule_cross_encoder_v1",
            "rerank_detail": detail,
            "rerank_top": [x[3] for x in reranked[: min(5, len(reranked))]],
        }
        return best, float(detail.get("rerank_score", final_base))

    best, score = retrieve_best_segment(boosted_query, active_index)
    if isinstance(best, dict):
        best["op_concept"] = op
        best["slot_role"] = slot_role
        best["slot_concept"] = slot_concept
        best["wrong_type"] = wrong_type
        best["sub_concept"] = sub_concept
        best["sub_concept_terms"] = merged_terms
        best["anchor_id"] = best.get("id", best.get("index", None))
    return best, score


def _resolve_subtitle_anchor_for_window(subtitle_index: dict, start_sec: float, end_sec: float) -> dict:
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

    best = None
    best_overlap = -1.0
    best_distance = 10**9
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
        text = str(seg.get("text") or "").strip()
        if _looks_like_intro_explanation_text(text):
            continue

        overlap = min(e, ee) - max(s, ss)
        if overlap > 0:
            if overlap > best_overlap:
                best = seg
                best_overlap = overlap
                best_distance = abs(((ss + ee) / 2.0) - ((s + e) / 2.0))
            continue

        distance = min(abs(ss - e), abs(ee - s))
        if best is None and distance < best_distance:
            best = seg
            best_distance = distance

    if not isinstance(best, dict):
        return {}

    return {
        "id": best.get("id"),
        "start": float(best.get("start", 0.0)),
        "end": float(best.get("end", 0.0)),
        "text": str(best.get("text") or ""),
        "index": int(best.get("index", 0)) if str(best.get("index", 0)).isdigit() or isinstance(best.get("index", 0), int) else 0,
    }

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

    ir_score_threshold = float(os.getenv("PARSONS_IR_SCORE_THRESHOLD") or "0.30")
    ir_soft_threshold = float(os.getenv("PARSONS_IR_SOFT_THRESHOLD") or "0.08")
    subtitle_index = {}
    subtitle_index_ready = False
    try:
        subtitle_index = task.get("subtitle_ir_cache") or {}
        subtitle_index_ready = isinstance(subtitle_index, dict) and bool(subtitle_index.get("segments"))
    except Exception:
        subtitle_index = {}
        subtitle_index_ready = False

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

    def _fallback_segment_from_task(task_doc, slot_idx: int, allow_ai_segment_map: bool = True):
        """
        依 task 裡的 ai_segment_map / ai_segments_compact + subtitle_path 推算秒數
        回傳 (t_start, t_end, subtitle_context, source)
        """
        try:
            slots = task_doc.get("template_slots") or task_doc.get("solution_blocks") or []
            total_slots = max(1, len(slots))
            cache_segments = []
            try:
                _cache = task_doc.get("subtitle_ir_cache") or {}
                if isinstance(_cache, dict) and isinstance(_cache.get("segments"), list):
                    for seg0 in (_cache.get("segments") or []):
                        try:
                            ss0 = float(seg0.get("start"))
                            ee0 = float(seg0.get("end"))
                        except Exception:
                            continue
                        if ee0 <= ss0:
                            continue
                        cache_segments.append({
                            "start": ss0,
                            "end": ee0,
                            "text": str(seg0.get("text") or ""),
                        })
            except Exception:
                cache_segments = []

            # ① 優先用 teacher_segment_map，其次 ai_segment_map
            base_map = task_doc.get("teacher_segment_map") or task_doc.get("ai_segment_map") or {}
            seg_map = (base_map or {}) if allow_ai_segment_map else {}
            key1 = str(slot_idx) if slot_idx is not None else "0"
            key2 = f"第{(slot_idx + 1)}格" if slot_idx is not None else "第1格"
            key3 = f"s{(slot_idx + 1)}" if slot_idx is not None else "s1"

            # 若 ai_segment_map 全格同一段，視為粗粒度映射，改走後續 slot-index fallback。
            if _is_generic_uniform_slot_map(seg_map):
                seg_map = {}

            seg = None
            if key1 in seg_map:
                seg = seg_map.get(key1)
            elif key2 in seg_map:
                seg = seg_map.get(key2)
            elif key3 in seg_map:
                seg = seg_map.get(key3)

            if isinstance(seg, dict):
                # 忽略 submit 階段概念推測產生的暫時片段（evidence=concept=...）
                if _is_runtime_concept_segment(seg):
                    seg = None

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
                    # slot map 合理性檢查：若格序與時間序偏差過大，拒用這個 map。
                    try:
                        if slot_idx is not None and total_slots > 1:
                            ranges = []
                            for k0, v0 in (seg_map or {}).items():
                                if not isinstance(v0, dict):
                                    continue
                                s0 = v0.get("start", v0.get("start_ts"))
                                e0 = v0.get("end", v0.get("end_ts"))
                                if s0 is None or e0 is None:
                                    continue
                                s0f = float(s0)
                                e0f = float(e0)
                                if e0f <= s0f:
                                    continue
                                idx0 = _safe_slot_index(str(k0))
                                if idx0 is None:
                                    continue
                                ranges.append((idx0, s0f, e0f))

                            if len(ranges) >= 3:
                                g_start = min(x[1] for x in ranges)
                                g_end = max(x[2] for x in ranges)
                                span = max(1e-6, g_end - g_start)
                                center = (float(ts) + float(te)) / 2.0
                                center_ratio = (center - g_start) / span
                                expected_ratio = float(slot_idx) / float(max(1, total_slots - 1))
                                ratio_gap = abs(center_ratio - expected_ratio)
                                # gap 太大表示「第 N 格卻跳到過前/過後」，拒用 map。
                                ratio_gap_threshold = float(os.getenv("PARSONS_SLOT_RATIO_GAP_THRESHOLD") or "0.20")
                                if ratio_gap > ratio_gap_threshold:
                                    seg = None
                    except Exception:
                        pass

                if isinstance(seg, dict) and (
                    ts is not None
                    and te is not None
                    and float(te) > float(ts)
                    and float(ts) > 0
                ):
                    ctx = seg.get("evidence") or ""
                    src = "teacher_segment_map" if (task_doc.get("teacher_segment_map") or {}) else "ai_segment_map"
                    return (float(ts), float(te), ctx, src)
                # otherwise continue to fallback below

            # ② 沒有 map，就用 compact + subtitle_path 做推算（B 電腦常見）
            compact = task_doc.get("ai_segments_compact") or ""
            subtitle_path = (((task_doc.get("prompt_source") or {}).get("subtitle_path")) or "").strip()
            if compact and (subtitle_path or cache_segments):
                import re
                pair_count = len(re.findall(r"\[(\d+)\s*-\s*(\d+)\]", compact or ""))
                # compact 若不足以覆蓋每格，會造成多格落在同一段，改走等比分段。
                if pair_count >= total_slots:
                    line_a, line_b = _pick_segment_from_compact(compact, slot_idx or 0)
                else:
                    line_a, line_b = (None, None)
                if line_a is not None and line_b is not None and line_b > line_a:
                    segs = list(cache_segments or [])
                    if not segs and subtitle_path:
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
                            return (ts, te, ctx, "compact_slot_map")

            # ③ 還是找不到？嘗試直接讀 srt 檔並平均分配時間區間
            try:
                segs = list(cache_segments or [])
                if (not segs) and subtitle_path:
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
                    # 以「字幕段索引」切格，比純時間均分更穩定。
                    n = len(segs)
                    idx = int(slot_idx or 0)
                    if idx < 0:
                        idx = 0
                    if idx >= total_slots:
                        idx = total_slots - 1

                    a = int((idx * n) / max(1, total_slots))
                    b = int((((idx + 1) * n) / max(1, total_slots)) - 1)
                    if b < a:
                        b = a
                    a = max(0, min(a, n - 1))
                    b = max(0, min(b, n - 1))

                    part_start = float(segs[a]["start"])
                    part_end = float(segs[b]["end"])
                    ctx_lines = [str(segs[i].get("text") or "") for i in range(a, min(b + 1, a + 4))]
                    ctx = "\n".join([x for x in ctx_lines if x]).strip()
                    return (part_start, part_end, ctx, "equal_split_by_segments")
            except Exception:
                pass
            return (None, None, "", "none")
        except Exception:
            return (None, None, "", "error")

    def _fallback_segment_from_teacher_chapters(task_doc, slot_idx: int, slot_concept: str = ""):
        """
        優先使用老師確認章節作為 submit 對齊 fallback。
        來源順序：block_chapter_map 對應 slot > concept 命中 > slot 序位對應。
        """
        try:
            chapters = task_doc.get("concept_chapters_formal") or task_doc.get("teacher_concept_chapters") or []
            if not isinstance(chapters, list) or not chapters:
                return (None, None, "", "none")

            if slot_idx is None:
                slot_idx = 0
            slot_idx = int(slot_idx)
            slot_key = str(slot_idx)

            bcm = task_doc.get("block_chapter_map") or {}
            if isinstance(bcm, dict):
                mapped = bcm.get(slot_key)
                if not isinstance(mapped, dict):
                    mapped = bcm.get(f"s{slot_idx + 1}")
                if isinstance(mapped, dict):
                    try:
                        ms = float(mapped.get("start", 0.0))
                        me = float(mapped.get("end", 0.0))
                    except Exception:
                        ms, me = None, None
                    if ms is not None and me is not None and me > ms:
                        return (ms, me, str(mapped.get("concept_label") or mapped.get("concept") or ""), "teacher_concept_chapter_map")

            target_concept = normalize_concept_name(slot_concept)
            if not target_concept:
                try:
                    scm = _derive_slot_concept_map(task_doc)
                    target_concept = normalize_concept_name((scm or {}).get(slot_key) or "")
                except Exception:
                    target_concept = ""

            best = None
            for idx, ch in enumerate(chapters):
                if not isinstance(ch, dict):
                    continue
                try:
                    cs = float(ch.get("start", 0.0))
                    ce = float(ch.get("end", 0.0))
                except Exception:
                    continue
                if ce <= cs:
                    continue

                ctag = normalize_concept_name(ch.get("concept_tag") or ch.get("concept") or ch.get("wrong_type"))
                score = 0
                if target_concept and ctag and target_concept == ctag:
                    score += 10
                score += max(0, 3 - abs(idx - slot_idx))

                cand = (score, cs, ce, ch)
                if best is None or cand[0] > best[0]:
                    best = cand

            if best is not None and best[2] > best[1]:
                _, cs, ce, ch = best
                return (float(cs), float(ce), str(ch.get("concept_label") or ch.get("concept") or ""), "teacher_concept_chapter")

            idx = max(0, min(slot_idx, len(chapters) - 1))
            ch = chapters[idx] if isinstance(chapters[idx], dict) else {}
            cs = float(ch.get("start", 0.0)) if ch else 0.0
            ce = float(ch.get("end", 0.0)) if ch else 0.0
            if ce > cs:
                return (float(cs), float(ce), str(ch.get("concept_label") or ch.get("concept") or ""), "teacher_concept_chapter_index")

            return (None, None, "", "none")
        except Exception:
            return (None, None, "", "error")

    def _expand_segment_with_subtitles(task_doc, start_sec, end_sec, min_span=24.0):
        """對齊字幕並擴展片段時長，避免只落在過短口語段。"""
        try:
            s = float(start_sec or 0.0)
            e = float(end_sec or 0.0)
            if e <= s:
                e = s + 12.0

            segs = _load_task_subtitle_segments(task_doc)
            if not segs:
                if (e - s) < min_span:
                    e = s + float(min_span)
                return (round(max(0.0, s), 2), round(max(s + 1.0, e), 2))

            parsed = []
            for seg in segs:
                ss = seg.get("start_sec")
                ee = seg.get("end_sec")
                if ss is None or ee is None:
                    continue
                parsed.append((float(ss), float(ee)))

            if not parsed:
                if (e - s) < min_span:
                    e = s + float(min_span)
                return (round(max(0.0, s), 2), round(max(s + 1.0, e), 2))

            parsed.sort(key=lambda x: x[0])

            nearest_start = min(parsed, key=lambda x: abs(x[0] - s))[0]
            nearest_end = min(parsed, key=lambda x: abs(x[1] - e))[1]
            s = max(0.0, nearest_start)
            e = max(s + 1.0, nearest_end)

            if (e - s) < min_span:
                target_end = s + float(min_span)
                for ss, ee in parsed:
                    if ss >= s and ee > e:
                        e = ee
                    if e >= target_end:
                        break
                if (e - s) < min_span:
                    e = target_end

            return (round(max(0.0, s), 2), round(max(s + 1.0, e), 2))
        except Exception:
            s = float(start_sec or 0.0)
            e = float(end_sec or 0.0)
            if e <= s:
                e = s + float(min_span)
            if (e - s) < min_span:
                e = s + float(min_span)
            return (round(max(0.0, s), 2), round(max(s + 1.0, e), 2))

    def _expand_review_window_for_learning(
        task_doc: dict,
        start_sec: float,
        end_sec: float,
        segment_source: str = "",
        segment_concept: str = "",
        wrong_index: Optional[int] = None,
        min_span_default: float = 10.0,
    ):
        """
        學生端專用：
        將短 anchor 擴成可學習的 review window。
        不改 teacher/ai segment map，只改 submit 回傳的 jump window。
        """
        try:
            s = float(start_sec or 0.0)
            e = float(end_sec or 0.0)
            if e <= s:
                e = s + float(min_span_default)

            concept = str(segment_concept or "").strip().lower()

            if concept == "input_int_cast":
                min_span = 12.0
            elif concept == "print_separator":
                min_span = 10.0
            elif concept in {"addition", "subtraction", "multiplication", "division"}:
                min_span = 8.0
            elif concept in {"python_syntax", "if_condition_logic", "if_branch_order", "edge_case_condition", "return"}:
                min_span = 8.0
            else:
                min_span = float(min_span_default)

            if (e - s) < min_span:
                e = s + min_span

            try:
                seg_map = task_doc.get("teacher_segment_map") or task_doc.get("ai_segment_map") or {}
                if isinstance(seg_map, dict) and isinstance(wrong_index, int) and wrong_index >= 0:
                    next_candidates = []
                    for k in [str(wrong_index + 1), f"s{wrong_index + 2}", f"第{wrong_index + 2}格"]:
                        seg = seg_map.get(k)
                        if not isinstance(seg, dict):
                            continue
                        ns = seg.get("start", seg.get("start_ts"))
                        if ns is None:
                            continue
                        try:
                            nsf = float(ns)
                        except Exception:
                            continue
                        if nsf > s:
                            next_candidates.append(nsf)
                    if next_candidates:
                        next_start = min(next_candidates)
                        if next_start > s + 1.0:
                            e = min(max(e, next_start - 0.2), s + 18.0)
            except Exception:
                pass

            try:
                segs = _load_task_subtitle_segments(task_doc)
                nearest_end = None
                best_dist = 10**9
                for seg in segs or []:
                    ee = seg.get("end_sec")
                    if ee is None:
                        ee = seg.get("end")
                    if ee is None:
                        continue
                    try:
                        eef = float(ee)
                    except Exception:
                        continue
                    if eef < s + 0.8:
                        continue
                    d = abs(eef - e)
                    if d < best_dist:
                        best_dist = d
                        nearest_end = eef
                if nearest_end is not None:
                    e = nearest_end
            except Exception:
                pass

            if e <= s:
                e = s + 1.2

            return (round(max(0.0, s), 2), round(max(s + 1.0, e), 2))

        except Exception:
            s = float(start_sec or 0.0)
            e = float(end_sec or 0.0)
            if e <= s:
                e = s + float(min_span_default)
            return (round(max(0.0, s), 2), round(max(s + 1.0, e), 2))

    def _refine_slot_segment_end(task_doc, slot_idx, start_sec, end_sec):
        """Keep slot hit focused: preserve start, tighten end to the current slot explanation window."""
        try:
            s = float(start_sec or 0.0)
            e = float(end_sec or 0.0)
            if e <= s:
                e = s + 4.0

            max_span = float(os.getenv("PARSONS_SLOT_MAX_SPAN") or "12")
            if max_span > 1.0 and (e - s) > max_span:
                e = s + max_span

            # Use next slot start as a hard boundary when available.
            seg_map = task_doc.get("ai_segment_map") or {}
            if isinstance(seg_map, dict) and isinstance(slot_idx, int) and slot_idx >= 0:
                next_candidates = []
                next_keys = [str(slot_idx + 1), f"s{slot_idx + 2}", f"第{slot_idx + 2}格"]
                for k in next_keys:
                    seg = seg_map.get(k)
                    if not isinstance(seg, dict):
                        continue
                    ns = seg.get("start", seg.get("start_ts"))
                    if ns is None:
                        continue
                    try:
                        nsf = float(ns)
                    except Exception:
                        continue
                    if nsf > s:
                        next_candidates.append(nsf)
                if next_candidates:
                    next_start = min(next_candidates)
                    if next_start > s + 1.0:
                        e = min(e, next_start - 0.2)

            # Snap end to nearest subtitle boundary to avoid awkward cut.
            segs = _load_task_subtitle_segments(task_doc)
            nearest_end = None
            best_dist = 10**9
            for seg in segs or []:
                ee = seg.get("end_sec")
                if ee is None:
                    ee = seg.get("end")
                if ee is None:
                    continue
                try:
                    eef = float(ee)
                except Exception:
                    continue
                if eef < s + 0.8:
                    continue
                d = abs(eef - e)
                if d < best_dist:
                    best_dist = d
                    nearest_end = eef
            if nearest_end is not None:
                e = nearest_end

            if e <= s:
                e = s + 1.2

            return (round(max(0.0, s), 2), round(max(s + 1.0, e), 2))
        except Exception:
            s = float(start_sec or 0.0)
            e = float(end_sec or 0.0)
            if e <= s:
                e = s + 1.2
            return (round(max(0.0, s), 2), round(max(s + 1.0, e), 2))

    def _operator_line_role(expected_line: str, actual_line: str) -> str:
        raw = (str(expected_line or "") + " " + str(actual_line or "")).strip().lower()
        if not raw:
            return "any"
        if raw.startswith(("if ", "elif ", "else")) or ("operator" in raw and ("==" in raw or "!=" in raw)):
            return "condition"
        if ("return" in raw) or ("print(" in raw) or ("回傳" in raw) or ("輸出" in raw):
            return "compute"
        return "any"

    def _operator_role_score(text: str, role_hint: str) -> int:
        t = str(text or "").strip().lower()
        if not t or role_hint == "any":
            return 0
        score = 0
        if role_hint == "condition":
            for kw in ["判斷", "符號", "是不是", "條件", "if", "elif", "operator", "==", "!="]:
                if kw in t:
                    score += 2
            for bad in ["回傳", "return", "print", "值"]:
                if bad in t:
                    score -= 1
        elif role_hint == "compute":
            for kw in ["回傳", "return", "print", "值", "除以", "乘以", "加", "減"]:
                if kw in t:
                    score += 2
            for bad in ["判斷", "條件", "if", "elif"]:
                if bad in t:
                    score -= 1
        return int(score)

    def _find_operator_anchor_segment(task_doc, wanted_ops, slot_idx=None, total_slots=1, role_hint="any"):
        """Find a subtitle segment that explicitly matches operator cues (+-*/, 中文詞) and nearest slot position."""
        try:
            segs = _load_task_subtitle_segments(task_doc)
            if not segs:
                return (None, None, "", 0)

            parsed = []
            for seg in segs:
                ss = seg.get("start_sec")
                ee = seg.get("end_sec")
                if ss is None:
                    ss = seg.get("start")
                if ee is None:
                    ee = seg.get("end")
                if ss is None or ee is None:
                    continue
                try:
                    s = float(ss)
                    e = float(ee)
                except Exception:
                    continue
                if e <= s:
                    continue
                txt = str(seg.get("text") or "")
                parsed.append((s, e, txt))

            if not parsed:
                return (None, None, "", 0)

            g_start = min(x[0] for x in parsed)
            g_end = max(x[1] for x in parsed)
            g_span = max(1e-6, g_end - g_start)
            target_ratio = None
            if isinstance(slot_idx, int) and slot_idx >= 0 and int(total_slots or 1) > 1:
                target_ratio = float(slot_idx) / float(max(1, int(total_slots) - 1))

            best = None
            best_key = None
            for s, e, txt in parsed:
                op_score = _calc_operator_score(txt, wanted_ops)
                if op_score <= 0:
                    continue
                role_score = _operator_role_score(txt, role_hint)
                center_ratio = ((s + e) / 2.0 - g_start) / g_span
                ratio_penalty = 0.0
                if target_ratio is not None:
                    ratio_penalty = abs(center_ratio - target_ratio)
                # Higher op_score and role_score are better; closer slot ratio and earlier time are better.
                key = (-float(op_score), -float(role_score), float(ratio_penalty), float(s))
                if best is None or key < best_key:
                    best = (s, e, txt, int(op_score), int(role_score))
                    best_key = key

            if best is None:
                return (None, None, "", 0, 0)
            return best
        except Exception:
            return (None, None, "", 0, 0)

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
        "review": {"student_choice": None},
        "is_first_wrong": False,
        "hint_click": False,
        "video_click": False,
        "hint_click_time": None,
        "video_click_time": None,
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

    video_review_enabled = _video_review_enabled()
    resp = {
        "ok": True,
        "attempt_id": attempt_id,
        "attempt_v2_id": v2_attempt_id,
        "task_attempt_session": v2_doc.get("task_attempt_session"),
        "attempt_no": v2_doc.get("attempt_no"),
        "attempt_sequence_no": v2_doc.get("attempt_sequence_no"),
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
    if video_review_enabled:
        resp["review_t"] = None

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
    user_profile_for_hint = db.users.find_one({"student_id": student_id}, {"group_type": 1}) or {}

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

    review_video_setting = str(os.getenv("PARSONS_REVIEW_VIDEO_ON_SUBMIT", "1")).strip().lower()
    review_video_on_submit = video_review_enabled and review_video_setting in {"1", "true", "yes", "on"}
    resp["review_video_hidden"] = not review_video_on_submit

    if not is_correct and not review_video_on_submit:
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
                }}
            )
        except Exception:
            pass
        return jsonify(resp)

    def _classify_error_taxonomy(local_error_type: str, actual_line: str, expected_line: str, local_segment_concept: str = ""):
        et = str(local_error_type or "logic").strip().lower()
        seg_c = str(local_segment_concept or "").strip().lower()
        a = str(actual_line or "").strip().lower()
        e = str(expected_line or "").strip().lower()

        if et == "indentation":
            return "indentation_mismatch", "syntax", "學生尚未掌握 Python 區塊縮排與控制流程關係"
        if et == "if_else":
            return "branch_mapping_error", "logic", "學生混淆 if/else 分支在條件成立與不成立時的對應"
        if et == "calculation":
            return "calculation_mismatch", "logic", "學生在運算式或計算步驟上發生語意錯誤"
        if et == "structure":
            return "main_order_error", "structure", "學生對主程式流程與函式呼叫順序的關係尚未穩定"
        if seg_c in {"input", "assignment"}:
            return "input_or_assignment_error", "logic", "學生對輸入、型別或初始值設定流程仍不穩定"
        if seg_c in {"loop", "condition", "logic"}:
            return "control_flow_error", "logic", "學生對控制流程順序與執行條件理解不足"
        if ("print(" in a) or ("print(" in e):
            return "output_style_mismatch", "logic", "學生尚未掌握運算結果與輸出位置的關係"
        return "generic_slot_misplacement", "logic", "學生對程式區塊角色與先後順序尚未建立穩定心智模型"

    if not is_correct:
        slot_key = str(wrong_index) if wrong_index is not None else "0"
        
        # [新增] 若是縮排錯誤，特別說明
        if indent_errors and wrong_index is not None and wrong_index in indent_errors:
            if wrong_index < len(answer_lines):
                exp_indent = int(expected_indent_list[wrong_index] or 0) if wrong_index < len(expected_indent_list) else 0
                user_indent = len(answer_lines[wrong_index]) - len(answer_lines[wrong_index].lstrip(" "))
                if user_indent < exp_indent:
                    feedback = f"❌ 你錯在：第{(wrong_index + 1)}格\n\n縮排不足：你的程式碼沒有正確的縮排。"  + f"\n\n預期縮排：{exp_indent} 空格\n你的縮排：{user_indent} 空格\n\n請檢查第 {(wrong_index + 1)} 格的縮排是否符合需求。"
                else:
                    feedback = f"❌ 你錯在：第{(wrong_index + 1)}格\n\n縮排過多：你的程式碼縮排超過預期。"  + f"\n\n預期縮排：{exp_indent} 空格\n你的縮排：{user_indent} 空格\n\n請檢查第 {(wrong_index + 1)} 格的縮排是否符合需求。"

        t_start = None
        t_end = None
        subtitle_context = ""
        segment_source = "none"
        segment_concept = ""
        alignment_score = 0.0
        alignment_debug = {}
        alignment_trace = []

        wrong_slot_concept = ""
        concept_window = None
        slot_concept_map_local = {}
        try:
            slot_concept_map_local = _derive_slot_concept_map(task)
            if wrong_index is not None:
                wrong_slot_concept = str(slot_concept_map_local.get(str(int(wrong_index))) or "").strip().lower()
            concept_map_local = _derive_concept_segment_map(task, slot_concept_map_local)
            segc = concept_map_local.get(wrong_slot_concept) if wrong_slot_concept else None
            if isinstance(segc, dict):
                cs = float(segc.get("start", 0.0))
                ce = float(segc.get("end", 0.0))
                if ce > cs:
                    concept_window = (cs, ce)
        except Exception:
            wrong_slot_concept = ""
            concept_window = None
            slot_concept_map_local = {}

        def _seg_overlap(a_s: float, a_e: float, b_s: float, b_e: float) -> float:
            return max(0.0, min(float(a_e), float(b_e)) - max(float(a_s), float(b_s)))

        def _clip_to_concept_window(start_s: float, end_s: float):
            if concept_window is None:
                return float(start_s), float(end_s), "no_concept_window"
            cws, cwe = concept_window
            ov = _seg_overlap(float(start_s), float(end_s), float(cws), float(cwe))
            if ov <= 0.0:
                return None, None, "outside_concept_window"
            ns = max(float(start_s), float(cws))
            ne = min(float(end_s), float(cwe))
            if ne <= ns:
                return None, None, "outside_concept_window"
            if round(ns, 2) == round(float(start_s), 2) and round(ne, 2) == round(float(end_s), 2):
                return float(ns), float(ne), "inside_concept_window"
            return float(ns), float(ne), "clipped_to_concept_window"

        def _overlap_conflict_with_other_slots(start_s: float, end_s: float):
            base_map = task.get("teacher_segment_map") or task.get("ai_segment_map") or {}
            if not isinstance(base_map, dict) or not base_map:
                return False, "", {}
            try:
                dur = max(0.0, float(end_s) - float(start_s))
            except Exception:
                return False, "", {}
            if dur <= 0:
                return False, "", {}

            max_diff_overlap = 0.0
            max_same_overlap = 0.0
            max_diff_slot = None
            max_same_slot = None
            for kk, vv in base_map.items():
                if not isinstance(vv, dict):
                    continue
                idx0 = _safe_slot_index(str(kk))
                if idx0 is None:
                    continue
                if wrong_index is not None and int(idx0) == int(wrong_index):
                    continue

                ss = vv.get("start", vv.get("start_ts"))
                ee = vv.get("end", vv.get("end_ts"))
                if ss is None or ee is None:
                    continue
                try:
                    ssf = float(ss)
                    eef = float(ee)
                except Exception:
                    continue
                if eef <= ssf:
                    continue

                ov = _seg_overlap(float(start_s), float(end_s), ssf, eef)
                if ov <= 0.0:
                    continue

                other_concept = str(slot_concept_map_local.get(str(idx0)) or "").strip().lower()
                if wrong_slot_concept and other_concept and other_concept != wrong_slot_concept:
                    if ov > max_diff_overlap:
                        max_diff_overlap = ov
                        max_diff_slot = idx0
                elif wrong_slot_concept and other_concept and other_concept == wrong_slot_concept:
                    if ov > max_same_overlap:
                        max_same_overlap = ov
                        max_same_slot = idx0

            diff_forbid = max(0.5, dur * 0.12)
            same_allow = max(0.6, min(2.0, dur * 0.28))
            if max_diff_overlap >= diff_forbid:
                return True, "cross_concept_overlap", {
                    "overlap": round(float(max_diff_overlap), 3),
                    "slot": max_diff_slot,
                    "threshold": round(float(diff_forbid), 3),
                }
            if max_same_overlap > same_allow:
                return True, "same_concept_large_overlap", {
                    "overlap": round(float(max_same_overlap), 3),
                    "slot": max_same_slot,
                    "threshold": round(float(same_allow), 3),
                }
            return False, "", {}

        # ① 後端已判定主錯誤格後，產生短句＋反思問題回饋。
        if primary_error_type == "indentation":
            err_for_feedback = "indentation"
        elif primary_error_type == "condition" or (str(actual_text or "").strip().lower().startswith("else")
              or str(expected_text or "").strip().lower().startswith("else")):
            err_for_feedback = "if_else"
        elif primary_error_type == "calculation":
            err_for_feedback = "calculation"
        elif primary_error_type == "structure":
            err_for_feedback = "structure"
        else:
            err_for_feedback = "logic"

        ai_diag = _build_short_reflective_feedback(
            task=task,
            slot_label=(slot_label or f"第{(wrong_index + 1)}格" if wrong_index is not None else "第1格"),
            expected_text=expected_text,
            actual_text=actual_text,
            error_type=err_for_feedback,
        )
        ai_feedback_detail = (ai_diag.get("feedback") or {}) if isinstance(ai_diag, dict) else {}

        resp["ai_feedback_detail"] = ai_feedback_detail
        resp["ai_diagnosis_summary"] = ai_diag.get("diagnosis_summary", "") if isinstance(ai_diag, dict) else ""

        hint = (
            ai_feedback_detail.get("concept_explanation")
            or ai_feedback_detail.get("guiding_question")
            or ""
        )

        # ② Structured mapping (primary): wrong slot -> concept -> fixed segment.
        mapped_s, mapped_e, mapped_concept, mapped_reason = _segment_from_concept_mapping(
            task,
            (wrong_index if wrong_index is not None else 0),
        )
        if mapped_s is not None and mapped_e is not None and mapped_e > mapped_s:
            t_start = float(mapped_s)
            t_end = float(mapped_e)
            segment_source = "slot_mapping"
            segment_concept = str(mapped_concept or "")
            alignment_trace.append({
                "step": "slot_mapping_applied",
                "segment_source": segment_source,
                "slot_index": (wrong_index if wrong_index is not None else 0),
                "concept": segment_concept,
                "reason": mapped_reason,
                "start": t_start,
                "end": t_end,
            })

        # ③ 優先用 IR 依 wrong slot 做定位（AI 不再主導時間軸）。
        ir_seg = None
        ir_score = 0.0
        ir_attempted = False
        if (t_start is None or t_end is None or t_end <= t_start) and subtitle_index_ready and wrong_index is not None:
            ir_attempted = True
            try:
                ir_seg, ir_score = retrieve_segment_for_wrong_slot(task, wrong_index, subtitle_index)
                alignment_score = float(ir_score)
            except Exception:
                ir_seg, ir_score = None, 0.0
                alignment_score = 0.0

        if ir_attempted and ir_seg and float(ir_score) >= float(ir_soft_threshold):
            t_start = float(ir_seg.get("start", 0.0))
            t_end = float(ir_seg.get("end", 0.0))
            subtitle_context = str(ir_seg.get("text") or "")
            segment_source = "ir_slot_retrieval" if float(ir_score) >= float(ir_score_threshold) else "ir_slot_retrieval_low_confidence"
            if isinstance(ir_seg, dict):
                alignment_debug["ir_top_k_count"] = int(ir_seg.get("top_k_count") or 1)
                alignment_debug["ir_top_k_scores"] = [float(x) for x in (ir_seg.get("top_k_scores") or [])]
                alignment_debug["ir_op_concept"] = str(ir_seg.get("op_concept") or "")
                alignment_debug["ir_slot_role"] = str(ir_seg.get("slot_role") or "")
                alignment_debug["ir_slot_concept"] = str(ir_seg.get("slot_concept") or "")
                alignment_debug["ir_rerank_strategy"] = str(ir_seg.get("rerank_strategy") or "")
                if isinstance(ir_seg.get("rerank_detail"), dict):
                    alignment_debug["ir_rerank_detail"] = ir_seg.get("rerank_detail")
            alignment_trace.append({
                "step": "ir_slot_retrieval_applied",
                "segment_source": segment_source,
                "slot_index": (wrong_index if wrong_index is not None else 0),
                "score": round(float(ir_score), 4),
                "threshold": float(ir_score_threshold),
                "soft_threshold": float(ir_soft_threshold),
                "top_k_count": int((ir_seg or {}).get("top_k_count") or 1),
                "op_concept": str((ir_seg or {}).get("op_concept") or ""),
                "start": t_start,
                "end": t_end,
            })
        elif ir_attempted:
            alignment_trace.append({
                "step": "ir_slot_retrieval_below_threshold",
                "slot_index": (wrong_index if wrong_index is not None else 0),
                "score": round(float(ir_score or 0.0), 4),
                "threshold": float(ir_score_threshold),
                "soft_threshold": float(ir_soft_threshold),
                "cache_ready": bool(subtitle_index_ready),
            })

        # AI 只負責解釋（以選中片段字幕內容為上下文），不負責決定時間。
        if request_ai_feedback and subtitle_context:
            try:
                explain_data = parsons_ai.call_openai_json(
                    model=_model_for_feedback(),
                    system="你是 Python Parsons 題助教。只輸出 JSON。",
                    user=(
                        "請根據學生錯誤與字幕上下文，產生一句精簡概念提示（繁體中文，不要給完整答案）。\n"
                        f"錯誤格：{slot_label or f'第{(wrong_index + 1)}格'}\n"
                        f"預期程式：{expected_text}\n"
                        f"學生程式：{actual_text}\n"
                        f"字幕上下文：{subtitle_context}\n"
                        "輸出 JSON：{\"hint\":\"...\"}"
                    ),
                ) or {}
                hinted = str(explain_data.get("hint") or "").strip()
                if hinted:
                    hint = hinted
            except Exception:
                pass

                # ④ 只有 mapping/IR 都不足時，AI 僅產生提示，不再決定時間。
        if (t_start is None or t_end is None or t_end <= t_start) and primary_error_type != "indentation":
            if primary_error_type == "condition" or (str(actual_text or "").strip().lower().startswith("else")
                  or str(expected_text or "").strip().lower().startswith("else")):
                error_type = "if_else"
            elif primary_error_type == "calculation":
                error_type = "calculation"
            else:
                error_type = "logic"

            hint2, s2, e2, subtitle_context2, concept2, align_debug2 = ai_hint_and_segment_for_wrong(
                task=task,
                slot_key=slot_key,
                expected_text=expected_text,
                actual_text=actual_text,
                level=(level or task.get("level") or "L1"),
                slot_label=(slot_label or f"第{(wrong_index + 1)}格" if wrong_index is not None else "第1格"),
                error_type=error_type,
                allow_ai=request_ai_feedback,
            )
            if not hint:
                hint = hint2
            if (not segment_concept) and concept2:
                segment_concept = str(concept2 or "")
            if isinstance(align_debug2, dict):
                alignment_debug["ai_concept_hint_debug"] = align_debug2
            alignment_trace.append({
                "step": "concept_hint_only",
                "segment_source": segment_source,
                "concept": segment_concept,
                "start": None,
                "end": None,
                "reason": (alignment_debug.get("concept_search") or {}).get("reason") if isinstance(alignment_debug, dict) else None,
            })

        alignment_debug["wrong_type"] = slot_wrong_type
        alignment_debug["concept_tag"] = slot_concept_tag

        # ⑤ 若概念搜尋仍不足，再用 task 內 fallback
        need_fallback = False
        if t_start is None or t_end is None or t_end <= t_start:
            need_fallback = True
        elif t_start == 0 and wrong_index is not None and wrong_index != 0:
            need_fallback = True

        alignment_trace.append({
            "step": "before_task_fallback",
            "need_fallback": bool(need_fallback),
            "wrong_index": wrong_index,
            "primary_error_type": primary_error_type,
            "slot_key": slot_key,
        })

        if need_fallback:
            reject_fallback = False
            reject_reason = ""

            # 若 IR 已命中（即使低分），優先保留 slot-level 命中，不再用 generic task fallback 覆蓋。
            if segment_source in {"ir_slot_retrieval", "ir_slot_retrieval_low_confidence"}:
                reject_fallback = True
                reject_reason = "keep_ir_hit"

            # 若 ai_segment_map 幾乎全格同一段，表示是粗粒度映射，不能直接採用。
            try:
                if _is_generic_uniform_slot_map(task.get("ai_segment_map") or {}):
                    reject_fallback = True
                    reject_reason = "uniform_slot_map"
            except Exception:
                pass

            use_ai_map_fallback = True
            try:
                # IR 無法命中時，優先避免再落回可疑 ai_segment_map（常見固定跳前段問題）。
                if wrong_index is not None and wrong_index >= 1:
                    use_ai_map_fallback = False
            except Exception:
                use_ai_map_fallback = True

            fb_start, fb_end, fb_ctx, fb_src = _fallback_segment_from_task(
                task,
                wrong_index if wrong_index is not None else 0,
                allow_ai_segment_map=use_ai_map_fallback,
            )

            # 計算題再多一層保護：fallback 字幕若與目標運算子不符，強制改走概念搜尋。
            if (not reject_fallback) and primary_error_type == "calculation":
                try:
                    wanted_ops = _detect_calc_ops(expected_text) or _detect_calc_ops(actual_text)
                    if wanted_ops:
                        op_score = _calc_operator_score(fb_ctx, wanted_ops)
                        if op_score <= 0:
                            reject_fallback = True
                            reject_reason = "fallback_operator_mismatch"
                            alignment_debug["fallback_operator_score"] = int(op_score)
                            alignment_debug["fallback_wanted_ops"] = wanted_ops
                except Exception:
                    pass

            if (not reject_fallback) and fb_start is not None and fb_end is not None and fb_end > fb_start:
                t_start = fb_start
                t_end = fb_end
                segment_source = "task_fallback"
                alignment_debug["task_fallback_source"] = str(fb_src or "")
                if not subtitle_context:
                    subtitle_context = fb_ctx
                alignment_trace.append({
                    "step": "task_fallback_applied",
                    "segment_source": segment_source,
                    "fallback_source": fb_src,
                    "start": t_start,
                    "end": t_end,
                })
            elif reject_fallback:
                alignment_debug["task_fallback_rejected"] = True
                alignment_debug["task_fallback_reject_reason"] = reject_reason or "rejected"
                alignment_trace.append({
                    "step": "task_fallback_rejected",
                    "reason": (reject_reason or "rejected"),
                })

        # 縮排錯誤：若 mapping/slot retrieval 仍不足，AI 僅補提示，不改時間。
        if primary_error_type == "indentation" and (t_start is None or t_end is None or t_end <= t_start):
            error_type = "indentation"
            hint2, s2, e2, subtitle_context2, concept2, align_debug2 = ai_hint_and_segment_for_wrong(
                task=task,
                slot_key=slot_key,
                expected_text=expected_text,
                actual_text=actual_text,
                level=(level or task.get("level") or "L1"),
                slot_label=(slot_label or f"第{(wrong_index + 1)}格" if wrong_index is not None else "第1格"),
                error_type=error_type,
                allow_ai=request_ai_feedback,
            )
            if not hint:
                hint = hint2
            if (not segment_concept) and concept2:
                segment_concept = str(concept2 or "")
            if isinstance(align_debug2, dict):
                alignment_debug["ai_concept_hint_debug"] = align_debug2
            alignment_trace.append({
                "step": "concept_hint_only_after_slot_fallback",
                "segment_source": segment_source,
                "concept": segment_concept,
                "start": None,
                "end": None,
                "reason": (alignment_debug.get("concept_search") or {}).get("reason") if isinstance(alignment_debug, dict) else None,
            })

        # 防重覆修復：若多個 slot 落在同一時間窗，優先用 IR slot retrieval 重新拉開。
        def _dup_count_in_seg_map(seg_map_obj: dict, start_s: float, end_s: float) -> int:
            if not isinstance(seg_map_obj, dict):
                return 0
            rs = round(float(start_s), 1)
            re = round(float(end_s), 1)
            c = 0
            for _, vv in seg_map_obj.items():
                if not isinstance(vv, dict):
                    continue
                ss = vv.get("start", vv.get("start_ts"))
                ee = vv.get("end", vv.get("end_ts"))
                if ss is None or ee is None:
                    continue
                try:
                    ssv = round(float(ss), 1)
                    eev = round(float(ee), 1)
                except Exception:
                    continue
                if ssv == rs and eev == re:
                    c += 1
            return c

        if (
            t_start is not None and t_end is not None and t_end > t_start
            and subtitle_index_ready and wrong_index is not None
        ):
            try:
                base_map = task.get("teacher_segment_map") or task.get("ai_segment_map") or {}
                dup_cnt = _dup_count_in_seg_map(base_map, float(t_start), float(t_end))
                alignment_debug["dup_range_count"] = int(dup_cnt)

                # 同一時間窗被 3 格以上共用時，視為可疑對齊，嘗試 IR 修復。
                if dup_cnt >= 3:
                    rep_seg, rep_score = retrieve_segment_for_wrong_slot(task, int(wrong_index), subtitle_index)
                    if rep_seg and float(rep_score or 0.0) >= float(ir_soft_threshold):
                        ns = float(rep_seg.get("start", 0.0))
                        ne = float(rep_seg.get("end", 0.0))
                        if ne > ns and (round(ns, 1), round(ne, 1)) != (round(float(t_start), 1), round(float(t_end), 1)):
                            t_start, t_end = ns, ne
                            subtitle_context = str(rep_seg.get("text") or subtitle_context or "")
                            segment_source = "ir_dedup_repair"
                            alignment_trace.append({
                                "step": "duplicate_segment_repaired",
                                "segment_source": segment_source,
                                "slot_index": int(wrong_index),
                                "old_dup_count": int(dup_cnt),
                                "score": round(float(rep_score), 4),
                                "start": float(t_start),
                                "end": float(t_end),
                            })
            except Exception:
                pass

        # Concept hard gate + overlap policy：
        # 1) 選中片段必須落在該 slot concept 時窗；
        # 2) 跨 concept 不可重疊；同 concept 僅允許小幅重疊。
        if t_start is not None and t_end is not None and t_end > t_start and wrong_index is not None:
            try:
                if wrong_slot_concept and (not segment_concept):
                    segment_concept = str(wrong_slot_concept)

                g_s, g_e, g_state = _clip_to_concept_window(float(t_start), float(t_end))
                if g_state == "clipped_to_concept_window" and g_s is not None and g_e is not None and g_e > g_s:
                    alignment_trace.append({
                        "step": "concept_window_clipped",
                        "segment_source": segment_source,
                        "concept": wrong_slot_concept,
                        "before_start": float(t_start),
                        "before_end": float(t_end),
                        "start": float(g_s),
                        "end": float(g_e),
                    })
                    t_start, t_end = float(g_s), float(g_e)

                if g_state == "outside_concept_window":
                    alignment_debug["concept_hard_gate_reject"] = True
                    alignment_debug["concept_hard_gate_reason"] = "outside_concept_window"

                    rep_seg2 = None
                    rep_score2 = 0.0
                    if subtitle_index_ready:
                        rep_seg2, rep_score2 = retrieve_segment_for_wrong_slot(task, int(wrong_index), subtitle_index)

                    if rep_seg2 and float(rep_score2 or 0.0) >= float(ir_soft_threshold):
                        ns2 = float(rep_seg2.get("start", 0.0))
                        ne2 = float(rep_seg2.get("end", 0.0))
                        rg_s, rg_e, rg_state = _clip_to_concept_window(ns2, ne2)
                        if rg_s is not None and rg_e is not None and rg_e > rg_s:
                            t_start, t_end = float(rg_s), float(rg_e)
                            subtitle_context = str(rep_seg2.get("text") or subtitle_context or "")
                            segment_source = "ir_concept_gate_repair"
                            alignment_trace.append({
                                "step": "concept_window_repaired_by_ir",
                                "segment_source": segment_source,
                                "concept": wrong_slot_concept,
                                "score": round(float(rep_score2), 4),
                                "clip_state": rg_state,
                                "start": float(t_start),
                                "end": float(t_end),
                            })
                        elif concept_window is not None:
                            t_start, t_end = float(concept_window[0]), float(concept_window[1])
                            segment_source = "concept_window_fallback"
                            alignment_trace.append({
                                "step": "concept_window_fallback_applied",
                                "segment_source": segment_source,
                                "concept": wrong_slot_concept,
                                "start": float(t_start),
                                "end": float(t_end),
                            })
                    elif concept_window is not None:
                        t_start, t_end = float(concept_window[0]), float(concept_window[1])
                        segment_source = "concept_window_fallback"
                        alignment_trace.append({
                            "step": "concept_window_fallback_applied",
                            "segment_source": segment_source,
                            "concept": wrong_slot_concept,
                            "start": float(t_start),
                            "end": float(t_end),
                        })

                has_conflict, conflict_reason, conflict_meta = _overlap_conflict_with_other_slots(float(t_start), float(t_end))
                if has_conflict:
                    alignment_debug["overlap_policy_conflict"] = {
                        "reason": conflict_reason,
                        **(conflict_meta or {}),
                    }
                    rep_seg3 = None
                    rep_score3 = 0.0
                    if subtitle_index_ready:
                        rep_seg3, rep_score3 = retrieve_segment_for_wrong_slot(task, int(wrong_index), subtitle_index)

                    repaired = False
                    if rep_seg3 and float(rep_score3 or 0.0) >= float(ir_soft_threshold):
                        rs3 = float(rep_seg3.get("start", 0.0))
                        re3 = float(rep_seg3.get("end", 0.0))
                        cg_s, cg_e, _ = _clip_to_concept_window(rs3, re3)
                        if cg_s is not None and cg_e is not None and cg_e > cg_s:
                            c2, _, _ = _overlap_conflict_with_other_slots(float(cg_s), float(cg_e))
                            if not c2:
                                t_start, t_end = float(cg_s), float(cg_e)
                                subtitle_context = str(rep_seg3.get("text") or subtitle_context or "")
                                segment_source = "ir_overlap_policy_repair"
                                repaired = True
                                alignment_trace.append({
                                    "step": "overlap_policy_repaired_by_ir",
                                    "segment_source": segment_source,
                                    "reason": conflict_reason,
                                    "score": round(float(rep_score3), 4),
                                    "start": float(t_start),
                                    "end": float(t_end),
                                })

                    if (not repaired) and concept_window is not None:
                        cw_s, cw_e = float(concept_window[0]), float(concept_window[1])
                        c3, _, _ = _overlap_conflict_with_other_slots(cw_s, cw_e)
                        if not c3 and cw_e > cw_s:
                            t_start, t_end = cw_s, cw_e
                            segment_source = "concept_overlap_fallback"
                            repaired = True
                            alignment_trace.append({
                                "step": "overlap_policy_fallback_to_concept_window",
                                "segment_source": segment_source,
                                "reason": conflict_reason,
                                "start": float(t_start),
                                "end": float(t_end),
                            })

                    if not repaired:
                        alignment_trace.append({
                            "step": "overlap_policy_conflict_unresolved",
                            "segment_source": segment_source,
                            "reason": conflict_reason,
                            "start": float(t_start),
                            "end": float(t_end),
                        })
            except Exception:
                pass

        error_code, error_type_label, misconception = _classify_error_taxonomy(
            local_error_type=(error_type if 'error_type' in locals() else "logic"),
            actual_line=actual_text,
            expected_line=expected_text,
            local_segment_concept=segment_concept,
        )

        resp["error_code"] = error_code
        resp["error_type"] = error_type_label
        resp["misconception"] = misconception

        print("DEBUG_SEGMENT:", t_start, t_end)

        # =========================
        # ⑥ 再不行，先吃老師確認章節時間軸
        # =========================
        if t_start is None or t_end is None or t_end <= t_start:
            ts_ch, te_ch, ctx_ch, src_ch = _fallback_segment_from_teacher_chapters(
                task,
                int(wrong_index) if wrong_index is not None else 0,
                wrong_slot_concept,
            )
            if ts_ch is not None and te_ch is not None and te_ch > ts_ch:
                t_start = float(ts_ch)
                t_end = float(te_ch)
                if str(ctx_ch or "").strip():
                    subtitle_context = str(ctx_ch)
                segment_source = str(src_ch or "teacher_concept_chapter")
                alignment_trace.append({
                    "step": "teacher_concept_chapter_applied",
                    "segment_source": segment_source,
                    "start": t_start,
                    "end": t_end,
                })

        # =========================
        # ⑦ 再不行，吃 subtitle_range / source_subtitle
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
                    segment_source = "subtitle_range"
                    alignment_trace.append({
                        "step": "subtitle_range_applied",
                        "segment_source": segment_source,
                        "start": t_start,
                        "end": t_end,
                    })
            except Exception:
                pass

        # =========================
        # ⑧ 最後最後才用固定保底值 (已移除 fake stability，改為 unresolved)
        # =========================
        if t_start is None or t_end is None or t_end <= t_start:
            t_start = 0.0
            t_end = 0.0
            segment_source = "unresolved"
            alignment_trace.append({
                "step": "unresolved_applied",
                "segment_source": segment_source,
                "start": t_start,
                "end": t_end,
            })

        # 運算子錨點保護：只要錯格有運算子訊號（含 elif operator == '/'），就可覆寫到正確運算子講解段。
        if t_start is not None and t_end is not None and t_end > t_start and primary_error_type != "indentation":
            try:
                wanted_ops = _detect_calc_ops(expected_text) or _detect_calc_ops(actual_text)
                if (not wanted_ops) and wrong_index is not None:
                    try:
                        sb = (task.get("solution_blocks") or [])
                        if 0 <= int(wrong_index) < len(sb):
                            blk = sb[int(wrong_index)] or {}
                            extra_text = (
                                str(blk.get("text") or "")
                                + " "
                                + str(blk.get("semantic_zh") or blk.get("meaning_zh") or "")
                            )
                            wanted_ops = _detect_calc_ops(extra_text)
                    except Exception:
                        pass
                if wanted_ops:
                    current_ctx = subtitle_context or ""
                    if not current_ctx:
                        try:
                            segs_now = _load_task_subtitle_segments(task)
                            if segs_now:
                                current_ctx = extract_context_around(segs_now, float(t_start), float(t_end), window=3)
                        except Exception:
                            current_ctx = ""
                    cur_score = _calc_operator_score(current_ctx or "", wanted_ops)
                    line_role = _operator_line_role(expected_text, actual_text)
                    cur_role_score = _operator_role_score(current_ctx or "", line_role)
                    alignment_debug["selected_operator_score"] = int(cur_score)
                    alignment_debug["selected_operator_role"] = str(line_role)
                    alignment_debug["selected_operator_role_score"] = int(cur_role_score)

                    total_slots_local = max(1, len(task.get("template_slots") or task.get("solution_blocks") or []))
                    op_s, op_e, op_ctx, op_score, op_role_score = _find_operator_anchor_segment(
                        task,
                        wanted_ops,
                        slot_idx=(wrong_index if wrong_index is not None else 0),
                        total_slots=total_slots_local,
                        role_hint=line_role,
                    )

                    should_override = False
                    if op_s is not None and op_e is not None and op_e > op_s:
                        if cur_score <= 0:
                            should_override = True
                        elif int(op_role_score) > int(cur_role_score) + 1:
                            should_override = True
                        elif str(line_role) == "condition" and float(op_s) + 0.8 < float(t_start):
                            # condition lines should prefer the earlier "判斷符號" segment, not later return segment.
                            should_override = True

                    if should_override:
                        prev_s, prev_e = float(t_start), float(t_end)
                        t_start = float(op_s)
                        t_end = float(op_e)
                        subtitle_context = op_ctx or subtitle_context
                        segment_source = "operator_anchor_override"
                        alignment_trace.append({
                            "step": "operator_anchor_override",
                            "segment_source": segment_source,
                            "wanted_ops": wanted_ops,
                            "operator_score": int(op_score),
                            "operator_role": str(line_role),
                            "operator_role_score": int(op_role_score),
                            "before_start": prev_s,
                            "before_end": prev_e,
                            "start": t_start,
                            "end": t_end,
                        })
            except Exception:
                pass

        # Slot-focused 收斂：保留命中起點，縮緊結束點到該錯格的講解區間。
        if t_start is not None and t_end is not None and t_end > t_start:
            before_s = float(t_start)
            before_e = float(t_end)
            t_start, t_end = _refine_slot_segment_end(
                task,
                (wrong_index if wrong_index is not None else 0),
                t_start,
                t_end,
            )
            alignment_trace.append({
                "step": "slot_window_refined",
                "segment_source": segment_source,
                "before_start": before_s,
                "before_end": before_e,
                "start": t_start,
                "end": t_end,
            })

        # 最終回看片段：anchor 對齊後，仍需擴成可學習視窗。
        disable_expand = str(os.getenv("PARSONS_DISABLE_EXPAND") or "").strip().lower() in {"1", "true", "yes", "on"}

        # task_fallback 仍保守處理；其餘 slot_mapping / IR 命中都允許擴窗。
        if segment_source == "task_fallback":
            disable_expand = True

        if primary_error_type == "indentation":
            min_span = 8.0
        else:
            min_span = 10.0

        if disable_expand:
            alignment_trace.append({
                "step": "expand_review_window_skipped",
                "segment_source": segment_source,
                "start": t_start,
                "end": t_end,
            })
        else:
            before_s = float(t_start) if t_start is not None else None
            before_e = float(t_end) if t_end is not None else None

            t_start, t_end = _expand_review_window_for_learning(
                task_doc=task,
                start_sec=t_start,
                end_sec=t_end,
                segment_source=segment_source,
                segment_concept=segment_concept,
                wrong_index=(wrong_index if wrong_index is not None else None),
                min_span_default=min_span,
            )

            alignment_trace.append({
                "step": "expand_review_window_for_learning",
                "segment_source": segment_source,
                "segment_concept": segment_concept,
                "wrong_type": slot_wrong_type,
                "concept_tag": slot_concept_tag,
                "before_start": before_s,
                "before_end": before_e,
                "start": t_start,
                "end": t_end,
                "min_span": min_span,
            })

        # 若主錯誤格與相鄰錯格屬於同 concept，合併成較完整的回看片段。
        try:
            merged_s, merged_e, merged_meta = _maybe_merge_adjacent_wrong_segments(
                task_doc=task,
                wrong_indices=wrong_indices,
                primary_wrong_idx=(wrong_index if wrong_index is not None else None),
                current_start=t_start,
                current_end=t_end,
            )
            if merged_s is not None and merged_e is not None and merged_e > merged_s:
                t_start, t_end = float(merged_s), float(merged_e)
                segment_source = "adjacent_merge"
                alignment_trace.append({
                    "step": "adjacent_same_concept_merged",
                    "segment_source": segment_source,
                    "meta": merged_meta,
                    "start": float(t_start),
                    "end": float(t_end),
                })
        except Exception:
            pass

        if not hint:
            hint = "請重新檢查這一格在整體程式流程中的角色。"

        resp["review_t"] = int(float(t_start))
        resp["hint"] = hint

        raw_vid = task.get("video_id") or data.get("video_id") or ""
        vid = normalize_video_id(raw_vid)

        resp["jump"] = {"video_id": vid, "start": float(t_start), "end": float(t_end)}
        resp["recommended_review"] = {
            "start": float(t_start),
            "end": float(t_end),
            "reason": (
                "依 slot 概念映射定位此片段"
                if segment_source == "slot_mapping"
                else (
                    "依規則守門 + IR top-k 重排定位此片段"
                    if segment_source in {"ir_slot_retrieval", "ir_slot_retrieval_low_confidence"}
                    else "IR 未命中，改用 deterministic fallback 對齊"
                )
            ),
        }
        resp["segment_source"] = segment_source
        resp["segment_concept"] = segment_concept
        resp["alignment_score"] = round(float(alignment_score or 0.0), 4)
        resp["wrong_index_debug"] = {
            "wrong_index": wrong_index,
            "slot_label": slot_label,
            "expected_id": expected_id,
            "actual_id": actual_id,
            "expected_text": expected_text,
            "actual_text": actual_text,
        }
        resp["alignment_debug"] = alignment_debug
        resp["alignment_trace"] = alignment_trace
        resp["subtitle_context_preview"] = (subtitle_context or "")[:120]
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

        subtitle_health = _build_subtitle_health_report(task)
        resp["subtitle_health"] = subtitle_health

        chapter_recommendation = {}
        try:
            chapter_recommendation = _pick_chapter_recommendation(
                task=task,
                wrong_type=slot_wrong_type,
                concept_tag=slot_concept_tag,
                slot_concept=segment_concept,
            )
        except Exception:
            chapter_recommendation = {}

        if isinstance(chapter_recommendation, dict) and chapter_recommendation:
            resp["chapter_recommendation"] = chapter_recommendation
            resp["review_mode"] = "chapter_recommendation" if str((subtitle_health or {}).get("mode") or "").strip() == "chapter_recommendation" else "subtitle_alignment"
        else:
            resp["chapter_recommendation"] = {}
            resp["review_mode"] = "subtitle_alignment"

        # 將本次片段選擇依據落盤，方便老師端/資料庫快速追蹤對齊品質。
        if video_review_enabled:
            try:
                db.parsons_attempts.update_one(
                    {"_id": legacy_attempt_oid},
                    {"$set": {
                        "segment_source": segment_source,
                        "segment_concept": segment_concept,
                        "alignment_score": round(float(alignment_score or 0.0), 4),
                        "jump_start": float(t_start),
                        "jump_end": float(t_end),
                        "alignment_debug": alignment_debug,
                        "subtitle_context_preview": (subtitle_context or "")[:120],
                        "error_code": error_code,
                        "error_type": error_type_label,
                        "misconception": misconception,
                    }}
                )
            except Exception:
                pass

    return jsonify(resp)


@parsons_bp.post("/fixed_task/segment_override")
def save_fixed_task_segment_override():
    """Teacher manual correction for slot->segment mapping.
    submit path prioritizes teacher_segment_map over ai_segment_map.
    """
    if not _video_review_enabled():
        return _video_review_disabled_response()

    data = request.get_json(silent=True) or {}
    task_id = str(data.get("task_id") or "").strip()
    if not task_id:
        return jsonify({"ok": False, "message": "missing task_id"}), 400

    try:
        tid = ObjectId(task_id)
    except Exception:
        return jsonify({"ok": False, "message": "invalid task_id"}), 400

    task = db.parsons_tasks.find_one({"_id": tid})
    if not task:
        return jsonify({"ok": False, "message": "task not found"}), 404

    raw_map = data.get("teacher_segment_map") or data.get("segment_map") or {}
    if not isinstance(raw_map, dict) or not raw_map:
        return jsonify({"ok": False, "message": "teacher_segment_map must be a non-empty object"}), 400

    cleaned = {}
    for k, v in raw_map.items():
        if not isinstance(v, dict):
            continue
        s = v.get("start", v.get("start_ts"))
        e = v.get("end", v.get("end_ts"))
        if s is None or e is None:
            continue
        try:
            sf = float(s)
            ef = float(e)
        except Exception:
            continue
        if ef <= sf:
            continue
        cleaned[str(k)] = {
            "start": sf,
            "end": ef,
            "evidence": str(v.get("evidence") or "teacher_override"),
            "source": "teacher",
        }

    if not cleaned:
        return jsonify({"ok": False, "message": "no valid segment entries"}), 400

    slot_map = _derive_slot_concept_map(task)
    teacher_concept = {}
    for k, concept in (slot_map or {}).items():
        seg = cleaned.get(str(k))
        if not seg:
            continue
        c = str(concept or "").strip().lower()
        if not c:
            continue
        cs = float(seg.get("start", 0.0))
        ce = float(seg.get("end", 0.0))
        if ce <= cs:
            continue
        cur = teacher_concept.get(c)
        if not cur:
            teacher_concept[c] = {"start": cs, "end": ce}
        else:
            teacher_concept[c]["start"] = min(float(cur.get("start", cs)), cs)
            teacher_concept[c]["end"] = max(float(cur.get("end", ce)), ce)

    db.parsons_tasks.update_one(
        {"_id": tid},
        {"$set": {
            "teacher_segment_map": cleaned,
            "teacher_concept_segment_map": teacher_concept,
            "teacher_segment_map_updated_at": now_utc(),
        }}
    )

    return jsonify({
        "ok": True,
        "task_id": task_id,
        "teacher_segment_map": cleaned,
        "teacher_concept_segment_map": teacher_concept,
        "count": len(cleaned),
    })


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


def _progressive_hint_float(value):
    try:
        return float(value)
    except Exception:
        return None


def _progressive_hint_segment_values(segment: dict):
    if not isinstance(segment, dict):
        return None, None, ""

    start = segment.get("start")
    if start is None:
        start = segment.get("start_sec", segment.get("start_ts"))

    end = segment.get("end")
    if end is None:
        end = segment.get("end_sec", segment.get("end_ts"))

    start_f = _progressive_hint_float(start)
    end_f = _progressive_hint_float(end)
    text = str(segment.get("text") or segment.get("content") or "").strip()

    if start_f is None or end_f is None or end_f <= start_f:
        return None, None, text

    return start_f, end_f, text


def _load_progressive_hint_segments(task: dict) -> list:
    """Load and normalize SRT/compact subtitle segments for hint generation."""
    raw_segments = []

    try:
        raw_segments = _load_task_subtitle_segments(task) or []
    except Exception:
        raw_segments = []

    if not raw_segments:
        try:
            raw = str(_read_subtitle_text_for_task(task) or "").strip()
        except Exception:
            raw = ""

        if raw:
            if "-->" in raw:
                try:
                    raw_segments = parse_srt_segments(raw) or []
                except Exception:
                    raw_segments = []
            else:
                compact_segments = []
                for line in raw.splitlines():
                    match = _re.match(
                        r"\s*\[(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\]\s*(.*)$",
                        str(line or "").strip(),
                    )
                    if not match:
                        continue
                    start = _progressive_hint_float(match.group(1))
                    end = _progressive_hint_float(match.group(2))
                    if start is None or end is None or end <= start:
                        continue
                    compact_segments.append({
                        "start": start,
                        "end": end,
                        "text": str(match.group(3) or "").strip(),
                    })
                raw_segments = compact_segments

    normalized = []
    for segment in raw_segments or []:
        start, end, text = _progressive_hint_segment_values(segment)
        if start is None or end is None:
            continue
        normalized.append({
            "start": float(start),
            "end": float(end),
            "text": text,
        })

    normalized.sort(key=lambda item: (item["start"], item["end"]))
    return normalized


def _progressive_hint_range_from_value(value):
    """Normalize one mapping/chapter value into a start/end range."""
    if isinstance(value, list):
        valid = []
        for item in value:
            start, end = _progressive_hint_range_from_value(item)
            if start is not None and end is not None and end > start:
                valid.append((start, end))
        if valid:
            return min(item[0] for item in valid), max(item[1] for item in valid)
        return None, None

    if not isinstance(value, dict):
        return None, None

    start = value.get("start")
    if start is None:
        start = value.get("start_sec", value.get("start_ts"))

    end = value.get("end")
    if end is None:
        end = value.get("end_sec", value.get("end_ts"))

    start_f = _progressive_hint_float(start)
    end_f = _progressive_hint_float(end)
    if start_f is None or end_f is None or end_f <= start_f:
        return None, None

    return float(start_f), float(end_f)


def _progressive_hint_lookup_concept_range(task: dict, concept_tag: str):
    normalized_target = normalize_concept_name(concept_tag or "")
    if not normalized_target:
        return None, None, ""

    try:
        slot_concept_map = _derive_slot_concept_map(task) or {}
    except Exception:
        slot_concept_map = {}

    def _slot_key_to_index(key):
        text = str(key or "").strip()
        if text.isdigit():
            return int(text)
        match = _re.match(r"^s(\d+)$", text, flags=_re.I)
        if match:
            return int(match.group(1)) - 1
        match = _re.match(r"^第\s*(\d+)\s*格$", text)
        if match:
            return int(match.group(1)) - 1
        return None

    def _range_from_slot_segment_map(segment_map, source):
        if not isinstance(segment_map, dict):
            return None, None, ""
        valid = []
        for key, value in segment_map.items():
            slot_index = _slot_key_to_index(key)
            if slot_index is None or slot_index < 0:
                continue
            slot_concept = normalize_concept_name(slot_concept_map.get(str(slot_index)) or "")
            if slot_concept != normalized_target:
                continue
            start, end = _progressive_hint_range_from_value(value)
            if start is not None and end is not None and end > start:
                valid.append((start, end))
        if not valid:
            return None, None, ""
        return min(item[0] for item in valid), max(item[1] for item in valid), source

    start, end, source = _range_from_slot_segment_map(
        task.get("teacher_segment_map") or {},
        "teacher_segment_map",
    )
    if start is not None and end is not None:
        return start, end, source

    mappings = [
        (task.get("teacher_concept_segment_map") or {}, "teacher_concept_segment_map"),
        (task.get("concept_segment_map") or {}, "concept_segment_map"),
    ]

    for mapping, source in mappings:
        if not isinstance(mapping, dict):
            continue

        direct = mapping.get(normalized_target)
        start, end = _progressive_hint_range_from_value(direct)
        if start is not None and end is not None:
            return start, end, source

        for key, value in mapping.items():
            if normalize_concept_name(key) != normalized_target:
                continue
            start, end = _progressive_hint_range_from_value(value)
            if start is not None and end is not None:
                return start, end, source

    for chapters, source in (
        (task.get("teacher_concept_chapters") or [], "teacher_concept_chapters"),
        (task.get("concept_chapters_formal") or [], "concept_chapters_formal"),
        (task.get("concept_chapters_draft") or [], "concept_chapters_draft"),
    ):
        for chapter in chapters if isinstance(chapters, list) else []:
            if not isinstance(chapter, dict):
                continue
            chapter_tag = normalize_concept_name(
                chapter.get("concept_tag")
                or chapter.get("concept")
                or chapter.get("wrong_type")
                or ""
            )
            if chapter_tag != normalized_target:
                continue
            start, end = _progressive_hint_range_from_value(chapter)
            if start is not None and end is not None:
                return start, end, source

    start, end, source = _range_from_slot_segment_map(
        task.get("ai_segment_map") or {},
        "ai_segment_map",
    )
    if start is not None and end is not None:
        return start, end, source

    return None, None, ""


def _progressive_hint_excerpt(segments: list, start: float, end: float, max_chars: int) -> str:
    pieces = []
    seen = set()

    for segment in segments or []:
        seg_start, seg_end, text = _progressive_hint_segment_values(segment)
        if seg_start is None or seg_end is None:
            continue
        if seg_end < start or seg_start > end:
            continue

        clean = " ".join(str(text or "").split())
        if not clean or clean in seen:
            continue
        seen.add(clean)
        pieces.append(clean)

    excerpt = " ".join(pieces).strip()
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars].rstrip("，。；;,.!?！？ ") + "…"
    return excerpt


def _progressive_hint_terms(concept: str, concept_tag: str) -> list:
    terms = []

    normalized_tag = normalize_concept_name(concept_tag or "")
    if normalized_tag:
        try:
            terms.extend(get_query_terms_for_concept_tag(normalized_tag) or [])
        except Exception:
            pass

    generic = {
        "input": ["輸入", "讀取", "資料", "型別", "轉換"],
        "print": ["輸出", "顯示", "結果", "呈現"],
        "condition": ["條件", "判斷", "成立", "不成立", "分支"],
        "if_else": ["條件", "分支", "互斥", "成立", "不成立"],
        "indentation": ["縮排", "區塊", "範圍", "層級"],
        "indentation_error": ["縮排", "區塊", "範圍", "層級"],
        "calculation": ["運算", "計算", "資料", "結果"],
        "loop": ["迴圈", "重複", "範圍", "次數"],
        "sequence_error": ["流程", "順序", "先後", "步驟"],
        "structure": ["流程", "結構", "區塊", "關係"],
        "logic": ["流程", "資料", "關係", "條件"],
    }
    terms.extend(generic.get(str(concept or "").strip().lower(), []))

    output = []
    seen = set()
    for term in terms:
        clean = str(term or "").strip().lower()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        output.append(clean)
    return output

# 找 SRT 時間
def _build_progressive_subtitle_scope(
    task: dict,
    wrong_index,
    concept: str,
    hint_level: int,
) -> dict:
    """Build broad (level 1) and narrow (level 2) SRT evidence scopes."""
    segments = _load_progressive_hint_segments(task)

    try:
        wrong_index = int(wrong_index) if wrong_index is not None else None
    except Exception:
        wrong_index = None

    slot_concept_map = {}
    try:
        slot_concept_map = _derive_slot_concept_map(task) or {}
    except Exception:
        slot_concept_map = {}

    concept_tag = ""
    if wrong_index is not None:
        concept_tag = normalize_concept_name(
            slot_concept_map.get(str(wrong_index)) or ""
        )

    if not concept_tag:
        concept_tag = normalize_concept_name(_extract_attempt_v2_target_concept(task) or concept or "")

    slot_start = None
    slot_end = None
    if wrong_index is not None:
        try:
            slot_segment = _get_slot_segment_from_maps(task, wrong_index)
        except Exception:
            slot_segment = None
        slot_start, slot_end = _progressive_hint_range_from_value(slot_segment)

    broad_start, broad_end, broad_source = _progressive_hint_lookup_concept_range(
        task,
        concept_tag,
    )

    if broad_start is None or broad_end is None:
        if slot_start is not None and slot_end is not None:
            broad_start = max(0.0, slot_start - 12.0)
            broad_end = slot_end + 12.0
            broad_source = "slot_segment_expanded"
        else:
            subtitle_range = task.get("subtitle_range") or {}
            broad_start, broad_end = _progressive_hint_range_from_value(subtitle_range)
            if broad_start is not None and broad_end is not None:
                broad_source = "task_subtitle_range"

    if segments:
        full_start = min(segment["start"] for segment in segments)
        full_end = max(segment["end"] for segment in segments)
    else:
        full_start = 0.0
        full_end = 0.0

    narrow_start = slot_start
    narrow_end = slot_end
    narrow_source = "slot_segment" if slot_start is not None else ""

    if (narrow_start is None or narrow_end is None) and segments:
        terms = _progressive_hint_terms(concept, concept_tag)
        candidates = []
        for index, segment in enumerate(segments):
            start = segment["start"]
            end = segment["end"]
            if broad_start is not None and broad_end is not None:
                if end < broad_start or start > broad_end:
                    continue
            low = str(segment.get("text") or "").lower()
            score = sum(1 for term in terms if term and term in low)
            candidates.append((score, -start, index))

        if candidates:
            candidates.sort(reverse=True)
            _, _, best_index = candidates[0]
            best_segment = segments[best_index]
            narrow_start = best_segment["start"]
            narrow_end = best_segment["end"]

            left = best_index - 1
            right = best_index + 1
            while (narrow_end - narrow_start) < 8.0 and (left >= 0 or right < len(segments)):
                if right < len(segments):
                    next_segment = segments[right]
                    if broad_end is None or next_segment["start"] <= broad_end:
                        narrow_end = max(narrow_end, next_segment["end"])
                    right += 1
                if (narrow_end - narrow_start) >= 8.0:
                    break
                if left >= 0:
                    previous_segment = segments[left]
                    if broad_start is None or previous_segment["end"] >= broad_start:
                        narrow_start = min(narrow_start, previous_segment["start"])
                    left -= 1
            narrow_source = "srt_keyword_match"

    if (
        (broad_start is None or broad_end is None)
        and narrow_start is not None
        and narrow_end is not None
        and segments
    ):
        broad_start = max(full_start, float(narrow_start) - 12.0)
        broad_end = min(full_end, float(narrow_end) + 12.0)
        if broad_end > broad_start:
            broad_source = f"{narrow_source or 'srt_keyword_match'}_expanded"
        else:
            broad_start = None
            broad_end = None
            broad_source = ""

    if narrow_start is None or narrow_end is None:
        if broad_start is not None and broad_end is not None:
            center = (broad_start + broad_end) / 2.0
            narrow_start = max(broad_start, center - 6.0)
            narrow_end = min(broad_end, center + 6.0)
            narrow_source = "broad_range_center"

    if broad_start is not None and broad_end is not None:
        center = (
            ((narrow_start + narrow_end) / 2.0)
            if narrow_start is not None and narrow_end is not None
            else ((broad_start + broad_end) / 2.0)
        )
        if (broad_end - broad_start) > 45.0:
            broad_start = max(broad_start, center - 22.5)
            broad_end = min(broad_end, center + 22.5)
        elif (broad_end - broad_start) < 20.0:
            broad_start = max(0.0, broad_start - 5.0)
            broad_end = broad_end + 5.0

    if narrow_start is not None and narrow_end is not None:
        center = (narrow_start + narrow_end) / 2.0
        if (narrow_end - narrow_start) > 20.0:
            narrow_start = center - 10.0
            narrow_end = center + 10.0
        elif (narrow_end - narrow_start) < 8.0:
            narrow_start = max(0.0, center - 4.0)
            narrow_end = center + 4.0

    if segments:
        if broad_start is not None and broad_end is not None:
            broad_start = max(full_start, broad_start)
            broad_end = min(full_end, broad_end)
        if narrow_start is not None and narrow_end is not None:
            narrow_start = max(full_start, narrow_start)
            narrow_end = min(full_end, narrow_end)

    broad_excerpt = ""
    narrow_excerpt = ""
    if broad_start is not None and broad_end is not None:
        broad_excerpt = _progressive_hint_excerpt(
            segments,
            broad_start,
            broad_end,
            max_chars=1200,
        )
    if narrow_start is not None and narrow_end is not None:
        narrow_excerpt = _progressive_hint_excerpt(
            segments,
            narrow_start,
            narrow_end,
            max_chars=700,
        )

    level = 2 if int(hint_level or 1) == 2 else 1
    selected_start = narrow_start if level == 2 else broad_start
    selected_end = narrow_end if level == 2 else broad_end
    selected_excerpt = narrow_excerpt if level == 2 else broad_excerpt
    selected_source = narrow_source if level == 2 else broad_source

    return {
        "hint_level": level,
        "concept": str(concept or "logic"),
        "concept_tag": concept_tag,
        "concept_label": _progressive_hint_concept_label(concept, concept_tag),
        "scope": "narrow" if level == 2 else "broad",
        "start": round(float(selected_start), 2) if selected_start is not None else None,
        "end": round(float(selected_end), 2) if selected_end is not None else None,
        "subtitle_excerpt": selected_excerpt,
        "source": selected_source or "no_subtitle_scope",
        "broad_range": {
            "start": round(float(broad_start), 2) if broad_start is not None else None,
            "end": round(float(broad_end), 2) if broad_end is not None else None,
            "source": broad_source or "",
        },
        "narrow_range": {
            "start": round(float(narrow_start), 2) if narrow_start is not None else None,
            "end": round(float(narrow_end), 2) if narrow_end is not None else None,
            "source": narrow_source or "",
        },
    }


def _progressive_hint_fallback(concept: str, concept_label: str, hint_level: int) -> str:
    level = 2 if int(hint_level or 1) == 2 else 1
    concept_key = str(concept or "logic").strip().lower()

    broad = {
        "input": "這次可先從「資料輸入與準備」的概念範圍思考，確認資料在被使用前需要完成哪些處理。",
        "print": "這次可先從「輸出內容與輸出時機」的概念範圍思考，確認資訊應在流程的哪個階段呈現。",
        "condition": "這次可先從「條件判斷與分支流程」思考，確認不同情況應由哪一類分支處理。",
        "if_else": "這次可先從「條件分支與互斥關係」思考，確認成立與不成立的情況是否各有適當範圍。",
        "indentation": "這次可先從「縮排與控制範圍」思考，確認哪些步驟應受同一個控制結構管理。",
        "indentation_error": "這次可先從「縮排與控制範圍」思考，確認哪些步驟應受同一個控制結構管理。",
        "calculation": "這次可先從「運算步驟與資料關係」思考，確認資料在運算前後扮演的角色。",
        "loop": "這次可先從「迴圈控制與重複範圍」思考，確認哪些步驟需要重複、哪些只需執行一次。",
        "sequence_error": "這次可先從「程式流程與區塊順序」思考，確認各步驟之間的先後依賴關係。",
        "structure": "這次可先從「程式結構與執行流程」思考，確認每個區塊在整體流程中的角色。",
        "logic": "這次可先從「程式流程與資料關係」思考，確認每個步驟產生的資料會被哪一步使用。",
    }

    narrow = {
        "input": "再縮小到「輸入結果與後續使用的關係」：目前資料的狀態是否符合下一步操作的需求？",
        "print": "再縮小到「輸出內容與時機的關係」：目前呈現的資訊是否完整，而且發生在合適的流程階段？",
        "condition": "再縮小到「條件與分支範圍的關係」：目前的判斷是否讓每種情況進入適合的處理路徑？",
        "if_else": "再縮小到「分支互斥與先後關係」：較一般的情況是否可能過早攔截其他情況？",
        "indentation": "再縮小到「語句與控制區塊的隸屬關係」：目前的縮排是否讓某一步多執行或少執行？",
        "indentation_error": "再縮小到「語句與控制區塊的隸屬關係」：目前的縮排是否讓某一步多執行或少執行？",
        "calculation": "再縮小到「資料產生與運算使用的關係」：目前運算所需的資料是否已在前面準備完成？",
        "loop": "再縮小到「迴圈本體與迴圈外操作的界線」：目前的範圍是否符合每一步應執行的次數？",
        "sequence_error": "再縮小到「前一步產生、後一步使用」的關係：目前是否有步驟在所需資料尚未準備前就執行？",
        "structure": "再縮小到「相鄰區塊的依賴關係」：目前這一步所需的條件或資料，是否已由前面的流程建立？",
        "logic": "再縮小到「資料產生與使用的關係」：目前這一步所需要的條件或資料是否已先準備完成？",
    }

    if level == 2:
        return narrow.get(concept_key, narrow["logic"])
    return broad.get(
        concept_key,
        f"這次可先從「{concept_label or '程式流程與資料關係'}」的概念範圍思考，確認各步驟在整體流程中的角色。",
    )


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
def _generate_progressive_srt_feedback(
    *,
    task: dict,
    slot_label: str,
    wrong_index,
    expected_text: str,
    actual_text: str,
    error_type: str,
    hint_level: int,
    first_hint: str = "",
) -> dict:
    """Generate one SRT-grounded hint level without exposing the answer to the model."""
    concept = _rule_based_wrong_concept(
        expected_text,
        actual_text,
        error_type=error_type,
    )

    level = 2 if int(hint_level or 1) == 2 else 1
    scope = _build_progressive_subtitle_scope(
        task,
        wrong_index,
        concept,
        level,
    )
    concept_label = str(scope.get("concept_label") or "程式流程與資料關係")
    fallback_hint = _progressive_hint_fallback(concept, concept_label, level)

    subtitle_excerpt = str(scope.get("subtitle_excerpt") or "").strip()
    first_hint_text = str(first_hint or "").strip()

    if not ai_enabled() or not subtitle_excerpt:
        return {
            "hint_text": fallback_hint,
            "concept": concept,
            "concept_tag": scope.get("concept_tag") or "",
            "concept_scope": concept_label,
            "guiding_question": fallback_hint if level == 2 else "",
            "subtitle_scope": scope,
            "answer_leakage_check": "fallback_safe",
            "source": "system_fallback",
        }

    prompt = f"""
你是 Python Parsons 題的漸進式教學提示產生器。

你只能根據以下三類資料產生提示：
1. 系統已判定的錯誤類型。
2. 系統已選定的概念範圍。
3. 老師影片 SRT 的概念說明。

你不是答案產生器，也不負責判斷正確答案。

【目前提示層級】
level = {level}

【層級規則】
- level 1：提供較廣的概念範圍，說明學生應思考的流程角色；一句繁體中文，20～45 字。
- level 2：在 level 1 的基礎上縮小到一個子概念或關係，並提出一個引導問題；一到兩句繁體中文，30～60 字。
- level 2 必須比 level 1 更聚焦，但仍不可等同答案。

【禁止事項】
1. 不可輸出完整或局部正確程式碼。
2. 不可輸出 block ID、變數名稱、數字常數或固定輸出字串。
3. 不可說哪一行要放到哪一格，也不可說要移到前面或後面。
4. 不可使用「正確答案是」、「正確寫法是」、「應改成」、「請改成」。
5. 不可逐字複製 SRT；只能用自己的話概括概念。
6. 不可創造 SRT 沒有教過的概念。
7. 不可直接提供操作步驟，只能提供概念範圍與檢查方向。
8. 不可輸出 Markdown、程式碼區塊或額外說明。

【系統判定】
錯誤位置：{slot_label}
錯誤類型：{error_type}
概念標籤：{scope.get('concept_tag') or concept}
概念名稱：{concept_label}
提示範圍：{scope.get('scope')}

【第一層既有提示】
{first_hint_text if level == 2 else '無'}

【SRT 證據】
開始秒數：{scope.get('start')}
結束秒數：{scope.get('end')}
字幕概念內容：{subtitle_excerpt}

請只輸出合法 JSON：
{{
  "hint_text": "給學生的提示",
  "concept_scope": "本次提示涵蓋的概念範圍",
  "guiding_question": "level 2 的引導問題；level 1 請輸出空字串",
  "subtitle_basis": "用一句話概括提示依據，不逐字複製字幕"
}}
""".strip()

    try:
        model = _model_for_feedback()
        data = parsons_ai.call_openai_json(
            model=model,
            system=(
                "你是教學鷹架提示產生器，不是解題器。"
                "你只能依據提供的 SRT 概念證據產生由廣到窄的提示，"
                "不得揭露程式碼、正確 block、正確排列或直接修改步驟。"
                "只輸出合法 JSON。"
            ),
            user=prompt,
        ) or {}

        hint_text = str(data.get("hint_text") or "").strip()
        if _progressive_hint_has_leakage(hint_text, expected_text):
            hint_text = fallback_hint
            leakage_status = "blocked_and_replaced"
        else:
            leakage_status = "passed"

        guiding_question = str(data.get("guiding_question") or "").strip()
        if level == 1:
            guiding_question = ""
        elif _progressive_hint_has_leakage(guiding_question, expected_text):
            guiding_question = fallback_hint

        return {
            "hint_text": hint_text or fallback_hint,
            "concept": concept,
            "concept_tag": scope.get("concept_tag") or "",
            "concept_scope": str(data.get("concept_scope") or concept_label).strip(),
            "guiding_question": guiding_question,
            "subtitle_basis": str(data.get("subtitle_basis") or "").strip(),
            "subtitle_scope": scope,
            "answer_leakage_check": leakage_status,
            "source": "ai_srt_progressive",
        }
    except Exception:
        return {
            "hint_text": fallback_hint,
            "concept": concept,
            "concept_tag": scope.get("concept_tag") or "",
            "concept_scope": concept_label,
            "guiding_question": fallback_hint if level == 2 else "",
            "subtitle_scope": scope,
            "answer_leakage_check": "ai_error_fallback_safe",
            "source": "system_fallback",
        }

# 保存第一次與第二次提示
def _generate_ai_hint_payload(att, task, requested_hint_no):
    """Generate one aggregated hint level using all current wrong slots."""
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
    wrong_labels = [
        f"第{idx + 1}格"
        for idx in _int_list(aggregate_detail.get("wrong_slots") or [])
    ]
    expected_for_leakage = "\n".join(
        str(item.get("expected_text") or "")
        for item in _collect_all_wrong_slot_contexts(att, task)
        if isinstance(item, dict)
    )
    hint = fallback_hint
    guiding_question = ""
    subtitle_basis = str(aggregate_detail.get("subtitle_basis") or "").strip()
    leakage_status = "fallback_safe"
    source = "system_fallback"
    quality_status = "ai_disabled_fallback"
    second_hint_similarity = None
    second_hint_similarity_threshold = 0.72
    hint_library_fields = {}

    hint_library_doc, hint_library_ctx = _find_hint_library_entry(
        aggregate_detail,
        task,
        level,
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
        source = "hint_library"
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
            "hint_level": level,
            "scope": "narrow" if level == 2 else "broad",
            "aggregation_mode": "all_current_errors",
            "wrong_slot_count": aggregate_detail.get("wrong_slot_count") or len(wrong_labels),
            "wrong_slots": wrong_labels,
            "error_concepts": error_concepts,
            "first_hint": first_hint if level == 2 else "",
        }
        prompt = f"""
你是 Python Parsons 題的整合式教學提示產生器。

你只能根據系統提供的錯誤概念與 SRT 概念摘要產生提示。
系統已完成正誤判斷、錯誤格位、錯誤類型與概念對應，你不可重新判斷答案。

【提示層級】
level = {level}

【層級規則】
- level 1：統整所有錯誤概念，提供較廣、較淺的概念方向；一句繁體中文，30～70 字。
- level 2：針對同一組錯誤概念提出更聚焦的檢查方向或一個引導問題；一到兩句繁體中文，40～90 字。

【禁止事項】
1. 不可輸出完整或局部正確程式碼。
2. 不可輸出 block ID、正確排列、正確格位或直接移動步驟。
3. 不可說哪一行要放到哪一格，也不可說要移到前面或後面。
4. 不可使用「正確答案是」、「正確寫法是」、「應改成」、「請改成」。
5. 不可輸出 Markdown、程式碼區塊或額外說明。
6. 不可創造 SRT 沒有教過的概念。

【後端整理後的錯誤概念資料】
{json.dumps(prompt_payload, ensure_ascii=False)}

請只輸出合法 JSON：
{{
  "hint_text": "給學生的整合提示",
  "concept_scope": "用頓號串接本次涵蓋的概念範圍",
  "guiding_question": "level 2 的聚焦問題；level 1 請輸出空字串",
  "subtitle_basis": "用一句話概括依據，不逐字複製字幕"
}}
""".strip()

        try:
            data = parsons_ai.call_openai_json(
                model=_model_for_feedback(),
                system=(
                    "你是教學鷹架提示產生器，不是解題器。"
                    "只能依據後端提供的錯誤概念與 SRT 概念摘要產生由廣到窄的提示。"
                    "不得揭露正確程式碼、block、排列或直接修改步驟。"
                    "只輸出合法 JSON。"
                ),
                user=prompt,
            ) or {}
            candidate = str(data.get("hint_text") or "").strip()

            # 檢查是否有洩漏答案或不合法的提示
            if not candidate:
                hint = fallback_hint
                leakage_status = "empty_candidate_fallback"
                quality_status = "empty_candidate_fallback"
                source = "system_fallback"

            elif _progressive_hint_has_leakage(
                candidate,
                expected_for_leakage,
            ):
                # answer leakage fallback
                hint = fallback_hint
                leakage_status = "rejected_fallback_used"
                quality_status = "answer_leakage_fallback"
                source = "system_fallback"

            elif level == 2 and first_hint:
                second_hint_similarity = _lexical_overlap_ratio(
                    candidate,
                    first_hint,
                )

                if second_hint_similarity >= second_hint_similarity_threshold:
                    hint = _aggregated_hint_fallback(
                        aggregate_detail,
                        hint_level=2,
                    )
                    leakage_status = "passed"
                    quality_status = "second_hint_too_similar_fallback"
                    source = "system_fallback"
                else:
                    hint = candidate
                    leakage_status = "passed"
                    quality_status = "second_hint_depth_check_passed"
                    source = "ai_srt_aggregated"

            else:
                hint = candidate
                leakage_status = "passed"
                quality_status = (
                    "first_hint_accepted"
                    if level == 1
                    else "second_hint_without_first_hint"
                )
                source = "ai_srt_aggregated"

            guiding_question = str(data.get("guiding_question") or "").strip()
            if level == 1 or _progressive_hint_has_leakage(guiding_question, expected_for_leakage):
                guiding_question = ""
            subtitle_basis = _compact_subtitle_basis(data.get("subtitle_basis"), subtitle_basis)
        except Exception:
            hint = fallback_hint
            leakage_status = "ai_error_fallback_safe"
            quality_status = "ai_error_fallback_safe"
            source = "system_fallback"

    if not hint_library_doc:
        auto_saved_hint_library_fields = _auto_save_generated_hint_to_library(
            aggregate_detail,
            task,
            level,
            hint,
            source=source,
            quality_status=quality_status,
            leakage_status=leakage_status,
        )
        if auto_saved_hint_library_fields:
            hint_library_fields = auto_saved_hint_library_fields

    concept_scope = "、".join(aggregate_detail.get("concept_scopes") or []) or "程式流程與區塊順序"
    ai_feedback_detail = {
        "feedback_type": "progressive_srt_aggregated_hint",
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
        "subtitle_basis": subtitle_basis,
        "answer_leakage_check": leakage_status,
        **hint_library_fields,
        **aggregate_detail,
    }
    ai_diagnosis_summary = (
        f"整合錯誤概念：{concept_scope}；"
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

    # 舊 Prompt 產生的快取可能過度接近答案，或缺少 SRT metadata。
    # 版本不一致時清空本場次 AI 快取，讓新版漸進式 Prompt 重新生成。
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
    key = {
        "hint_key": doc["hint_key"],
        "version": doc["version"],
    }
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
# (C) POST /review_choice  記錄學生查看第一次、第二次提示，以及切換提示的行為
# =========================
@parsons_bp.route("/review_choice", methods=["POST", "OPTIONS"], endpoint="parsons_review_choice_v17")
def review_choice():
    # CORS preflight
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(silent=True) or {}

    attempt_id = (data.get("attempt_id") or "").strip()  # 前端有送
    student_choice = (data.get("student_choice") or "").strip().lower()  # yes/no
    click_type = (data.get("click_type") or "").strip().lower()  # hint/video

    # ✅ A 方案：後端盡量補齊 student_id
    student_id = current_student_id()
    participant_id = current_participant_id()

    if not attempt_id:
        return jsonify({"ok": False, "message": "missing attempt_id"}), 400
    if student_choice not in ("yes", "no") and click_type not in ("hint", "hint_retry", "video"):
        return jsonify({"ok": False, "message": "student_choice must be yes/no"}), 400

    # 先從 attempts 補 task_id / video_id（不改 schema）
    task_id_f = None
    video_id_f = None

    att = None
    try:
        att = db.parsons_attempts.find_one({"_id": ObjectId(attempt_id)})
        if att:
            task_id_f = att.get("task_id") or None
            video_id_f = att.get("video_id") or None
    except Exception:
        pass

    if not att:
        return jsonify({"ok": False, "message": "attempt not found"}), 404
    if not _attempt_belongs_to_current_student(att):
        return jsonify({
            "ok": False,
            "error": "attempt_access_denied",
            "message": "不得更新其他學生的提示紀錄。",
        }), 403

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

    click_set = {}
    click_now = now_utc()
    is_wrong_attempt = bool(att) and (att.get("is_correct") is False)
    if click_type in {"hint", "hint_retry"} and is_wrong_attempt:
        click_set["hint_click"] = True
        click_set["hint_click_time"] = click_now
    elif click_type == "video" and is_wrong_attempt:
        click_set["video_click"] = True
        click_set["video_click_time"] = click_now

    if click_set:
        try:
            db.parsons_attempts.update_one(
                {"_id": ObjectId(attempt_id)},
                {"$set": click_set}
            )
        except Exception:
            pass

    if click_type in {"hint", "hint_retry"} and is_wrong_attempt:
        hint_no = data.get("hint_no") or data.get("hint_click_no")
        if not hint_no and click_type == "hint_retry":
            hint_no = 2
        elif not hint_no:
            hint_no = 1
        trigger_method = data.get("trigger_method") or (
            "click_hint_retry" if click_type == "hint_retry" else "click_ai_hint"
        )
        button_name = data.get("button_name") or (
            "再次提示" if click_type == "hint_retry" else "AI提示"
        )
        hint_content = str(
            data.get("hint_content")
            or data.get("hint_text")
            or data.get("first_hint")
            or ""
        ).strip()
        write_or_update_hint_learning_log_safely({
            "session_id": data.get("session_id"),
            "student_id": student_id,
            "event_type": "view_hint",
            "page": data.get("page") or "parsons",
            "activity_type": data.get("activity_type") or "practice",
            "test_role": data.get("test_role"),
            "task_id": data.get("task_id") or task_id_f,
            "attempt_id": data.get("attempt_v2_id") or data.get("learning_attempt_id"),
            "attempt_no": data.get("attempt_no"),
            "target_concept": data.get("target_concept") or att.get("target_concept"),
            "event_at": data.get("event_at") or click_now,
            "metadata": {
                "review_type": "ai_hint",
                "hint_type": "ai_hint",
                "hint_no": hint_no,
                "hint_click_no": hint_no,
                "max_hint_count": 2,
                "hint_retry_count": data.get("hint_retry_count"),
                "trigger_method": trigger_method,
                "button_name": button_name,
                "question_id": data.get("question_id") or data.get("task_id") or task_id_f,
                "unit_id": data.get("unit_id"),
                "question_type": data.get("question_type") or "parsons",
                "hint_text": hint_content,
                "hint_content": hint_content,
                "hint_source": data.get("hint_source") or "frontend",
                "hint_loaded": True if hint_content else data.get("hint_loaded"),
                "error_type": data.get("error_type") or att.get("primary_error_type") or att.get("error_type"),
                "wrong_slots": data.get("wrong_slots") if isinstance(data.get("wrong_slots"), list) else att.get("wrong_indices") or [],
                "ai_diagnosis_summary": data.get("ai_diagnosis_summary"),
            },
        })

        hint_record = _get_hint_record(
            student_id,
            str(data.get("task_id") or task_id_f or "").strip(),
            att.get("task_attempt_session") or data.get("task_attempt_session"),
        )
        hint_meta = data.get("hint_meta") if isinstance(data.get("hint_meta"), dict) else {}
        if not hint_meta:
            hint_meta = data.get("ai_hint_2_meta") if int(hint_no or 1) == 2 else data.get("ai_hint_1_meta")
        if not isinstance(hint_meta, dict):
            hint_meta = {}
        _update_attempt_v2_ai_hint_event(
            att,
            hint_record,
            requested_hint_no=hint_no,
            explicit_attempt_v2_id=data.get("attempt_v2_id"),
            hint_text_override=hint_content,
            hint_meta_override=hint_meta,
            event_type="review_choice_hint_click",
            generated=False,
            trigger_method=trigger_method,
            button_name=button_name,
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
    if not _video_review_enabled():
        return _video_review_disabled_response()

    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(silent=True) or {}
    
    attempt_id = (data.get("attempt_id") or "").strip()
    video_id = (data.get("video_id") or "").strip()
    student_id = current_student_id()
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
        if not _attempt_belongs_to_current_student(original_attempt):
            return jsonify({
                "ok": False,
                "error": "attempt_access_denied",
                "message": "不得寫入其他學生的回看紀錄。",
            }), 403
        
        # 若前端沒傳 student_id，從原始 attempt 取得
        participant_id = current_participant_id()
        
        # 取得用戶 participant_id（用于研究分析）
        
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
def _concept_keywords_map() -> Dict[str, list]:
    return {
        "condition": ["條件", "判斷", "成立", "不成立", "if"],
        "if_else": ["else", "否則", "分支", "條件分支", "if"],
        "indentation": ["縮排", "區塊", "冒號", "層級"],
        "print": ["print", "輸出", "顯示", "結果"],
        "input": ["input", "輸入", "讀取"],
        "loop": ["迴圈", "巢狀迴圈", "for", "while", "range", "重複"],
        "assignment": ["變數", "賦值", "="],
        "calculation": ["運算", "計算", "加", "減", "乘", "除", "+", "-", "*", "/"],
        "logic": ["邏輯", "流程", "順序", "判斷"],
    }

def _is_action_coding_text(text: str) -> bool:
    """偵測字幕是否包含老師開始實作的動詞訊號"""
    low = str(text or "").strip().lower()
    # 這些關鍵字通常代表老師正要開始打代碼
    action_terms = ["打上", "輸入這行", "定義一個", "接著寫", "寫下", "補上", "寫成", "運行"]
    return any(term in low for term in action_terms)

def _rule_based_wrong_concept(expected_text: str, actual_text: str, error_type: str = "") -> str:
    et = str(expected_text or "").lower()
    at = str(actual_text or "").lower()
    e = str(error_type or "").lower()
    merged = f"{et}\n{at}"

    # Priority: explicit structural issues first, then concept lines.
    if "indent" in e:
        return "indentation"

    # for / while / nested loop all map to loop.
    has_for = bool(_re.search(r"(^|\n)\s*for\s+", merged))
    has_while = bool(_re.search(r"(^|\n)\s*while\s+", merged))
    if has_for or has_while or ("range(" in merged):
        return "loop"

    if "if_else" in e or at.strip().startswith("else") or et.strip().startswith("else"):
        return "if_else"
    if any(op in merged for op in [" + ", " - ", " * ", " / ", "+", "-", "*", "/"]):
        return "calculation"
    if "print(" in at or "print(" in et:
        return "print"
    if at.strip().startswith("if ") or et.strip().startswith("if "):
        return "condition"
    return "logic"


def _ai_classify_wrong_concept(
    task: dict,
    expected_text: str,
    actual_text: str,
    error_type: str = "",
) -> Tuple[str, str, str]:
    """
    Classify the main error concept only.

    This function intentionally does not generate student-facing hints. The
    expected/actual code may be used internally for classification, but the
    progressive hint prompt never receives the correct answer.
    """
    concept = _rule_based_wrong_concept(
        expected_text,
        actual_text,
        error_type,
    )

    if not ai_enabled():
        return concept, "", ""

    try:
        model = _model_for_feedback()
        question_text = str(task.get("question_text") or "")
        prompt = f"""
你是 Python Parsons 題錯誤概念分類器。
你的工作只有分類，不得產生提示、答案、修改步驟或程式碼。

規則：
1. 只分類第一個主要錯誤概念。
2. 若存在縮排錯誤，優先分類為 indentation。
3. 只能從以下概念選一個：
   input, print, condition, if_else, indentation,
   calculation, loop, assignment, logic。
4. 只輸出合法 JSON，不要輸出其他文字。

題目：{question_text}
預期程式：{expected_text}
學生程式：{actual_text}
已知錯誤型別：{error_type}

輸出：
{{"concept": "condition"}}
""".strip()

        data = parsons_ai.call_openai_json(
            model=model,
            system="你是程式學習錯誤分類器，只分類概念並輸出 JSON。",
            user=prompt,
        ) or {}

        candidate = str(data.get("concept") or "").strip().lower()
        if candidate in _concept_keywords_map():
            concept = candidate
    except Exception:
        pass

    return concept, "", ""

def _short_code_snippet(s: str, max_len: int = 28) -> str:
    t = str(s or "").replace("\n", " ").strip()
    if len(t) <= max_len:
        return t
    return (t[:max_len].rstrip() + "...")


def _short_text(s: str, max_len: int = 44) -> str:
    t = " ".join(str(s or "").replace("\n", " ").split())
    if len(t) <= max_len:
        return t
    return t[:max_len].rstrip("，。；;,.!?！？ ") + "..."


def _build_short_reflective_feedback(
    task: dict,
    slot_label: str,
    expected_text: str,
    actual_text: str,
    error_type: str = "",
    hint_level: int = 1,
    wrong_index=None,
    first_hint: str = "",
) -> dict:
    """
    Build one progressive SRT-grounded hint.

    The correct answer is used only for internal concept classification and the
    final leakage check. It is never inserted into the student-facing AI prompt.
    """
    level = 2 if int(hint_level or 1) == 2 else 1
    result = _generate_progressive_srt_feedback(
        task=task,
        slot_label=slot_label,
        wrong_index=wrong_index,
        expected_text=expected_text,
        actual_text=actual_text,
        error_type=error_type,
        hint_level=level,
        first_hint=first_hint,
    )

    hint_text = str(result.get("hint_text") or "").strip()
    concept_scope = str(
        result.get("concept_scope")
        or _progressive_hint_concept_label(
            result.get("concept") or "logic",
            result.get("concept_tag") or "",
        )
    ).strip()
    guiding_question = str(result.get("guiding_question") or "").strip()
    subtitle_scope = result.get("subtitle_scope") or {}

    first_hint_text = hint_text if level == 1 else str(first_hint or "").strip()
    second_hint_text = hint_text if level == 2 else ""

    return {
        "diagnosis_summary": f"主錯誤：{slot_label}；概念範圍：{concept_scope}",
        "feedback": {
            "feedback_type": "progressive_srt_hint",
            "hint_level": level,
            "hint_source": result.get("source") or "system_fallback",
            "concept": result.get("concept") or "logic",
            "concept_tag": result.get("concept_tag") or "",
            "concept_scope": concept_scope,
            "concept_explanation": concept_scope,
            "concept_hint": concept_scope,
            "possible_causes": [],
            "impact": "請先依照目前的概念範圍修正，再重新送出讓系統判斷。",
            "guiding_question": guiding_question,
            "reflection_questions": [guiding_question] if guiding_question else [],
            "first_hint": first_hint_text,
            "second_hint": second_hint_text,
            "subtitle_range": {
                "start": subtitle_scope.get("start"),
                "end": subtitle_scope.get("end"),
                "scope": subtitle_scope.get("scope"),
                "source": subtitle_scope.get("source"),
            },
            "subtitle_broad_range": subtitle_scope.get("broad_range") or {},
            "subtitle_narrow_range": subtitle_scope.get("narrow_range") or {},
            "subtitle_basis": result.get("subtitle_basis") or "",
            "subtitle_excerpt": subtitle_scope.get("subtitle_excerpt") or "",
            "answer_leakage_check": result.get("answer_leakage_check") or "unknown",
        },
    }

def _load_task_subtitle_segments(task: dict) -> list:
    segs = []

    try:
        sub_path = ((task.get("prompt_source") or {}).get("subtitle_path") or "").strip()
        if sub_path:
            sub_text = read_subtitle_text(sub_path)
            segs = parse_srt_segments(sub_text)
            if segs:
                return segs
    except Exception:
        pass

    try:
        source_sub = task.get("source_subtitle") or {}
        raw = str(source_sub.get("text_used") or task.get("subtitle_text_used") or "").strip()
        if raw and "-->" in raw:
            segs = parse_srt_segments(raw)
            if segs:
                return segs
    except Exception:
        pass

    return []


def _load_task_subtitle_segments_for_health(task: dict) -> list:
    segs = _load_task_subtitle_segments(task)
    if segs:
        return segs

    try:
        raw = _read_subtitle_text_for_task(task)
    except Exception:
        raw = ""
    raw = str(raw or "").strip()
    if not raw:
        return []

    segs = []
    if "-->" in raw:
        try:
            segs = parse_srt_segments(raw)
        except Exception:
            segs = []
    if not segs:
        for ln in raw.splitlines():
            m = _re.match(r"\s*\[(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\]\s*(.*)$", str(ln or "").strip())
            if not m:
                continue
            try:
                s = float(m.group(1))
                e = float(m.group(2))
            except Exception:
                continue
            if e <= s:
                continue
            segs.append({"id": len(segs) + 1, "start": s, "end": e, "text": str(m.group(3) or "").strip()})
    return segs


def _is_code_like_subtitle_text(text: str) -> bool:
    low = str(text or "").strip().lower()
    if not low:
        return False
    markers = ["if ", "elif", "else:", "for ", "while ", "def ", "return", "print(", "input(", "range(", "==", "!=", "<=", ">=", "=", ":"]
    if any(marker in low for marker in markers):
        return True
    if re.search(r"\b[a-z_][a-z0-9_]*\s*\(", low):
        return True
    if re.search(r"[\+\-\*/%]", low):
        return True
    return False


def _looks_like_intro_explanation_text(text: str) -> bool:
    low = str(text or "").strip().lower()
    if not low:
        return False
    if _is_code_like_subtitle_text(low):
        return False
    intro_terms = [
        "題目", "我們先", "我們來", "先來", "那我們", "這一題", "這邊", "接著", "然後", "開始", "修改", "說明", "畫面", "編輯器", "先看", "先把", "目前", "現在", "教學", "講解",
    ]
    if any(term in low for term in intro_terms):
        return True
    if len(low) <= 16 and not re.search(r"\d", low) and any(ch.isalpha() or "\u4e00" <= ch <= "\u9fff" for ch in low):
        return True
    return False


def _detect_opening_explanation_cutoff(segs: list) -> tuple:
    """Return (cutoff_sec, intro_count). Segments before cutoff are treated as opening explanation."""
    if not isinstance(segs, list) or not segs:
        return 0.0, 0

    cutoff = 0.0
    intro_count = 0
    for seg in segs:
        if not isinstance(seg, dict):
            continue
        try:
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
        except Exception:
            continue
        if end <= start:
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        if _looks_like_intro_explanation_text(text):
            cutoff = max(cutoff, end)
            intro_count += 1
            continue
        if cutoff > 0.0:
            break
        if _is_code_like_subtitle_text(text):
            break
    return round(float(cutoff), 2), int(intro_count)


def _build_subtitle_health_report(task: dict) -> dict:
    segs = _load_task_subtitle_segments_for_health(task)
    blocks = task.get("solution_blocks") or []
    if not segs:
        return {
            "ok": False,
            "level": "bad",
            "mode": "chapter_recommendation",
            "reason": "subtitle_missing",
            "recommendation": "字幕不可用，先改走章節推薦。",
            "probes": [],
        }

    if not blocks:
        return {
            "ok": False,
            "level": "warn",
            "mode": "chapter_recommendation",
            "reason": "solution_missing",
            "recommendation": "題目結構不足，暫時用章節推薦。",
            "probes": [],
        }

    opening_explanation_cutoff_sec, opening_explanation_count = _detect_opening_explanation_cutoff(segs)

    subtitle_index = task.get("subtitle_ir_cache") or {}
    if not isinstance(subtitle_index, dict) or not (subtitle_index.get("segments") or []):
        try:
            subtitle_index = _build_or_get_task_subtitle_ir_cache(task, force_rebuild=False) or {}
        except Exception:
            subtitle_index = {}

    def _chapter_family(concept_tag: str, code_text: str = "") -> str:
        tag = normalize_concept_name(concept_tag)
        low = str(code_text or "").lower()
        has_condition = tag in {"if_condition_logic", "if_branch_order", "edge_case_condition"} or any(term in low for term in ["if ", "elif ", "else:", "else "])
        has_print = "print(" in low or "sep=" in low or "end=" in low
        if has_condition and has_print:
            return "condition"
        if tag in {"print_separator"} or has_print:
            return "output"
        if tag in {"input_int_cast"} or "input(" in low or "讀取" in low:
            return "input"
        if tag in {"if_condition_logic", "if_branch_order", "edge_case_condition"}:
            return "condition"
        if tag in {"loop_count_control", "loop_reverse_range", "nested_loop_structure", "star_formula_2i_minus_1", "space_formula_n_minus_i"}:
            return "loop"
        return tag or "other"

    def _chapter_label(family: str, concept_tag: str) -> str:
        fam = str(family or "").strip().lower()
        if fam == "loop":
            return "迴圈章節"
        if fam == "output":
            return "輸出章節"
        if fam == "input":
            return "輸入章節"
        if fam == "condition":
            return "條件判斷章節"
        if fam == "other":
            return "其他章節"
        return concept_tag_to_label(concept_tag) or fam or "章節"

    def _chapter_ai_title(family: str, concept_tag: str, concept_tags: list, blocks: list) -> str:
        fam = str(family or "").strip().lower()
        tags = [normalize_concept_name(x) for x in (concept_tags or []) if normalize_concept_name(x)]
        main_tag = normalize_concept_name(concept_tag) or (tags[0] if tags else "")

        if fam == "loop":
            if "nested_loop_structure" in tags:
                return "巢狀迴圈與次數控制"
            if "loop_reverse_range" in tags:
                return "反向迴圈與遞減範圍"
            return "迴圈錯誤與控制"
        if fam == "output":
            return "輸出格式與換行控制"
        if fam == "input":
            return "輸入與型別轉換"
        if fam == "condition":
            return "條件判斷與分支順序"
        if main_tag:
            return concept_tag_to_label(main_tag) or _chapter_label(fam, main_tag)
        return _chapter_label(fam, concept_tag)

    def _chapter_ai_note(family: str, concept_tag: str, concept_tags: list, blocks: list) -> str:
        fam = str(family or "").strip().lower()
        tags = [normalize_concept_name(x) for x in (concept_tags or []) if normalize_concept_name(x)]
        joined = "、".join([concept_tag_to_label(t) for t in tags[:3] if concept_tag_to_label(t)])
        if fam == "loop":
            return "學生常在迴圈次數、遞減範圍或巢狀順序上出錯，適合先看這一段。"
        if fam == "output":
            return "學生常在 print、end、sep 或換行控制上出錯，建議確認輸出格式。"
        if fam == "input":
            return "學生常在 input 與 int 轉換順序上出錯，容易影響後續計算。"
        if fam == "condition":
            return "學生常在 if / elif / else 的判斷與分支順序上出錯。"
        if joined:
            return f"這段聚焦於 {joined}，可讓老師直接確認是否要保留此章節名稱。"
        return "先用系統章節確認，再由老師決定正式命名。"

    chapter_recommendations = []
    current_ch = None
    for idx, block in enumerate(blocks):
        code_text = str(block.get("text") or "")
        sem_text = str(block.get("semantic_zh") or block.get("meaning_zh") or "")
        wrong_type = infer_wrong_type_from_code(code_text, sem_text)
        concept_tag = resolve_concept_tag_from_wrong_type(wrong_type, code_text, sem_text)
        family = _chapter_family(concept_tag, code_text)
        label = _chapter_label(family, concept_tag)

        if current_ch and current_ch["family"] == family and idx == current_ch["slot_end"] + 1:
            current_ch["slot_end"] = idx
            current_ch["slots"].append(idx)
            current_ch["blocks"].append(code_text)
            current_ch["concept_tags"].append(concept_tag)
            continue

        if current_ch:
            chapter_recommendations.append(current_ch)

        current_ch = {
            "chapter_key": f"{family}:{idx}",
            "chapter_label": label,
            "family": family,
            "concept_tag": concept_tag,
            "slot_start": idx,
            "slot_end": idx,
            "slots": [idx],
            "blocks": [code_text],
            "concept_tags": [concept_tag],
        }

    if current_ch:
        chapter_recommendations.append(current_ch)

    def _sec_to_tc(sec: float) -> str:
        try:
            total = max(0, int(round(float(sec))))
        except Exception:
            return "—"
        mm, ss = divmod(total, 60)
        hh, mm = divmod(mm, 60)
        if hh > 0:
            return f"{hh:02d}:{mm:02d}:{ss:02d}"
        return f"{mm:02d}:{ss:02d}"

    def _slot_seconds_from_maps(slot_idx: int):
        seg = _get_slot_segment_from_maps(task, int(slot_idx))
        if not isinstance(seg, dict):
            return None, None
        try:
            start = float(seg.get("start"))
            end = float(seg.get("end"))
        except Exception:
            return None, None
        if end <= start:
            return None, None
        return start, end

    for ch in chapter_recommendations:
        slot_start = int(ch.get("slot_start") or 0)
        slot_end = int(ch.get("slot_end") or slot_start)
        slot_windows = []

        for slot in range(slot_start, slot_end + 1):
            s0, e0 = None, None
            source = "unresolved"
            score = None
            hit = None

            if subtitle_index and (subtitle_index.get("segments") or []):
                try:
                    hit, hit_score = retrieve_segment_for_wrong_slot(task, int(slot), subtitle_index)
                except Exception:
                    hit, hit_score = None, 0.0
                if isinstance(hit, dict):
                    try:
                        hs = float(hit.get("start", 0.0))
                        he = float(hit.get("end", 0.0))
                    except Exception:
                        hs, he = None, None
                    if hs is not None and he is not None and he > hs:
                        if opening_explanation_cutoff_sec > 0.0 and he <= opening_explanation_cutoff_sec and _looks_like_intro_explanation_text(hit.get("text") or ""):
                            continue
                        s0, e0 = hs, he
                        source = str(hit.get("source") or "subtitle_ir")
                        score = float(hit_score or 0.0)

            if s0 is None or e0 is None:
                s0, e0 = _slot_seconds_from_maps(slot)
                if s0 is not None and e0 is not None:
                    source = "task_map"

            if s0 is None or e0 is None or e0 <= s0:
                continue

            anchor_id = None
            if isinstance(hit, dict):
                anchor_id = hit.get("anchor_id")
                if anchor_id is None:
                    anchor_id = hit.get("id")
                if anchor_id is None:
                    anchor_id = hit.get("index")
            if anchor_id is None and subtitle_index and (subtitle_index.get("segments") or []):
                anchor_seg = _resolve_subtitle_anchor_for_window(subtitle_index, s0, e0)
                anchor_id = anchor_seg.get("id") if isinstance(anchor_seg, dict) else None
            if anchor_id is None:
                try:
                    anchor_hit, _ = retrieve_segment_for_wrong_slot(task, int(slot), subtitle_index)
                except Exception:
                    anchor_hit = None
                if isinstance(anchor_hit, dict):
                    anchor_id = anchor_hit.get("anchor_id") or anchor_hit.get("id") or anchor_hit.get("index")

            slot_windows.append({
                "slot": int(slot),
                "start_sec": round(float(s0), 2),
                "end_sec": round(float(e0), 2),
                "time_axis_label": f"{_sec_to_tc(s0)} - {_sec_to_tc(e0)}",
                "time_axis_detail": f"{float(s0):.1f} - {float(e0):.1f}",
                "source": source,
                "score": score,
                "anchor_id": anchor_id,
            })

        if slot_windows:
            start_sec = min(float(x["start_sec"]) for x in slot_windows)
            end_sec = max(float(x["end_sec"]) for x in slot_windows)
            ordered_windows = sorted(slot_windows, key=lambda x: float(x.get("start_sec") or 0.0))
            ch["slot_windows"] = slot_windows
            ch["start_sec"] = round(float(start_sec), 2)
            ch["end_sec"] = round(float(end_sec), 2)
            ch["time_axis_label"] = f"{_sec_to_tc(start_sec)} - {_sec_to_tc(end_sec)}"
            ch["time_axis_detail"] = " → ".join([f"slot {x['slot']}: {x['time_axis_label']}" for x in ordered_windows])
            ch["time_axis_source"] = "slot_windows"
            anchor_ids = []
            for x in slot_windows:
                aid = x.get("anchor_id")
                if aid is None:
                    continue
                if aid in anchor_ids:
                    continue
                anchor_ids.append(aid)
            ch["anchor_ids"] = anchor_ids
            ch["primary_anchor_id"] = anchor_ids[0] if anchor_ids else None
        else:
            ch["slot_windows"] = []
            ch["start_sec"] = None
            ch["end_sec"] = None
            ch["time_axis_label"] = "slot 未對應到秒數"
            ch["time_axis_detail"] = ""
            ch["time_axis_source"] = "unresolved"
            ch["anchor_ids"] = []
            ch["primary_anchor_id"] = None

        ch["ai_chapter_title"] = _chapter_ai_title(ch.get("family"), ch.get("concept_tag"), ch.get("concept_tags") or [], ch.get("blocks") or [])
        ch["ai_chapter_note"] = _chapter_ai_note(ch.get("family"), ch.get("concept_tag"), ch.get("concept_tags") or [], ch.get("blocks") or [])
        ch["chapter_name_candidates"] = [
            ch.get("ai_chapter_title") or "",
            ch.get("chapter_label") or "",
        ]

    probe_indices = sorted(set([0, len(blocks) // 2, len(blocks) - 1]))
    probes = []

    for idx in probe_indices:
        block = blocks[idx] or {}
        code_text = str(block.get("text") or "")
        sem_text = str(block.get("semantic_zh") or block.get("meaning_zh") or "")
        wrong_type = infer_wrong_type_from_code(code_text, sem_text)
        concept_tag = resolve_concept_tag_from_wrong_type(wrong_type, code_text, sem_text)
        query_concept = concept_tag or infer_sub_concept_from_code(code_text, sem_text) or ""

        hit = None
        score = 0.0
        if subtitle_index and (subtitle_index.get("segments") or []):
            try:
                hit, score = retrieve_segment_for_wrong_slot(task, int(idx), subtitle_index)
            except Exception:
                hit, score = None, 0.0

        hit_text = str((hit or {}).get("text") or "")
        concept_hits = 0
        wrong_type_hits = 0
        if query_concept:
            concept_hits = len([t for t in get_query_terms_for_concept_tag(query_concept) if t and str(t).lower() in hit_text.lower()])
        if wrong_type:
            wrong_type_hits = len([t for t in (_WRONG_TYPE_QUERY_TERMS.get(wrong_type) or []) if t and str(t).lower() in hit_text.lower()])

        anchor_id = None
        if isinstance(hit, dict):
            anchor_id = hit.get("anchor_id")
            if anchor_id is None:
                anchor_id = hit.get("id")
            if anchor_id is None:
                anchor_id = hit.get("index")
        if anchor_id is None and subtitle_index and (subtitle_index.get("segments") or []):
            anchor_seg = _resolve_subtitle_anchor_for_window(subtitle_index, hit.get("start", 0.0) if isinstance(hit, dict) else 0.0, hit.get("end", 0.0) if isinstance(hit, dict) else 0.0)
            anchor_id = anchor_seg.get("id") if isinstance(anchor_seg, dict) else None

        probes.append({
            "slot": int(idx),
            "code": code_text,
            "wrong_type": wrong_type,
            "concept_tag": concept_tag,
            "start": float((hit or {}).get("start", 0.0) or 0.0),
            "end": float((hit or {}).get("end", 0.0) or 0.0),
            "score": float(score or 0.0),
            "concept_hits": int(concept_hits),
            "wrong_type_hits": int(wrong_type_hits),
            "text": hit_text,
            "source": str((hit or {}).get("source") or "none"),
            "anchor_id": anchor_id,
        })

    valid = [p for p in probes if p["end"] > p["start"]]
    strengths = [float(p["score"] or 0.0) + (0.05 * float(p["concept_hits"] or 0)) + (0.03 * float(p["wrong_type_hits"] or 0)) for p in probes]
    avg_strength = sum(strengths) / max(1, len(strengths))
    strong = [p for p in probes if (float(p["score"] or 0.0) >= 0.12) or (p["concept_hits"] > 0) or (p["wrong_type_hits"] > 0)]

    ordered = True
    if len(valid) >= 2:
        ordered_starts = [p["start"] for p in sorted(valid, key=lambda x: x["slot"])]
        ordered = all(ordered_starts[i] <= ordered_starts[i + 1] + 0.25 for i in range(len(ordered_starts) - 1))

    gap_spread = 0.0
    if len(valid) >= 3:
        ordered_valid = sorted(valid, key=lambda x: x["slot"])
        gaps = [max(0.0, ordered_valid[i + 1]["start"] - ordered_valid[i]["start"]) for i in range(len(ordered_valid) - 1)]
        if len(gaps) >= 2 and sum(gaps) > 0:
            try:
                gap_spread = float(statistics.pstdev(gaps)) / max(0.1, float(statistics.mean(gaps)))
            except Exception:
                gap_spread = 0.0

    if len(strong) <= 1 or avg_strength < 0.12:
        level = "bad"
        mode = "chapter_recommendation"
        reason = "semantic_mismatch"
        recommendation = "字幕和程式概念的對應太弱，直接改成章節推薦。"
    elif not ordered or gap_spread > 0.85:
        level = "warn"
        mode = "chapter_recommendation"
        reason = "local_drift"
        recommendation = "字幕有局部漂移，不適合再做 slot 級精準對齊，建議改章節推薦。"
    else:
        level = "good"
        mode = "subtitle_alignment"
        reason = "alignment_consistent"
        recommendation = "目前字幕仍可用於概念級對齊，但先保留健檢結果。"

    return {
        "ok": True,
        "level": level,
        "mode": mode,
        "reason": reason,
        "recommendation": recommendation,
        "probe_count": len(probes),
        "strong_probe_count": len(strong),
        "avg_strength": round(float(avg_strength), 4),
        "ordered": bool(ordered),
        "gap_spread": round(float(gap_spread), 4),
        "opening_explanation_end_sec": round(float(opening_explanation_cutoff_sec), 2),
        "opening_explanation_count": int(opening_explanation_count),
        "probes": probes,
        "chapter_recommendations": chapter_recommendations,
    }


def _pick_chapter_recommendation(task: dict, wrong_type: str = "", concept_tag: str = "", slot_concept: str = "") -> dict:
    # 優先使用老師確認章節，確保章節推薦時間軸與老師儲存一致。
    teacher_chapters = task.get("concept_chapters_formal") or task.get("teacher_concept_chapters") or []
    if isinstance(teacher_chapters, list) and teacher_chapters:
        target_concept = normalize_concept_name(concept_tag or slot_concept)

        def _family_for_wrong_type(value: str) -> str:
            wt = str(value or "").strip().lower()
            if wt in {"loop_count", "loop_reverse_range", "nested_loop_structure", "star_count", "space_count"}:
                return "loop"
            if wt in {"output_format"}:
                return "output"
            if wt in {"input_int_cast"}:
                return "input"
            if wt in {"if_condition_logic", "if_branch_order", "edge_case_condition"}:
                return "condition"
            return ""

        def _chapter_family(tag: str) -> str:
            t = normalize_concept_name(tag)
            if t in {"loop_count_control", "loop_reverse_range", "nested_loop_structure", "star_formula_2i_minus_1", "space_formula_n_minus_i"}:
                return "loop"
            if t in {"print_separator"}:
                return "output"
            if t in {"input_int_cast"}:
                return "input"
            if t in {"if_condition_logic", "if_branch_order", "edge_case_condition"}:
                return "condition"
            return ""

        target_family = _family_for_wrong_type(wrong_type)
        scored_teacher = []
        for idx, ch in enumerate(teacher_chapters):
            if not isinstance(ch, dict):
                continue
            try:
                s = float(ch.get("start", 0.0))
                e = float(ch.get("end", 0.0))
            except Exception:
                continue
            if e <= s:
                continue
            ctag = normalize_concept_name(ch.get("concept_tag") or ch.get("concept") or ch.get("wrong_type"))
            family = _chapter_family(ctag)
            score = 0
            if target_concept and ctag and target_concept == ctag:
                score += 6
            if target_family and family and target_family == family:
                score += 3
            scored_teacher.append((score, idx, ch, ctag, s, e))

        if scored_teacher:
            scored_teacher.sort(key=lambda item: (item[0], -item[1]), reverse=True)
            _, idx, chosen_ch, chosen_tag, start_sec, end_sec = scored_teacher[0]

            def _sec_to_tc(sec: float) -> str:
                try:
                    total = max(0, int(round(float(sec))))
                except Exception:
                    return "—"
                mm, ss = divmod(total, 60)
                hh, mm = divmod(mm, 60)
                if hh > 0:
                    return f"{hh:02d}:{mm:02d}:{ss:02d}"
                return f"{mm:02d}:{ss:02d}"

            return {
                "chapter_key": f"teacher:{idx}",
                "chapter_label": str(chosen_ch.get("concept_label") or concept_tag_to_label(chosen_tag) or chosen_tag or "章節推薦"),
                "family": _chapter_family(chosen_tag),
                "concept_tag": chosen_tag,
                "concept_tags": [chosen_tag] if chosen_tag else [],
                "slot_start": None,
                "slot_end": None,
                "slots": [],
                "blocks": [],
                "start_sec": round(float(start_sec), 2),
                "end_sec": round(float(end_sec), 2),
                "start": round(float(start_sec), 2),
                "end": round(float(end_sec), 2),
                "time_axis_label": f"{_sec_to_tc(start_sec)} - {_sec_to_tc(end_sec)}",
                "time_axis_detail": f"{float(start_sec):.1f} - {float(end_sec):.1f}",
                "time_axis_source": "teacher_confirmed_chapter",
                "chapter_selection_source": "teacher_confirmed",
            }

    report = _build_subtitle_health_report(task)
    chapters = report.get("chapter_recommendations") or []
    if not isinstance(chapters, list) or not chapters:
        return {}

    def _family_for_wrong_type(value: str) -> str:
        wt = str(value or "").strip().lower()
        if wt in {"loop_count", "loop_reverse_range", "nested_loop_structure", "star_count", "space_count"}:
            return "loop"
        if wt in {"output_format"}:
            return "output"
        if wt in {"input_int_cast"}:
            return "input"
        if wt in {"if_condition_logic", "if_branch_order", "edge_case_condition"}:
            return "condition"
        return ""

    target_family = _family_for_wrong_type(wrong_type)
    target_concept = normalize_concept_name(concept_tag or slot_concept)

    scored = []
    for ch in chapters:
        if not isinstance(ch, dict):
            continue
        family = str(ch.get("family") or "").strip().lower()
        concept_tags = [normalize_concept_name(x) for x in (ch.get("concept_tags") or []) if normalize_concept_name(x)]
        score = 0
        if target_family and family == target_family:
            score += 3
        if target_concept and target_concept in concept_tags:
            score += 4
        if target_concept and target_concept == normalize_concept_name(ch.get("concept_tag") or ""):
            score += 5
        if score > 0:
            scored.append((score, ch))

    if scored:
        scored.sort(key=lambda item: (item[0], int(item[1].get("slot_start") or 0)), reverse=True)
        chosen = dict(scored[0][1])
        chosen["chapter_selection_source"] = "rule_score"
        return chosen

    # 沒有明確命中時，回傳最前面的章節，至少讓學生有可回看的區間。
    chosen = dict(chapters[0] or {})
    chosen["chapter_selection_source"] = "fallback_first"
    return chosen


def _safe_slot_index(slot_key: str) -> Optional[int]:
    try:
        s = str(slot_key or "").strip()
        if not s:
            return None
        # 支援 "1"、"s2"、"第3格"
        if s.isdigit():
            return int(s)
        m = _re.search(r"(\d+)", s)
        if m:
            # 「第3格」轉成 index 2
            n = int(m.group(1))
            if "第" in s and "格" in s:
                return max(0, n - 1)
            return n
        return None
    except Exception:
        return None


def _is_runtime_concept_segment(seg: dict) -> bool:
    try:
        ev = str((seg or {}).get("evidence") or "").strip().lower()
        return ev.startswith("concept=")
    except Exception:
        return False


def _is_generic_uniform_slot_map(seg_map: dict) -> bool:
    """Detect coarse slot maps where most/all slots share the same time range."""
    try:
        if not isinstance(seg_map, dict) or not seg_map:
            return False

        ranges = []
        for _, seg in seg_map.items():
            if not isinstance(seg, dict):
                continue
            if _is_runtime_concept_segment(seg):
                continue
            s = seg.get("start", seg.get("start_ts"))
            e = seg.get("end", seg.get("end_ts"))
            if s is None or e is None:
                continue
            sf = float(s)
            ef = float(e)
            if ef <= sf:
                continue
            ranges.append((round(sf, 1), round(ef, 1)))

        if len(ranges) < 2:
            return False

        uniq = set(ranges)
        return len(uniq) <= 1
    except Exception:
        return False


def _code_anchor_tokens(text: str) -> list:
    """Extract lightweight code anchors to avoid overfitting to full-line matches."""
    raw = str(text or "").strip().lower()
    if not raw:
        return []

    out = []
    seen = set()

    def _push(v: str):
        vv = str(v or "").strip().lower()
        if not vv or vv in seen:
            return
        seen.add(vv)
        out.append(vv)

    # Structural anchors.
    if raw.startswith("if ") or raw.startswith("elif "):
        for t in ["if", "elif", "條件", "判斷", "比較", "成立", "不成立"]:
            _push(t)
    if raw.startswith("else"):
        for t in ["else", "否則", "不成立"]:
            _push(t)

    # Keep only identifier-like tokens; skip Python keywords/operators.
    stop = {
        "if", "elif", "else", "and", "or", "not", "print", "input", "int", "float", "str",
        "for", "while", "in", "range", "true", "false", "none", "pass", "break", "continue",
    }
    for t in _re.findall(r"[a-z_][a-z0-9_]*", raw):
        if t in stop:
            continue
        _push(t)

    return out


def _detect_calc_ops(text: str) -> list:
    """Detect arithmetic operator intent from a code line, prioritized by expected line."""
    s = str(text or "")
    out = []

    def _add(v: str):
        if v not in out:
            out.append(v)

    if "+" in s:
        _add("add")
    if "-" in s:
        _add("sub")
    if "*" in s:
        _add("mul")
    if "//" in s or "/" in s:
        _add("div")
    if "%" in s:
        _add("mod")

    return out


def _calc_op_keyword_map() -> Dict[str, list]:
    return {
        "add": ["加", "加法", "相加", "plus"],
        "sub": ["減", "減法", "相減", "minus", "扣掉"],
        "mul": ["乘", "乘法", "相乘", "times"],
        "div": ["除", "除法", "相除", "divide"],
        "mod": ["餘", "取餘", "餘數", "模", "mod"],
    }


def _calc_operator_score(text: str, wanted_ops: list) -> int:
    """Score subtitle text by whether it matches the desired arithmetic operator(s)."""
    t = str(text or "").lower()
    if not t or not wanted_ops:
        return 0

    score = 0
    kw_map = _calc_op_keyword_map()
    opposites = {
        "add": ["sub"],
        "sub": ["add"],
        "mul": ["div"],
        "div": ["mul"],
        "mod": [],
    }

    for op in wanted_ops:
        if op == "add" and _re.search(r"[a-z0-9_\)\]]\s*\+\s*[a-z0-9_\(\[]", t):
            score += 4
        elif op == "sub" and _re.search(r"[a-z0-9_\)\]]\s*-\s*[a-z0-9_\(\[]", t):
            score += 4
        elif op == "mul" and _re.search(r"[a-z0-9_\)\]]\s*\*\s*[a-z0-9_\(\[]", t):
            score += 4
        elif op == "div" and (_re.search(r"[a-z0-9_\)\]]\s*//\s*[a-z0-9_\(\[]", t) or _re.search(r"[a-z0-9_\)\]]\s*/\s*[a-z0-9_\(\[]", t)):
            score += 4
        elif op == "mod" and _re.search(r"[a-z0-9_\)\]]\s*%\s*[a-z0-9_\(\[]", t):
            score += 4

        for kw in kw_map.get(op, []):
            if kw and kw.lower() in t:
                score += 3

        for opp in opposites.get(op, []):
            for kw in kw_map.get(opp, []):
                if kw and kw.lower() in t:
                    score -= 2

    return score


def _segment_has_completion_tone(text: str) -> bool:
    t = str(text or "").strip().lower()
    if not t:
        return False
    markers = [
        "完成", "作答完成", "寫完", "最後", "總結", "答案", "這樣就", "到這邊", "結束", "收尾",
        "完成了", "完整", "done", "finish", "final",
    ]
    return any(m in t for m in markers)


def _segment_has_topic_shift(text: str, concept: str) -> bool:
    t = str(text or "").strip().lower()
    c = str(concept or "").strip().lower()
    if not t:
        return False

    shift_markers = ["接下來", "下一個", "換成", "再來", "然後", "接著"]
    if any(m in t for m in shift_markers):
        return True

    if c in ("condition", "if_else"):
        # 條件判斷講完後，常切到其他主題詞。
        other_topic_markers = ["for", "while", "迴圈", "input", "輸入", "函式", "def ", "return"]
        return any(m in t for m in other_topic_markers)

    return False


def _looks_like_problem_statement(text: str, concept: str = "", role: str = "") -> bool:
    low = str(text or "").strip().lower()
    if not low:
        return False

    intro_terms = [
        "請設計", "題目", "這一題", "請你", "我們要來", "這裡", "示範", "接著", "再來", "同步編輯器",
        "請輸入", "請完成", "根據題目", "根據以下", "請先", "請直接", "今天我們", "現在我們",
    ]
    if not any(term in low for term in intro_terms):
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
    if any(_re.search(pattern, low) for pattern in code_patterns):
        return False

    if any(sig in low for sig in ["=", "==", "!=", "+", "-", "*", "/", "%"]):
        return False

    try:
        concept_terms = list((_concept_keywords_map().get(str(concept or ""), []) or []))
    except Exception:
        concept_terms = []

    role_terms = []
    if role == "input_read":
        role_terms = ["輸入", "input", "讀入", "讀取"]
    elif role == "output_print":
        role_terms = ["輸出", "印出", "print", "顯示"]
    elif role == "branch_condition":
        role_terms = ["判斷", "如果", "條件", "if", "elif"]
    elif role == "branch_return":
        role_terms = ["回傳", "return", "結果"]
    elif role == "function_def":
        role_terms = ["函式", "定義", "def", "參數"]

    has_concept_signal = any(str(term).lower() in low for term in (concept_terms + role_terms) if str(term or "").strip())
    if has_concept_signal:
        return True

    return True


def _concept_hit_count(text: str, concept: str) -> int:
    t = str(text or "").lower()
    kws = _concept_keywords_map().get(str(concept or ""), []) or []
    c = 0
    for kw in kws:
        k = str(kw or "").strip().lower()
        if k and k in t:
            c += 1
    return c


def _find_subtitle_segment_by_concept(task: dict, concept: str, expected_text: str, actual_text: str, slot_key: str = "") -> Tuple[Optional[float], Optional[float], str, dict]:
    segs = _load_task_subtitle_segments(task)
    if not segs:
        return None, None, "", {"reason": "no_subtitles"}

    slot_idx = _safe_slot_index(slot_key)
    total_slots = 0
    target_i = None
    try:
        parsed = t5doc_to_parsons_task(task)
        total_slots = len((parsed.get("template_slots") or [])) or len((parsed.get("solution_blocks") or []))
    except Exception:
        total_slots = 0
    if slot_idx is not None and total_slots > 1 and len(segs) > 1:
        ratio = max(0.0, min(1.0, float(slot_idx) / float(total_slots - 1)))
        target_i = ratio * float(len(segs) - 1)

    keywords = list(_concept_keywords_map().get(concept, []))
    if concept == "indentation":
        generic = {"縮排", "區塊", "層級"}
        keywords = [k for k in keywords if str(k) not in generic]

    strong_anchors = []
    wanted_ops = _detect_calc_ops(expected_text) or _detect_calc_ops(actual_text)
    for token in [expected_text, actual_text]:
        for anchor in _code_anchor_tokens(token):
            keywords.append(anchor)
            if anchor not in strong_anchors:
                strong_anchors.append(anchor)

    best_score = 0
    scored_idx = []

    for i, s in enumerate(segs):
        t = str(s.get("text") or "").lower()
        score = 0
        anchor_hit_count = 0
        for kw in keywords:
            k = str(kw or "").strip().lower()
            if not k:
                continue
            if k in t:
                # Structural concept keywords weigh slightly higher.
                score += 2 if len(k) >= 2 else 1

        if concept == "calculation" and wanted_ops:
            score += _calc_operator_score(t, wanted_ops)

        if strong_anchors:
            anchor_hit_count = sum(1 for a in strong_anchors if str(a) in t)

        if concept == "indentation" and strong_anchors:
            if anchor_hit_count <= 0:
                score -= 6
            else:
                score += min(6, anchor_hit_count * 2)

        # 優先「真的在講/打程式碼」的字幕句，避免只跳到口語鋪陳。
        if any(tok in t for tok in ["def ", "if ", "elif", "else", "print", "input", "return", "for ", "while ", "range("]):
            score += 3
        if any(tok in t for tok in ["程式", "函式", "縮排", "冒號", "條件", "運算"]):
            score += 1

        # For condition errors, avoid late "already-finished" narration segments.
        if concept in ("condition", "if_else", "logic") and _segment_has_completion_tone(t):
            score -= 3

        # slot 對齊偏好：已知錯誤格時，偏向時間軸上相近位置的字幕段。
        if target_i is not None:
            dist_norm = abs(float(i) - float(target_i)) / max(1.0, float(len(segs) - 1))
            score += max(0.0, 2.5 - (dist_norm * 5.0))

        if score > best_score:
            best_score = score
        scored_idx.append((i, score, anchor_hit_count))

    if not scored_idx:
        return None, None, "", {"reason": "no_candidates"}

    candidates = [i for i, sc, _ in scored_idx if sc == best_score and sc > 0]
    if concept == "indentation" and strong_anchors:
        anchored = [i for i, sc, ah in scored_idx if sc == best_score and sc > 0 and ah > 0]
        if anchored:
            candidates = anchored
    if not candidates:
        return None, None, "", {
            "reason": "no_positive_score",
            "best_score": int(best_score),
            "keywords": keywords[:20],
            "strong_anchors": strong_anchors[:20],
            "wanted_ops": wanted_ops,
        }

    # 若有 slot 資訊，依「程式格順序」對齊字幕時間軸，避免總是跳到前段。
    best_i = candidates[0]
    if target_i is not None:
        # Condition explanation usually appears before "completed answer" recap.
        if concept in ("condition", "if_else"):
            early = [i for i in candidates if i <= int(0.85 * (len(segs) - 1))]
            pool = early or candidates
        else:
            pool = candidates
        best_i = min(pool, key=lambda i: abs(float(i) - target_i))
    else:
        # 沒有 slot 資訊時，保守取最晚的同分段，避免過早跳段。
        best_i = max(candidates)

    before = 1
    after = 2 if concept in ("if_else", "condition", "logic", "indentation") else 1
    a = max(0, best_i - before)
    b = min(len(segs) - 1, best_i + after)
    start = float(segs[a].get("start") or 0.0)
    end = float(segs[b].get("end") or 0.0)

    # 片段先保底，再偵測是否已切換到「不是學生主錯概念」的段落。
    min_span = 12.0 if concept in ("condition", "if_else", "logic") else 18.0
    miss_concept_streak = 0
    stop_reason = "reach_tail"
    while b < len(segs) - 1:
        if (end - start) >= min_span:
            next_txt = str((segs[b + 1] or {}).get("text") or "")
            next_hit = _concept_hit_count(next_txt, concept)

            if next_hit <= 0:
                miss_concept_streak += 1
            else:
                miss_concept_streak = 0

            if miss_concept_streak >= 2 and _segment_has_topic_shift(next_txt, concept):
                stop_reason = "topic_shift_after_miss"
                break

        b += 1
        end = float(segs[b].get("end") or end)

        # 最長仍給上限，避免回看片段太長。
        if (end - start) >= 26.0:
            stop_reason = "max_span_cap"
            break

    if end <= start:
        return None, None, "", {"reason": "invalid_range"}

    ctx = extract_context_around(segs, start, end, window=5)
    debug = {
        "reason": "ok",
        "concept": concept,
        "wanted_ops": wanted_ops,
        "strong_anchors": strong_anchors[:20],
        "slot_idx": slot_idx,
        "target_index": (float(target_i) if target_i is not None else None),
        "best_score": int(best_score),
        "candidate_count": len(candidates),
        "selected_index": int(best_i),
        "selected_start": float(start),
        "selected_end": float(end),
        "stop_reason": stop_reason,
        "miss_concept_streak": int(miss_concept_streak),
        "keywords": [str(k) for k in (keywords[:20] or [])],
    }
    return start, end, (ctx or ""), debug


def ai_hint_and_segment_for_wrong(
    task: dict,
    slot_key: str,
    expected_text: str,
    actual_text: str,
    level: str,
    slot_label: str,
    error_type: str = "",
    allow_ai: bool = True,
) -> Tuple[str, Optional[float], Optional[float], str, str, dict]:
    """
    錯誤時回傳 (hint, start, end, subtitle_context, concept, debug)
    - LLM/AI 僅負責提示與概念分類
    - 本函式不參與任何時間軸對齊（start/end 永遠為 None）
    """
    del level  # kept for compatibility
    del slot_label  # kept for compatibility

    start = None
    end = None
    subtitle_context = ""
    align_debug = {
        "source": "hint_only",
        "allow_ai": bool(allow_ai),
        "concept": "",
        "alignment_disabled": True,
    }

    if allow_ai:
        concept, ai_hint, _ = _ai_classify_wrong_concept(task, expected_text, actual_text, error_type=error_type)
    else:
        concept = _rule_based_wrong_concept(expected_text, actual_text, error_type=error_type)
        ai_hint = ""

    align_debug["concept"] = str(concept or "")

    if ai_hint:
        hint = ai_hint
    elif concept == "indentation":
        hint = "請確認這行程式是否需要放在 if / else 區塊內，並檢查縮排層級是否正確。"
    elif concept == "if_else":
        hint = "請確認條件成立與不成立時的輸出分支是否正確對應到 if / else。"
    elif concept == "print":
        hint = "請檢查目前這行輸出內容與輸出時機是否符合題目要求。"
    elif concept == "condition":
        hint = "請重新確認判斷條件本身是否正確，條件成立時應執行哪些語句。"
    else:
        hint = "請檢查這一格在程式流程中的角色，確認其位置與語意是否正確。"

    try:
        update = {
            f"ai_slot_hints.{str(slot_key)}": hint,
            f"ai_slot_hints_concept.{str(slot_key)}": concept,
        }
        db.parsons_tasks.update_one({"_id": task.get("_id")}, {"$set": update})
    except Exception:
        pass

    return hint, start, end, subtitle_context, concept, align_debug


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


def _get_wrong_slots_and_core_compare(task_doc: dict, student_order: list, answer_lines: list = None):
    parsed = t5doc_to_parsons_task(task_doc)
    expected_ids = _get_expected_ids_from_task(task_doc)
    pool = parsed.get("pool") or []
    pool_map = {str(b.get("id")): b for b in pool if b.get("id") is not None}

    aligned = list(student_order or [])
    if len(aligned) < len(expected_ids):
        aligned = aligned + [None] * (len(expected_ids) - len(aligned))

    wrong_slots = []
    id_mismatch_slots = []
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
        id_mismatch_slots.append(i)

    expected_blocks = parsed.get("solution_blocks") or []

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

    indent_errors = []
    lines = list(answer_lines or [])
    for i in range(min(len(expected_ids), len(lines), len(expected_indent_list))):
        expected_indent = int(expected_indent_list[i] or 0)
        user_line = str(lines[i] or "")
        user_indent = len(user_line) - len(user_line.lstrip(" "))
        if user_indent != expected_indent:
            indent_errors.append(i)
            if i not in wrong_slots:
                wrong_slots.append(i)

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

    expected_lines = [str((b or {}).get("text") or "") for b in (expected_blocks or [])]
    if len(expected_lines) < len(expected_ids):
        expected_lines += [""] * (len(expected_ids) - len(expected_lines))

    control_slots = []
    semantic_slots = []
    main_slots = []
    for i in id_mismatch_slots:
        exp_line = expected_lines[i] if i < len(expected_lines) else ""
        act_id = str(aligned[i]) if i < len(aligned) and aligned[i] is not None else ""
        act_line = str(pool_map.get(act_id, {}).get("text", "") or "")
        k = _line_kind(exp_line)
        if k == "control":
            control_slots.append(i)
        elif k == "semantic":
            semantic_slots.append(i)
        else:
            k2 = _line_kind(act_line)
            if k2 == "control":
                control_slots.append(i)
            elif k2 == "semantic":
                semantic_slots.append(i)
            else:
                main_slots.append(i)

    first_error_type = None
    if indent_errors:
        first_error_type = "indentation"
    elif control_slots:
        first_error_type = "condition"
    elif semantic_slots:
        first_error_type = "calculation"
    elif main_slots:
        first_error_type = "structure"

    wrong_slots = sorted(set(wrong_slots))

    extra_wrong = max(0, len(student_order or []) - len(expected_ids))
    is_correct = (len(wrong_slots) == 0 and extra_wrong == 0)

    return {
        "is_correct": is_correct,
        "wrong_slots": wrong_slots,
        "indent_errors": indent_errors,
        "expected_ids": expected_ids,
        "aligned_student_ids": aligned,
        "extra_wrong": extra_wrong,
        "first_error_type": first_error_type,
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

def _generate_submit_ai_feedback(task_doc: dict, answer_ids: list, answer_lines: list = None) -> dict:
    try:
        compare_result = _get_wrong_slots_and_core_compare(task_doc, answer_ids, answer_lines=answer_lines)
        return _call_ai_for_generic_diagnosis(task_doc, compare_result)
    except Exception as e:
        import traceback
        print("\n========== SUBMIT AI DIAGNOSIS ERROR ==========")
        print("error =", repr(e))
        traceback.print_exc()
        print("===============================================\n")

        wrong_slots = []
        try:
            compare_result = _get_wrong_slots_and_core_compare(task_doc, answer_ids, answer_lines=answer_lines)
            wrong_slots = compare_result.get("wrong_slots", []) or []
        except Exception:
            pass

        return {
            "is_correct": False,
            "error_code": "SUBMIT_AI_DIAGNOSIS_ERROR",
            "wrong_slots": wrong_slots,
            "diagnosis_summary": "AI 診斷暫時無法使用，已改用系統提示。",
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



def _sanitize_hint_text(hint_text: str, expected_text: str, actual_text: str = "") -> str:
    """防止提示直接洩漏完整答案，但保留錯誤類型資訊。"""
    hint = str(hint_text or "").strip()
    expected = str(expected_text or "").strip()
    actual = str(actual_text or "").strip()

    if not hint:
        return ""

    low = hint.lower()
    expected_low = expected.lower()
    actual_low = actual.lower()

    if expected and expected_low in low:
        if "print(" in actual_low:
            return "請檢查這一行是否只輸出變數內容，而少了題目要求的固定文字。"
        return "請檢查這一行是否缺少題目要求的一部分必要內容。"

    if expected and actual and expected_low in low and actual_low in low:
        if "print(" in actual_low:
            return "目前這行輸出可能不完整，請比較是否同時包含固定文字與變數資訊。"
        return "請比較你目前這一行與題目要求的差異，檢查是否少了一部分內容。"

    return hint


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


def _rewrite_second_hint_with_ai(
    raw_hint: str,
    expected_text: str,
    actual_text: str,
    concept_hint: str = "",
) -> str:
    if not ai_enabled():
        return ""

    model = _model_for_feedback()
    user_prompt = f"""
你是 Python Parsons 提示改寫器。
請把以下 second_hint 改寫為「更具體但不洩漏答案」的提示。

規則：
1) 只能用繁體中文，一句到兩句。
2) 不可包含完整正解、完整程式碼、或引號包住的答案字串。
3) 不可使用「請改成 XXX」「正確寫法是 XXX」語句。
4) 要指出差異方向（例如：少了固定文字、只輸出變數、輸出內容不完整）。
5) 不可離題（不要引入 if/else、迴圈 等無關概念）。

raw_second_hint: {raw_hint}
expected_text: {expected_text}
actual_text: {actual_text}
concept_hint: {concept_hint}

輸出 JSON：
{{"safe_second_hint": "..."}}
""".strip()

    try:
        data = parsons_ai.call_openai_json(
            model=model,
            system="你是教學提示改寫器，只輸出 JSON。",
            user=user_prompt,
        ) or {}
        return str(data.get("safe_second_hint") or "").strip()
    except Exception:
        return ""


def _safe_second_hint(ai_second_hint: str, expected_text: str, actual_text: str, concept_hint: str = "", possible_causes=None) -> str:
    """second_hint 優先用 AI，但若 AI 過度洩漏或太弱，改用安全且有資訊量的版本。"""
    possible_causes = possible_causes or []
    raw = str(ai_second_hint or "").strip()
    cleaned = _sanitize_hint_text(raw, expected_text, actual_text)

    overlap = _lexical_overlap_ratio(cleaned, expected_text)
    leaked = False
    if raw and cleaned != raw:
        leaked = True
    if overlap >= 0.50:
        leaked = True
    if _re.search(r"`[^`]{3,}`|\"[^\"]{3,}\"|'[^']{3,}'", cleaned or ""):
        leaked = True

    if cleaned and not leaked:
        return cleaned

    # 洩漏風險時，優先讓 AI 重新改寫成安全但具體的二次提示。
    rewritten = _rewrite_second_hint_with_ai(raw or cleaned, expected_text, actual_text, concept_hint=concept_hint)
    rewritten_clean = _sanitize_hint_text(rewritten, expected_text, actual_text)
    if rewritten_clean and _lexical_overlap_ratio(rewritten_clean, expected_text) < 0.50:
        return rewritten_clean

    for cause in possible_causes:
        c = _sanitize_hint_text(str(cause or "").strip(), expected_text, actual_text)
        if c:
            return c

    actual_low = str(actual_text or "").strip().lower()
    if "print(" in actual_low:
        return "目前這行只呈現部分輸出內容，請檢查是否同時包含固定文字與變數資訊。"

    concept_cleaned = _sanitize_hint_text(concept_hint, expected_text, actual_text)
    if concept_cleaned:
        return concept_cleaned

    return "請檢查這一行是否缺少題目要求的一部分必要內容。"


def _call_ai_for_generic_diagnosis(task_doc: dict, compare_result: dict) -> dict:
    """Safe generic diagnosis that reuses the SRT-grounded progressive hint path."""
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
                "reason": "",
            },
        }

    wrong_slots = _int_list(compare_result.get("wrong_slots") or [])
    wrong_index = wrong_slots[0] if wrong_slots else None
    slot_label = (
        f"第 {wrong_index + 1} 格"
        if wrong_index is not None
        else "錯誤位置"
    )

    expected_text = str(compare_result.get("expected_text") or "").strip()
    actual_text = str(compare_result.get("actual_text") or "").strip()
    error_type = str(
        compare_result.get("error_type")
        or compare_result.get("first_error_type")
        or compare_result.get("error_code")
        or "logic"
    ).strip()

    if wrong_index is not None and (not expected_text or not actual_text):
        try:
            expected_ids = compare_result.get("expected_ids") or []
            student_ids = compare_result.get("aligned_student_ids") or []
            parsed = t5doc_to_parsons_task(task_doc)
            pool = {
                str(block.get("id")): block
                for block in (parsed.get("pool") or [])
                if isinstance(block, dict)
            }
            if not expected_text and wrong_index < len(expected_ids):
                expected_text = str(
                    pool.get(str(expected_ids[wrong_index]), {}).get("text") or ""
                ).strip()
            if not actual_text and wrong_index < len(student_ids):
                actual_text = str(
                    pool.get(str(student_ids[wrong_index]), {}).get("text") or ""
                ).strip()
        except Exception:
            pass

    first_result = _build_short_reflective_feedback(
        task=task_doc,
        slot_label=slot_label,
        expected_text=expected_text,
        actual_text=actual_text,
        error_type=error_type,
        hint_level=1,
        wrong_index=wrong_index,
        first_hint="",
    )
    first_feedback = first_result.get("feedback") or {}
    first_hint = str(first_feedback.get("first_hint") or "").strip()

    second_result = _build_short_reflective_feedback(
        task=task_doc,
        slot_label=slot_label,
        expected_text=expected_text,
        actual_text=actual_text,
        error_type=error_type,
        hint_level=2,
        wrong_index=wrong_index,
        first_hint=first_hint,
    )
    second_feedback = second_result.get("feedback") or {}

    merged_feedback = {
        **first_feedback,
        **second_feedback,
        "first_hint": first_hint,
        "second_hint": str(second_feedback.get("second_hint") or "").strip(),
    }

    subtitle_range = second_feedback.get("subtitle_range") or first_feedback.get("subtitle_range") or {}

    return {
        "is_correct": False,
        "error_code": "PROGRESSIVE_SRT_DIAGNOSIS",
        "wrong_slots": wrong_slots,
        "diagnosis_summary": str(
            second_result.get("diagnosis_summary")
            or first_result.get("diagnosis_summary")
            or "學生作答與預期程式流程不一致"
        ),
        "feedback": merged_feedback,
        "recommended_review": {
            "start": subtitle_range.get("start"),
            "end": subtitle_range.get("end"),
            "reason": "依錯誤概念對應的 SRT 範圍提供由廣到窄的提示。",
        },
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
    };
