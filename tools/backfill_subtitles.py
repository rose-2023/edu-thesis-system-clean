"""Utility to populate subtitle_range and ai_segment_map for existing Parsons tasks.

Run this script from the workspace root. It will connect to the same MongoDB used
by the app (env var MONGODB_URI or localhost) and update any task document that
does not already have a subtitle_range or ai_segment_map defined.

Because the original database only stored video_id and often left the
subtitle_path empty, you must provide a mapping from video_id to actual
.srt filename in the `VIDEO_TO_FILE` dictionary below.  If your subtitle files
are renamed to include the video ID, the script will attempt to discover them
automatically.

After running the script the Parsons pages should jump to the correct time
based on the computed ranges; the shape of the documents is preserved (we
simply set two extra fields).

Example usage:
    python tools/backfill_subtitles.py

"""

import os
import re
import sys

from pymongo import MongoClient

# -------------------------------------------------------------
# customize this mapping if your .srt files don't contain the id
# -------------------------------------------------------------
VIDEO_TO_FILE = {
    # 'wrKREda0-6o': 'U1_20260224_152353_v1_2ab81789.srt',
    # add entries here as needed
}

# copy of the helpers from parsons.py (slightly simplified)
def _parse_srt_time_to_seconds(t: str) -> float:
    try:
        t = (t or "").strip()
        hh, mm, rest = t.split(":")
        ss, ms = rest.split(",")
        return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0
    except Exception:
        return 0.0


def _read_srt_segments(abs_or_rel_path: str):
    try:
        import os
        import re

        p = (abs_or_rel_path or "").strip()
        if not p:
            return []
        if not os.path.isabs(p):
            root = os.getcwd()
            p = os.path.join(root, p)
        if not os.path.exists(p):
            return []
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
        blocks = re.split(r"\n\s*\n", raw.strip(), flags=re.M)
        out = []
        for b in blocks:
            lines = [x.strip("\ufeff").strip() for x in b.splitlines() if x.strip()]
            if len(lines) < 2:
                continue
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


def compute_range_and_map(srt_path, slots_count):
    segs = _read_srt_segments(srt_path)
    if not segs:
        return None, None
    start = segs[0]["start"]
    end = segs[-1]["end"]
    if end <= start:
        return None, None
    total = end - start
    slots = max(1, slots_count or 1)
    length = total / slots
    ai_map = {str(i): {"start": start + i * length, "end": start + (i + 1) * length} for i in range(slots)}
    return [start, end], ai_map


def find_subtitle_for_task(task):
    # try explicit path first
    p = ((task.get("prompt_source") or {}).get("subtitle_path") or "").strip()
    if p:
        return p
    vid = (task.get("video_id") or "").strip()
    if not vid:
        return None
    # mapping dict override
    if vid in VIDEO_TO_FILE:
        return os.path.join("uploads", "subtitles", VIDEO_TO_FILE[vid])
    # otherwise scan directory for file containing id
    base = os.path.join(os.getcwd(), "uploads", "subtitles")
    try:
        for fn in os.listdir(base):
            if vid in fn:
                return os.path.join(base, fn)
    except Exception:
        pass
    return None


def main():
    uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
    client = MongoClient(uri)
    db = client.get_default_database() or client["thesis_system"]
    coll = db["parsons_tasks"]

    cursor = coll.find({})
    for task in cursor:
        need_update = False
        if not task.get("subtitle_range") or not task.get("ai_segment_map"):
            srt = find_subtitle_for_task(task)
            if not srt:
                print("no subtitle for task", task.get("_id"))
                continue
            rng, ai_map = compute_range_and_map(srt, task.get("slots") or 1)
            if rng is None:
                print("failed parse", srt, "for", task.get("_id"))
                continue
            upd = {}
            if not task.get("subtitle_range"):
                upd["subtitle_range"] = rng
                need_update = True
            if not task.get("ai_segment_map"):
                upd["ai_segment_map"] = ai_map
                need_update = True
            if need_update:
                coll.update_one({"_id": task["_id"]}, {"$set": upd})
                print("updated", task.get("_id"), "=>", upd)
    print("done")


if __name__ == "__main__":
    main()
