from __future__ import annotations

import random
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
    # U5: Function
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


# =========================================================
# concept detection
# =========================================================
def detect_concept(unit: str, subtitle_text: str, teacher_description: str = "") -> str:
    u = (unit or "").strip().upper()
    raw = (subtitle_text or "").strip()
    t = raw.lower()

    # 老師描述優先：若有明確描述，直接從描述推斷 concept
    td = (teacher_description or "").strip()
    if td:
        if any(k in td for k in ["三角形", "正方形", "菱形", "星號", "圖形", "pattern", "*"]):
            return "range_pattern"
        if any(k in td for k in ["累加", "總和", "加總", "sum", "total"]):
            return "range_sum"
        if any(k in td for k in ["遞減", "倒數", "由大到小"]):
            return "range_desc"
        if any(k in td for k in ["偶數"]):
            return "range_even"
        if any(k in td for k in ["倍數"]):
            return "range_multiples"
        if any(k in td for k in ["計算", "面積", "加法"]):
            return "io_calculation"
        if any(k in td for k in ["交換", "swap"]):
            return "io_swap"
        if any(k in td for k in ["判斷", "if", "條件", "及格"]):
            return "if_grade"
        if any(k in td for k in ["函式", "function", "def", "回傳"]):
            return "function_return"

    # -------------------------
    # U1: Input / Output
    # -------------------------
    if u.startswith("U1"):
        if ("交換" in raw) or ("swap" in t):
            return "io_swap"
        if ("格式" in raw) or ("format" in t) or ("f-string" in t):
            return "io_format"
        if (("兩個" in raw) and ("輸入" in raw)) or (("two" in t) and ("input" in t)):
            return "io_two_inputs"
        if ("計算" in raw) or ("加總" in raw) or ("運算" in raw) or ("面積" in raw):
            return "io_calculation"
        return "io_basic"

    # -------------------------
    # U2: If
    # -------------------------
    if u.startswith("U2"):
        if ("奇數" in raw) or ("偶數" in raw) or ("%" in raw):
            return "if_mod"
        if ("成績" in raw) or ("及格" in raw) or ("pass" in t) or ("fail" in t):
            return "if_grade"
        if ("範圍" in raw) or ("區間" in raw) or ("之間" in raw):
            return "if_range"
        if ("大於" in raw) or ("小於" in raw) or (">" in raw) or ("<" in raw):
            return "if_compare"
        if ("否則" in raw) or ("else" in t):
            return "if_else"
        return "if_basic"

    # -------------------------
    # U3: For Loop
    # -------------------------
    if u.startswith("U3"):
        if ("總和" in raw) or ("加總" in raw) or ("累加" in raw) or ("sum" in t) or ("total" in t):
            return "range_sum"
        if ("遞減" in raw) or ("倒數" in raw) or ("-1" in raw):
            return "range_desc"
        if ("偶數" in raw):
            return "range_even"
        if ("倍數" in raw):
            return "range_multiples"
        if ("m到n" in raw) or ("m 到 n" in raw) or ("從 m 到 n" in raw) or ("起點" in raw and "終點" in raw):
            return "range_input"
        if ("輸出" in raw) or ("列印" in raw) or ("print" in t):
            return "range_print"
        return "range_print"

    # -------------------------
    # U4: While Loop
    # -------------------------
    if u.startswith("U4"):
        if ("直到" in raw) or ("輸入 quit" in raw) or ("停止" in raw) or ("quit" in t):
            return "while_input"
        if ("總和" in raw) or ("累加" in raw):
            return "while_sum"
        if ("條件" in raw):
            return "while_condition"
        return "while_counter"

    # -------------------------
    # U5: Function
    # -------------------------
    if u.startswith("U5"):
        if ("回傳" in raw) or ("return" in t):
            return "function_return"
        if ("兩個參數" in raw) or ("兩個數" in raw) or ("兩個整數" in raw):
            return "function_two_params"
        if ("面積" in raw) or ("平均" in raw) or ("折扣" in raw) or ("計算" in raw):
            return "function_calculation"
        return "function_basic"

    return "generic"


# =========================================================
# scenario picker
# =========================================================
def pick_scenario(concept: str) -> str:
    items = SCENARIO_POOL.get(concept) or ["一般生活情境"]
    return random.choice(items)


# =========================================================
# generation plan
# =========================================================
def build_generation_plan(unit: str, subtitle_text: str, video_title: str = "", teacher_description: str = "") -> Dict[str, Any]:
    concept = detect_concept(unit, subtitle_text, teacher_description)
    scenario = pick_scenario(concept)

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