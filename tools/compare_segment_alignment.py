"""
Compare subtitle segment boundary strategies for Parsons review jumps.

A strategy (baseline): fixed expansion toward 24-26 sec.
B strategy (current-like): concept-aware boundary with topic-shift stop.

Usage:
  python tools/compare_segment_alignment.py --limit 200
  python tools/compare_segment_alignment.py --student_id 11461127 --limit 100
  python tools/compare_segment_alignment.py --export_csv alignment_ab.csv
"""

import argparse
import csv
import os
import re
from typing import Dict, List, Optional, Tuple

from pymongo import MongoClient
from bson import ObjectId


CONCEPT_KEYWORDS = {
    "condition": ["if", "\u689d\u4ef6", "\u5224\u65b7", "\u6210\u7acb", "\u4e0d\u6210\u7acb"],
    "if_else": ["if", "else", "\u5426\u5247", "\u5206\u652f"],
    "indentation": ["\u7e2e\u6392", "\u5c64\u7d1a", "\u5340\u584a", ":"],
    "calculation": ["\u904b\u7b97", "\u8a08\u7b97", "+", "-", "*", "/"],
    "logic": ["\u908f\u8f2f", "\u6d41\u7a0b", "\u9806\u5e8f", "\u689d\u4ef6"],
    "print": ["print", "\u8f38\u51fa", "\u986f\u793a"],
    "input": ["input", "\u8f38\u5165"],
}


def parse_srt_segments(text: str) -> List[Dict]:
    if not text:
        return []
    lines = text.replace("\r\n", "\n").split("\n")
    out = []
    i = 0

    def to_sec(ts: str) -> float:
        ts = ts.strip()
        hh, mm, rest = ts.split(":")
        ss, ms = rest.split(",")
        return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0

    while i < len(lines):
        ln = lines[i].strip()
        if not ln:
            i += 1
            continue

        if re.match(r"^\d+$", ln):
            i += 1
            if i >= len(lines):
                break

        ts = lines[i].strip()
        if "-->" not in ts:
            i += 1
            continue

        try:
            a, b = [x.strip() for x in ts.split("-->")]
            start = to_sec(a)
            end = to_sec(b)
        except Exception:
            i += 1
            continue

        i += 1
        buf = []
        while i < len(lines) and lines[i].strip() != "":
            buf.append(lines[i].strip())
            i += 1

        out.append({"start": start, "end": end, "text": " ".join(buf)})
        i += 1

    return out


def read_subtitle_from_task(task: Dict, repo_root: str) -> List[Dict]:
    source_sub = task.get("source_subtitle") or {}
    raw = str(source_sub.get("text_used") or task.get("subtitle_text_used") or "").strip()
    if raw and "-->" in raw:
        segs = parse_srt_segments(raw)
        if segs:
            return segs

    p = ((task.get("prompt_source") or {}).get("subtitle_path") or "").strip()
    if not p:
        return []

    path = p
    if not os.path.isabs(path):
        path = os.path.join(repo_root, path)
    if not os.path.exists(path):
        return []

    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return parse_srt_segments(f.read())
    except Exception:
        return []


def concept_hit_count(text: str, concept: str) -> int:
    t = str(text or "").lower()
    kws = CONCEPT_KEYWORDS.get(str(concept or ""), [])
    c = 0
    for kw in kws:
        k = str(kw).lower().strip()
        if k and k in t:
            c += 1
    return c


def segment_has_completion_tone(text: str) -> bool:
    t = str(text or "").lower()
    markers = [
        "\u5b8c\u6210", "\u5beb\u5b8c", "\u6700\u5f8c", "\u7e3d\u7d50", "\u7d50\u675f", "done", "finish", "final",
    ]
    return any(m in t for m in markers)


def segment_has_topic_shift(text: str, concept: str) -> bool:
    t = str(text or "").lower()
    shift_markers = ["\u63a5\u4e0b\u4f86", "\u4e0b\u4e00\u500b", "\u63db\u6210", "\u518d\u4f86", "\u7136\u5f8c", "\u63a5\u8457"]
    if any(m in t for m in shift_markers):
        return True
    if concept in ("condition", "if_else"):
        other_topic_markers = ["for", "while", "\u8ff4\u5708", "input", "\u8f38\u5165", "def ", "return"]
        return any(m in t for m in other_topic_markers)
    return False


def nearest_index_by_time(segs: List[Dict], t: float) -> int:
    if not segs:
        return 0
    best = 0
    best_diff = float("inf")
    for i, s in enumerate(segs):
        d = abs(float(s.get("start", 0.0)) - float(t))
        if d < best_diff:
            best_diff = d
            best = i
    return best


def simulate_baseline(segs: List[Dict], start_idx: int) -> Tuple[float, float, str]:
    a = max(0, min(start_idx, len(segs) - 1))
    b = a
    start = float(segs[a]["start"])
    end = float(segs[b]["end"])

    while b < len(segs) - 1 and (end - start) < 24.0:
        b += 1
        end = float(segs[b]["end"])
        if (end - start) >= 26.0:
            return start, end, "max_span_cap"

    return start, end, "min_24_or_tail"


def simulate_new(segs: List[Dict], start_idx: int, concept: str) -> Tuple[float, float, str]:
    a = max(0, min(start_idx, len(segs) - 1))
    b = a
    start = float(segs[a]["start"])
    end = float(segs[b]["end"])

    min_span = 12.0 if concept in ("condition", "if_else", "logic") else 18.0
    miss_streak = 0

    while b < len(segs) - 1:
        if (end - start) >= min_span:
            next_txt = str(segs[b + 1].get("text") or "")
            next_hit = concept_hit_count(next_txt, concept)
            if next_hit <= 0:
                miss_streak += 1
            else:
                miss_streak = 0

            if miss_streak >= 2 and segment_has_topic_shift(next_txt, concept):
                return start, end, "topic_shift_after_miss"

        b += 1
        end = float(segs[b]["end"])
        if (end - start) >= 26.0:
            return start, end, "max_span_cap"

    return start, end, "reach_tail"


def end_metrics(segs: List[Dict], end_sec: float, concept: str) -> Tuple[bool, bool, int]:
    i = nearest_index_by_time(segs, end_sec)
    txt = str((segs[i] or {}).get("text") or "") if segs else ""
    return (
        segment_has_completion_tone(txt),
        segment_has_topic_shift(txt, concept),
        concept_hit_count(txt, concept),
    )


def main():
    ap = argparse.ArgumentParser(description="Compare subtitle segment strategy A/B with proxy metrics")
    ap.add_argument("--mongo_uri", default="mongodb://127.0.0.1:27017")
    ap.add_argument("--db", default="thesis_system")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--student_id", default="")
    ap.add_argument("--task_id", default="")
    ap.add_argument("--export_csv", default="")
    args = ap.parse_args()

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    cli = MongoClient(args.mongo_uri)
    db = cli[args.db]

    q = {
        "is_correct": False,
        "jump_start": {"$exists": True},
        "jump_end": {"$exists": True},
        "task_id": {"$exists": True},
    }
    if args.student_id:
        q["student_id"] = args.student_id
    if args.task_id:
        q["task_id"] = args.task_id

    cur = db.parsons_attempts.find(q).sort("created_at", -1).limit(max(1, args.limit))

    rows = []
    for a in cur:
        tid = str(a.get("task_id") or "").strip()
        if not tid or not ObjectId.is_valid(tid):
            continue

        task = db.parsons_tasks.find_one({"_id": ObjectId(tid)}) or {}
        segs = read_subtitle_from_task(task, repo_root)
        if not segs:
            continue

        concept = str(a.get("segment_concept") or "logic").strip().lower() or "logic"
        start0 = float(a.get("jump_start") or 0.0)

        sidx = nearest_index_by_time(segs, start0)
        a_s, a_e, a_stop = simulate_baseline(segs, sidx)
        b_s, b_e, b_stop = simulate_new(segs, sidx, concept)

        a_comp, a_shift, a_hit = end_metrics(segs, a_e, concept)
        b_comp, b_shift, b_hit = end_metrics(segs, b_e, concept)

        rows.append({
            "attempt_id": str(a.get("_id") or ""),
            "student_id": str(a.get("student_id") or ""),
            "task_id": tid,
            "concept": concept,
            "start_idx": sidx,
            "baseline_start": round(a_s, 3),
            "baseline_end": round(a_e, 3),
            "baseline_duration": round(a_e - a_s, 3),
            "baseline_stop": a_stop,
            "baseline_end_completion": int(bool(a_comp)),
            "baseline_end_topic_shift": int(bool(a_shift)),
            "baseline_end_concept_hits": int(a_hit),
            "new_start": round(b_s, 3),
            "new_end": round(b_e, 3),
            "new_duration": round(b_e - b_s, 3),
            "new_stop": b_stop,
            "new_end_completion": int(bool(b_comp)),
            "new_end_topic_shift": int(bool(b_shift)),
            "new_end_concept_hits": int(b_hit),
        })

    if not rows:
        print("No eligible attempts found for comparison.")
        return

    n = len(rows)
    a_bad_completion = sum(r["baseline_end_completion"] for r in rows)
    b_bad_completion = sum(r["new_end_completion"] for r in rows)
    a_bad_shift = sum(r["baseline_end_topic_shift"] for r in rows)
    b_bad_shift = sum(r["new_end_topic_shift"] for r in rows)
    a_avg_hit = sum(r["baseline_end_concept_hits"] for r in rows) / float(n)
    b_avg_hit = sum(r["new_end_concept_hits"] for r in rows) / float(n)
    a_avg_dur = sum(r["baseline_duration"] for r in rows) / float(n)
    b_avg_dur = sum(r["new_duration"] for r in rows) / float(n)

    print("=" * 72)
    print("Alignment A/B proxy comparison")
    print("=" * 72)
    print(f"samples: {n}")
    print(f"baseline_end_completion_rate: {a_bad_completion}/{n} = {a_bad_completion / n:.2%}")
    print(f"new_end_completion_rate:      {b_bad_completion}/{n} = {b_bad_completion / n:.2%}")
    print(f"baseline_end_topic_shift_rate:{a_bad_shift}/{n} = {a_bad_shift / n:.2%}")
    print(f"new_end_topic_shift_rate:     {b_bad_shift}/{n} = {b_bad_shift / n:.2%}")
    print(f"baseline_avg_end_concept_hits:{a_avg_hit:.3f}")
    print(f"new_avg_end_concept_hits:     {b_avg_hit:.3f}")
    print(f"baseline_avg_duration_sec:    {a_avg_dur:.3f}")
    print(f"new_avg_duration_sec:         {b_avg_dur:.3f}")

    if args.export_csv:
        path = args.export_csv
        if not os.path.isabs(path):
            path = os.path.join(repo_root, path)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"csv exported: {path}")


if __name__ == "__main__":
    main()
