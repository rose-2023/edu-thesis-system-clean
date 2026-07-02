import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple

_OPENAI_CLIENT = None


def get_retrieval_mode() -> str:
    mode = str(os.getenv("PARSONS_RETRIEVAL_MODE") or "local").strip().lower()
    return mode if mode in {"local", "openai"} else "local"


def get_openai_embedding_model() -> str:
    return str(os.getenv("PARSONS_RETRIEVAL_EMBED_MODEL") or "text-embedding-3-small").strip()


def _get_openai_client():
    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is not None:
        return _OPENAI_CLIENT
    from openai import OpenAI  # lazy import

    _OPENAI_CLIENT = OpenAI()
    return _OPENAI_CLIENT


def _tokenize(text: str) -> List[str]:
    s = str(text or "").lower().strip()
    if not s:
        return []

    # Keep common code tokens and operators.
    word_tokens = re.findall(r"[a-z_][a-z0-9_]*|\d+|==|!=|<=|>=|\+|\-|\*|/|%|\(|\)|\[|\]|\{|\}", s)

    # Add CJK bi-grams for semantic_zh style queries.
    cjk = "".join(ch for ch in s if "\u4e00" <= ch <= "\u9fff")
    cjk_bigrams = [cjk[i : i + 2] for i in range(len(cjk) - 1)]

    return word_tokens + cjk_bigrams


def _build_idf(docs_tokens: List[List[str]]) -> Dict[str, float]:
    n = max(1, len(docs_tokens))
    df: Dict[str, int] = {}
    for toks in docs_tokens:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1

    idf: Dict[str, float] = {}
    for t, c in df.items():
        idf[t] = math.log((1.0 + n) / (1.0 + c)) + 1.0
    return idf


def _vectorize(tokens: List[str], idf: Dict[str, float]) -> Dict[str, float]:
    if not tokens:
        return {}
    tf: Dict[str, float] = {}
    for t in tokens:
        tf[t] = tf.get(t, 0.0) + 1.0

    v: Dict[str, float] = {}
    norm = 0.0
    for t, f in tf.items():
        w = f * idf.get(t, 0.0)
        if w <= 0:
            continue
        v[t] = w
        norm += w * w

    if norm <= 0:
        return {}
    norm = math.sqrt(norm)
    for t in list(v.keys()):
        v[t] = v[t] / norm
    return v


def _cosine_sparse(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    s = 0.0
    for k, v in a.items():
        bv = b.get(k)
        if bv is not None:
            s += v * bv
    return max(0.0, min(1.0, s))


def _cosine_dense(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    if n <= 0:
        return 0.0

    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(n):
        x = float(a[i])
        y = float(b[i])
        dot += x * y
        na += x * x
        nb += y * y

    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (math.sqrt(na) * math.sqrt(nb) + 1e-8)))


def _embed_text_openai(text: str, model: str) -> List[float]:
    client = _get_openai_client()
    resp = client.embeddings.create(model=model, input=str(text or ""))
    emb = resp.data[0].embedding if resp and resp.data else []
    return [float(x) for x in (emb or [])]


def build_subtitle_index(
    segs: List[Dict[str, Any]],
    mode: Optional[str] = None,
    embed_model: Optional[str] = None,
) -> Dict[str, Any]:
    selected_mode = (mode or get_retrieval_mode()).strip().lower()
    if selected_mode not in {"local", "openai"}:
        selected_mode = "local"
    selected_model = (embed_model or get_openai_embedding_model()).strip()

    safe_segs: List[Dict[str, Any]] = []
    for s in segs or []:
        try:
            seg_id = s.get("id")
            start = float(s.get("start"))
            end = float(s.get("end"))
        except Exception:
            continue
        if end <= start:
            continue
        text = str(s.get("text") or "").strip()
        safe_segs.append({"id": seg_id, "start": start, "end": end, "text": text})

    if selected_mode == "openai":
        try:
            embeddings: List[List[float]] = []
            for s in safe_segs:
                embeddings.append(_embed_text_openai(str(s.get("text") or ""), selected_model))
            return {
                "mode": "openai",
                "embed_model": selected_model,
                "segments": safe_segs,
                "embeddings": embeddings,
            }
        except Exception:
            # graceful fallback keeps system running even if OpenAI is unavailable.
            selected_mode = "local"

    docs_tokens: List[List[str]] = []
    for s in safe_segs:
        docs_tokens.append(_tokenize(str(s.get("text") or "")))

    idf = _build_idf(docs_tokens)
    vectors = [_vectorize(toks, idf) for toks in docs_tokens]

    return {
        "mode": "local",
        "embed_model": "",
        "segments": safe_segs,
        "idf": idf,
        "vectors": vectors,
    }


def retrieve_best_segment(query: str, subtitle_index: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], float]:
    top = retrieve_top_k_segments(query, subtitle_index, k=1)
    if not top:
        return None, 0.0
    first = top[0]
    return {
        "id": first.get("id"),
        "start": float(first.get("start", 0.0)),
        "end": float(first.get("end", 0.0)),
        "text": str(first.get("text") or ""),
        "index": int(first.get("index", 0)),
    }, float(first.get("score", 0.0))


def retrieve_top_k_segments(query: str, subtitle_index: Dict[str, Any], k: int = 3) -> List[Dict[str, Any]]:
    segs = (subtitle_index or {}).get("segments") or []
    mode = str((subtitle_index or {}).get("mode") or "local").strip().lower()
    k = max(1, int(k or 1))

    if not segs:
        return []

    scored: List[Tuple[int, float]] = []

    if mode == "openai":
        embeddings = (subtitle_index or {}).get("embeddings") or []
        model = str((subtitle_index or {}).get("embed_model") or get_openai_embedding_model()).strip()
        if not embeddings:
            return []
        try:
            q_emb = _embed_text_openai(query, model)
        except Exception:
            return []

        for i, emb in enumerate(embeddings):
            s = _cosine_dense(q_emb, emb or [])
            scored.append((i, float(max(0.0, min(1.0, s)))))
    else:
        idf = (subtitle_index or {}).get("idf") or {}
        vecs = (subtitle_index or {}).get("vectors") or []
        if not idf or not vecs:
            return []

        q_tokens = _tokenize(query)
        q_vec = _vectorize(q_tokens, idf)
        if not q_vec:
            return []

        for i, sv in enumerate(vecs):
            s = _cosine_sparse(q_vec, sv)
            scored.append((i, float(max(0.0, min(1.0, s)))))

    scored.sort(key=lambda x: x[1], reverse=True)
    out: List[Dict[str, Any]] = []
    for i, score in scored[:k]:
        seg = segs[i]
        out.append({
            "id": seg.get("id"),
            "start": float(seg.get("start", 0.0)),
            "end": float(seg.get("end", 0.0)),
            "text": str(seg.get("text") or ""),
            "index": int(i),
            "score": float(score),
        })
    return out


def merge_top_k_window(top_k: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], float]:
    """Merge top-k hits into one review window using timeline continuity."""
    if not top_k:
        return None, 0.0

    ordered = sorted(top_k, key=lambda x: float(x.get("start", 0.0)))
    start = float(ordered[0].get("start", 0.0))
    end = float(ordered[-1].get("end", 0.0))
    text = "\n".join([str(x.get("text") or "").strip() for x in ordered if str(x.get("text") or "").strip()])
    score = sum(float(x.get("score", 0.0)) for x in ordered) / float(len(ordered))

    return {
        "start": start,
        "end": end,
        "text": text,
        "index": int(ordered[0].get("index", 0)),
    }, float(score)
