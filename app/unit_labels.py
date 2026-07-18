from datetime import datetime, timezone
import re

from app.db import db


def now_utc():
    return datetime.now(timezone.utc)


def normalize_unit_key(unit):
    raw = str(unit or "").strip()
    if not raw:
        return ""
    return re.sub(r"^A(?=U\d+)", "", raw, flags=re.I).strip()


def _natural_text_key(value):
    parts = re.split(r"(\d+)", str(value or "").strip().lower())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def unit_sort_key(unit, label=None):
    raw = str(unit or "").strip()
    key = normalize_unit_key(raw)
    text = f"{raw} {key} {label or ''}".lower()

    match = re.match(r"^U(\d+)(?:[-_ ]*([A-Za-z]+))?", key, flags=re.I)
    if match:
        unit_rank = int(match.group(1))
        subtag = (match.group(2) or "").lower()
    else:
        is_io = (
            "\u8f38\u5165\u8f38\u51fa" in text
            or re.search(r"\binput[ /_-]*output\b", text, flags=re.I)
            or re.search(r"\bio\b", text, flags=re.I)
        )
        unit_rank = 1 if is_io else 9999
        subtag = ""

    sub_rank = {
        "io": 0,
        "int": 1,
        "if": 0,
        "ifelse": 1,
        "elif": 2,
        "for": 0,
        "loop": 1,
    }.get(subtag, 50)
    return (unit_rank, sub_rank, _natural_text_key(key or raw), _natural_text_key(label or ""))


def sort_units(units, labels=None):
    label_map = labels or {}
    return sorted(list(units or []), key=lambda unit: unit_sort_key(unit, label_map.get(unit)))


def default_unit_label(unit):
    raw = normalize_unit_key(unit)
    if not raw:
        return ""

    match = re.match(r"^(U\d+)(?:[-_ ]*([A-Za-z]+))?(.*)$", raw, flags=re.I)
    prefix = match.group(1).upper() if match else raw.upper()
    subtag = match.group(2).lower() if match and match.group(2) else ""
    tail = match.group(3).strip() if match and match.group(3) else ""

    if tail:
        clean_tail = re.sub(r"^[-_\s]+", "", tail).strip()
        if clean_tail:
            return clean_tail

    if prefix == "U1" and subtag == "io":
        return "\u8f38\u5165\u8f38\u51fa"
    if prefix == "U1" and subtag == "int":
        return "\u6578\u503c\u904b\u7b97"
    if prefix == "U2":
        return "\u689d\u4ef6\u5224\u65b7"
    if prefix == "U3" and subtag == "for":
        return "\u5b9a\u6578\u8ff4\u5708"
    if prefix == "U3" and subtag == "loop":
        return "\u8ff4\u5708\u89c0\u5ff5\u89e3\u6790"

    name_map = {
        "U1": "\u8f38\u5165\u8f38\u51fa",
        "U2": "\u689d\u4ef6\u5224\u65b7",
        "U3": "\u8ff4\u5708\u89c0\u5ff5\u89e3\u6790",
        "U4": "\u5de2\u72c0\u8ff4\u5708",
        "U5": "\u4e0d\u5b9a\u6578\u8ff4\u5708",
        "U6": "\u4e32\u5217\u89c0\u5ff5\u89e3\u6790",
        "U7": "\u51fd\u5f0f\u89c0\u5ff5\u89e3\u6790",
    }
    return name_map.get(prefix) or raw


def unit_label(unit):
    key = normalize_unit_key(unit)
    if not key:
        return ""
    doc = db.unit_labels.find_one({"_id": key}, {"label": 1})
    label = str((doc or {}).get("label") or "").strip()
    return label or default_unit_label(key)


def unit_label_map(units):
    pairs = [
        (str(unit or "").strip(), normalize_unit_key(unit))
        for unit in (units or [])
        if normalize_unit_key(unit)
    ]
    keys = sorted({key for _, key in pairs if key})
    if not keys:
        return {}
    stored = {
        str(doc.get("_id") or ""): str(doc.get("label") or "").strip()
        for doc in db.unit_labels.find({"_id": {"$in": keys}}, {"label": 1})
    }
    labels = {key: stored.get(key) or default_unit_label(key) for key in keys}
    for raw, key in pairs:
        if raw:
            labels[raw] = labels.get(key) or default_unit_label(key)
    return labels


def save_unit_label(unit, label, updated_by="teacher"):
    key = normalize_unit_key(unit)
    clean_label = str(label or "").strip()
    if not key:
        raise ValueError("missing unit")
    if not clean_label:
        raise ValueError("missing label")
    db.unit_labels.update_one(
        {"_id": key},
        {
            "$set": {
                "unit": key,
                "label": clean_label,
                "updated_by": str(updated_by or "teacher"),
                "updated_at": now_utc(),
            },
            "$setOnInsert": {"created_at": now_utc()},
        },
        upsert=True,
    )
    return {"unit": key, "unit_label": clean_label}
