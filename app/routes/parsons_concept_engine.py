from __future__ import annotations

import hashlib
import re
from typing import Dict, Any, Optional, List


# =========================================================
# Concept -> scenario pool
# =========================================================
SCENARIO_POOL: Dict[str, List[str]] = {
    # -------------------------
    # U1: Input / Output
    # -------------------------
    "io_basic": [
        "顯示使用者輸入的姓名",
        "輸入一個數字後顯示結果",
        "輸入一段文字後輸出內容",
    ],
    "io_two_inputs": [
        "輸入兩位學生的分數",
        "輸入兩件商品的價格",
        "輸入兩個整數後顯示結果",
    ],
    "io_calculation": [
        "計算長方形面積",
        "計算兩數總和",
        "計算購物總金額",
    ],
    "io_swap": [
        "交換兩位同學的座號",
        "交換兩個箱子的編號",
        "交換兩個變數的內容",
    ],
    "io_format": [
        "格式化顯示成績資訊",
        "格式化顯示商品價格",
        "格式化顯示使用者資料",
    ],

    # -------------------------
    # U2: If
    # -------------------------
    "if_basic": [
        "是否符合活動參加條件",
        "是否能進入系統",
        "是否達到基本要求",
    ],
    "if_else": [
        "是否及格",
        "是否成年",
        "是否符合開通條件",
    ],
    "if_compare": [
        "比較兩位同學的成績高低",
        "比較兩件商品價格",
        "比較兩個數值大小",
    ],
    "if_grade": [
        "考試是否及格",
        "作業成績是否達標",
        "測驗分數是否通過",
    ],
    "if_range": [
        "判斷溫度是否落在安全範圍",
        "判斷成績是否落在指定區間",
        "判斷身高是否在規定範圍",
    ],
    "if_mod": [
        "判斷座號是奇數還是偶數",
        "判斷今天是單數日還是雙數日",
        "判斷號碼是奇數或偶數",
    ],

    # -------------------------
    # U3: For Loop
    # -------------------------
    "range_print": [
        "列出活動編號",
        "列出關卡編號",
        "列出書本頁碼",
    ],
    "range_pattern": [
        "用星號繪製直角三角形",
        "用符號印出階梯圖形",
        "每行多印一個星號",
    ],
    "range_sum": [
        "計算關卡編號總和",
        "計算累積點數總和",
        "計算每日步數編號總和",
    ],
    "range_desc": [
        "遊戲關卡倒數",
        "電梯樓層下降",
        "剩餘天數倒數",
    ],
    "range_even": [
        "列出偶數號座位",
        "列出雙數編號",
        "列出偶數樓層",
    ],
    "range_multiples": [
        "列出 3 的倍數站點",
        "列出 5 的倍數編號",
        "列出倍數日期",
    ],
    "range_input": [
        "從起始樓層到終點樓層列出編號",
        "從起始關卡到終點關卡列出編號",
        "從起始頁碼到終點頁碼列出數字",
    ],

    # -------------------------
    # U6: List
    # -------------------------
    "list_basic": [
        "讀入一串成績後逐一輸出",
        "將多筆資料存進列表並顯示",
        "依序走訪列表中的每個元素",
    ],
    "list_sum": [
        "計算列表中所有數值的總和",
        "統計清單資料的累積值",
        "加總多筆輸入後輸出結果",
    ],
    "list_filter": [
        "篩選列表中的偶數項目",
        "挑出符合條件的資料並輸出",
        "從清單中找出達標項目",
    ],

    # -------------------------
    # U4: While Loop
    # -------------------------
    "while_counter": [
        "計數器逐步增加",
        "持續顯示計數結果",
        "逐步累進顯示數值",
    ],
    "while_condition": [
        "直到條件不成立為止",
        "當數值仍在範圍內時持續執行",
        "當條件成立時反覆執行",
    ],
    "while_input": [
        "直到輸入結束指令",
        "重複輸入直到停止",
        "持續輸入直到指定值",
    ],
    "while_sum": [
        "持續累加輸入數值",
        "重複輸入並計算總和",
        "逐次加入數字直到結束",
    ],

    # -------------------------
    # U7: Function
    # -------------------------
    "function_basic": [
        "撰寫函式顯示訊息",
        "撰寫函式處理基本資料",
        "撰寫函式輸出結果",
    ],
    "function_return": [
        "撰寫函式回傳加總結果",
        "撰寫函式回傳面積",
        "撰寫函式回傳計算值",
    ],
    "function_two_params": [
        "撰寫函式處理兩個數值",
        "撰寫函式比較兩個分數",
        "撰寫函式計算兩個整數",
    ],
    "function_calculation": [
        "撰寫函式計算平均值",
        "撰寫函式計算折扣價格",
        "撰寫函式計算矩形面積",
    ],
}


TRACK_CONCEPTS: Dict[str, List[str]] = {
    "io": ["io_basic", "io_two_inputs", "io_calculation", "io_swap", "io_format"],
    "condition": ["if_basic", "if_else", "if_compare", "if_grade", "if_range", "if_mod"],
    "for": ["range_print", "range_pattern", "range_sum", "range_desc", "range_even", "range_multiples", "range_input"],
    "while": ["while_counter", "while_condition", "while_input", "while_sum"],
    "list": ["list_basic", "list_sum", "list_filter"],
    "function": ["function_basic", "function_return", "function_two_params", "function_calculation"],
}


CONCEPT_KEYWORDS: Dict[str, List[str]] = {
    "io_basic": ["輸入", "輸出", "顯示", "print", "input"],
    "io_two_inputs": ["兩個", "兩位", "two", "兩次輸入"],
    "io_calculation": ["計算", "總和", "加總", "運算", "面積", "average", "total"],
    "io_swap": ["交換", "swap"],
    "io_format": ["格式", "format", "f-string"],
    "if_basic": ["判斷", "條件", "if"],
    "if_else": ["else", "否則", "否則就"],
    "if_compare": ["比較", "大於", "小於", ">", "<"],
    "if_grade": ["成績", "及格", "pass", "fail", "達標"],
    "if_range": ["範圍", "區間", "之間", "介於"],
    "if_mod": ["奇數", "偶數", "%", "餘數"],
    "range_print": ["列出", "輸出", "編號", "print"],
    "range_pattern": ["圖形", "星號", "三角形", "pattern", "*"],
    "range_sum": ["總和", "加總", "累加", "sum", "total"],
    "range_desc": ["遞減", "倒數", "由大到小", "-1"],
    "range_even": ["偶數"],
    "range_multiples": ["倍數"],
    "range_input": ["m到n", "m 到 n", "起點", "終點", "from", "to"],
    "while_counter": ["計數", "counter", "逐步", "遞增"],
    "while_condition": ["條件", "成立", "while"],
    "while_input": ["直到", "停止", "quit", "sentinel"],
    "while_sum": ["累加", "總和", "sum", "total"],
    "list_basic": ["list", "列表", "清單", "陣列", "走訪", "append"],
    "list_sum": ["list", "列表", "總和", "加總", "累積", "sum", "total"],
    "list_filter": ["list", "列表", "篩選", "過濾", "條件", "偶數", "達標"],
    "function_basic": ["函式", "function", "def"],
    "function_return": ["回傳", "return"],
    "function_two_params": ["兩個參數", "兩個數", "two params"],
    "function_calculation": ["計算", "面積", "平均", "折扣"],
}


def _norm_text(s: str) -> str:
    return (s or "").strip().lower()


def _infer_track(unit: str, subtitle_text: str, teacher_description: str = "", video_title: str = "") -> str:
    u = (unit or "").strip().upper()
    all_text = _norm_text("\n".join([subtitle_text or "", teacher_description or "", video_title or ""]))

    # 課程單元優先，避免關鍵字把題型帶偏
    if "-IO" in u or u.startswith("U1"):
        return "io"
    if any(k in u for k in ["-IF", "-ELIF", "-IFELSE"]) or u.startswith("U2"):
        return "condition"
    if "-FOR" in u or u.startswith("U3"):
        return "for"

    # 新課綱對齊：U4-NESTED、U5-WHILE、U6-LIST、U7-FUNCTION
    if "-NESTED" in u or (u.startswith("U4") and "WHILE" not in u):
        return "for"
    if "-WHILE" in u or u.startswith("U5"):
        return "while"
    if "-FUNCTION" in u or u.startswith("U7"):
        return "function"
    if "-LIST" in u or u.startswith("U6"):
        return "list"

    if any(k in all_text for k in ["while", "直到", "停止", "quit"]):
        return "while"
    if any(k in all_text for k in ["for", "range", "迴圈", "重複"]):
        return "for"
    if any(k in all_text for k in ["if", "elif", "else", "條件", "判斷"]):
        return "condition"
    return "io"


def _score_concept(concept: str, subtitle_text: str, teacher_description: str, video_title: str) -> int:
    keys = CONCEPT_KEYWORDS.get(concept) or []
    if not keys:
        return 0

    score = 0
    sub = _norm_text(subtitle_text)
    tea = _norm_text(teacher_description)
    title = _norm_text(video_title)

    for k in keys:
        kk = _norm_text(k)
        if not kk:
            continue
        if kk in sub:
            score += 2
        if kk in tea:
            score += 3
        if kk in title:
            score += 1
    return score


# =========================================================
# concept detection
# =========================================================
def detect_concept(unit: str, subtitle_text: str, teacher_description: str = "", video_title: str = "") -> str:
    track = _infer_track(unit, subtitle_text, teacher_description, video_title)
    candidates = TRACK_CONCEPTS.get(track) or ["generic"]

    scored = [(c, _score_concept(c, subtitle_text, teacher_description, video_title)) for c in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    top_score = scored[0][1] if scored else 0
    if top_score <= 0:
        return candidates[0]

    # 分數相同時用穩定 hash 決定，避免 random 導致同素材卻不同題目
    top = [c for c, s in scored if s == top_score]
    seed = f"{unit}|{subtitle_text}|{teacher_description}|{video_title}"
    idx = int(hashlib.md5(seed.encode("utf-8", errors="ignore")).hexdigest()[:8], 16) % len(top)
    return top[idx]


# =========================================================
# scenario picker
# =========================================================
def pick_scenario(concept: str, unit: str = "", subtitle_text: str = "", teacher_description: str = "", video_title: str = "") -> str:
    items = SCENARIO_POOL.get(concept) or ["一般生活情境"]
    evidence = _norm_text("\n".join([subtitle_text or "", teacher_description or "", video_title or ""]))

    # 先做語意貼合分數，分數相同再用穩定 hash 選擇，避免每次 random。
    best_score = -1
    best_items: List[str] = []
    for item in items:
        parts = re.findall(r"[a-z_]{3,}|[\u4e00-\u9fff]{2,}", _norm_text(item))
        score = sum(1 for p in parts if p and p in evidence)
        if score > best_score:
            best_score = score
            best_items = [item]
        elif score == best_score:
            best_items.append(item)

    seed = f"{unit}|{concept}|{subtitle_text}|{teacher_description}|{video_title}"
    idx = int(hashlib.md5(seed.encode("utf-8", errors="ignore")).hexdigest()[:8], 16) % len(best_items)
    return best_items[idx]


# =========================================================
# generation plan
# =========================================================
def build_generation_plan(unit: str, subtitle_text: str, video_title: str = "", teacher_description: str = "") -> Dict[str, Any]:
    concept = detect_concept(unit, subtitle_text, teacher_description, video_title)
    scenario = pick_scenario(concept, unit, subtitle_text, teacher_description, video_title)

    return {
        "unit": unit,
        "concept": concept,
        "scenario": scenario,
        "subtitle_text": subtitle_text,
        "video_title": video_title,
        "anti_copy_rules": [
            "題目情境必須不同於影片中的原始示例",
            "不可直接重複字幕中的例句",
            "可保留相同程式概念，但需改寫為新的生活情境",
        ],
    }


# =========================================================
# template solution builder
# 目的：讓系統更穩，不必每次都讓 AI 自由生 code
# =========================================================
def build_template_solution(concept: str) -> Optional[Dict[str, Any]]:
    if concept == "range_sum":
        return {
            "solution_lines": [
                "total = 0",
                "for i in range(1, 4):",
                "    total += i",
                "print(total)",
            ],
            "template_slots": [
                {"slot": "0", "label": "先建立累加變數 total"},
                {"slot": "1", "label": "使用 for 迴圈走訪 1 到 3"},
                {"slot": "2", "label": "把每次的數值加入 total"},
                {"slot": "3", "label": "輸出最後的總和"},
            ],
        }

    if concept == "range_print":
        return {
            "solution_lines": [
                "for i in range(1, 4):",
                "    print(i)",
            ],
            "template_slots": [
                {"slot": "0", "label": "使用 for 迴圈走訪 1 到 3"},
                {"slot": "1", "label": "逐一輸出每個數值"},
            ],
        }

    if concept == "range_desc":
        return {
            "solution_lines": [
                "m = int(input())",
                "n = int(input())",
                "for i in range(m, n - 1, -1):",
                "    print(i)",
            ],
            "template_slots": [
                {"slot": "0", "label": "讀入起始值 m"},
                {"slot": "1", "label": "讀入終點值 n"},
                {"slot": "2", "label": "使用遞減 for 迴圈從 m 走到 n"},
                {"slot": "3", "label": "逐一輸出每個數值"},
            ],
        }

    if concept == "range_even":
        return {
            "solution_lines": [
                "for i in range(2, 11, 2):",
                "    print(i)",
            ],
            "template_slots": [
                {"slot": "0", "label": "使用 for 迴圈走訪偶數範圍"},
                {"slot": "1", "label": "逐一輸出每個偶數"},
            ],
        }

    if concept == "range_input":
        return {
            "solution_lines": [
                "m = int(input())",
                "n = int(input())",
                "for i in range(m, n + 1):",
                "    print(i)",
            ],
            "template_slots": [
                {"slot": "0", "label": "讀入起始值 m"},
                {"slot": "1", "label": "讀入終點值 n"},
                {"slot": "2", "label": "使用 for 迴圈走訪從 m 到 n 的範圍"},
                {"slot": "3", "label": "逐一輸出每個數值"},
            ],
        }

    if concept == "if_grade":
        return {
            "solution_lines": [
                "score = int(input())",
                "if score >= 60:",
                "    print('pass')",
                "else:",
                "    print('fail')",
            ],
            "template_slots": [
                {"slot": "0", "label": "讀入成績"},
                {"slot": "1", "label": "判斷成績是否達到及格標準"},
                {"slot": "2", "label": "若達標則輸出 pass"},
                {"slot": "3", "label": "否則執行另一個分支"},
                {"slot": "4", "label": "未達標則輸出 fail"},
            ],
        }

    if concept == "if_mod":
        return {
            "solution_lines": [
                "n = int(input())",
                "if n % 2 == 0:",
                "    print('even')",
                "else:",
                "    print('odd')",
            ],
            "template_slots": [
                {"slot": "0", "label": "讀入整數"},
                {"slot": "1", "label": "利用餘數判斷是否為偶數"},
                {"slot": "2", "label": "若是偶數則輸出 even"},
                {"slot": "3", "label": "否則執行另一個分支"},
                {"slot": "4", "label": "若不是偶數則輸出 odd"},
            ],
        }

    if concept == "io_two_inputs":
        return {
            "solution_lines": [
                "a = int(input())",
                "b = int(input())",
                "print(a + b)",
            ],
            "template_slots": [
                {"slot": "0", "label": "讀入第一個整數"},
                {"slot": "1", "label": "讀入第二個整數"},
                {"slot": "2", "label": "輸出兩數相加結果"},
            ],
        }

    if concept == "io_swap":
        return {
            "solution_lines": [
                "a = int(input())",
                "b = int(input())",
                "a, b = b, a",
                "print(a)",
                "print(b)",
            ],
            "template_slots": [
                {"slot": "0", "label": "讀入第一個值"},
                {"slot": "1", "label": "讀入第二個值"},
                {"slot": "2", "label": "交換兩個變數的內容"},
                {"slot": "3", "label": "輸出交換後的第一個值"},
                {"slot": "4", "label": "輸出交換後的第二個值"},
            ],
        }

    if concept == "function_return":
        return {
            "solution_lines": [
                "def add(a, b):",
                "    return a + b",
                "x = int(input())",
                "y = int(input())",
                "print(add(x, y))",
            ],
            "template_slots": [
                {"slot": "0", "label": "定義一個函式 add，接收兩個參數"},
                {"slot": "1", "label": "回傳兩數相加結果"},
                {"slot": "2", "label": "讀入第一個整數"},
                {"slot": "3", "label": "讀入第二個整數"},
                {"slot": "4", "label": "呼叫函式並輸出結果"},
            ],
        }

    if concept == "function_calculation":
        return {
            "solution_lines": [
                "def area(w, h):",
                "    return w * h",
                "w = int(input())",
                "h = int(input())",
                "print(area(w, h))",
            ],
            "template_slots": [
                {"slot": "0", "label": "定義計算面積的函式"},
                {"slot": "1", "label": "回傳寬乘以高的結果"},
                {"slot": "2", "label": "讀入寬度"},
                {"slot": "3", "label": "讀入高度"},
                {"slot": "4", "label": "呼叫函式並輸出結果"},
            ],
        }

    return None