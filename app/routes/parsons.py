import os
import re
import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Tuple, Optional

from flask import Blueprint, request, jsonify
from bson import ObjectId
from bson.errors import InvalidId

from ..db import db

from . import parsons_ai  # [新增] 統一管理 OpenAI 呼叫（方案1）

# OpenAI SDK
try:
    from openai import OpenAI
except Exception:
    OpenAI = None


parsons_bp = Blueprint("parsons", __name__)


# =========================
# Utils
# =========================
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def maybe_oid(s: str) -> Optional[ObjectId]:
    try:
        return ObjectId(str(s))
    except Exception:
        return None


def normalize_video_id(x) -> str:
    if x is None:
        return ""
    if isinstance(x, ObjectId):
        return str(x)
    return str(x).strip()



# =========================
# ✅ V1.8 Test (Pre/Post) Utils
# =========================
_TEST_INDEX_READY = False

def ensure_test_indexes():
    """Create unique index for test attempts (one per student per cycle per role)."""
    global _TEST_INDEX_READY
    if _TEST_INDEX_READY:
        return
    try:
        db.parsons_test_attempts.create_index(
            [("student_id", 1), ("test_cycle_id", 1), ("test_role", 1)],
            unique=True,
            name="uniq_student_cycle_role",
        )
    except Exception:
        pass
    _TEST_INDEX_READY = True


def get_default_test_cycle_id() -> str:
    """預設測驗批次。v1.8 統一使用 test_control，不再依賴 parsons_test_cycles。"""
    return "default"

def is_posttest_open(test_cycle_id: str) -> bool:
    """是否開放後測（統一讀 test_control）。"""
    test_cycle_id = (test_cycle_id or "default").strip() or "default"
    doc = db.test_control.find_one({"_id": f"post_open:{test_cycle_id}"}) or {}
    return bool(doc.get("post_open", False))

def log_event(event: str, **kwargs):
    try:
        doc = {"event": event, "created_at": now_utc()}
        doc.update(kwargs)
        db.events.insert_one(doc)
    except Exception:
        pass


def read_subtitle_text(path: str) -> str:
    if not path:
        return ""
    path = path.strip()
    full = path
    if not os.path.isabs(path):
        full = os.path.join(os.getcwd(), path)

    if not os.path.exists(full):
        return ""
    try:
        with open(full, "r", encoding="utf-8-sig", errors="ignore") as f:
            return f.read()
    except Exception:
        try:
            with open(full, "r", encoding="cp950", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""


def env_snapshot() -> Dict[str, Any]:
    return {
        "AI_ENABLED": os.getenv("AI_ENABLED"),
        "OPENAI_MODEL": os.getenv("OPENAI_MODEL"),
        "OPENAI_API_KEY_exists": bool(os.getenv("OPENAI_API_KEY")),
    }


def ai_enabled() -> bool:
    v = (os.getenv("AI_ENABLED") or "").strip().lower()
    return v in ["1", "true", "yes", "y", "on"]


def get_openai_client() -> "OpenAI":
    if OpenAI is None:
        raise RuntimeError("openai 套件未安裝，請先 pip install openai")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 未設定（請在 .env 設定 OPENAI_API_KEY=...）")
    return OpenAI(api_key=api_key)


def safe_json_loads(s: str) -> Optional[dict]:
    try:
        return json.loads(s)
    except Exception:
        return None


# =========================
# subtitles / parsing helpers
# =========================
SRT_TIME_RE = re.compile(r"(\d+):(\d+):(\d+),(\d+)")


def srt_time_to_seconds(t: str) -> float:
    m = SRT_TIME_RE.search(t or "")
    if not m:
        return 0.0
    hh, mm, ss, ms = m.groups()
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


def strip_srt_noise(srt_text: str) -> str:
    if not srt_text:
        return ""
    lines = []
    for ln in srt_text.splitlines():
        t = ln.strip()
        if not t:
            continue
        if t.isdigit():
            continue
        if "-->" in t:
            continue
        lines.append(t)
    return "\n".join(lines).strip()


def parse_srt_segments(srt_text: str) -> list:
    if not srt_text:
        return []
    lines = [ln.rstrip("\n") for ln in srt_text.splitlines()]
    segs = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.isdigit():
            i += 1
            if i >= len(lines):
                break
            time_line = lines[i].strip()
            if "-->" not in time_line:
                continue
            parts = [p.strip() for p in time_line.split("-->")]
            start = srt_time_to_seconds(parts[0])
            end = srt_time_to_seconds(parts[1]) if len(parts) > 1 else start
            i += 1
            text_buf = []
            while i < len(lines) and lines[i].strip() != "":
                text_buf.append(lines[i].strip())
                i += 1
            txt = " ".join(text_buf).strip()
            if txt:
                segs.append({"start": start, "end": end, "text": txt})
        i += 1
    return segs


def compact_segments_for_prompt(segs: list, max_chars: int = 12000) -> str:
    out = []
    total = 0
    for s in segs:
        st = int(float(s.get("start", 0)))
        ed = int(float(s.get("end", 0)))
        txt = (s.get("text") or "").strip()
        line = f"[{st}-{ed}] {txt}"
        if total + len(line) + 1 > max_chars:
            break
        out.append(line)
        total += len(line) + 1
    return "\n".join(out)


def extract_context_around(segs: list, start: float, end: float, window: int = 5) -> str:
    picked = []
    for s in segs:
        st = float(s.get("start", 0))
        ed = float(s.get("end", 0))
        if ed < start:
            continue
        if st > end:
            continue
        picked.append(s)
    if not picked:
        return ""
    picked = picked[: max(1, window)]
    return "\n".join([f"[{int(p.get('start', 0))}-{int(p.get('end', 0))}] {p.get('text','')}" for p in picked])


def pick_latest_subtitle_path(video_doc: dict, video_id_str: str) -> str:
    try:
        vid_oid = video_doc.get("_id") or maybe_oid(video_id_str)
        if vid_oid:
            sub_doc = db.subtitles.find_one(
                {"video_id": vid_oid},
                sort=[("version", -1), ("created_at", -1)]
            )
            if sub_doc and (sub_doc.get("path") or "").strip():
                return (sub_doc.get("path") or "").strip()

        if vid_oid:
            sub_doc2 = db.subtitles.find_one(
                {"video_id": str(vid_oid)},
                sort=[("version", -1), ("created_at", -1)]
            )
            if sub_doc2 and (sub_doc2.get("path") or "").strip():
                return (sub_doc2.get("path") or "").strip()
    except Exception:
        pass

    return (video_doc.get("subtitle_path", "") or "").strip()


# =========================
# t5doc_to_parsons_task (teacher->student normalize)
# =========================
def t5doc_to_parsons_task(doc: dict) -> dict:
    """
    將 parsons_tasks / parsons_test_tasks 來源任務（doc）轉成前端 Parsons.vue 期待的格式。
    - 支援 template_slots 可能是 dict list 或 str list（舊資料/手動匯入）。
    """
    question_text = doc.get("question_text") or doc.get("question") or ""
    solution_blocks = doc.get("solution_blocks") or []
    distractor_blocks = doc.get("distractor_blocks") or []
    template_slots = doc.get("template_slots") or []

    # --- Normalize blocks (防呆：若舊資料是 string list) ---
    def _norm_blocks(blocks):
        out = []
        for i, b in enumerate(blocks or []):
            if isinstance(b, dict):
                bid = b.get("id") or b.get("_id") or f"b{i+1}"
                out.append({
                    "id": str(bid),
                    "text": b.get("text") if b.get("text") is not None else b.get("code", ""),
                    "type": b.get("type") or "solution",
                })
            else:
                out.append({"id": f"b{i+1}", "text": str(b), "type": "solution"})
        return out

    solution_blocks = _norm_blocks(solution_blocks)
    distractor_blocks = _norm_blocks(distractor_blocks)

    # --- Normalize template_slots ---
    # 期望是 list[dict]：{slot, label, expected_id}
    if template_slots and not isinstance(template_slots[0], dict):
        # 例如：["s1","s2"...] 或 [0,1,2...] → 轉成 dict list
        _tmp = []
        for i, s in enumerate(template_slots):
            _tmp.append({
                "slot": str(s),
                "label": f"第{i+1}格",
            })
        template_slots = _tmp

    # 若 template_slots 為空，依 solution_blocks 長度自動生成
    if not template_slots:
        template_slots = [{"slot": f"s{i+1}", "label": f"第{i+1}格"} for i in range(len(solution_blocks))]

    # 補 expected_id：若資料本身沒有，就按照 solution_blocks 順序對齊
    for i in range(min(len(template_slots), len(solution_blocks))):
        if not template_slots[i].get("expected_id"):
            template_slots[i]["expected_id"] = solution_blocks[i]["id"]

    pool = doc.get("pool")
    if not pool:
        pool = solution_blocks + distractor_blocks

    # 中文語意：測驗不需要，但若有也一起帶給前端（前端可選擇不顯示）
    ai_slot_hints = doc.get("ai_slot_hints") or doc.get("slot_hints") or {}

    return {
        "task_id": str(doc.get("_id", "")),
        "question_text": question_text,
        "solution_blocks": solution_blocks,
        "distractor_blocks": distractor_blocks,
        "pool": pool,
        "template_slots": template_slots,
        "ai_slot_hints": ai_slot_hints,
        "status": doc.get("status") or doc.get("review_status") or "",
        "enabled": bool(doc.get("enabled", True)),
    }


# =========================
# Fallback generator
# =========================
def simple_fallback_generate(sub_text: str, unit: str, video_title: str, level: str = "L1") -> Dict[str, Any]:
    question_text = "（備援題目）請完成一段程式：根據題目需求輸入資料並輸出結果。"
    solution_lines = ["x = int(input())", "y = int(input())", "print(x + y)"]
    distractor_lines = ["x = input()", "y = input()", "print(x - y)", "print(x * y)"]

    solution_blocks = [{"id": f"b{i+1}", "text": line, "type": "core"} for i, line in enumerate(solution_lines)]
    distractor_blocks = [{"id": f"d{i+1}", "text": line, "type": "distractor"} for i, line in enumerate(distractor_lines)]
    pool = distractor_blocks + solution_blocks
    template_slots = [{"label": f"請放入正確的第{i+1}行", "slot": str(i)} for i in range(len(solution_lines))]

    return {
        "question_text": question_text,
        "solution_blocks": solution_blocks,
        "distractor_blocks": distractor_blocks,
        "pool": pool,
        "template_slots": template_slots,
        "ai_feedback": {"general": "（AI 生成失敗，使用系統備援題目）", "common_mistakes": [], "hints": []},
    }


# =========================
# OpenAI generator
# =========================
def ai_generate_parsons_from_subtitle(subtitle_text: str, unit: str, video_title: str, level: str = "L1") -> Dict[str, Any]:
    """
    V1.6-goClass-lite（Parsons 低 token + 閉環驗收 + 回看片段）
    - Parsons 題（非選擇題）
    - 低 token：AI 只生「question_text + solution_lines」，干擾題/中文語意用本地規則（0 token）
    - goClass 風：本地驗收確保 solution_lines 可執行且符合題意（閉環），縮排錯也算錯（透過干擾題 + 本地檢查）
    - B：額外用 AI 產出 segment_map（含 evidence）+ slot_hints（每格一句），用於錯誤回看影片片段
    """
    if not ai_enabled():
        raise RuntimeError("AI_ENABLED is false -> skip OpenAI")
    if not subtitle_text:
        raise RuntimeError("subtitle_text is empty")

    # --- subtitles ---
    segs = parse_srt_segments(subtitle_text)
    cleaned = strip_srt_noise(subtitle_text)

    # [新增] 低 token：字幕壓縮（只提供必要片段給 AI）
    segs_compact = compact_segments_for_prompt(segs, max_chars=4000)
    if not segs_compact:
        # 仍允許無字幕，但 segment_map 會走 fallback
        segs_compact = ""

    import random, hashlib, ast, io, contextlib

    model = (os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()

    # ===============================
    # [新增] 0 token：用字幕關鍵字挑情境（看不出來就 seed 隨機）
    # ===============================
    def _kw_hit(*kws: str) -> bool:
        t = (cleaned or "").lower()
        return any((kw.lower() in t) for kw in kws if kw)

    scenarios = [
        {
            "name": "avg_scores",
            "desc": "成績平均：持續輸入成績直到輸入 -1（-1 不納入），最後輸出平均與筆數。",
            "tests": [["10", "20", "-1"]],
            "check": "nums>=2",  # avg + count
            "keywords": ["平均", "成績", "分數", "mean", "average"],
        },
        {
            "name": "sum_prices",
            "desc": "購物累計：重複輸入價格直到輸入 0 結束，最後輸出總金額與筆數。",
            "tests": [["30", "70", "0"]],
            "check": "nums>=2",
            "keywords": ["金額", "價格", "總和", "sum", "total"],
        },
        {
            "name": "menu_loop",
            "desc": "選單迴圈：反覆輸入 1/2/0，1=新增一筆、2=顯示筆數、0=離開，最後輸出總筆數。",
            "tests": [["1", "1", "2", "0"]],
            "check": "nums>=1",
            "keywords": ["選單", "功能", "menu", "選項"],
        },
        {
            "name": "validate_range",
            "desc": "資料驗證：反覆輸入數值直到介於指定範圍內才結束，最後輸出有效數值。",
            "tests": [["-5", "200", "18"]],
            "check": "nums>=1",
            "keywords": ["驗證", "範圍", "合法", "valid"],
        },
        {
            "name": "sentinel_ok",
            "desc": "資料輸入：反覆輸入文字直到輸入 'ok' 結束，最後輸出輸入筆數。",
            "tests": [["a", "b", "ok"]],
            "check": "nums>=1",
            "keywords": ["直到", "結束", "停止", "ok"],
        },
        {
            "name": "guess_game",
            "desc": "猜數字：反覆輸入猜測直到猜中答案（可在程式中寫死答案），輸出猜測次數。",
            "tests": [["3", "7"]],
            "check": "nums>=1",
            "keywords": ["猜", "猜數字", "guess"],
        },
    ]

    # keyword pick
    picked = None
    for sc in scenarios:
        if _kw_hit(*sc.get("keywords", [])):
            picked = sc
            break

    if not picked:
        seed_src = (unit or "") + "|" + (video_title or "") + "|" + ((cleaned or "")[:1500])
        seed_int = int(hashlib.md5(seed_src.encode("utf-8")).hexdigest(), 16)
        rnd = random.Random(seed_int ^ random.randint(0, 10**9))  # regenerate 可變化
        picked = rnd.choice(scenarios)
    else:
        # 若字幕命中，仍讓 regenerate 有變化：用影片資訊做微隨機
        seed_src = (unit or "") + "|" + (video_title or "") + "|" + picked["name"] + "|" + ((cleaned or "")[:800])
        seed_int = int(hashlib.md5(seed_src.encode("utf-8")).hexdigest(), 16)
        rnd = random.Random(seed_int ^ random.randint(0, 10**9))
        # 同一情境下也可能變體（例如換 sentinel 值/輸出格式），交給 AI
        # 這裡不更換情境，僅保留 rnd 供後續使用

    scenario_desc = picked["desc"]
    scenario_tests = picked["tests"]
    scenario_check = picked["check"]

    # ===============================
    # [新增] 本地 0 token：安全驗收（閉環）
    # ===============================
    import re as _re

    _cjk_re = _re.compile(r"[\u4e00-\u9fff]")
    _python_token_re = _re.compile(r"\b(while|for|if|elif|else|break|continue|input|print|int|float|str|len|range)\b|[=:+\-*/()<>]")

    def _looks_like_code(line: str) -> bool:
        s = (line or "").rstrip("\n")
        if not s.strip():
            return False
        if _cjk_re.search(s):
            return False
        return bool(_python_token_re.search(s))

    def _compile_ok(lines: list) -> bool:
        try:
            compile("\n".join(lines), "<parsons_ai>", "exec")
            return True
        except Exception:
            return False

    def _has_loop(lines: list) -> bool:
        joined = "\n".join(lines).lower()
        return ("while " in joined) or ("for " in joined)

    def _loop_has_body_indent(lines: list) -> bool:
        for i, ln in enumerate(lines):
            if ln.strip().startswith("while ") or ln.strip().startswith("for "):
                if i + 1 < len(lines):
                    nxt = lines[i + 1]
                    if nxt.startswith("    "):  # 你要的縮排檢查
                        return True
        return False

    def _has_print(lines: list) -> bool:
        return "print(" in ("\n".join(lines).lower())

    def _has_input(lines: list) -> bool:
        return "input(" in ("\n".join(lines).lower())

    def _has_end_control(lines: list) -> bool:
        joined = "\n".join(lines).lower()
        return ("break" in joined) or any(ln.strip().startswith("while ") and ("true" not in ln.lower()) for ln in lines)

    # AST sandbox
    ALLOWED_NODES = (
        ast.Module, ast.Assign, ast.AugAssign, ast.Expr, ast.Call,
        ast.Name, ast.Load, ast.Store, ast.Constant,
        ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
        ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
        ast.If, ast.While, ast.For, ast.Break, ast.Continue, ast.Pass,
        ast.List, ast.Tuple,
    )
    ALLOWED_FUNCS = {"input", "print", "int", "float", "str", "len", "range"}

    def _ast_safe(code: str) -> bool:
        try:
            tree = ast.parse(code, mode="exec")
        except Exception:
            return False
        for node in ast.walk(tree):
            if not isinstance(node, ALLOWED_NODES):
                return False
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.Attribute, ast.Subscript, ast.Lambda, ast.With, ast.Try, ast.FunctionDef, ast.ClassDef)):
                return False
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id not in ALLOWED_FUNCS:
                        return False
                else:
                    return False
        return True

    def _run_with_inputs(code: str, inputs: list) -> str:
        it = iter(inputs)

        def _fake_input(prompt=""):
            try:
                return next(it)
            except StopIteration:
                return ""

        out = io.StringIO()
        safe_globals = {"__builtins__": {}}
        safe_locals = {
            "input": _fake_input,
            "print": lambda *args, **kwargs: print(*args, file=out, **{k: v for k, v in kwargs.items() if k in ("sep", "end")}),
            "int": int,
            "float": float,
            "str": str,
            "len": len,
            "range": range,
        }
        with contextlib.redirect_stdout(out):
            exec(code, safe_globals, safe_locals)
        return out.getvalue().strip()

    def _output_check(check: str, out: str) -> bool:
        if not out:
            return False
        nums = _re.findall(r"-?\d+\.?\d*", out)
        if check.startswith("nums>="):
            try:
                need = int(check.split(">=")[1])
            except Exception:
                need = 1
            return len(nums) >= need
        return True

    def _auto_grade(solution_lines: list) -> str:
        if not _compile_ok(solution_lines):
            return "compile failed"
        if any(not _looks_like_code(x) for x in solution_lines):
            return "solution_lines contains Chinese or non-code"
        if any(_re.search(r"^\s*def\s+", x) for x in solution_lines):
            return "contains def"
        if any(_re.search(r"^\s*class\s+", x) for x in solution_lines):
            return "contains class"
        if not _has_loop(solution_lines):
            return "missing loop"
        if not _loop_has_body_indent(solution_lines):
            return "loop body indentation missing"
        if not _has_input(solution_lines):
            return "missing input"
        if not _has_print(solution_lines):
            return "missing print output"
        if not _has_end_control(solution_lines):
            return "missing end condition/break"

        code = "\n".join(solution_lines)
        if not _ast_safe(code):
            return "AST rejected"

        # [新增] 低 token：只跑 1 組測資
        test_inputs = (scenario_tests[:1] or [[]])
        for inputs in test_inputs:
            try:
                out = _run_with_inputs(code, inputs)
            except Exception as e:
                return f"exec failed: {e}"
            if not _output_check(scenario_check, out):
                return "output check failed"
        return ""

    # ===============================
    # [新增] AI 生成（最小輸出）+ 失敗只修一次（省 token）
    # ===============================
    system_msg = "你是Python程式設計助教。請嚴格只輸出『合法 JSON』，不要輸出 Markdown 或多餘文字。"

    banned_patterns = """
請避免以下過度常見模板與禁止項：
- 直到輸入 'ok' 為止（除非情境就是 ok sentinel）
- def / class
- 中文註解或中文程式碼
"""

    base_prompt = f"""
你要產生一題 Parsons 程式重組題（不是選擇題）。
題型固定：迴圈（while 或 for），不得使用 def/class。

【情境】
{scenario_desc}

【硬性規則（必須遵守）】
- 只輸出合法 JSON（不要 Markdown、不要多餘文字）
- 只輸出以下欄位：question_text、solution_lines
- question_text：繁體中文，描述要做什麼（不要直接透露程式碼）
- solution_lines：英文 Python code 4~10 行，且必須包含：
  1) 迴圈
  2) 迴圈內至少一行縮排（4 spaces）
  3) 結束條件（可用 break）
  4) 最後要輸出結果（print）
- solution_lines 禁止中文與註解
{banned_patterns}

輸出 JSON 格式：
{{
  "question_text": "題目敘述（繁中）",
  "solution_lines": ["python code line 1", "..."]
}}

字幕（含時間戳）（格式：[start-end] text）：
{segs_compact}
""".strip()

    MAX_RETRY = 2
    data = None
    last_error = ""
    last_candidate = None

    for attempt in range(MAX_RETRY):
        if attempt == 0:
            user_msg = base_prompt
        else:
            prev = last_candidate or {}
            prev_q = (prev.get("question_text") or "").strip()
            prev_sol = prev.get("solution_lines") or []
            user_msg = f"""
你上一版未通過自動驗收：{last_error}

【只修正 solution_lines】（保持同一情境方向）
- 修正縮排/缺少結束條件/缺少輸出/缺少更新等問題
- 仍需 while/for、仍需迴圈內縮排、仍需 print、仍需可執行
- solution_lines 禁止中文與註解
- 不得 def/class
- 只輸出合法 JSON（欄位仍是 question_text + solution_lines）

上一版 question_text（參考）：{prev_q}
上一版 solution_lines：{prev_sol}
""".strip()

        text = (parsons_ai.call_openai_output_text(system=system_msg, user=user_msg, model=model, max_output_tokens=900) or "").strip()
        cand = safe_json_loads(text)
        if not cand:
            last_error = "non-JSON"
            last_candidate = None
            continue

        q = (cand.get("question_text") or "").strip()
        sol = cand.get("solution_lines") or []
        last_candidate = cand

        if not q:
            last_error = "missing question_text"
            continue
        if not isinstance(sol, list) or len(sol) < 4 or len(sol) > 10:
            last_error = "solution_lines length not 4~10"
            continue

        sol = [str(x) for x in sol]
        cand["solution_lines"] = sol

        grade_err = _auto_grade(sol)
        if grade_err:
            last_error = grade_err
            continue

        data = cand
        break

    if not data:
        raise RuntimeError(f"AI generation failed: {last_error}")

    question_text = (data.get("question_text") or "").strip()
    solution_lines = data["solution_lines"]

    # ===============================
    # [新增] B：segment_map + slot_hints（含 evidence）— 低 token 對齊任務
    # ===============================
    seg_map = {}
    slot_hints = {}

    if ai_enabled() and segs_compact:
        try:
            align_system = (
                "你是Python教學助教。你要把『每一行程式（slot）』對齊到字幕時間戳。\n"
                "請嚴格只輸出合法 JSON，不要輸出 Markdown 或多餘文字。\n"
                "必須包含：segment_map、slot_hints。\n"
                "segment_map 每格都要有，並含 evidence（引用字幕關鍵句，可短）。"
            )
            align_user = f"""
請根據題目與字幕，為每一格（slot_index）提供最相關的回看片段時間（start/end，秒），並給一句短提示（hint）。

輸出 JSON 格式：
{{
  "segment_map": [
    {{"slot_index": 0, "start": 12.3, "end": 24.8, "evidence": "字幕關鍵句（繁中，可短）"}},
    ...
  ],
  "slot_hints": [
    {{"slot_index": 0, "hint": "如果第1格錯，提醒學生什麼（繁中，1句）"}},
    ...
  ]
}}

限制：
- slot_index 必須從 0 開始，數量要等於 solution_lines 行數
- start/end 必須是字幕時間戳合理區間（秒數）
- evidence 必須是字幕中的關鍵句（可短，不要超過 15 字）
- hint 每格 1 句，繁體中文，不要直接給出程式碼或變數名

題目：{question_text}
solution_lines：{solution_lines}

字幕（含時間戳）（格式：[start-end] text）：
{segs_compact}
""".strip()

            align = parsons_ai.call_openai_json(
                system=align_system,
                user=align_user,
                model=model,
                temperature=0.2,
                max_output_tokens=900,
            ) or {}

            seg_map_in = align.get("segment_map") or []
            hint_in = align.get("slot_hints") or []

            for it in seg_map_in:
                try:
                    si = int(it.get("slot_index"))
                    s = float(it.get("start"))
                    e = float(it.get("end"))
                    if si < 0 or si >= len(solution_lines) or e <= s:
                        continue
                    seg_map[str(si)] = {
                        "start": s,
                        "end": e,
                        "evidence": (it.get("evidence") or "").strip(),
                    }
                except Exception:
                    continue

            for it in hint_in:
                try:
                    si = int(it.get("slot_index"))
                    if si < 0 or si >= len(solution_lines):
                        continue
                    slot_hints[str(si)] = (it.get("hint") or "").strip()
                except Exception:
                    continue
        except Exception:
            seg_map = {}
            slot_hints = {}

    # [新增] 若 AI 對齊失敗：用字幕片段平均分配 fallback（0 token）
    if not seg_map:
        if segs:
            n = len(solution_lines)
            picked_segs = segs[: max(n, 1)]
            for i in range(n):
                s = picked_segs[min(i, len(picked_segs)-1)]
                seg_map[str(i)] = {
                    "start": float(s.get("start", 0.0)),
                    "end": float(s.get("end", float(s.get("start", 0.0)) + 5.0)),
                    "evidence": (s.get("text", "") or "").strip()[:15],
                }
        else:
            for i in range(len(solution_lines)):
                seg_map[str(i)] = {"start": 0.0, "end": 5.0, "evidence": ""}

    if not slot_hints:
        for i in range(len(solution_lines)):
            slot_hints[str(i)] = "請確認此步驟是否在正確的流程位置與縮排層級。"

    # ===============================
    # [新增] 干擾題 mutation（0 token，含縮排錯）
    # ===============================
    def _normalize_line(s: str) -> str:
        return _re.sub(r"\s+", "", (s or "").strip()).lower()

    def _tokenize(s: str):
        return _re.findall(r"[A-Za-z_]+|\d+|==|!=|<=|>=|\+=|-=|\*=|/=|[=:+\-*/()<>]", (s or ""))

    def _diff_score(a: str, b: str) -> int:
        sa, sb = set(_tokenize(a)), set(_tokenize(b))
        return len(sa.symmetric_difference(sb))

    def _mutate_line(line: str) -> list:
        variants = []
        s = line
        if "+=" in s:
            variants.append(s.replace("+=", "="))
        if "==" in s:
            variants.append(s.replace("==", "!="))
        if "!=" in s:
            variants.append(s.replace("!=", "=="))
        if "<=" in s:
            variants.append(s.replace("<=", ">="))
        if ">=" in s:
            variants.append(s.replace(">=", "<="))
        if s.strip() == "break":
            variants.append("continue")
        if "int(input(" in s:
            variants.append(s.replace("int(input(", "input("))
        if "float(input(" in s:
            variants.append(s.replace("float(input(", "input("))
        if _re.search(r"\bcount\s*\+=\s*1\b", s):
            variants.append(_re.sub(r"\bcount\s*\+=\s*1\b", "count += 0", s))
        return variants

    def _make_indent_wrong(line: str) -> str:
        if line.startswith("    "):
            return line[4:]
        return "    " + line

    def _distractor_semantic_zh(line: str) -> str:
        s = (line or "").strip().lower()
        if not (line or "").startswith("    ") and ("+=" in s or "=" in s):
            return "此行可能不在正確縮排層級，導致流程不符合預期"
        if "==" in s or "!=" in s or "<" in s or ">" in s:
            return "條件判斷可能寫反，導致提早結束或無法結束"
        if "break" in s or "continue" in s:
            return "迴圈控制語句可能使用不當，造成流程錯誤"
        if "+=" in s or "=" in s:
            return "統計更新方式可能錯誤，導致結果不正確"
        if "input" in s:
            return "資料處理方式可能不一致，導致判斷或計算出錯"
        return "此行邏輯可能與題目需求不一致"

    distractor_lines = []
    sol_norm = {_normalize_line(x) for x in solution_lines}

    candidates = []
    for ln in solution_lines:
        for v in _mutate_line(ln):
            if v and _looks_like_code(v):
                candidates.append(v)

    # 強制加入至少一個縮排錯版本
    indented = [x for x in solution_lines if x.startswith("    ")]
    if indented:
        candidates.append(_make_indent_wrong(indented[0]))
    else:
        candidates.append(_make_indent_wrong(solution_lines[-1]))

    uniq = []
    seen = set()
    for c in candidates:
        cn = _normalize_line(c)
        if cn in sol_norm:
            continue
        if cn in seen:
            continue
        seen.add(cn)
        uniq.append(c)

    def _best_diff_to_solution(c: str) -> int:
        return min((_diff_score(c, s) for s in solution_lines), default=0)

    uniq.sort(key=_best_diff_to_solution, reverse=True)
    for c in uniq:
        if len(distractor_lines) >= 3:
            break
        distractor_lines.append(c)

    if len(distractor_lines) < 2:
        fallback = ["count = 1", "total = score", "if score != -1: break", "continue"]
        for f in fallback:
            fn = _normalize_line(f)
            if fn in sol_norm or fn in {_normalize_line(x) for x in distractor_lines}:
                continue
            distractor_lines.append(f)
            if len(distractor_lines) >= 3:
                break

    # ===============================
    # [新增] 中文語意 template_slots（0 token，不洩漏答案/不出現 input）
    # ===============================
    def _label_for_solution_line(line: str) -> str:
        raw = (line or "").strip()
        low = raw.lower()
        if _re.search(r"=\s*0\b", low):
            return "設定統計所需的起始狀態"
        if raw.endswith(":") and (low.startswith("while ") or low.startswith("for ")):
            return "建立重複處理的流程架構"
        if "input" in low:
            return "取得新資料並準備後續處理"
        if low.startswith("if ") and "break" in low:
            return "判斷是否達到結束條件並結束流程"
        if low.startswith("if "):
            return "根據條件決定下一步處理"
        if "+=" in low:
            return "更新統計或累積的結果"
        if "print" in low:
            return "輸出整理後的結果"
        if "=" in low:
            return "更新流程中需要記錄的狀態"
        if low.strip() == "break":
            return "在適當時機結束重複流程"
        return "完成此步驟所需的處理"

    labels = [_label_for_solution_line(x) for x in solution_lines]
    template_slots = [{"label": labels[i], "slot": str(i)} for i in range(len(solution_lines))]

    solution_blocks = [{"id": f"b{i+1}", "text": line, "type": "core"} for i, line in enumerate(solution_lines)]
    distractor_blocks = [{"id": f"d{i+1}", "text": line, "type": "distractor"} for i, line in enumerate(distractor_lines)]

    for i in range(len(distractor_blocks)):
        try:
            distractor_blocks[i]["semantic_zh"] = _distractor_semantic_zh(distractor_blocks[i]["text"])
        except Exception:
            pass

    pool = distractor_blocks + solution_blocks

    ai_feedback = {
        "general": "請注意結束條件、統計更新與縮排層級是否正確。",
        "common_mistakes": [
            "結束條件寫反導致無法停止或提早停止",
            "忘記更新統計值（總和/次數）",
            "縮排錯誤導致程式流程不在迴圈內",
        ],
        "hints": [
            "確認結束條件是在迴圈內判斷",
            "確認統計更新行也在迴圈內",
            "最後要輸出題目要求的結果",
        ],
    }

    return {
        "question_text": question_text,
        "solution_blocks": solution_blocks,
        "distractor_blocks": distractor_blocks,
        "pool": pool,
        "template_slots": template_slots,
        "ai_feedback": ai_feedback,
        "ai_segment_map": seg_map,
        "ai_slot_hints": slot_hints,
        "ai_segments_compact": segs_compact,
    }


# =========================
# Create task
# =========================
def create_task_for_video(video_doc: dict, video_id_str: str, level: str, force_fallback: bool = False) -> Tuple[dict, str, Optional[str], dict]:
    unit = video_doc.get("unit", "") or ""
    video_title = video_doc.get("title", "") or ""

    subtitle_path = pick_latest_subtitle_path(video_doc, video_id_str)
    sub_text = read_subtitle_text(subtitle_path)

    gen_source = None
    gen_error = None
    env = env_snapshot()

    vid_oid = video_doc.get("_id") or maybe_oid(video_id_str)
    video_id_match = {"$or": [{"video_id": video_id_str}] + ([{"video_id": vid_oid}] if vid_oid else [])}

    db.parsons_tasks.update_many({**video_id_match, "level": level, "active": True}, {"$set": {"active": False}})

    try:
        if force_fallback:
            raise RuntimeError("force_fallback")
        ai = ai_generate_parsons_from_subtitle(sub_text, unit, video_title, level=level)
        gen_source = "openai"
    except Exception as e:
        ai = simple_fallback_generate(sub_text, unit, video_title, level=level)
        gen_source = "fallback"
        gen_error = str(e)

    doc = {
        "video_id": vid_oid if vid_oid else video_id_str,
        "video_id_str": video_id_str,

        "unit": unit,
        "video_title": video_title,
        "level": level,

        "enabled": False,
        "review_status": "draft",

        "prompt_source": {"subtitle_path": subtitle_path},
        "question_text": ai.get("question_text"),
        "solution_blocks": ai.get("solution_blocks", []),
        "distractor_blocks": ai.get("distractor_blocks", []),
        "pool": ai.get("pool", []),
        "template_slots": ai.get("template_slots", []),
        "ai_feedback": ai.get("ai_feedback", {}),

        "ai_generated": True if gen_source == "openai" else False,
        "ai_segment_map": ai.get("ai_segment_map", {}) or {},
        "ai_slot_hints": ai.get("ai_slot_hints", {}) or {},
        "ai_segments_compact": ai.get("ai_segments_compact", "") or "",

        "created_at": now_utc(),
        "active": True,

        "gen_source": gen_source,
        "gen_error": gen_error,
        "env": env,
    }

    inserted = db.parsons_tasks.insert_one(doc)
    doc["_id"] = inserted.inserted_id

    log_event(
        "parsons_task_generated",
        video_id=video_id_str,
        task_id=str(inserted.inserted_id),
        gen_source=gen_source,
        gen_error=gen_error,
        unit=unit,
        title=video_title,
        level=level,
    )

    return doc, gen_source, gen_error, env


# =========================
# (A) GET /task  取得學生端題目（只取 enabled=True 最新）
# =========================
@parsons_bp.get("/task")
def get_task():
    video_id = request.args.get("video_id", "").strip()
    level = request.args.get("level", "L2").strip()

    if not video_id:
        return jsonify({"ok": False, "message": "missing video_id"}), 400

    try:
        vid_oid = ObjectId(video_id)
    except Exception:
        vid_oid = None

    q = {"level": level, "enabled": True}
    if vid_oid:
        q["video_id"] = {"$in": [video_id, vid_oid]}
    else:
        q["video_id"] = video_id

    task = db.parsons_tasks.find_one(q, sort=[("created_at", -1)])
    if not task:
        return jsonify({"ok": True, "noTask": True, "message": "此影片尚未發布題目"})

    parsed = t5doc_to_parsons_task(task)

    return jsonify({
        "ok": True,
        "noTask": False,
        "task_id": str(task.get("_id")),
        "video_id": normalize_video_id(task.get("video_id")),
        "level": task.get("level"),
        "question_text": parsed.get("question_text", ""),
        "pool": parsed.get("pool", []),
        "template_slots": parsed.get("template_slots", []),
        "solution_blocks": parsed.get("solution_blocks", []),
        "distractor_blocks": parsed.get("distractor_blocks", []),
        "ai_feedback": parsed.get("ai_feedback", {}),
        "version": task.get("version", "v1.AI"),
    })


# =========================
# (A) POST /publish  老師端：發布題目（同影片同 level 只允許一題 enabled）
# =========================
@parsons_bp.post("/publish")
def publish_task():
    data = request.get_json(silent=True) or {}
    task_id = (data.get("task_id") or "").strip()

    if not task_id:
        return jsonify({"ok": False, "message": "missing task_id"}), 400

    try:
        oid = ObjectId(task_id)
    except Exception:
        return jsonify({"ok": False, "message": "invalid task_id"}), 400

    task = db.parsons_tasks.find_one({"_id": oid})
    if not task:
        return jsonify({"ok": False, "message": "task not found"}), 404

    task_video_id = task.get("video_id")
    task_level = task.get("level")

    db.parsons_tasks.update_many(
        {"video_id": task_video_id, "level": task_level, "_id": {"$ne": oid}},
        {"$set": {"enabled": False, "review_status": "draft"}}
    )
    if isinstance(task_video_id, str):
        v_oid = maybe_oid(task_video_id)
        if v_oid:
            db.parsons_tasks.update_many(
                {"video_id": v_oid, "level": task_level, "_id": {"$ne": oid}},
                {"$set": {"enabled": False, "review_status": "draft"}}
            )

    r = db.parsons_tasks.update_one(
        {"_id": oid},
        {"$set": {
            "enabled": True,
            "review_status": "published",
            "published_at": now_utc()
        }}
    )

    return jsonify({"ok": True, "matched": r.matched_count, "modified": r.modified_count}), 200


# =========================
# AI: build hint + jump segment for wrong slot
# =========================
def ai_hint_and_segment_for_wrong(task: dict, slot_key: str, expected_text: str, actual_text: str, level: str, slot_label: str) -> Tuple[str, Optional[float], Optional[float], str]:
    """
    [新增] V1.5：錯誤時的 AI 回饋與回看時間軸
    回傳 (hint, start, end, subtitle_context)
    - 優先使用 task.ai_segment_map / task.ai_slot_hints（生成時已產出，穩定、可重現）
    - 若缺少，才用 OpenAI 依字幕時間戳即時推估，並回寫到 task（不改 schema，只新增欄位內容）
    """
    hint = ""
    start = None
    end = None
    subtitle_context = ""

    seg_map = (task.get("ai_segment_map") or {}) if isinstance(task.get("ai_segment_map"), dict) else {}
    slot_hints = (task.get("ai_slot_hints") or {}) if isinstance(task.get("ai_slot_hints"), dict) else {}

    seg = seg_map.get(str(slot_key)) or None
    if isinstance(seg, dict):
        try:
            s = float(seg.get("start"))
            e = float(seg.get("end"))
            if e > s:
                start, end = s, e
        except Exception:
            start, end = None, None

    hint = (slot_hints.get(str(slot_key)) or "").strip()

    try:
        sub_path = ((task.get("prompt_source") or {}).get("subtitle_path") or "").strip()
        if sub_path:
            sub_text = read_subtitle_text(sub_path)
            segs = parse_srt_segments(sub_text)
            if start is not None and end is not None:
                subtitle_context = extract_context_around(segs, start, end, window=5)
            else:
                subtitle_context = compact_segments_for_prompt(segs[:18], max_chars=3000)
    except Exception:
        subtitle_context = ""

    if ai_enabled() and (not hint or start is None or end is None):
        try:
            model = (os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()
            segs_compact = (task.get("ai_segments_compact") or "").strip()
            if not segs_compact:
                sub_path = ((task.get("prompt_source") or {}).get("subtitle_path") or "").strip()
                segs_compact = compact_segments_for_prompt(parse_srt_segments(read_subtitle_text(sub_path)), max_chars=12000)

            prompt = f"""
你是一位 Python 程式設計助教。學生在 Parsons 題目中把某一格放錯了。
請你做兩件事：
1) 給「繁體中文」提示（1~2句，針對該格錯誤）
2) 從字幕時間戳中選出最適合回看的片段 start/end（秒數，必須是字幕裡存在的合理範圍）

請輸出「純 JSON」，不要多餘文字：
{{
  "hint": "繁體中文提示",
  "start": 120.0,
  "end": 150.0,
  "evidence": "引用字幕關鍵句（可短）"
}}

資訊：
- 難度 level: {level}
- 錯誤格：{slot_label}
- 正確應該是（expected）：{expected_text}
- 學生放的是（actual）：{actual_text if actual_text else "（空白）"}

字幕（含時間戳）如下（格式：[start-end] text）：
{segs_compact}
""".strip()

            # [新增] OpenAI 呼叫改由 parsons_ai 統一管理（不改既有 prompt/解析）
            data = parsons_ai.call_openai_json(model=model, prompt=prompt) or {}

            ai_hint = (data.get("hint") or "").strip()
            ai_s = data.get("start", None)
            ai_e = data.get("end", None)

            ai_start = float(ai_s) if ai_s is not None else None
            ai_end = float(ai_e) if ai_e is not None else None

            if ai_hint:
                hint = ai_hint
            if ai_start is not None and ai_end is not None and ai_end > ai_start:
                start, end = ai_start, ai_end

            try:
                update = {}
                if hint:
                    update[f"ai_slot_hints.{str(slot_key)}"] = hint
                if start is not None and end is not None:
                    update[f"ai_segment_map.{str(slot_key)}"] = {
                        "start": float(start),
                        "end": float(end),
                        "evidence": (data.get("evidence") or "").strip(),
                    }
                if update:
                    db.parsons_tasks.update_one({"_id": task.get("_id")}, {"$set": update})
            except Exception:
                pass

            try:
                sub_path = ((task.get("prompt_source") or {}).get("subtitle_path") or "").strip()
                if sub_path:
                    sub_text = read_subtitle_text(sub_path)
                    segs = parse_srt_segments(sub_text)
                    if start is not None and end is not None:
                        subtitle_context = extract_context_around(segs, start, end, window=5)
            except Exception:
                pass

        except Exception:
            pass

    return hint, start, end, subtitle_context


# =========================
# (B) POST /submit 送出作答
# =========================

# =========================
# ✅ V1.8 Test (Pre/Post) APIs
#  - collections:
#    - parsons_test_tasks: {test_cycle_id, test_role, test_task_id, source_task_id}
#    - parsons_test_cycles: {test_cycle_id, post_open, open_at, close_at}
#    - parsons_test_attempts: {student_id, test_cycle_id, test_role, test_task_id, is_correct, score, duration_sec, wrong_indices, submitted_at}
# =========================

@parsons_bp.get("/test/status")
def test_status():
    ensure_test_indexes()
    student_id = (request.args.get("student_id") or "").strip()
    test_cycle_id = (request.args.get("test_cycle_id") or get_default_test_cycle_id()).strip() or get_default_test_cycle_id()

    if not student_id:
        return jsonify({"ok": False, "message": "missing student_id"}), 400

    pre_done = bool(db.parsons_test_attempts.find_one({"student_id": student_id, "test_cycle_id": test_cycle_id, "test_role": "pre"}))
    post_done = bool(db.parsons_test_attempts.find_one({"student_id": student_id, "test_cycle_id": test_cycle_id, "test_role": "post"}))

    return jsonify({
        "ok": True,
        "student_id": student_id,
        "test_cycle_id": test_cycle_id,
        "pre_done": pre_done,
        "post_done": post_done,
        "post_open": is_posttest_open(test_cycle_id),
    })


@parsons_bp.get("/test/task")
def get_test_task():
    '''
    回傳一題 Parsons 測驗題（前測/後測各一題，先跑通流程）
    query:
      - student_id
      - test_role: pre/post
      - test_cycle_id
    '''
    # 從 query string 讀參數，避免 NameError
    test_role = (request.args.get("test_role") or "").strip().lower()
    test_cycle_id = (request.args.get("test_cycle_id") or "").strip()

    if test_role not in ("pre", "post"):
        return jsonify({"ok": False, "message": "invalid test_role"}), 400
    if not test_cycle_id:
        return jsonify({"ok": False, "message": "missing test_cycle_id"}), 400

    tt = db.parsons_test_tasks.find_one({"test_cycle_id": test_cycle_id, "test_role": test_role})
    if not tt:
        return jsonify({"ok": False, "message": "test task not configured"}), 404

    # 取得原始題目
    source_task_id = (tt.get("source_task_id") or "").strip()
    try:
        task_doc = db.parsons_tasks.find_one({"_id": ObjectId(source_task_id)})
    except:
        task_doc = None

    if not task_doc:
        return jsonify({"ok": False, "message": "source task not found"}), 404

    # 使用現有的 normalize 函式
    parsed = t5doc_to_parsons_task(task_doc)

    return jsonify({
        "ok": True,
        "test_task_id": str(tt.get("_id")),
         # ✅【新增】一定要回傳 parsons_tasks 的 _id，給前端 submit 用
        "source_task_id": str(task_doc.get("_id")),  # <= 最重要
        "task_id": str(task_doc.get("_id")),         # <= 可選：給前端相容用（你後端 submit 也吃 task_id）
        "question_text": task_doc.get("question_text") or "",
        "template_slots": parsed.get("template_slots") or [],
        "pool": parsed.get("pool") or [],
        "total": 1, # 目前設計為一人一題
        "current_index": 1
    })


@parsons_bp.post("/test/submit")
def submit_test_answer():
    '''
    payload:
      - student_id
      - test_cycle_id
      - test_role: pre/post
      - test_task_id
      - source_task_id / task_id
      - answer_ids: [block_id,...]
      - duration_sec
    '''
    ensure_test_indexes()
    data = request.get_json(silent=True) or {}

    student_id = (data.get("student_id") or "").strip()
    test_cycle_id = (data.get("test_cycle_id") or get_default_test_cycle_id()).strip() or get_default_test_cycle_id()
    test_role = (data.get("test_role") or "").strip().lower()
    test_task_id = (data.get("test_task_id") or "").strip()
    source_task_id = (data.get("source_task_id") or data.get("task_id") or "").strip()
    answer_ids = data.get("answer_ids") or []
    duration_sec = int(data.get("duration_sec") or 0)

    if not student_id:
        return jsonify({"ok": False, "message": "missing student_id"}), 400
    if test_role not in ("pre", "post"):
        return jsonify({"ok": False, "message": "invalid test_role"}), 400

    if test_role == "post" and not is_posttest_open(test_cycle_id):
        return jsonify({"ok": False, "message": "posttest not open"}), 403

    # 取得題目
    try:
        task = db.parsons_tasks.find_one({"_id": ObjectId(source_task_id)})
    except Exception:
        task = None
    if not task:
        return jsonify({"ok": False, "message": "task not found"}), 404

    parsed = t5doc_to_parsons_task(task)
    expected_ids = [str(s.get("expected_id")) for s in (parsed.get("template_slots") or [])]

    aligned = list(answer_ids)
    if len(aligned) < len(expected_ids):
        aligned = aligned + [None] * (len(expected_ids) - len(aligned))

    wrong_indices = []
    for i in range(len(expected_ids)):
        if str(aligned[i]) != str(expected_ids[i]):
            wrong_indices.append(i)

    extra_wrong = max(0, len(answer_ids) - len(expected_ids))
    is_correct = (len(wrong_indices) == 0 and extra_wrong == 0)

    total_slots = max(1, len(expected_ids))
    score = (total_slots - len(wrong_indices)) / total_slots

    attempt_doc = {
        "student_id": student_id,
        "test_cycle_id": test_cycle_id,
        "test_role": test_role,
        "test_task_id": test_task_id or str(task.get("_id")),
        "source_task_id": str(task.get("_id")),
        "answer_ids": answer_ids,
        "is_correct": is_correct,
        "score": score,
        "duration_sec": duration_sec,
        "wrong_indices": wrong_indices,
        "submitted_at": now_utc(),
    }

    try:
        ins = db.parsons_test_attempts.insert_one(attempt_doc)
        attempt_id = str(ins.inserted_id)
        return jsonify({
            "ok": True,
            "already_submitted": False,
            "attempt_id": attempt_id,
            "is_correct": is_correct,
            "score": score,
            "wrong_indices": wrong_indices,
        })
    except Exception:
        # duplicate key => already submitted
        return jsonify({
            "ok": True,
            "already_submitted": True,
            "is_correct": is_correct,
            "score": score,
            "wrong_indices": wrong_indices,
        })


@parsons_bp.post("/test/cycle/toggle")
def toggle_test_cycle():
    """
    老師端控制後測開放/關閉（v1.8 統一使用 test_control）
    支援：
      1) JSON body: { test_cycle_id, post_open } 或 { test_cycle_id, open }
      2) Query string: ?test_cycle_id=default&post_open=true
      3) 若未提供 post_open/open，則直接「反轉」目前狀態
    """
    try:
        data = request.get_json(silent=True) or {}

        # test_cycle_id：body 優先，其次 query，最後 default
        test_cycle_id = (
            data.get("test_cycle_id")
            or request.args.get("test_cycle_id")
            or get_default_test_cycle_id()
            or "default"
        )
        test_cycle_id = str(test_cycle_id).strip() or "default"

        # 允許 post_open / open 兩種欄位
        raw_open = data.get("post_open", None)
        if raw_open is None:
            raw_open = data.get("open", None)
        if raw_open is None:
            raw_open = request.args.get("post_open", None)
        if raw_open is None:
            raw_open = request.args.get("open", None)

        # 目前狀態
        doc = db.test_control.find_one({"_id": f"post_open:{test_cycle_id}"}) or {}
        cur_open = bool(doc.get("post_open", False))

        # 若沒提供 open 值 → toggle
        if raw_open is None:
            post_open = (not cur_open)
        else:
            # 將字串/布林都轉成 bool
            if isinstance(raw_open, bool):
                post_open = raw_open
            else:
                s = str(raw_open).strip().lower()
                post_open = s in ("1", "true", "t", "yes", "y", "open", "on")

        now = now_utc()

        update = {
            "test_cycle_id": test_cycle_id,
            "post_open": bool(post_open),
            "updated_at": now,
        }

        # 開啟：補 open_at（僅當目前沒有 open_at 時）
        if post_open:
            if not doc.get("open_at"):
                update["open_at"] = now
            update["close_at"] = None
        else:
            update["close_at"] = now

        db.test_control.update_one(
            {"_id": f"post_open:{test_cycle_id}"},
            {"$set": update},
            upsert=True
        )

        # 回傳最新狀態
        new_doc = db.test_control.find_one({"_id": f"post_open:{test_cycle_id}"}) or {}
        return jsonify({
            "ok": True,
            "test_cycle_id": test_cycle_id,
            "post_open": bool(new_doc.get("post_open", False)),
            "open_at": new_doc.get("open_at"),
            "close_at": new_doc.get("close_at"),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# =========================
# v1.8 後測開關（統一只用 test_control）
# 新增：GET /test/cycle/get
# =========================
@parsons_bp.get("/test/cycle/get")
def get_test_cycle_control():
    test_cycle_id = (request.args.get("test_cycle_id") or "default").strip()

    doc_id = f"post_open:{test_cycle_id}"
    doc = db.test_control.find_one({"_id": doc_id}) or {}

    # 統一輸出給前端判斷顯示/隱藏「後測區塊」
    return jsonify({
        "ok": True,
        "test_cycle_id": test_cycle_id,
        "post_open": bool(doc.get("post_open", False)),
        "open_at": doc.get("open_at"),
        "close_at": doc.get("close_at"),
        "updated_at": doc.get("updated_at"),
        "_id": doc_id,
    })    

@parsons_bp.get("/test/export_csv")
def export_test_csv():
    ensure_test_indexes()
    test_cycle_id = (request.args.get("test_cycle_id") or get_default_test_cycle_id()).strip() or get_default_test_cycle_id()

    cur = db.parsons_test_attempts.find({"test_cycle_id": test_cycle_id}).sort("submitted_at", 1)

    headers = [
        "student_id",
        "test_cycle_id",
        "test_role",
        "test_task_id",
        "is_correct",
        "score",
        "duration_sec",
        "wrong_indices",
        "submitted_at",
    ]

    import io, csv
    output = io.StringIO()
    w = csv.DictWriter(output, fieldnames=headers)
    w.writeheader()

    for d in cur:
        row = {
            "student_id": d.get("student_id", ""),
            "test_cycle_id": d.get("test_cycle_id", ""),
            "test_role": d.get("test_role", ""),
            "test_task_id": d.get("test_task_id", ""),
            "is_correct": d.get("is_correct", False),
            "score": d.get("score", ""),
            "duration_sec": d.get("duration_sec", ""),
            "wrong_indices": json.dumps(d.get("wrong_indices", []), ensure_ascii=False),
            "submitted_at": (d.get("submitted_at").isoformat() if isinstance(d.get("submitted_at"), datetime) else str(d.get("submitted_at") or "")),
        }
        w.writerow(row)

    csv_text = output.getvalue()
    output.close()

    from flask import Response
    filename = f"parsons_test_attempts_{test_cycle_id}.csv"
    return Response(
        csv_text,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@parsons_bp.post("/submit")
def submit_answer():
    data = request.get_json(silent=True) or {}

    task_id = (data.get("task_id") or "").strip()
    answer_ids = data.get("answer_ids") or []
    student_id = (data.get("student_id") or "").strip()
    level = (data.get("level") or "").strip()

    if not task_id:
        return jsonify({"ok": False, "message": "missing task_id"}), 400

    # 1️⃣ 取得題目
    try:
        task = db.parsons_tasks.find_one({"_id": ObjectId(task_id)})
    except Exception:
        return jsonify({"ok": False, "message": "invalid task_id"}), 400

    if not task:
        return jsonify({"ok": False, "message": "task not found"}), 404

    solution_ids = task.get("solution_order") or None
    if not solution_ids:
        solution_blocks = task.get("solution_blocks", []) or []
        solution_ids = [b.get("id") for b in solution_blocks if b.get("type") in ("core", "solution", None)]
        solution_ids = [sid for sid in solution_ids if sid]

    # [新增] V1.3：Submit 端 fallback 解析 SRT（把 [行-行] 轉成秒數）
    def _parse_srt_time_to_seconds(t: str) -> float:
        # 格式：HH:MM:SS,mmm
        try:
            t = (t or "").strip()
            hh, mm, rest = t.split(":")
            ss, ms = rest.split(",")
            return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0
        except Exception:
            return 0.0

    def _read_srt_segments(abs_or_rel_path: str):
        """
        回傳 list[dict]: [{start:float, end:float, text:str}, ...]
        若讀不到就回 []
        """
        try:
            import os
            import re

            p = (abs_or_rel_path or "").strip()
            if not p:
                return []

            # 允許 DB 存 uploads/... 的相對路徑
            if not os.path.isabs(p):
                # 專案根目錄下的 uploads
                # 你原本 DB 例子：uploads/subtitles/xxx.srt
                root = os.getcwd()
                p = os.path.join(root, p)

            if not os.path.exists(p):
                return []

            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read()

            # 以空行分段
            blocks = re.split(r"\n\s*\n", raw.strip(), flags=re.M)
            out = []
            for b in blocks:
                lines = [x.strip("\ufeff").strip() for x in b.splitlines() if x.strip()]
                if len(lines) < 2:
                    continue
                # lines[0] 可能是序號
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

    def _pick_segment_from_compact(compact_text: str, slot_idx: int):
        """
        compact 格式像：
        [0-5] ...
        [5-11] ...
        回傳 (line_start:int, line_end:int) 或 (None,None)
        """
        try:
            import re
            pairs = re.findall(r"\[(\d+)\s*-\s*(\d+)\]", compact_text or "")
            if not pairs:
                return (None, None)
            # 簡單穩定策略：用 slot_idx 映射到第 k 段
            k = slot_idx if slot_idx is not None else 0
            if k < 0:
                k = 0
            if k >= len(pairs):
                k = len(pairs) - 1
            a, b = pairs[k]
            return (int(a), int(b))
        except Exception:
            return (None, None)

    def _fallback_segment_from_task(task_doc, slot_idx: int):
        """
        依 task 裡的 ai_segment_map / ai_segments_compact + subtitle_path 推算秒數
        回傳 (t_start, t_end, subtitle_context)
        """
        try:
            # ① 優先用 ai_segment_map（A 電腦通常有）
            seg_map = task_doc.get("ai_segment_map") or {}
            key1 = str(slot_idx) if slot_idx is not None else "0"
            key2 = f"第{(slot_idx + 1)}格" if slot_idx is not None else "第1格"

            seg = None
            if key1 in seg_map:
                seg = seg_map.get(key1)
            elif key2 in seg_map:
                seg = seg_map.get(key2)

            if isinstance(seg, dict):
                ts = seg.get("start")
                te = seg.get("end")
                if ts is not None and te is not None and float(te) > float(ts):
                    ctx = seg.get("evidence") or ""
                    return (float(ts), float(te), ctx)

            # ② 沒有 map，就用 compact + subtitle_path 做推算（B 電腦常見）
            compact = task_doc.get("ai_segments_compact") or ""
            subtitle_path = (((task_doc.get("prompt_source") or {}).get("subtitle_path")) or "").strip()
            if compact and subtitle_path:
                line_a, line_b = _pick_segment_from_compact(compact, slot_idx or 0)
                if line_a is not None and line_b is not None and line_b > line_a:
                    segs = _read_srt_segments(subtitle_path)
                    if segs:
                        a = max(0, min(line_a, len(segs) - 1))
                        b = max(0, min(line_b - 1, len(segs) - 1))
                        ts = float(segs[a]["start"])
                        te = float(segs[b]["end"])
                        # context：取範圍內前幾句
                        ctx_lines = [segs[i]["text"] for i in range(a, min(b + 1, a + 6))]
                        ctx = "\n".join([x for x in ctx_lines if x]).strip()
                        if te > ts:
                            return (ts, te, ctx)

            return (None, None, "")
        except Exception:
            return (None, None, "")

    # [新增] V1.4：用「template_slots 的順序」做一格一格比對，避免 idx 錯位（第3格變第4格）
    parsed = t5doc_to_parsons_task(task)
    expected_ids = [str(s.get("expected_id")) for s in (parsed.get("template_slots") or [])]

    # [保留] 仍保留 answer_core_ids 欄位（不改 DB schema）
    answer_core = [bid for bid in answer_ids if str(bid).startswith("b")]

    # [新增] 對齊長度：不足補 None；多出的視為錯（但不會影響 wrong_indices 的 index 對齊）
    aligned = list(answer_ids)
    if len(aligned) < len(expected_ids):
        aligned = aligned + [None] * (len(expected_ids) - len(aligned))

    wrong_indices = []
    for i in range(len(expected_ids)):
        if str(aligned[i]) != str(expected_ids[i]):
            wrong_indices.append(i)

    # 額外多填的答案也算錯（不新增不存在的格 index，只影響 is_correct / score）
    extra_wrong = max(0, len(answer_ids) - len(expected_ids))

    wrong_index = wrong_indices[0] if wrong_indices else None
    is_correct = (len(wrong_indices) == 0 and extra_wrong == 0)

    # [新增] 分數：以格數正確率計算
    total_slots = max(1, len(expected_ids))
    score = (total_slots - len(wrong_indices)) / total_slots

    # [新增] 產生回饋需要的欄位（slot_label / actual_text / expected_text）
    slot_label = f"第{(wrong_index + 1)}格" if wrong_index is not None else ""
    pool_by_id = {str(b.get("id")): b for b in (parsed.get("pool") or [])}

    actual_id = str(aligned[wrong_index]) if (wrong_index is not None and aligned[wrong_index] is not None) else ""
    expected_id = str(expected_ids[wrong_index]) if wrong_index is not None else ""

    actual_text = pool_by_id.get(actual_id, {}).get("text", "") if actual_id else ""
    expected_text = pool_by_id.get(expected_id, {}).get("text", "") if expected_id else ""

    feedback = "✅ 完全正確！" if is_correct else ((task.get("ai_feedback") or {}).get("general") or f"❌ 目前正確率 {score:.0%}，建議先確認「輸入 → 計算 → 輸出」的順序。")

    video_id_str = normalize_video_id(task.get("video_id"))

    attempt_doc = {
        "task_id": task_id,
        "video_id": video_id_str,
        "unit": task.get("unit"),
        "student_id": student_id or None,
        "level": level or task.get("level") or None,
        "answer_ids": answer_ids,
        "answer_block_ids": answer_ids,
        "answer_core_ids": answer_core,
        "is_correct": is_correct,
        "score": score,
        "feedback": feedback,
        "wrong_index": wrong_index,
        "wrong_indices": wrong_indices,
        "review": {"student_choice": None},
        "created_at": now_utc(),
    }

    ins = db.parsons_attempts.insert_one(attempt_doc)
    attempt_id = str(ins.inserted_id)
    review_attempt_id = (data.get("review_attempt_id") or "").strip()

    if review_attempt_id:
        db.parsons_review_logs.update_one(
            {"attempt_id": review_attempt_id},
            {"$set": {
                "followup_is_correct": bool(is_correct),
                "followup_submitted_at": now_utc(),
                "followup_attempt_id": attempt_id,
            }}
        )

    resp = {
        "ok": True,
        "attempt_id": attempt_id,
        "is_correct": is_correct,
        "score": score,
        "feedback": feedback,
        "wrong_index": wrong_index,
        "wrong_indices": wrong_indices,

        # [新增] V1.4：送出後回傳欄位（前端顯示以後端為準）
        "slot_label": slot_label,
        "actual_text": actual_text,
        "expected_text": expected_text,
        "hint": "",
        "review_t": None,
    }

    if not is_correct:
        # [修改] V1.5：錯誤回饋由 AI（提示 + 建議回看時間軸）決定；若 AI 不可用則保留既有 fallback
        slot_key = str(wrong_index) if wrong_index is not None else "0"
        hint, t_start, t_end, subtitle_context = ai_hint_and_segment_for_wrong(
            task=task,
            slot_key=slot_key,
            expected_text=expected_text,
            actual_text=actual_text,
            level=(level or task.get("level") or "L1"),
            slot_label=(slot_label or f"第{(wrong_index + 1)}格" if wrong_index is not None else "第1格"),
        )
        print("DEBUG_SEGMENT:", t_start, t_end)

        # [新增] V1.3：若 AI 沒回出有效秒數，嘗試從 task 的 ai_segment_map / ai_segments_compact 推算
        if t_start is None or t_end is None or t_end <= t_start:
            fb_start, fb_end, fb_ctx = _fallback_segment_from_task(task, wrong_index if wrong_index is not None else 0)
            if fb_start is not None and fb_end is not None and fb_end > fb_start:
                t_start = fb_start
                t_end = fb_end
                if not subtitle_context:
                    subtitle_context = fb_ctx

        # fallback（維持你原本行為：一定會有 jump 秒數，不讓學生卡住）
        if t_start is None or t_end is None or t_end <= t_start:
            t_start = 120.0
            t_end = 170.0
        if not hint:
            hint = "（AI 暫時不可用）請回看影片片段並檢查輸入型別與運算順序。"

        resp["review_t"] = int(float(t_start))
        resp["hint"] = hint

        raw_vid = task.get("video_id") or data.get("video_id") or ""
        vid = normalize_video_id(raw_vid)

        resp["jump"] = {"video_id": vid, "start": float(t_start), "end": float(t_end)}
        resp["data"] = {
            "title": "回答錯誤",
            "error_detail": feedback,
            "segment": {"start": float(t_start), "end": float(t_end), "label": f"影片片段 [{int(float(t_start))}–{int(float(t_end))} 秒]"},
            "subtitle_context": subtitle_context or "（未找到字幕）",
            "ai_hint": hint,
            "video_id": vid,
        }

    return jsonify(resp)

# =========================
# (C) POST /review  學生選擇是否回看影片（V1.7-A）
# =========================
# @parsons_bp.post("/review")
# def review_choice():
#     data = request.get_json(silent=True) or {}

#     attempt_id = (data.get("attempt_id") or "").strip()
#     student_id = (data.get("student_id") or "").strip()
#     task_id = (data.get("task_id") or "").strip()
#     video_id = (data.get("video_id") or "").strip()
#     student_choice = (data.get("student_choice") or "").strip().lower()  # yes / no

#     if not attempt_id:
#         return jsonify({"ok": False, "message": "missing attempt_id"}), 400

#     if student_choice not in ("yes", "no"):
#         return jsonify({"ok": False, "message": "student_choice must be 'yes' or 'no'"}), 400

#     # 允許前端沒傳 task_id/video_id：我們嘗試從 attempts 裡補
#     task_id_f = task_id or None
#     video_id_f = video_id or None
#     student_id_f = student_id or None

#     try:
#         att = db.parsons_attempts.find_one({"_id": ObjectId(attempt_id)})
#         if att:
#             if not task_id_f:
#                 task_id_f = att.get("task_id") or None
#             if not video_id_f:
#                 video_id_f = att.get("video_id") or None
#             if not student_id_f:
#                 student_id_f = att.get("student_id") or None
#     except Exception:
#         # attempt_id 不是 ObjectId 也沒關係（你目前 attempts 的 _id 是 ObjectId）
#         pass

#     doc = {
#         "attempt_id": attempt_id,
#         "task_id": task_id_f,
#         "video_id": video_id_f,
#         "student_id": student_id_f,
#         "student_choice": student_choice,  # yes/no
#         "created_at": now_utc(),
#     }

#     # ✅ 安全策略：同一個 attempt_id 只留一筆（避免重複點擊產生多筆）
#     # - 若已存在：更新 choice + updated_at
#     # - 若不存在：插入
#     existed = db.parsons_review_logs.find_one({"attempt_id": attempt_id})
#     if existed:
#         db.parsons_review_logs.update_one(
#             {"_id": existed["_id"]},
#             {"$set": {
#                 "student_choice": student_choice,
#                 "student_id": student_id_f,
#                 "task_id": task_id_f,
#                 "video_id": video_id_f,
#                 "updated_at": now_utc(),
#             }}
#         )
#         return jsonify({"ok": True, "mode": "update"})
#     else:
#         db.parsons_review_logs.insert_one(doc)
#         return jsonify({"ok": True, "mode": "insert"})

# =========================
# (C) POST /review_choice  記錄學生是否選擇回看（yes/no）
# =========================
@parsons_bp.route("/review_choice", methods=["POST", "OPTIONS"], endpoint="parsons_review_choice_v17")
def review_choice():
    # CORS preflight
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(silent=True) or {}

    attempt_id = (data.get("attempt_id") or "").strip()
    student_choice = (data.get("student_choice") or "").strip().lower()  # yes/no

    # ✅ A 方案：後端盡量補齊 student_id
    student_id = (data.get("student_id") or "").strip()  # 若前端未來願意送，直接吃
    participant_id = (data.get("participant_id") or data.get("token") or "").strip()  # 兼容你登入的 uid

    if not attempt_id:
        return jsonify({"ok": False, "message": "missing attempt_id"}), 400
    if student_choice not in ("yes", "no"):
        return jsonify({"ok": False, "message": "student_choice must be yes/no"}), 400

    # 先從 attempts 補 task_id / video_id（不改 schema）
    task_id_f = None
    video_id_f = None

    try:
        att = db.parsons_attempts.find_one({"_id": ObjectId(attempt_id)})
        if att:
            task_id_f = att.get("task_id") or None
            video_id_f = att.get("video_id") or None
            # 若 attempts 裡本來就有 student_id，也可以吃（目前你多半是 null）
            if not student_id:
                student_id = (att.get("student_id") or "").strip()
    except Exception:
        pass

    # 若仍沒有 student_id，但有 participant_id → 去 users 查 student_id
    if (not student_id) and participant_id:
        try:
            u = db.users.find_one({"_id": ObjectId(participant_id)})
            if u:
                student_id = (u.get("student_id") or "").strip()
        except Exception:
            pass

    if not student_id:
        student_id = "unknown"  # 至少不要空，方便你統計

    db.parsons_review_logs.update_one(
        {"attempt_id": attempt_id},
        {
            "$set": {
                "attempt_id": attempt_id,
                "task_id": task_id_f,
                "video_id": video_id_f,
                "student_id": student_id,
                "participant_id": participant_id or None,
                "student_choice": student_choice,
                "updated_at": now_utc(),
            },
            "$setOnInsert": {"created_at": now_utc()},
        },
        upsert=True,
    )

    return jsonify({"ok": True, "student_id": student_id})


# =========================
# (D) POST/OPTIONS /review_watch  (V1.7-C 完整實現：記錄回看詳情)
# 前端正在呼叫：/api/parsons/review_watch
# 記錄學生回看視頻的所有互動數據
# =========================
@parsons_bp.route("/review_watch", methods=["POST", "OPTIONS"])
def review_watch():
    """
    V1.7 完整實現：記錄視頻回看與互動詳情
    
    Expected Payload:
    {
        "attempt_id": "xxx",                    # 關聯的練習嘗試
        "video_id": "xxx",
        "task_id": "xxx",
        "student_id": "xxx",                     # 學號
        "start_sec": 120,                        # 指定回看片段開始時間
        "end_sec": 180,                          # 指定回看片段結束時間
        "watch_seconds": 3600,                   # 本次回看觀看秒數
        "reached_end": true,                     # 是否播放到結束
        "watch_start_at": "2026-02-26T...",     # 開始觀看時間
        "watch_end_at": "2026-02-26T...",       # 停止觀看時間
        "seek_events": [{"from": 10, "to": 50, "timestamp": "..."}, ...],  # V1.7 NEW
        "is_complete_playback": true              # V1.7: 是否完整播放（無中斷）
    }
    """
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(silent=True) or {}
    
    attempt_id = (data.get("attempt_id") or "").strip()
    video_id = (data.get("video_id") or "").strip()
    student_id = (data.get("student_id") or "").strip()
    task_id = (data.get("task_id") or "").strip()
    
    watch_seconds = int(data.get("watch_seconds") or 0)
    reached_end = bool(data.get("reached_end"))
    watch_start_at = data.get("watch_start_at")
    watch_end_at = data.get("watch_end_at")
    start_sec = data.get("start_sec")
    end_sec = data.get("end_sec")
    seek_events = data.get("seek_events") or []  # V1.7: seek 事件列表
    
    # 基本檢驗
    if not attempt_id or not video_id:
        return jsonify({"ok": False, "message": "missing attempt_id or video_id"}), 400
    
    try:
        # 從 parsons_attempts 中找到原始嘗試記錄
        original_attempt = db.parsons_attempts.find_one({"_id": ObjectId(attempt_id)})
        if not original_attempt:
            return jsonify({"ok": False, "message": "attempt not found"}), 404
        
        # 若前端沒傳 student_id，從原始 attempt 取得
        if not student_id:
            student_id = original_attempt.get("student_id")
        
        # 取得用戶 participant_id（用于研究分析）
        participant_id = None
        user = db.users.find_one({"student_id": student_id}) if student_id else None
        if user:
            participant_id = user.get("participant_id")
        
        # 計算 seek 統計
        seek_count = len(seek_events)
        total_seek_distance = sum(abs(e.get("to", 0) - e.get("from", 0)) for e in seek_events)
        avg_seek_distance = total_seek_distance / seek_count if seek_count > 0 else 0
        
        # 創建回看日誌記錄
        rewatch_log = {
            "attempt_id": attempt_id,
            "video_id": video_id,
            "task_id": task_id,
            "student_id": student_id,
            "participant_id": participant_id,
            
            # === 觀看行為 ===
            "watch_seconds": watch_seconds,
            "reached_end": reached_end,
            "watch_start_at": watch_start_at,
            "watch_end_at": watch_end_at,
            "duration_minutes": round(watch_seconds / 60, 2),
            
            # === 回看片段信息 ===
            "segment_start_sec": start_sec,
            "segment_end_sec": end_sec,
            "segment_duration_sec": (end_sec - start_sec) if end_sec and start_sec else None,
            
            # === V1.7 Seek 統計 ===
            "seek_count": seek_count,
            "total_seek_distance": total_seek_distance,
            "avg_seek_distance": round(avg_seek_distance, 2),
            "is_frequent_seeker": seek_count > 5,  # 5次以上視為頻繁 seek（可調整閾值）
            "seek_events": seek_events,  # 保留原始事件供詳細分析
            
            # === 播放完整性 ===
            "completed_fully": reached_end and seek_count <= 2,  # V1.7: 判定為完整播放
            
            # === 後續回答 ===
            "has_followup": bool(original_attempt.get("followup_is_correct") is not None),
            "followup_is_correct": original_attempt.get("followup_is_correct"),
            "followup_attempt_id": original_attempt.get("followup_attempt_id"),
            
            # === 時間戳 ===
            "recorded_at": now_utc(),
        }
        
        # 插入回看日誌
        log_result = db.video_rewatch_logs.insert_one(rewatch_log)
        rewatch_log_id = str(log_result.inserted_id)
        
        # V1.7: 更新用戶的回看統計
        if student_id:
            db.users.update_one(
                {"student_id": student_id},
                {
                    "$inc": {"rewatch_stats.total_rewatch_count": 1},
                    "$push": {
                        "rewatch_stats.rewatch_sessions": {
                            "video_id": video_id,
                            "attempt_id": attempt_id,
                            "watched_at": watch_start_at,
                            "watch_duration_sec": watch_seconds,
                            "reached_end": reached_end,
                            "is_frequent_seeker": seek_count > 5,
                        }
                    },
                    "$set": {"last_login_at": now_utc()}
                }
            )
        
        # V1.7: 更新原始 attempt，記錄回看日誌 ID
        db.parsons_attempts.update_one(
            {"_id": ObjectId(attempt_id)},
            {
                "$set": {
                    "review_log_id": rewatch_log_id,
                    "review_log_recorded_at": now_utc()
                }
            }
        )
        
        return jsonify({
            "ok": True,
            "rewatch_log_id": rewatch_log_id,
            "message": f"✅ 回看記錄已保存 (seek_count={seek_count})",
            "stats": {
                "watch_duration": watch_seconds,
                "reached_end": reached_end,
                "seek_count": seek_count,
                "is_frequent_seeker": seek_count > 5,
            }
        })
    
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


# =========================
# (B) POST /regenerate  (老師端產生題目：預設 draft)
# =========================
@parsons_bp.post("/regenerate")
def regenerate():
    data = request.get_json(silent=True) or {}
    video_id = (data.get("video_id") or "").strip()
    level = (data.get("level") or "L1").strip()

    if not video_id:
        return jsonify({"ok": False, "message": "missing video_id"}), 400

    try:
        vid_oid = ObjectId(video_id)
    except InvalidId:
        return jsonify({"ok": False, "message": "video_id must be a 24-char ObjectId"}), 400

    v = db.videos.find_one({"_id": vid_oid})
    if not v:
        return jsonify({"ok": False, "message": "video not found"}), 404

    doc, gen_source, gen_error, env = create_task_for_video(v, video_id, level)

    return jsonify({
        "ok": True,
        "task": {
            "task_id": str(doc["_id"]),
            "video_id": doc.get("video_id"),
            "unit": doc.get("unit"),
            "title": doc.get("video_title"),
            "level": doc.get("level"),
            "question_text": doc.get("question_text"),
            "template_slots": doc.get("template_slots", []),
            "pool": doc.get("pool", []),
            "ai_feedback": doc.get("ai_feedback", {}),
        },
        "gen_source": gen_source,
        "gen_error": gen_error,
        "env": env,
    })