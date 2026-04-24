"""TTS 前的文字正規化 — 消除會讓 edge-tts 咬字出錯的標點與寫法。

常見問題：
  - `——` / `─` 破折號 → edge-tts 會吞掉導致前後字黏在一起
  - 全形單/雙引號『』《》 → 讀出「單引號」「雙引號」聲音
  - 連續標點 `。。` / `，，` → 過長停頓
  - `%` 後無空格 → 有時讀成「百分之」有時讀成「percent」
  - 英文縮寫 ASCII 跟中文緊貼 → 有機率被當成一個音節
"""
from __future__ import annotations

import re


# 破折號類 → 替換成逗號（edge-tts 對逗號的停頓最自然）
_DASH_PATTERN = re.compile(r"[——─—–─]+")

# 全形引號清除
_QUOTE_PATTERN = re.compile(r"[『』「」《》〈〉]")

# 連續標點收斂
_REPEAT_PUNCT = re.compile(r"([，。！？；：])\1+")

# 中英文緊貼：在中文字和 ASCII 之間插入細微停頓（用零寬或半形空格）
_CN_EN_BOUNDARY = re.compile(r"(?<=[\u4e00-\u9fff])(?=[A-Za-z0-9])|(?<=[A-Za-z0-9])(?=[\u4e00-\u9fff])")

# 百分號前加空格，讓 TTS 把數字和「%」分開處理
_PERCENT = re.compile(r"(\d)%")

# 括號內的短註釋通常會被 TTS 讀出「括號」 → 把括號換成逗號
_PARENS = re.compile(r"[（\(]([^）\)]{1,30})[）\)]")


def normalize_for_tts(text: str) -> str:
    """把 narration 文字正規化為 TTS 友善格式。"""
    if not text:
        return text

    # 1. 破折號 → 逗號（保留句子節奏）
    text = _DASH_PATTERN.sub("，", text)

    # 2. 全形引號 → 去掉（內容保留）
    text = _QUOTE_PATTERN.sub("", text)

    # 3. 括號內容 → 逗號包住
    text = _PARENS.sub(r"，\1，", text)

    # 4. 中英文之間加薄空格，避免咬字黏一起
    text = _CN_EN_BOUNDARY.sub(" ", text)

    # 5. 百分號前加空格
    text = _PERCENT.sub(r"\1 %", text)

    # 6. 連續標點收斂為單一
    text = _REPEAT_PUNCT.sub(r"\1", text)

    # 7. 收尾：去掉多餘空格
    text = re.sub(r" {2,}", " ", text).strip()

    return text


if __name__ == "__main__":
    # 測試
    cases = [
        "這不是幾家公司的小實驗——這是Adobe根據幾百家主流零售網站的真實數據。",
        "直接問ChatGPT或Perplexity『推薦一個藍牙喇叭』，然後AI會給你連結。",
        "393%聽起來驚人，但目前佔比還不到5%。",
        "第一批積極與AI購物助手合作的台灣電商（包括蝦皮、momo、PChome），會贏得紅利。",
        "，。，，。。試試看看",
    ]
    for c in cases:
        print("IN :", c)
        print("OUT:", normalize_for_tts(c))
        print()
