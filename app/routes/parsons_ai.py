# parsons_ai.py
# 統一管理 OpenAI 呼叫（方案1）
# ⚠️ 重要：本檔案不得 import parsons.py（避免 circular import）
# 目的：集中 OpenAI 呼叫，提供可重用的 prompt builder / JSON 解析 / 生成干擾題中文語意

import os
import json
import re
from typing import Any, Dict, Optional, Tuple, List

from dotenv import load_dotenv

# OpenAI SDK
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

print("[AI] start generate", {
  "enabled": os.environ.get("AI_ENABLED"),
  "model": os.environ.get("OPENAI_MODEL"),
  "has_key": bool(os.environ.get("OPENAI_API_KEY")),
})
# =========================
# (A) ENV / 開關
# =========================
load_dotenv()

def _env_bool(name: str, default: bool = False) -> bool:
    v = (os.getenv(name, "") or "").strip().lower()
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    return default

AI_ENABLED = _env_bool("AI_ENABLED", default=False)
OPENAI_MODEL = (os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()


def _ensure_client() -> "OpenAI":
    if not AI_ENABLED:
        raise RuntimeError("AI_ENABLED=false（目前 AI 關閉）")
    if OpenAI is None:
        raise RuntimeError("openai 套件未安裝：請在 venv 執行 pip install openai")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY 未設定（.env 沒讀到或 key 空）")
    return OpenAI(api_key=OPENAI_API_KEY)


# =========================
# (B) 低階：呼叫 OpenAI
# =========================
def call_openai_output_text(
    *,
    system: str,
    user: str,
    temperature: float = 0.2,
    max_output_tokens: int = 1200,
    model: Optional[str] = None,
) -> str:
    """回傳純文字（適合一般生成）"""
    client = _ensure_client()
    m = (model or OPENAI_MODEL).strip()

    resp = client.responses.create(
        model=m,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )
    return (getattr(resp, "output_text", "") or "").strip()


def call_openai_json(
    *,
    system: str,
    user: str,
    temperature: float = 0.2,
    max_output_tokens: int = 1200,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """回傳 JSON（支援 fenced json 或裸 json）"""
    client = _ensure_client()
    m = (model or OPENAI_MODEL).strip()

    resp = client.responses.create(
        model=m,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )
    txt = (getattr(resp, "output_text", "") or "").strip()
    return extract_json(txt)


# =========================
# (C) 輔助：解析 JSON
# =========================
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)

def extract_json(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    m = _JSON_FENCE_RE.search(text)
    cand = m.group(1).strip() if m else text.strip()

    try:
        return json.loads(cand)
    except Exception:
        pass

    lb = cand.find("{")
    rb = cand.rfind("}")
    if lb != -1 and rb != -1 and rb > lb:
        try:
            return json.loads(cand[lb:rb+1])
        except Exception:
            return {}

    return {}


# =========================
# (D) 既有：錯誤提示 prompt
# =========================
def build_hint_prompt(
    *,
    slot_label: str,
    expected_text: str,
    actual_text: str,
    subtitle_context: str,
) -> Tuple[str, str]:
    system = (
        "你是Python教學助教，負責針對Parsons除錯題給出精準回饋。\n"
        "你必須回傳 JSON，欄位包含：\n"
        "- slot_label: 字串\n"
        "- expected_text: 字串\n"
        "- actual_text: 字串\n"
        "- hint: 中文建議（一句到兩句）\n"
        "- review_t: 建議回看秒數（數字）\n"
        "- start: 片段起點秒數（數字）\n"
        "- end: 片段終點秒數（數字）\n"
        "只回 JSON，不要其他文字。"
    )
    user = (
        f"學生在 {slot_label} 放錯了。\n"
        f"expected_text: {expected_text}\n"
        f"actual_text: {actual_text}\n\n"
        f"字幕上下文：\n{subtitle_context}\n\n"
        "請依字幕內容推估最相關的解釋片段時間（start/end/review_t）。"
    )
    return system, user


# =========================================================
# (E) [新增] 產生「干擾題中文語意」：block_id -> semantic_zh
# =========================================================
def build_distractor_semantics_prompt(
    *,
    question_text: str,
    solution_blocks: List[Dict[str, Any]],
    distractor_blocks: List[Dict[str, Any]],
    level: str = "L1",
) -> Tuple[str, str]:
    system = (
        "你是Python教學助教，要為 Parsons 題目的「干擾程式碼片段」撰寫中文語意。\n"
        "請只回傳 JSON，不要任何解釋文字。\n"
        "JSON 格式必須是：\n"
        "{\n"
        '  "semantics": {\n'
        '    "<block_id>": "<繁體中文語意，10~25字，描述這段程式碼在做什麼/為何是干擾>",\n'
        "    ...\n"
        "  }\n"
        "}\n"
        "規則：\n"
        "- 每個 distractor 的 block_id 都要有一筆 semantics。\n"
        "- 語意要貼近題目情境，但不要洩漏正解順序。\n"
        "- 語意用繁體中文、簡短清楚。\n"
    )

    sol_lines = []
    for b in (solution_blocks or []):
        if isinstance(b, dict):
            sol_lines.append(f'{b.get("id","")}: {b.get("text","")}')
    dis_lines = []
    for b in (distractor_blocks or []):
        if isinstance(b, dict):
            dis_lines.append(f'{b.get("id","")}: {b.get("text","")}')

    user = (
        f"難度：{level}\n"
        f"題目敘述：{question_text}\n\n"
        "正確解答 blocks（參考用）：\n" + "\n".join(sol_lines) + "\n\n"
        "干擾 blocks（請為每個 block_id 產生中文語意）：\n" + "\n".join(dis_lines)
    )
    return system, user


def generate_distractor_semantics(
    *,
    question_text: str,
    solution_blocks: List[Dict[str, Any]],
    distractor_blocks: List[Dict[str, Any]],
    level: str = "L1",
    temperature: float = 0.2,
    max_output_tokens: int = 900,
    model: Optional[str] = None,
) -> Dict[str, str]:
    """回傳 { block_id: semantic_zh }；失敗或關閉 AI 則回 {}"""
    if not AI_ENABLED:
        return {}
    if not distractor_blocks:
        return {}

    ids = []
    for b in distractor_blocks:
        if isinstance(b, dict) and b.get("id"):
            ids.append(str(b.get("id")))
    if not ids:
        return {}

    system, user = build_distractor_semantics_prompt(
        question_text=question_text or "",
        solution_blocks=solution_blocks or [],
        distractor_blocks=distractor_blocks or [],
        level=level or "L1",
    )

    try:
        obj = call_openai_json(
            system=system,
            user=user,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            model=model,
        )
    except Exception:
        return {}

    sem = obj.get("semantics") if isinstance(obj, dict) else None
    if not isinstance(sem, dict):
        return {}

    out: Dict[str, str] = {}
    for bid in ids:
        v = sem.get(bid)
        if isinstance(v, str) and v.strip():
            out[bid] = v.strip()

    return out
