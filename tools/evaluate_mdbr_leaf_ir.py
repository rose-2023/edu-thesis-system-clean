"""
Evaluate subtitle retrieval quality with MongoDB/mdbr-leaf-ir (offline, non-invasive).

Purpose:
- Do NOT modify online submit flow.
- Measure whether mdbr-leaf-ir can stably retrieve slot-specific subtitle segments.

Usage examples:
  python tools/evaluate_mdbr_leaf_ir.py --task_id 69c56958bf201de3522c6869
  python tools/evaluate_mdbr_leaf_ir.py --video_id 69bb823ef339ec9732fc7a3f --limit 20
  python tools/evaluate_mdbr_leaf_ir.py --only_published --export_csv mdbr_eval.csv

Notes:
- Requires sentence-transformers (or transformers+torch fallback).
- Default model: MongoDB/mdbr-leaf-ir
"""

import argparse
import csv
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
from pymongo import MongoClient


def parse_srt_segments(text: str) -> List[Dict[str, Any]]:
    if not text:
        return []

    lines = text.replace("\r\n", "\n").split("\n")
    out: List[Dict[str, Any]] = []
    i = 0

    def _to_sec(ts: str) -> float:
        hh, mm, rest = ts.strip().split(":")
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
            start = _to_sec(a)
            end = _to_sec(b)
        except Exception:
            i += 1
            continue

        i += 1
        buf: List[str] = []
        while i < len(lines) and lines[i].strip() != "":
            buf.append(lines[i].strip())
            i += 1

        out.append({"start": start, "end": end, "text": " ".join(buf)})
        i += 1

    return out


def read_subtitle_from_task(task: Dict[str, Any], repo_root: str) -> List[Dict[str, Any]]:
    source_sub = task.get("source_subtitle") or {}
    raw = str(source_sub.get("text_used") or task.get("subtitle_text_used") or "").strip()
    if raw and "-->" in raw:
        segs = parse_srt_segments(raw)
        if segs:
            return segs

    p = ((task.get("prompt_source") or {}).get("subtitle_path") or "").strip()
    if not p:
        return []

    path = p if os.path.isabs(p) else os.path.join(repo_root, p)
    if not os.path.exists(path):
        return []

    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return parse_srt_segments(f.read())
    except Exception:
        return []


def _extract_slot_count(task: Dict[str, Any]) -> int:
    slots = task.get("template_slots") or task.get("solution_blocks") or []
    return max(1, len(slots))


def _extract_solution_slot_line(task: Dict[str, Any], idx: int) -> Tuple[str, str]:
    sol = task.get("solution_blocks") or []
    if idx < 0 or idx >= len(sol):
        return "", ""
    b = sol[idx] or {}
    text = str(b.get("text") or "").strip()
    sem = str(b.get("semantic_zh") or b.get("meaning_zh") or b.get("zh") or "").strip()
    return text, sem


def _existing_map_range(task: Dict[str, Any], idx: int) -> Optional[Tuple[float, float]]:
    seg_map = task.get("ai_segment_map") or {}
    if not isinstance(seg_map, dict):
        return None
    keys = [str(idx), f"s{idx + 1}", f"第{idx + 1}格"]
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
        if ef > sf:
            return sf, ef
    return None


def _overlap_ratio(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    ov = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    base = max(0.001, min(a[1] - a[0], b[1] - b[0]))
    return ov / base


def _cosine(a: List[float], b: List[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class LeafIR:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self.mode = "none"
        self.model = None
        self.tokenizer = None

        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            self.model = SentenceTransformer(model_name)
            self.mode = "sentence_transformers"
            return
        except Exception:
            pass

        try:
            import torch  # type: ignore
            from transformers import AutoModel, AutoTokenizer  # type: ignore

            self.tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
            self.model = AutoModel.from_pretrained(model_name, trust_remote_code=True)
            self.model.eval()
            self._torch = torch
            self.mode = "transformers"
            return
        except Exception:
            pass

        raise RuntimeError(
            "Cannot load model. Install sentence-transformers or transformers+torch, and ensure model access is available."
        )

    def embed(self, texts: List[str]) -> List[List[float]]:
        if self.mode == "sentence_transformers":
            vecs = self.model.encode(texts, normalize_embeddings=True)  # type: ignore
            return [list(map(float, v)) for v in vecs]

        if self.mode == "transformers":
            torch = self._torch  # type: ignore
            out: List[List[float]] = []
            for t in texts:
                batch = self.tokenizer(  # type: ignore
                    t,
                    return_tensors="pt",
                    truncation=True,
                    max_length=512,
                )
                with torch.no_grad():
                    h = self.model(**batch).last_hidden_state  # type: ignore
                v = h[:, 0, :].squeeze(0).detach().cpu().tolist()
                n = math.sqrt(sum(x * x for x in v)) or 1.0
                out.append([float(x / n) for x in v])
            return out

        raise RuntimeError("model not initialized")


def evaluate_task(task: Dict[str, Any], segs: List[Dict[str, Any]], ir: LeafIR) -> Dict[str, Any]:
    total_slots = _extract_slot_count(task)
    if not segs:
        return {
            "task_id": str(task.get("_id") or ""),
            "total_slots": total_slots,
            "usable_slots": 0,
            "top1_score_mean": 0.0,
            "top1_score_min": 0.0,
            "monotonicity_ratio": 0.0,
            "unique_pred_ratio": 0.0,
            "map_overlap_mean": 0.0,
            "map_overlap_slots": 0,
        }

    seg_texts = [str(s.get("text") or "") for s in segs]
    seg_vecs = ir.embed(seg_texts)

    pred_ranges: List[Tuple[float, float]] = []
    pred_idx: List[int] = []
    scores: List[float] = []
    overlaps: List[float] = []

    usable_slots = 0
    for i in range(total_slots):
        code_text, sem_text = _extract_solution_slot_line(task, i)
        query = (code_text + "\n" + sem_text).strip()
        if not query:
            continue

        q_vec = ir.embed([query])[0]
        best_j = -1
        best_s = -1.0
        for j, sv in enumerate(seg_vecs):
            s = _cosine(q_vec, sv)
            if s > best_s:
                best_s = s
                best_j = j

        if best_j < 0:
            continue

        usable_slots += 1
        scores.append(float(best_s))
        pred_idx.append(best_j)
        pr = (float(segs[best_j]["start"]), float(segs[best_j]["end"]))
        pred_ranges.append(pr)

        mapped = _existing_map_range(task, i)
        if mapped is not None:
            overlaps.append(_overlap_ratio(pr, mapped))

    if usable_slots <= 1:
        mono = 1.0 if usable_slots == 1 else 0.0
    else:
        ok = 0
        tot = 0
        for i in range(len(pred_idx) - 1):
            tot += 1
            if pred_idx[i + 1] >= pred_idx[i]:
                ok += 1
        mono = ok / float(tot) if tot else 1.0

    unique_pred_ratio = 0.0
    if pred_idx:
        unique_pred_ratio = len(set(pred_idx)) / float(len(pred_idx))

    return {
        "task_id": str(task.get("_id") or ""),
        "video_id": str(task.get("video_id") or ""),
        "level": str(task.get("level") or ""),
        "total_slots": total_slots,
        "usable_slots": usable_slots,
        "top1_score_mean": round(sum(scores) / float(len(scores)) if scores else 0.0, 4),
        "top1_score_min": round(min(scores) if scores else 0.0, 4),
        "monotonicity_ratio": round(mono, 4),
        "unique_pred_ratio": round(unique_pred_ratio, 4),
        "map_overlap_mean": round(sum(overlaps) / float(len(overlaps)) if overlaps else 0.0, 4),
        "map_overlap_slots": len(overlaps),
        "strict_ready_proxy": int(
            bool(
                usable_slots == total_slots
                and mono >= 1.0
                and unique_pred_ratio >= 0.8
                and (len(scores) == 0 or min(scores) >= 0.25)
            )
        ),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate MongoDB/mdbr-leaf-ir retrieval on Parsons tasks")
    ap.add_argument("--mongo_uri", default="mongodb://127.0.0.1:27017")
    ap.add_argument("--db", default="thesis_system")
    ap.add_argument("--task_id", default="")
    ap.add_argument("--video_id", default="")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--only_published", action="store_true")
    ap.add_argument("--model", default="MongoDB/mdbr-leaf-ir")
    ap.add_argument("--export_csv", default="")
    args = ap.parse_args()

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    cli = MongoClient(args.mongo_uri)
    db = cli[args.db]

    q: Dict[str, Any] = {}
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
    if args.only_published:
        q["review_status"] = "published"

    tasks = list(db.parsons_tasks.find(q).sort("created_at", -1).limit(max(1, args.limit)))
    if not tasks:
        print("No tasks matched.")
        return

    ir = LeafIR(args.model)

    rows: List[Dict[str, Any]] = []
    for t in tasks:
        segs = read_subtitle_from_task(t, repo_root)
        rows.append(evaluate_task(t, segs, ir))

    n = len(rows)
    avg_score = sum(float(r["top1_score_mean"]) for r in rows) / float(n)
    avg_mono = sum(float(r["monotonicity_ratio"]) for r in rows) / float(n)
    avg_unique = sum(float(r["unique_pred_ratio"]) for r in rows) / float(n)
    avg_overlap = sum(float(r["map_overlap_mean"]) for r in rows) / float(n)
    ready = sum(int(r["strict_ready_proxy"]) for r in rows)

    print("=" * 72)
    print("MongoDB/mdbr-leaf-ir Offline Evaluation")
    print("=" * 72)
    print(f"tasks: {n}")
    print(f"avg_top1_score_mean: {avg_score:.4f}")
    print(f"avg_monotonicity:    {avg_mono:.4f}")
    print(f"avg_unique_pred:     {avg_unique:.4f}")
    print(f"avg_map_overlap:     {avg_overlap:.4f}")
    print(f"strict_ready_proxy:  {ready}/{n} = {ready / n:.2%}")

    if args.export_csv:
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
