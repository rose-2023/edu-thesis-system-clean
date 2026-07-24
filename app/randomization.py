"""Server-side variable-block randomization for the feedback study.

The slot list is created before recruitment and is append-only.  A formal
student claims one slot when the account is imported; the browser never decides
a group and completing a pretest never changes an assignment.
新增 40 個隨機化位置，接在現有位置之後
A → control、B → experimental_1、C → experimental_2
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from .db import db


TEST_STUDENT_ID = "11461127"
DEFAULT_STUDY_ID = os.environ.get(
    "RANDOMIZATION_STUDY_ID", "parsons_feedback_study_v2"
).strip() or "parsons_feedback_study_v2"
DEFAULT_SEQUENCE_VERSION = os.environ.get(
    "RANDOMIZATION_SEQUENCE_VERSION", "v2_three_arm"
).strip() or "v2_three_arm"
ALLOCATION_RATIO = "1:1:1"
ASSIGNMENT_METHOD = "variable_block_randomization_v2_three_arm"
SLOT_COLLECTION = "randomization_slots"


class RandomizationSlotsExhausted(RuntimeError):
    """Raised when the pre-generated, append-only sequence has no free slot."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def study_config() -> Dict[str, str]:
    return {
        "study_id": DEFAULT_STUDY_ID,
        "sequence_version": DEFAULT_SEQUENCE_VERSION,
        "allocation_ratio": ALLOCATION_RATIO,
    }


def is_test_data_user(user: Optional[Dict[str, Any]], student_id: str = "") -> bool:
    user = user or {}
    sid = str(student_id or user.get("student_id") or "").strip()
    return sid == TEST_STUDENT_ID or user.get("is_test_data") is True


def ensure_randomization_indexes() -> None:
    """Create only additive indexes; never modify participant or slot records."""
    db.users.create_index(
        [("student_id", ASCENDING)],
        name="uniq_users_student_id",
        unique=True,
        partialFilterExpression={"student_id": {"$type": "string"}},
    )
    slots = db[SLOT_COLLECTION]
    slots.create_index(
        [("study_id", ASCENDING), ("position", ASCENDING)],
        name="uniq_randomization_slots_study_position",
        unique=True,
    )
    slots.create_index(
        [("study_id", ASCENDING), ("student_id", ASCENDING)],
        name="uniq_randomization_slots_study_student",
        unique=True,
        # A compound sparse index would also index every available slot
        # because ``study_id`` is present.  A partial index indexes only
        # claimed slots with an actual string student_id.
        partialFilterExpression={"student_id": {"$type": "string"}},
    )
    slots.create_index(
        [
            ("study_id", ASCENDING),
            ("sequence_version", ASCENDING),
            ("status", ASCENDING),
            ("position", ASCENDING),
        ],
        name="randomization_slots_claim_order",
    )


def _normalized_strategy(value: Any) -> Optional[str]:
    strategy = str(value or "").strip().upper()
    return strategy if strategy in {"A", "B", "C"} else None


def _strategy_from_user(user: Dict[str, Any]) -> Optional[str]:
    strategy = _normalized_strategy(user.get("feedback_strategy"))
    if strategy:
        return strategy
    group_type = str(user.get("group_type") or "").strip().lower()
    if group_type in {"experimental", "experimental_1"}:
        return "B"
    if group_type == "experimental_2":
        return "C"
    if group_type == "control":
        # The current control condition is A.  Historical two-arm users retain
        # their explicit feedback_strategy (C), so this fallback is only for
        # records that were never assigned a strategy.
        return "A"
    return None


def _group_type_from_strategy(strategy: str) -> str:
    return {
        "A": "control",
        "B": "experimental_1",
        "C": "experimental_2",
    }[strategy]


def _slot_for_student(student_id: str) -> Optional[Dict[str, Any]]:
    return db[SLOT_COLLECTION].find_one(
        {
            "study_id": DEFAULT_STUDY_ID,
            "student_id": student_id,
            "status": "claimed",
        }
    )


def _assignment_payload(slot: Dict[str, Any], assigned_at: Optional[datetime] = None) -> Dict[str, Any]:
    strategy = _normalized_strategy(slot.get("feedback_strategy"))
    if not strategy:
        raise ValueError("randomization slot has an invalid feedback_strategy")
    return {
        "assignment_status": "assigned",
        "assignment_method": ASSIGNMENT_METHOD,
        "allocation_ratio": ALLOCATION_RATIO,
        "feedback_strategy": strategy,
        "group_type": _group_type_from_strategy(strategy),
        "randomization_sequence_version": str(slot.get("sequence_version") or DEFAULT_SEQUENCE_VERSION),
        "randomization_position": int(slot.get("position")),
        "randomization_block_id": str(slot.get("block_id") or ""),
        "assigned_at": assigned_at or slot.get("claimed_at") or now_utc(),
        "assignment_locked": True,
        "updated_at": now_utc(),
    }


def _persist_slot_assignment(student_id: str, slot: Dict[str, Any]) -> Dict[str, Any]:
    payload = _assignment_payload(slot)
    result = db.users.update_one(
        {"student_id": student_id, "role": "student"},
        {"$set": payload},
    )
    if result.matched_count != 1:
        raise RuntimeError("student record disappeared while completing randomization")
    return payload


def _claimed_result(slot: Dict[str, Any], assignment: Dict[str, Any], *, recovered: bool) -> Dict[str, Any]:
    return {
        "assigned": True,
        "recovered": recovered,
        "feedback_strategy": assignment["feedback_strategy"],
        "group_type": assignment["group_type"],
        "study_id": DEFAULT_STUDY_ID,
        "sequence_version": assignment["randomization_sequence_version"],
        "position": assignment["randomization_position"],
        "block_id": assignment["randomization_block_id"],
        "assignment_status": "assigned",
    }


def assign_feedback_strategy_on_import(student_id: str) -> Dict[str, Any]:
    """Assign one A/B/C feedback strategy exactly once for an imported student.

    The unique sparse slot index is the cross-process concurrency guard.  If two
    requests for the same student race, only one can claim a slot; the loser
    re-reads that claimed slot and returns the identical assignment.
    """
    sid = str(student_id or "").strip()
    if not sid:
        raise ValueError("student_id is required")

    ensure_randomization_indexes()
    user = db.users.find_one({"student_id": sid, "role": "student"})
    if not user:
        raise ValueError("student not found")

    if is_test_data_user(user, sid):
        db.users.update_one(
            {"_id": user["_id"]},
            {
                "$set": {
                    "assignment_status": "excluded_test_data",
                    "updated_at": now_utc(),
                }
            },
        )
        return {
            "assigned": False,
            "assignment_status": "excluded_test_data",
            "reason": "test_data",
            "feedback_strategy": _strategy_from_user(user),
        }

    locked_strategy = _strategy_from_user(user)
    if user.get("assignment_locked") is True:
        return {
            "assigned": True,
            "recovered": False,
            "feedback_strategy": locked_strategy or "B",
            "group_type": user.get("group_type"),
            "study_id": DEFAULT_STUDY_ID,
            "sequence_version": user.get("randomization_sequence_version"),
            "position": user.get("randomization_position"),
            "block_id": user.get("randomization_block_id"),
            "assignment_status": user.get("assignment_status") or "assigned",
        }

    # Historical manual/matched-pair assignments remain untouched and must not
    # silently consume a new randomized position.
    existing_method = str(user.get("assignment_method") or "").strip()
    if user.get("assignment_status") == "assigned" and existing_method != ASSIGNMENT_METHOD:
        return {
            "assigned": False,
            "assignment_status": "legacy_assignment_preserved",
            "reason": "legacy_assignment",
            "feedback_strategy": locked_strategy,
        }

    existing_slot = _slot_for_student(sid)
    if existing_slot:
        assignment = _persist_slot_assignment(sid, existing_slot)
        return _claimed_result(existing_slot, assignment, recovered=True)

    claimed_at = now_utc()
    try:
        slot = db[SLOT_COLLECTION].find_one_and_update(
            {
                "study_id": DEFAULT_STUDY_ID,
                "sequence_version": DEFAULT_SEQUENCE_VERSION,
                "status": "available",
            },
            {
                "$set": {
                    "status": "claimed",
                    "student_id": sid,
                    "claimed_at": claimed_at,
                }
            },
            sort=[("position", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        # A concurrent request for this same student has already claimed the
        # only allowed slot.  Re-read it instead of consuming another one.
        slot = _slot_for_student(sid)
        if slot:
            assignment = _persist_slot_assignment(sid, slot)
            return _claimed_result(slot, assignment, recovered=True)
        raise
    except PyMongoError:
        raise

    if not slot:
        # A winning concurrent request may have claimed this student's slot
        # immediately before the available-slot query became empty.
        slot = _slot_for_student(sid)
        if slot:
            assignment = _persist_slot_assignment(sid, slot)
            return _claimed_result(slot, assignment, recovered=True)
        raise RandomizationSlotsExhausted(
            f"No available randomization slot for study {DEFAULT_STUDY_ID}"
        )

    assignment = _persist_slot_assignment(sid, slot)
    return _claimed_result(slot, assignment, recovered=False)


def assign_feedback_strategy_after_pretest(student_id: str) -> Dict[str, Any]:
    """Backward-compatible alias for older callers.

    Assignment is now performed at student import.  This alias deliberately
    remains idempotent for deployments that still call it during a retry.
    """
    return assign_feedback_strategy_on_import(student_id)
