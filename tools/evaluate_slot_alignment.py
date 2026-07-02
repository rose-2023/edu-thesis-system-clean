"""
Evaluate slot-level subtitle alignment quality for Parsons tasks.

Metrics per task:
- coverage_ratio: mapped_slots / total_slots
- unique_ratio: non-duplicated slots / mapped_slots
- heavy_overlap_pair_count: number of slot pairs with overlap ratio >= threshold
- monotonicity_ratio: non-decreasing start-time transitions / transitions
- span_mean_sec, span_std_sec, span_cv
- strict_ready: True only if all slots mapped + no duplicates + monotonicity == 1.0

Usage:
  python tools/evaluate_slot_alignment.py --limit 200
  python tools/evaluate_slot_alignment.py --task_id 69c56958bf201de3522c6869
  python tools/evaluate_slot_alignment.py --video_id 69bb823ef339ec9732fc7a3f
  python tools/evaluate_slot_alignment.py --export_csv alignment_quality.csv
"""

import argparse
import csv
import math
import os
from typing import Dict, List, Optional, Tuple

from bson import ObjectId
from pymongo import MongoClient


def _get_slot_count(task: Dict) -> int:
    slots = task.get("template_slots") or task.get("solution_blocks") or []
    return max(1, len(slots))


def _extract_slot_segment(task: Dict, slot_idx: int) -> Optional[Dict]:
    seg_map = task.get("ai_segment_map") or {}
    if not isinstance(seg_map, dict):
        return None

    keys = [str(slot_idx), f"s{slot_idx + 1}", f"第{slot_idx + 1}格"]
    for k in keys:
        v = seg_map.get(k)
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
        return {"slot": slot_idx, "key": k, "start": sf, "end": ef}

    return None


def _overlap_ratio(a: Dict, b: Dict) -> float:
    ov = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    base = max(0.001, min(a["end"] - a["start"], b["end"] - b["start"]))
    return ov / base


def _mean(xs: List[float]) -> float:
    if not xs:
        return 0.0
    return sum(xs) / float(len(xs))


def _std(xs: List[float]) -> float:
    if len(xs) <= 1:
        return 0.0
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / float(len(xs))
    return math.sqrt(var)


def evaluate_task_alignment(task: Dict, overlap_threshold: float = 0.8) -> Dict:
    total_slots = _get_slot_count(task)

    mapped: List[Dict] = []
    missing_slots: List[int] = []
    for i in range(total_slots):
        seg = _extract_slot_segment(task, i)
        if seg is None:
            missing_slots.append(i)
        else:
            mapped.append(seg)

    mapped_slots = len(mapped)
    coverage_ratio = mapped_slots / float(total_slots)

    duplicate_slots = set()
    heavy_overlap_pairs = 0
    exact_duplicate_pairs = 0

    for i in range(len(mapped)):
        for j in range(i + 1, len(mapped)):
            a = mapped[i]
            b = mapped[j]
            exact = (
                round(a["start"], 1) == round(b["start"], 1)
                and round(a["end"], 1) == round(b["end"], 1)
            )
            ratio = _overlap_ratio(a, b)
            if exact:
                exact_duplicate_pairs += 1
            if exact or ratio >= overlap_threshold:
                heavy_overlap_pairs += 1
                duplicate_slots.add(int(a["slot"]))
                duplicate_slots.add(int(b["slot"]))

    non_duplicate_slots = max(0, mapped_slots - len(duplicate_slots))
    unique_ratio = (non_duplicate_slots / float(mapped_slots)) if mapped_slots else 0.0

    monotonic_total = 0
    monotonic_ok = 0
    mapped_sorted = sorted(mapped, key=lambda x: x["slot"])
    for i in range(len(mapped_sorted) - 1):
        monotonic_total += 1
        if mapped_sorted[i + 1]["start"] >= mapped_sorted[i]["start"]:
            monotonic_ok += 1
    monotonicity_ratio = (
        monotonic_ok / float(monotonic_total) if monotonic_total > 0 else 1.0
    )

    spans = [max(0.0, s["end"] - s["start"]) for s in mapped_sorted]
    span_mean_sec = _mean(spans)
    span_std_sec = _std(spans)
    span_cv = (span_std_sec / span_mean_sec) if span_mean_sec > 0 else 0.0

    strict_ready = (
        mapped_slots == total_slots
        and len(duplicate_slots) == 0
        and monotonicity_ratio >= 1.0
    )

    return {
        "task_id": str(task.get("_id") or ""),
        "video_id": str(task.get("video_id") or ""),
        "level": str(task.get("level") or ""),
        "enabled": int(bool(task.get("enabled", False))),
        "review_status": str(task.get("review_status") or ""),
        "total_slots": total_slots,
        "mapped_slots": mapped_slots,
        "coverage_ratio": round(coverage_ratio, 4),
        "missing_slot_count": len(missing_slots),
        "missing_slots": "|".join(str(x) for x in missing_slots),
        "duplicate_slot_count": len(duplicate_slots),
        "duplicate_slots": "|".join(str(x) for x in sorted(duplicate_slots)),
        "unique_ratio": round(unique_ratio, 4),
        "heavy_overlap_pair_count": heavy_overlap_pairs,
        "exact_duplicate_pair_count": exact_duplicate_pairs,
        "monotonicity_ratio": round(monotonicity_ratio, 4),
        "span_mean_sec": round(span_mean_sec, 4),
        "span_std_sec": round(span_std_sec, 4),
        "span_cv": round(span_cv, 4),
        "strict_ready": int(bool(strict_ready)),
    }


def _build_query(args) -> Dict:
    q: Dict = {}

    if args.task_id:
        if ObjectId.is_valid(args.task_id):
            q["_id"] = ObjectId(args.task_id)
        else:
            q["_id"] = args.task_id

    if args.video_id:
        if ObjectId.is_valid(args.video_id):
            q["video_id"] = {"$in": [args.video_id, ObjectId(args.video_id)]}
        else:
            q["video_id"] = args.video_id

    if args.only_enabled:
        q["enabled"] = True

    if args.only_published:
        q["review_status"] = "published"

    return q


def _print_summary(rows: List[Dict]) -> None:
    n = len(rows)
    if n == 0:
        print("No tasks matched.")
        return

    avg_cov = _mean([float(r["coverage_ratio"]) for r in rows])
    avg_uni = _mean([float(r["unique_ratio"]) for r in rows])
    avg_mono = _mean([float(r["monotonicity_ratio"]) for r in rows])
    avg_cv = _mean([float(r["span_cv"]) for r in rows])

    strict_ready_count = sum(int(r["strict_ready"]) for r in rows)
    has_missing = sum(1 for r in rows if int(r["missing_slot_count"]) > 0)
    has_duplicate = sum(1 for r in rows if int(r["duplicate_slot_count"]) > 0)

    print("=" * 72)
    print("Slot Alignment Quality Summary")
    print("=" * 72)
    print(f"tasks: {n}")
    print(f"strict_ready: {strict_ready_count}/{n} = {strict_ready_count / n:.2%}")
    print(f"avg_coverage_ratio:    {avg_cov:.4f}")
    print(f"avg_unique_ratio:      {avg_uni:.4f}")
    print(f"avg_monotonicity_ratio:{avg_mono:.4f}")
    print(f"avg_span_cv:           {avg_cv:.4f}")
    print(f"tasks_with_missing:    {has_missing}/{n} = {has_missing / n:.2%}")
    print(f"tasks_with_duplicates: {has_duplicate}/{n} = {has_duplicate / n:.2%}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate slot-level subtitle alignment quality")
    ap.add_argument("--mongo_uri", default="mongodb://127.0.0.1:27017")
    ap.add_argument("--db", default="thesis_system")
    ap.add_argument("--task_id", default="")
    ap.add_argument("--video_id", default="")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--only_enabled", action="store_true")
    ap.add_argument("--only_published", action="store_true")
    ap.add_argument("--overlap_threshold", type=float, default=0.8)
    ap.add_argument("--export_csv", default="")
    args = ap.parse_args()

    client = MongoClient(args.mongo_uri)
    db = client[args.db]

    q = _build_query(args)
    cur = db.parsons_tasks.find(q).sort("created_at", -1).limit(max(1, args.limit))

    rows: List[Dict] = []
    for task in cur:
        rows.append(evaluate_task_alignment(task, overlap_threshold=float(args.overlap_threshold)))

    _print_summary(rows)

    if args.export_csv and rows:
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        out = args.export_csv
        if not os.path.isabs(out):
            out = os.path.join(repo_root, out)

        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"csv exported: {out}")


if __name__ == "__main__":
    main()
