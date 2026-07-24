"""Initialize or append the append-only feedback randomization sequence.

Examples:
  python -m app.scripts.init_feedback_randomization
  python -m app.scripts.init_feedback_randomization --append --slots 198
  新增 40 個隨機化位置，接在現有位置之後
"""

from __future__ import annotations

import argparse
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError


load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.db import db
from app.randomization import (
    DEFAULT_SEQUENCE_VERSION,
    DEFAULT_STUDY_ID,
    SLOT_COLLECTION,
    ensure_randomization_indexes,
)

def _utc_now():
    return datetime.now(timezone.utc)


def _block_sizes(total_slots: int) -> list[int]:
    """Return a shuffled 3/6 block mix that exactly fills a three-arm capacity."""
    if total_slots < 3 or total_slots % 3:
        raise ValueError("slots must be a multiple of 3 and at least 3")

    # Find a mixed sequence of 3- and 6-person balanced blocks.
    candidates = []
    for size6_count in range(1, total_slots // 6 + 1):
        remaining = total_slots - (size6_count * 6)
        if remaining >= 3 and remaining % 3 == 0:
            size3_count = remaining // 3
            if size3_count >= 1:
                candidates.append((size3_count, size6_count))
    if not candidates:
        # A single 3-person block is still balanced, even though it cannot be
        # mixed with a 6-person block.
        if total_slots in {3, 6}:
            return [total_slots]
        raise ValueError("slots cannot be represented by a mixed 3/6 block sequence")

    size3_count, size6_count = min(
        candidates,
        key=lambda item: (abs(item[0] - item[1]), -min(item[0], item[1])),
    )
    sizes = [3] * size3_count + [6] * size6_count
    secrets.SystemRandom().shuffle(sizes)
    return sizes


def _slot_documents(study_id: str, sequence_version: str, start_position: int, start_block_id: int, total_slots: int):
    rng = secrets.SystemRandom()
    docs = []
    position = start_position
    block_id = start_block_id
    created_at = _utc_now()
    for block_size in _block_sizes(total_slots):
        repetitions = block_size // 3
        strategies = ["A"] * repetitions + ["B"] * repetitions + ["C"] * repetitions
        rng.shuffle(strategies)
        for strategy in strategies:
            docs.append({
                "study_id": study_id,
                "sequence_version": sequence_version,
                "position": position,
                "block_id": f"block_{block_id:04d}",
                "block_size": block_size,
                "feedback_strategy": strategy,
                "status": "available",
                "created_at": created_at,
            })
            position += 1
        block_id += 1
    return docs


def main(argv=None):
    parser = argparse.ArgumentParser(description="Initialize feedback randomization slots")
    parser.add_argument("--study-id", default=DEFAULT_STUDY_ID)
    parser.add_argument("--sequence-version", default=DEFAULT_SEQUENCE_VERSION)
    parser.add_argument("--slots", type=int, default=198)
    parser.add_argument("--append", action="store_true", help="append slots after the current last position")
    args = parser.parse_args(argv)

    study_id = str(args.study_id or "").strip()
    sequence_version = str(args.sequence_version or "").strip()
    if not study_id or not sequence_version:
        parser.error("study-id and sequence-version are required")

    try:
        ensure_randomization_indexes()
        slots = db[SLOT_COLLECTION]
        existing = list(slots.find(
            {"study_id": study_id},
            {"position": 1, "block_id": 1, "sequence_version": 1},
        ).sort("position", ASCENDING))

        if existing and not args.append:
            available_count = slots.count_documents({
                "study_id": study_id,
                "sequence_version": sequence_version,
                "status": "available",
            })
            claimed_count = slots.count_documents({
                "study_id": study_id,
                "sequence_version": sequence_version,
                "status": "claimed",
            })
            print(
                "Sequence already exists "
                f"({available_count} available, {claimed_count} claimed); "
                "use --append to add new positions without reordering."
            )
            return 2
        if existing and any(str(row.get("sequence_version") or "") != sequence_version for row in existing):
            print("Existing study_id uses another sequence_version; create a new study_id instead.")
            return 2

        last_position = int(existing[-1].get("position") or 0) if existing else 0
        last_block = 0
        if existing:
            try:
                last_block = max(int(str(row.get("block_id") or "").rsplit("_", 1)[-1]) for row in existing)
            except (TypeError, ValueError):
                print("Existing block_id values are invalid; refusing to append.")
                return 2

        docs = _slot_documents(study_id, sequence_version, last_position + 1, last_block + 1, args.slots)
        slots.insert_many(docs, ordered=True)
        print(f"Created {len(docs)} slots for {study_id}/{sequence_version}, positions {docs[0]['position']}-{docs[-1]['position']}.")
        return 0
    except (DuplicateKeyError, PyMongoError) as exc:
        print(f"Randomization initialization failed without deleting data: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
