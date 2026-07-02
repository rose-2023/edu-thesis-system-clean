"""
Evaluate subtitle alignment precision@1 using manual labels.

Label JSON format (list):
[
  {
    "task_id": "...",
    "slot_index": 2,
    "true_start": 52.0,
    "true_end": 70.0
  }
]

Usage:
  python tools/evaluate_precision_at_1.py --labels tools/labels_p1.json
  python tools/evaluate_precision_at_1.py --labels tools/labels_p1.json --overlap_threshold 0.3
"""

import argparse
import json
import os
from typing import Any, Dict, List, Tuple

from bson import ObjectId
from pymongo import MongoClient

from app.routes.parsons_retrieval import build_subtitle_index, retrieve_best_segment
from app.routes.parsons_service import parse_srt_segments, read_subtitle_text


def _normalize_overlap(pred: Tuple[float, float], truth: Tuple[float, float]) -> float:
    ps, pe = pred
    ts, te = truth
    ov = max(0.0, min(pe, te) - max(ps, ts))
    base = max(1e-6, min(max(pe - ps, 0.0), max(te - ts, 0.0)))
    return ov / base


def _read_subtitle_text_for_task(task: Dict[str, Any]) -> str:
    prompt_source = task.get("prompt_source") or {}
    raw = (
        str(prompt_source.get("subtitle_preview") or "")
        or str(prompt_source.get("subtitle_text") or "")
        or str((task.get("source_subtitle") or {}).get("text_used") or "")
        or str(task.get("subtitle_text_used") or "")
    ).strip()
    if raw:
        return raw

    subtitle_path = (
        str(prompt_source.get("subtitle_path") or "").strip()
        or str(task.get("subtitle_path") or "").strip()
    )
    if subtitle_path:
        return str(read_subtitle_text(subtitle_path) or "").strip()

    return ""


def _build_index_from_task(task: Dict[str, Any]) -> Dict[str, Any]:
    cached = task.get("subtitle_ir_cache") or {}
    if isinstance(cached, dict) and (cached.get("segments") or []):
        return cached

    raw = _read_subtitle_text_for_task(task)
    segs = parse_srt_segments(raw) if "-->" in raw else []
    if not segs:
        return {}
    return build_subtitle_index(segs)


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate precision@1 for slot-to-subtitle retrieval")
    ap.add_argument("--mongo_uri", default="mongodb://127.0.0.1:27017")
    ap.add_argument("--db", default="thesis_system")
    ap.add_argument("--labels", required=True)
    ap.add_argument("--overlap_threshold", type=float, default=0.3)
    ap.add_argument("--export_json", default="")
    args = ap.parse_args()

    labels_path = args.labels
    if not os.path.exists(labels_path):
        raise SystemExit(f"labels file not found: {labels_path}")

    with open(labels_path, "r", encoding="utf-8") as f:
        labels = json.load(f)
    if not isinstance(labels, list):
        raise SystemExit("labels must be a JSON array")

    cli = MongoClient(args.mongo_uri)
    db = cli[args.db]

    total = 0
    correct = 0
    rows: List[Dict[str, Any]] = []

    for item in labels:
        try:
            task_id = str(item.get("task_id") or "").strip()
            slot_index = int(item.get("slot_index"))
            true_start = float(item.get("true_start"))
            true_end = float(item.get("true_end"))
        except Exception:
            continue

        if not task_id:
            continue

        task = None
        if ObjectId.is_valid(task_id):
            task = db.parsons_tasks.find_one({"_id": ObjectId(task_id)})
        if not task:
            task = db.parsons_tasks.find_one({"_id": task_id})
        if not task:
            continue

        solution_blocks = task.get("solution_blocks") or []
        if slot_index < 0 or slot_index >= len(solution_blocks):
            continue

        idx = _build_index_from_task(task)
        if not idx:
            continue

        block = solution_blocks[slot_index] or {}
        query = (
            str(block.get("text") or "").strip()
            + " "
            + str(block.get("semantic_zh") or block.get("meaning_zh") or "").strip()
        ).strip()

        pred_seg, pred_score = retrieve_best_segment(query, idx)
        if not pred_seg:
            continue

        pred_start = float(pred_seg.get("start", 0.0))
        pred_end = float(pred_seg.get("end", 0.0))
        ov = _normalize_overlap((pred_start, pred_end), (true_start, true_end))
        ok = ov >= float(args.overlap_threshold)

        total += 1
        if ok:
            correct += 1

        rows.append({
            "task_id": task_id,
            "slot_index": slot_index,
            "pred_start": pred_start,
            "pred_end": pred_end,
            "pred_score": round(float(pred_score), 4),
            "true_start": true_start,
            "true_end": true_end,
            "overlap": round(float(ov), 4),
            "correct": bool(ok),
            "level": str(task.get("level") or ""),
            "unit": str(task.get("unit") or ""),
        })

    p1 = (correct / float(total)) if total else 0.0

    print("=" * 64)
    print("Precision@1 Evaluation")
    print("=" * 64)
    print(f"samples: {total}")
    print(f"correct: {correct}")
    print(f"precision@1: {p1:.4f}")
    print(f"overlap_threshold: {float(args.overlap_threshold):.2f}")

    if args.export_json:
        out_path = args.export_json
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({
                "samples": total,
                "correct": correct,
                "precision_at_1": p1,
                "overlap_threshold": float(args.overlap_threshold),
                "rows": rows,
            }, f, ensure_ascii=False, indent=2)
        print(f"exported: {out_path}")


if __name__ == "__main__":
    main()
