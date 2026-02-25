import os
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from bson import ObjectId

from ..db import db
from .admin_upload import validate_subtitle_text_by_ext    # 重用驗證器（若循環 import，改成複製一份到此檔）

subtitle_bp = Blueprint("subtitle", __name__)

PROJECT_ROOT = os.getcwd()
SUBTITLE_DIR = os.path.join(PROJECT_ROOT, "uploads", "subtitles")
os.makedirs(SUBTITLE_DIR, exist_ok=True)

def now_utc():
    return datetime.now(timezone.utc)

def safe_iso(dt):
    try:
        return dt.isoformat()
    except Exception:
        return str(dt)

@subtitle_bp.get("/units")
def units():
    units = db.videos.distinct("unit", {"deleted": {"$ne": True}})
    units = [u for u in units if u]
    units.sort()
    return jsonify({"ok": True, "units": units})

@subtitle_bp.get("/videos")
def videos():
    unit = (request.args.get("unit") or "").strip()
    q = {"deleted": False}
    if unit:
        q["unit"] = unit
    cursor = db.videos.find(q).sort("created_at", -1)
    out = []
    for v in cursor:
        out.append({
            "_id": str(v["_id"]),
            "unit": v.get("unit"),
            "title": v.get("title"),
            "path": v.get("path"),
            "thumbnail": v.get("thumbnail"),
            "subtitle_verified": bool(v.get("subtitle_verified", False)),
            "subtitle_current_version": int(v.get("subtitle_current_version") or 1),
            "subtitle_versions_count": int(v.get("subtitle_versions_count") or 1),
            "subtitle_updated_at": safe_iso(v["subtitle_updated_at"]) if isinstance(v.get("subtitle_updated_at"), datetime) else v.get("subtitle_updated_at"),
        })
    return jsonify({"ok": True, "videos": out})

@subtitle_bp.get("/versions")
def versions():
    video_id = (request.args.get("video_id") or "").strip()
    if not video_id:
        return jsonify({"ok": False, "message": "missing video_id"}), 400
    try:
        vid = ObjectId(video_id)
    except Exception:
        return jsonify({"ok": False, "message": "bad video_id"}), 400

    cursor = db.subtitles.find({"video_id": vid}).sort("version", -1)
    items = []
    for s in cursor:
        items.append({
            "version": int(s.get("version") or 1),
            "path": s.get("path"),
            "blocks": s.get("blocks"),
            "created_at": safe_iso(s["created_at"]) if isinstance(s.get("created_at"), datetime) else s.get("created_at"),
            "created_by": s.get("created_by"),
            "source": s.get("source", "upload"),
            "note": s.get("note", "")
        })
    return jsonify({"ok": True, "versions": items})

@subtitle_bp.get("/content")
def content():
    video_id = (request.args.get("video_id") or "").strip()
    version = int(request.args.get("version") or 0)
    if not video_id:
        return jsonify({"ok": False, "message": "missing video_id"}), 400
    try:
        vid = ObjectId(video_id)
    except Exception:
        return jsonify({"ok": False, "message": "bad video_id"}), 400

    if version <= 0:
        v = db.videos.find_one({"_id": vid})
        version = int((v or {}).get("subtitle_current_version") or 1)

    s = db.subtitles.find_one({"video_id": vid, "version": version})
    if s and s.get("path"):
        rel = s["path"]
    else:
        v = db.videos.find_one({"_id": vid})
        rel = (v or {}).get("subtitle_path")
        if not rel:
            return jsonify({"ok": False, "message": "no subtitle path"}), 404

    abs_path = os.path.join(PROJECT_ROOT, rel.replace("/", os.sep))
    if not os.path.exists(abs_path):
        return jsonify({"ok": False, "message": "file not found"}), 404

    try:
        try:
            text = open(abs_path, "r", encoding="utf-8-sig").read()
        except UnicodeDecodeError:
            text = open(abs_path, "r", encoding="cp950", errors="ignore").read()
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500

    ext = abs_path.rsplit(".", 1)[-1].lower() if "." in abs_path else "srt"
    blocks = None
    if s:
        blocks = s.get("blocks")
    
    # ✅ 若 blocks 是整數（計數），改為 None，強制前端使用 text 解析
    if isinstance(blocks, int):
        blocks = None
    
    # 讀檔 text 後，回傳時加 blocks
    return jsonify({
        "ok": True,
        "version": version,
        "ext": ext,
        "text": text,
        "path": rel,
        "blocks": blocks,   # ✅ 新增這行
    })

@subtitle_bp.post("/save")
def save():
    data = request.get_json(silent=True) or {}
    video_id = (data.get("video_id") or "").strip()
    base_version = int(data.get("base_version") or 0)
    text = data.get("text") or ""
    created_by = (data.get("created_by") or "admin").strip()
    set_current = bool(data.get("set_current", True))

    if not video_id or not text.strip():
        return jsonify({"ok": False, "message": "missing video_id or text"}), 400
    try:
        vid = ObjectId(video_id)
    except Exception:
        return jsonify({"ok": False, "message": "bad video_id"}), 400

    v = db.videos.find_one({"_id": vid})
    if not v:
        return jsonify({"ok": False, "message": "video not found"}), 404

    ext = "srt"
    if v.get("subtitle_path") and "." in v["subtitle_path"]:
        ext = v["subtitle_path"].rsplit(".", 1)[1].lower()

    ok_sub, blocks, errors = validate_subtitle_text_by_ext(text, ext)
    if not ok_sub:
        return jsonify({"ok": False, "message": "字幕時間軸有錯", "subtitle_errors": errors, "blocks": blocks}), 400

    last = db.subtitles.find_one({"video_id": vid}, sort=[("version", -1)])
    next_ver = int((last or {}).get("version") or 1) + 1 if last else 2

    unit = (v.get("unit") or "U").strip()
    fname = f"{unit}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_v{next_ver}.{ext}"
    abs_path = os.path.join(SUBTITLE_DIR, fname)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(text)

    rel = os.path.join("uploads", "subtitles", fname).replace("\\", "/")

    db.subtitles.insert_one({
        "video_id": vid,
        "version": next_ver,
        "path": rel,
        "blocks": blocks,
        "created_at": now_utc(),
        "created_by": created_by,
        "source": "corrected",
        "note": f"from v{base_version}" if base_version else ""
    })

    upd = {"subtitle_versions_count": next_ver, "subtitle_verified": True, "subtitle_updated_at": now_utc()}
    if set_current:
        upd.update({"subtitle_current_version": next_ver, "subtitle_path": rel, "subtitle_blocks": blocks})
    db.videos.update_one({"_id": vid}, {"$set": upd})

    return jsonify({"ok": True, "new_version": next_ver, "path": rel, "blocks": blocks, "set_current": set_current})

@subtitle_bp.patch("/set_current")
def set_current():
    data = request.get_json(silent=True) or {}
    video_id = (data.get("video_id") or "").strip()
    version = int(data.get("version") or 0)
    if not video_id or version <= 0:
        return jsonify({"ok": False, "message": "missing video_id/version"}), 400
    try:
        vid = ObjectId(video_id)
    except Exception:
        return jsonify({"ok": False, "message": "bad video_id"}), 400

    s = db.subtitles.find_one({"video_id": vid, "version": version})
    if not s:
        return jsonify({"ok": False, "message": "version not found"}), 404

    db.videos.update_one({"_id": vid}, {"$set": {
        "subtitle_current_version": version,
        "subtitle_path": s.get("path"),
        "subtitle_blocks": s.get("blocks"),
    }})
    return jsonify({"ok": True, "version": version, "path": s.get("path")})

@subtitle_bp.post("/upload")
def upload_subtitle():
    """
    POST /api/subtitle/upload
    上傳字幕檔案到指定影片（覆蓋現有版本）
    """
    video_id = (request.form.get("video_id") or "").strip()
    file = request.files.get("file")

    if not video_id or not file:
        return jsonify({"ok": False, "message": "missing video_id or file"}), 400

    try:
        vid = ObjectId(video_id)
    except Exception:
        return jsonify({"ok": False, "message": "bad video_id"}), 400

    v = db.videos.find_one({"_id": vid})
    if not v:
        return jsonify({"ok": False, "message": "video not found"}), 404

    # 驗證檔案副檔名
    allowed_ext = {"srt", "vtt", "sub"}
    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in allowed_ext:
        return jsonify({"ok": False, "message": f"只接受 {','.join(allowed_ext)} 格式"}), 400

    # 讀取上傳的字幕內容
    try:
        try:
            data = file.read()
            raw = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raw = data.decode("cp950", errors="ignore")
    except Exception as e:
        return jsonify({"ok": False, "message": f"讀取檔案失敗: {str(e)}"}), 400

    # 驗證字幕格式
    ok_sub, blocks, errors = validate_subtitle_text_by_ext(raw, ext)
    if not ok_sub:
        return jsonify({"ok": False, "message": "字幕格式有誤", "subtitle_errors": errors}), 400

    # 生成新版本號（支援多版本）
    last = db.subtitles.find_one({"video_id": vid}, sort=[("version", -1)])
    next_ver = int((last or {}).get("version") or 1) + 1 if last else 2

    unit = (v.get("unit") or "U").strip()
    fname = f"{unit}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_v{next_ver}.{ext}"
    abs_path = os.path.join(SUBTITLE_DIR, fname)
    
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(raw)

    rel = os.path.join("uploads", "subtitles", fname).replace("\\", "/")

    # 存入 subtitles collection（允許多版本）
    db.subtitles.insert_one({
        "video_id": vid,
        "version": next_ver,
        "path": rel,
        "blocks": blocks,  # 不存儲 blocks，讓 text 自動解析
        "created_at": now_utc(),
        "created_by": "admin",
        "source": "upload",
        "note": filename
    })

    # 更新 videos collection（設為最新版本）
    db.videos.update_one({"_id": vid}, {"$set": {
        "subtitle_versions_count": next_ver,
        "subtitle_current_version": next_ver,
        "subtitle_path": rel,
        "subtitle_blocks": blocks,
        "subtitle_uploaded": True,
        "subtitle_updated_at": now_utc()
    }})

    return jsonify({
        "ok": True,
        "message": "字幕檔案上傳成功",
        "new_version": next_ver,
        "path": rel,
        "filename": filename
    })
