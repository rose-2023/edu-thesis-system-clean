import os
import re
import uuid
import subprocess
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from bson import ObjectId
from ..db import db

admin_upload_bp = Blueprint("admin_upload", __name__)

PROJECT_ROOT = os.getcwd()
UPLOADS_ROOT = os.path.join(PROJECT_ROOT, "uploads")

# =============================
# 基本設定
# =============================
ALLOWED_VIDEO_EXT = {"mp4", "webm", "mov"}
ALLOWED_SUB_EXT = {"srt", "txt"}

UPLOAD_DIR = os.path.join(PROJECT_ROOT, "uploads", "videos")
THUMB_DIR = os.path.join(UPLOADS_ROOT, "thumbnails")
SUBTITLE_DIR = os.path.join(PROJECT_ROOT, "uploads", "subtitles")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(THUMB_DIR, exist_ok=True)
os.makedirs(SUBTITLE_DIR, exist_ok=True)

MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB

# ✅ Windows 若找不到 ffmpeg/ffprobe，會改用環境變數 PATH 查找
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.environ.get("FFPROBE_BIN", "ffprobe")


@admin_upload_bp.get("/uploads/<path:filename>")
def serve_uploads(filename):
    return send_from_directory(UPLOADS_ROOT, filename)


# =============================
# 工具函式
# =============================
def allowed_ext(filename: str, allowed_set: set) -> bool:
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in allowed_set


def get_file_size(f):
    """取得檔案大小（支援流式讀取）"""
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(0)
    return size


def now_utc():
    return datetime.now(timezone.utc)


def safe_iso(dt: datetime):
    try:
        return dt.isoformat()
    except Exception:
        return str(dt)


# =============================
# SRT / TXT 時間軸檢查
# =============================
SRT_TIME_RE = re.compile(
    r"(?P<hs>\d{2}):(?P<ms>\d{2}):(?P<ss>\d{2}),(?P<hms>\d{3})\s*-->\s*"
    r"(?P<he>\d{2}):(?P<me>\d{2}):(?P<se>\d{2}),(?P<hme>\d{3})"
)


def _to_ms(h, m, s, ms):
    h = int(h); m = int(m); s = int(s)
    ms = int(ms) if ms is not None and ms != "" else 0
    if ms < 0:
        ms = 0
    if ms > 999:
        ms = 999
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def validate_srt_text(text: str, max_errors=20):
    """
    檢查：
    1) 每段時間格式正確
    2) start < end
    3) 時間不得倒退（下一段 start 不能小於上一段 start）
    """
    errors = []
    blocks = 0
    last_start = None

    lines = text.splitlines()
    for idx, line in enumerate(lines, start=1):
        line_stripped = line.strip()
        if "-->" not in line_stripped:
            continue

        m = SRT_TIME_RE.search(line_stripped.replace(".", ","))
        if not m:
            errors.append(f"第 {idx} 行：時間格式錯誤（應為 00:00:00,000 --> 00:00:00,000）")
            if len(errors) >= max_errors:
                break
            continue

        start_ms = _to_ms(m.group("hs"), m.group("ms"), m.group("ss"), m.group("hms"))
        end_ms = _to_ms(m.group("he"), m.group("me"), m.group("se"), m.group("hme"))

        blocks += 1

        if start_ms >= end_ms:
            errors.append(f"第 {idx} 行：開始時間 >= 結束時間")
        if last_start is not None and start_ms < last_start:
            errors.append(f"第 {idx} 行：時間軸倒退（本段開始小於上一段開始）")

        last_start = start_ms

        if len(errors) >= max_errors:
            break

    if blocks == 0:
        errors.append("找不到任何字幕時間段（沒有 '-->'）")

    return (len(errors) == 0), blocks, errors


def validate_txt_text(text: str, max_errors=20):
    """
    TXT 很多格式不一，這裡採「保守」檢查：
    - 只要找到像時間的行（含 00:00:xx），嘗試解析
    - 若行內同時有 start/end（含 -->）就檢查 start < end
    - 若只有單一時間，檢查時間不得倒退
    """
    errors = []
    blocks = 0
    last_t = None

    lines = text.splitlines()
    for idx, line in enumerate(lines, start=1):
        line_stripped = line.strip()
        if not line_stripped:
            continue

        if not re.search(r"\d{2}:\d{2}:\d{2}", line_stripped):
            continue

        if "-->" in line_stripped:
            line_norm = line_stripped.replace(".", ",")
            m = SRT_TIME_RE.search(line_norm)
            if not m:
                errors.append(f"第 {idx} 行：時間格式錯誤")
                if len(errors) >= max_errors:
                    break
                continue

            start_ms = _to_ms(m.group("hs"), m.group("ms"), m.group("ss"), m.group("hms"))
            end_ms = _to_ms(m.group("he"), m.group("me"), m.group("se"), m.group("hme"))

            blocks += 1
            if start_ms >= end_ms:
                errors.append(f"第 {idx} 行：開始時間 >= 結束時間")
            if last_t is not None and start_ms < last_t:
                errors.append(f"第 {idx} 行：時間軸倒退（本段開始小於上一段）")
            last_t = start_ms
        else:
            m = re.search(r"(\d{2}):(\d{2}):(\d{2})([.,](\d{1,3}))?", line_stripped)
            if not m:
                continue
            hh, mm, ss = m.group(1), m.group(2), m.group(3)
            ms = m.group(5) if m.group(5) else "0"
            t_ms = _to_ms(hh, mm, ss, ms)
            blocks += 1

            if last_t is not None and t_ms < last_t:
                errors.append(f"第 {idx} 行：時間軸倒退（此時間小於上一個時間）")
            last_t = t_ms

        if len(errors) >= max_errors:
            break

    if blocks == 0:
        errors.append("找不到任何可解析的時間（例如 00:00:01）")

    return (len(errors) == 0), blocks, errors


def validate_subtitle_text_by_ext(text: str, ext: str):
    ext = (ext or "").lower()
    if ext == "srt":
        return validate_srt_text(text)
    return validate_txt_text(text)


def validate_subtitle_file(path: str):
    ext = path.rsplit(".", 1)[1].lower()
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            text = f.read()
    except UnicodeDecodeError:
        with open(path, "r", encoding="cp950", errors="ignore") as f:
            text = f.read()

    if ext == "srt":
        return validate_srt_text(text)
    else:
        return validate_txt_text(text)


# =============================
# ffprobe 取得影片長度（秒）
# =============================
def probe_duration_sec(video_path: str):
    try:
        cmd = [
            FFPROBE_BIN,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if r.returncode != 0:
            print(f"[WARN] ffprobe duration failed: {r.stderr.strip()}")
            return None
        val = (r.stdout or "").strip()
        if not val:
            return None
        return int(float(val))
    except Exception as e:
        print(f"[WARN] ffprobe duration exception: {e}")
        return None


# =============================
# ffmpeg 產生縮圖
# =============================
def generate_thumbnail(video_path: str, filename_no_ext: str):
    try:
        out_name = f"{filename_no_ext}.jpg"
        out_path = os.path.join(THUMB_DIR, out_name)

        cmd = [
            FFMPEG_BIN,
            "-y",
            "-ss", "00:00:01",
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            out_path
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if r.returncode != 0:
            print(f"[WARN] generate_thumbnail failed: {r.stderr.strip()}")
            return None

        rel = os.path.join("thumbnails", out_name).replace("\\", "/")
        return rel
    except Exception as e:
        print(f"[WARN] generate_thumbnail exception: {e}")
        return None


# =============================
# ✅ 單元列表（給 StudentLearning / 字幕校正用）
# GET /api/admin_upload/units
# =============================
@admin_upload_bp.get("/units")
def list_units():
    try:
        # 只抓未刪除（可含 inactive）
        units = db.videos.distinct("unit", {"deleted": {"$ne": True}})
        units = [u for u in units if u]
        units.sort()
        return jsonify({"ok": True, "units": units})
    except Exception as e:
        return jsonify({"ok": False, "message": "讀取單元失敗", "error": str(e)}), 500


# =============================
# ✅ 查單一影片（給字幕校正頁顯示影片資訊）
# GET /api/admin_upload/video/<video_id>
# =============================
@admin_upload_bp.get("/video/<video_id>")
def get_video(video_id):
    try:
        vid = ObjectId(video_id)
    except Exception:
        return jsonify({"ok": False, "message": "video_id 格式錯誤"}), 400

    v = db.videos.find_one({"_id": vid})
    if not v:
        return jsonify({"ok": False, "message": "找不到影片"}), 404

    v["_id"] = str(v["_id"])
    for k in ("created_at", "deleted_at", "subtitle_updated_at"):
        if isinstance(v.get(k), datetime):
            v[k] = safe_iso(v[k])

    # 防呆欄位
    v.setdefault("subtitle_verified", False)
    v.setdefault("subtitle_current_version", 1)
    v.setdefault("subtitle_versions_count", 1)

    return jsonify({"ok": True, "video": v})


# =============================
# ✅ 字幕版本列表
# GET /api/admin_upload/subtitle/versions?video_id=xxx
# =============================
@admin_upload_bp.get("/subtitle/versions")
def subtitle_versions():
    video_id = (request.args.get("video_id") or "").strip()
    if not video_id:
        return jsonify({"ok": False, "message": "缺少 video_id"}), 400
    try:
        vid = ObjectId(video_id)
    except Exception:
        return jsonify({"ok": False, "message": "video_id 格式錯誤"}), 400

    # subtitles collection：一筆一版本
    cursor = db.subtitles.find({"video_id": vid}).sort("version", -1)
    items = list(cursor)

    out = []
    for s in items:
        out.append({
            "_id": str(s["_id"]),
            "video_id": str(s["video_id"]),
            "version": s.get("version", 1),
            "path": s.get("path"),
            "blocks": s.get("blocks"),
            "created_at": safe_iso(s["created_at"]) if isinstance(s.get("created_at"), datetime) else s.get("created_at"),
            "created_by": s.get("created_by"),
            "source": s.get("source", "upload"),   # upload / corrected
            "note": s.get("note", "")
        })

    # 若沒有 subtitles 記錄（舊資料），用 videos 的 subtitle_path 補一筆虛擬版本
    if not out:
        v = db.videos.find_one({"_id": vid})
        if v and v.get("subtitle_path"):
            out = [{
                "_id": "",
                "video_id": str(vid),
                "version": int(v.get("subtitle_current_version") or 1),
                "path": v.get("subtitle_path"),
                "blocks": v.get("subtitle_blocks"),
                "created_at": safe_iso(v["created_at"]) if isinstance(v.get("created_at"), datetime) else "",
                "created_by": v.get("uploaded_by", "admin"),
                "source": "upload",
                "note": "legacy"
            }]

    return jsonify({"ok": True, "versions": out})


# =============================
# ✅ 取得字幕內容
# GET /api/admin_upload/subtitle/content?video_id=xxx&version=1
# =============================
@admin_upload_bp.get("/subtitle/content")
def subtitle_content():
    video_id = (request.args.get("video_id") or "").strip()
    version = int(request.args.get("version") or 0)

    if not video_id:
        return jsonify({"ok": False, "message": "缺少 video_id"}), 400
    try:
        vid = ObjectId(video_id)
    except Exception:
        return jsonify({"ok": False, "message": "video_id 格式錯誤"}), 400

    if version <= 0:
        # 預設用 videos 的 current_version
        v = db.videos.find_one({"_id": vid})
        version = int((v or {}).get("subtitle_current_version") or 1)

    s = db.subtitles.find_one({"video_id": vid, "version": version})
    if s and s.get("path"):
        rel_path = s["path"]
    else:
        v = db.videos.find_one({"_id": vid})
        if not v or not v.get("subtitle_path"):
            return jsonify({"ok": False, "message": "找不到字幕路徑"}), 404
        rel_path = v["subtitle_path"]

    abs_path = os.path.join(PROJECT_ROOT, rel_path.replace("/", os.sep))
    if not os.path.exists(abs_path):
        return jsonify({"ok": False, "message": "字幕檔不存在（路徑無效）"}), 404

    # 讀檔
    try:
        try:
            with open(abs_path, "r", encoding="utf-8-sig") as f:
                text = f.read()
        except UnicodeDecodeError:
            with open(abs_path, "r", encoding="cp950", errors="ignore") as f:
                text = f.read()
    except Exception as e:
        return jsonify({"ok": False, "message": f"讀字幕失敗: {e}"}), 500

    ext = abs_path.rsplit(".", 1)[-1].lower() if "." in abs_path else "srt"
    return jsonify({"ok": True, "version": version, "ext": ext, "text": text, "path": rel_path})


# =============================
# ✅ 儲存校正版字幕（建立新版本 + 可設為 current）
# POST /api/admin_upload/subtitle/save_corrected
# json: { video_id, base_version, text, created_by, set_current=true }
# =============================
@admin_upload_bp.post("/subtitle/save_corrected")
def subtitle_save_corrected():
    data = request.get_json(silent=True) or {}

    video_id = (data.get("video_id") or "").strip()
    base_version = int(data.get("base_version") or 0)
    text = data.get("text") or ""
    created_by = (data.get("created_by") or "admin").strip()
    set_current = bool(data.get("set_current", True))

    if not video_id:
        return jsonify({"ok": False, "message": "缺少 video_id"}), 400
    if not text.strip():
        return jsonify({"ok": False, "message": "字幕內容不可為空"}), 400
    try:
        vid = ObjectId(video_id)
    except Exception:
        return jsonify({"ok": False, "message": "video_id 格式錯誤"}), 400

    v = db.videos.find_one({"_id": vid})
    if not v:
        return jsonify({"ok": False, "message": "找不到影片"}), 404

    # 依 base_version 找原始副檔名（srt/txt），找不到就用 video 的 subtitle_path
    ext = "srt"
    if base_version > 0:
        s = db.subtitles.find_one({"video_id": vid, "version": base_version})
        if s and s.get("path") and "." in s["path"]:
            ext = s["path"].rsplit(".", 1)[1].lower()
    if v.get("subtitle_path") and "." in v["subtitle_path"]:
        ext = v["subtitle_path"].rsplit(".", 1)[1].lower()

    ok_sub, blocks, errors = validate_subtitle_text_by_ext(text, ext)
    if not ok_sub:
        return jsonify({
            "ok": False,
            "message": "字幕時間軸有錯，請修正後再儲存",
            "subtitle_errors": errors,
            "blocks": blocks
        }), 400

    # 新版本號：取 subtitles 最大 version + 1
    last = db.subtitles.find_one({"video_id": vid}, sort=[("version", -1)])
    next_ver = int((last or {}).get("version") or 1) + 1 if last else 2

    # 生成檔名
    uniq = uuid.uuid4().hex[:8]
    unit = (v.get("unit") or "U").strip()
    save_name = f"{unit}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_v{next_ver}_{uniq}.{ext}"
    abs_path = os.path.join(SUBTITLE_DIR, save_name)

    # 寫檔
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(text)

    rel_path = os.path.join("uploads", "subtitles", save_name).replace("\\", "/")

    # 寫 subtitles collection
    db.subtitles.insert_one({
        "video_id": vid,
        "version": next_ver,
        "path": rel_path,
        "blocks": blocks,
        "created_at": now_utc(),
        "created_by": created_by,
        "source": "corrected",
        "note": f"from v{base_version}" if base_version else ""
    })

    # 更新 videos：可設 current 版本與 verified 狀態
    upd = {
        "subtitle_versions_count": next_ver,
        "subtitle_updated_at": now_utc(),
        "subtitle_verified": True,  # 校正後視為已校正
    }
    if set_current:
        upd["subtitle_current_version"] = next_ver
        upd["subtitle_path"] = rel_path
        upd["subtitle_blocks"] = blocks

    db.videos.update_one({"_id": vid}, {"$set": upd})

    return jsonify({
        "ok": True,
        "video_id": video_id,
        "new_version": next_ver,
        "path": rel_path,
        "blocks": blocks,
        "set_current": set_current
    })


# =============================
# ✅ 切換當前字幕版本
# PATCH /api/admin_upload/subtitle/set_current
# json: { video_id, version }
# =============================
@admin_upload_bp.patch("/subtitle/set_current")
def subtitle_set_current():
    data = request.get_json(silent=True) or {}
    video_id = (data.get("video_id") or "").strip()
    version = int(data.get("version") or 0)

    if not video_id or version <= 0:
        return jsonify({"ok": False, "message": "缺少 video_id 或 version"}), 400
    try:
        vid = ObjectId(video_id)
    except Exception:
        return jsonify({"ok": False, "message": "video_id 格式錯誤"}), 400

    s = db.subtitles.find_one({"video_id": vid, "version": version})
    if not s:
        return jsonify({"ok": False, "message": "找不到該字幕版本"}), 404

    db.videos.update_one(
        {"_id": vid},
        {"$set": {
            "subtitle_current_version": version,
            "subtitle_path": s.get("path"),
            "subtitle_blocks": s.get("blocks"),
        }}
    )
    return jsonify({"ok": True, "video_id": video_id, "version": version, "path": s.get("path")})


# =============================
# 診斷端點
# =============================
@admin_upload_bp.get("/health")
def health_check():
    try:
        total = db.videos.count_documents({})
        users_total = db.users.count_documents({})
        return jsonify({
            "ok": True,
            "db_connected": True,
            "videos_count": total,
            "users_count": users_total,
            "db_name": db.name
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "db_connected": False,
            "error": str(e)
        }), 500


@admin_upload_bp.get("/debug/videos-raw")
def debug_videos_raw():
    try:
        cursor = db.videos.find({}).limit(5)
        videos = list(cursor)
        result = []
        for v in videos:
            v["_id"] = str(v["_id"])
            result.append(v)
        return jsonify({"ok": True, "count": len(result), "videos": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# =============================
# ✅ 字幕檢查端點（上傳前）
# POST /api/admin_upload/subtitle/validate
# form-data: file
# =============================
@admin_upload_bp.post("/subtitle/validate")
def subtitle_validate():
    if "file" not in request.files:
        return jsonify({"ok": False, "message": "缺少檔案欄位 file"}), 400

    f = request.files["file"]
    if not f or f.filename == "":
        return jsonify({"ok": False, "message": "未選擇檔案"}), 400

    if not allowed_ext(f.filename, ALLOWED_SUB_EXT):
        return jsonify({"ok": False, "message": "字幕格式只支援 .srt / .txt"}), 400

    # 存到暫存
    original_name = f.filename
    original_ext = original_name.rsplit(".", 1)[1].lower()

    safe_name = secure_filename(original_name) or "subtitle"
    uniq = uuid.uuid4().hex[:8]
    save_name = f"validate_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uniq}.{original_ext}"
    save_path = os.path.join(SUBTITLE_DIR, save_name)
    f.save(save_path)

    ok, blocks, errors = validate_subtitle_file(save_path)

    try:
        os.remove(save_path)
    except Exception:
        pass

    if ok:
        return jsonify({"ok": True, "blocks": blocks})
    else:
        return jsonify({"ok": False, "errors": errors, "blocks": blocks}), 400


# =============================
# 上傳影片（字幕必填 + 先檢查字幕時間軸）
# POST /api/admin_upload/video
# form-data: unit, title, description, uploaded_by, file(video), subtitle(srt/txt)
# =============================
@admin_upload_bp.post("/video")
def upload_video():
    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip()
    uploaded_by = (request.form.get("uploaded_by") or "").strip()
    unit = (request.form.get("unit") or "").strip()

    if not unit or not title:
        return jsonify({"ok": False, "message": "unit 與 title 必填"}), 400

    # 影片
    if "file" not in request.files:
        return jsonify({"ok": False, "message": "缺少影片欄位 file"}), 400
    vf = request.files["file"]
    if not vf or vf.filename == "":
        return jsonify({"ok": False, "message": "未選擇影片檔案"}), 400
    if not allowed_ext(vf.filename, ALLOWED_VIDEO_EXT):
        return jsonify({"ok": False, "message": "影片格式不支援（建議 mp4）"}), 400

    # 字幕（必填）
    if "subtitle" not in request.files:
        return jsonify({"ok": False, "message": "字幕檔必填（欄位 subtitle）"}), 400
    sf = request.files["subtitle"]
    if not sf or sf.filename == "":
        return jsonify({"ok": False, "message": "字幕檔必填（.srt / .txt）"}), 400
    if not allowed_ext(sf.filename, ALLOWED_SUB_EXT):
        return jsonify({"ok": False, "message": "字幕格式只支援 .srt / .txt"}), 400

    # 影片大小驗證
    file_size = get_file_size(vf)
    if file_size > MAX_FILE_SIZE:
        max_size_mb = MAX_FILE_SIZE / (1024 * 1024)
        actual_size_mb = file_size / (1024 * 1024)
        return jsonify({
            "ok": False,
            "message": f"影片檔案太大（{actual_size_mb:.1f}MB）。最大限制：{max_size_mb:.0f}MB"
        }), 413

    # 先存字幕並驗證
    sub_original_name = sf.filename
    sub_ext = sub_original_name.rsplit(".", 1)[1].lower()

    sub_uniq = uuid.uuid4().hex[:8]
    subtitle_filename = f"{unit}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_v1_{sub_uniq}.{sub_ext}"
    subtitle_abs = os.path.join(SUBTITLE_DIR, subtitle_filename)
    sf.save(subtitle_abs)

    ok_sub, blocks, sub_errors = validate_subtitle_file(subtitle_abs)
    if not ok_sub:
        try:
            os.remove(subtitle_abs)
        except Exception:
            pass
        return jsonify({
            "ok": False,
            "message": "字幕時間軸有錯，請檢查後再上傳",
            "subtitle_errors": sub_errors,
            "blocks": blocks
        }), 400

    subtitle_rel = os.path.join("uploads", "subtitles", subtitle_filename).replace("\\", "/")

    # 存影片
    original_name = vf.filename
    video_ext = original_name.rsplit(".", 1)[1].lower()
    uniq = uuid.uuid4().hex[:8]
    filename = f"{unit}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uniq}.{video_ext}"
    save_path = os.path.join(UPLOAD_DIR, filename)
    vf.save(save_path)

    size = os.path.getsize(save_path)
    content_type = vf.mimetype

    duration_sec = probe_duration_sec(save_path)
    filename_no_ext = filename.rsplit(".", 1)[0]
    thumbnail_rel = generate_thumbnail(save_path, filename_no_ext)

    # 寫 videos
    doc = {
        "unit": unit,
        "title": title,
        "description": description,
        "filename": filename,
        "original_name": original_name,
        "content_type": content_type,
        "size": size,
        "path": os.path.join("uploads", "videos", filename).replace("\\", "/"),
        "uploaded_by": uploaded_by or "admin",
        "created_at": now_utc(),

        "active": True,
        "deleted": False,
        "deleted_at": None,
        "deleted_by": None,

        # 字幕
        "subtitle_filename": subtitle_filename,
        "subtitle_path": subtitle_rel,
        "subtitle_blocks": blocks,

        # ✅ 校正/版本狀態（你之前的「字幕版本顯示」需求）
        "subtitle_verified": False,            # 初上傳先視為未校正
        "subtitle_current_version": 1,
        "subtitle_versions_count": 1,
        "subtitle_updated_at": now_utc(),

        # 影片資訊
        "duration_sec": duration_sec,
        "thumbnail": thumbnail_rel
    }

    r = db.videos.insert_one(doc)
    video_id = r.inserted_id

    # ✅ 同步寫入 subtitles collection（v1）
    db.subtitles.insert_one({
        "video_id": video_id,
        "version": 1,
        "path": subtitle_rel,
        "blocks": blocks,
        "created_at": now_utc(),
        "created_by": uploaded_by or "admin",
        "source": "upload",
        "note": ""
    })

    return jsonify({
        "ok": True,
        "video_id": str(video_id),
        "filename": filename,
        "path": doc["path"],
        "thumbnail": doc["thumbnail"],
        "duration_sec": doc["duration_sec"],
        "subtitle_path": doc["subtitle_path"],
        "subtitle_current_version": 1,
        "subtitle_versions_count": 1,
        "subtitle_verified": False
    })


# =============================
# 影片清單（分頁/搜尋/分類）
# GET /api/admin_upload/videos?status=active|inactive|deleted&unit=&title=&page=1&per_page=10
# =============================
@admin_upload_bp.get("/videos")
def list_videos():
    try:
        status = (request.args.get("status") or "active").strip()
        q_unit = (request.args.get("unit") or "").strip()
        q_title = (request.args.get("title") or "").strip()

        page = int(request.args.get("page") or 1)
        per_page = int(request.args.get("per_page") or 10)
        if page < 1:
            page = 1
        if per_page < 1 or per_page > 50:
            per_page = 10

        q = {}

        if status == "deleted":
            q["deleted"] = True
        elif status == "inactive":
            q["deleted"] = False
            q["active"] = False
        else:
            q["deleted"] = False
            q["active"] = True

        if q_unit:
            q["unit"] = q_unit

        if q_title:
            q["title"] = {"$regex": re.escape(q_title), "$options": "i"}

        total = db.videos.count_documents(q)

        cursor = (
            db.videos.find(q)
            .sort("created_at", -1)
            .skip((page - 1) * per_page)
            .limit(per_page)
        )

        vids = list(cursor)

        for v in vids:
            v["_id"] = str(v["_id"])
            if isinstance(v.get("created_at"), datetime):
                v["created_at"] = safe_iso(v["created_at"])
            if isinstance(v.get("deleted_at"), datetime):
                v["deleted_at"] = safe_iso(v["deleted_at"])
            if isinstance(v.get("subtitle_updated_at"), datetime):
                v["subtitle_updated_at"] = safe_iso(v["subtitle_updated_at"])

            v.setdefault("active", True)
            v.setdefault("deleted", False)
            v.setdefault("subtitle_verified", False)
            v.setdefault("subtitle_current_version", 1)
            v.setdefault("subtitle_versions_count", 1)

        return jsonify({
            "ok": True,
            "videos": vids,
            "total": total,
            "page": page,
            "per_page": per_page
        })
    except Exception as e:
        return jsonify({"ok": False, "message": "讀取失敗", "error": str(e)}), 500


# =============================
# 啟用/停用
# PATCH /api/admin_upload/video/<video_id>/active  json: {active: true/false}
# =============================
@admin_upload_bp.patch("/video/<video_id>/active")
def set_video_active(video_id):
    try:
        vid = ObjectId(video_id)
    except Exception:
        return jsonify({"ok": False, "message": "video_id 格式錯誤"}), 400

    data = request.get_json() or {}
    active = data.get("active", None)
    if active is None:
        return jsonify({"ok": False, "message": "缺少 active 欄位"}), 400

    v = db.videos.find_one({"_id": vid})
    if not v:
        return jsonify({"ok": False, "message": "找不到影片"}), 404
    if v.get("deleted"):
        return jsonify({"ok": False, "message": "已刪除影片不可切換啟用狀態"}), 400

    r = db.videos.update_one({"_id": vid}, {"$set": {"active": bool(active)}})
    if r.matched_count == 0:
        return jsonify({"ok": False, "message": "找不到影片"}), 404

    return jsonify({"ok": True, "video_id": video_id, "active": bool(active)})


# =============================
# 軟刪除
# PATCH /api/admin_upload/video/<video_id>/delete  json: {deleted_by: "admin"}
# =============================
@admin_upload_bp.patch("/video/<video_id>/delete")
def soft_delete_video(video_id):
    try:
        vid = ObjectId(video_id)
    except Exception:
        return jsonify({"ok": False, "message": "video_id 格式錯誤"}), 400

    data = request.get_json() or {}
    deleted_by = (data.get("deleted_by") or "admin").strip()

    v = db.videos.find_one({"_id": vid})
    if not v:
        return jsonify({"ok": False, "message": "找不到影片"}), 404
    if v.get("deleted"):
        return jsonify({"ok": False, "message": "此影片已在已刪除分類"}), 400

    db.videos.update_one(
        {"_id": vid},
        {"$set": {
            "deleted": True,
            "deleted_at": now_utc(),
            "deleted_by": deleted_by,
            "active": False
        }}
    )
    return jsonify({"ok": True, "video_id": video_id})


# =============================
# （保留）真刪除端點：不提供
# =============================
@admin_upload_bp.delete("/video/<video_id>")
def delete_video(video_id):
    return jsonify({
        "ok": False,
        "message": "此系統採用軟刪除，資料庫不提供硬刪除。"
    }), 400
