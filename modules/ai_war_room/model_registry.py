"""模型發布偵測 — 從 title + summary 判斷是否為「某某新模型發布」類新聞。

命中條件：標題或摘要包含模型系列名 + 版本號（例 GPT-5、Claude 3.5、Llama 4、Gemini 2.0）。
這些用於戰情室「模型發布時間軸」面板。
"""
from __future__ import annotations

import re

# 模型系列 regex — 後面必須接空白/連字符/句點 + 數字
_MODEL_PATTERNS = [
    re.compile(
        r"\b("
        r"gpt[\-\s]?\d(?:\.\d)?(?:[\-\s]?[a-z]+)?|"       # GPT-4, GPT-4o, GPT-5
        r"claude[\-\s]?\d(?:\.\d)?(?:[\-\s]?[a-z]+)?|"    # Claude 3.5 Sonnet
        r"gemini[\-\s]?\d(?:\.\d)?(?:[\-\s]?[a-z]+)?|"    # Gemini 2.0 Flash
        r"llama[\-\s]?\d(?:\.\d)?|"                        # Llama 3.1
        r"grok[\-\s]?\d(?:\.\d)?|"                         # Grok 2
        r"mixtral[\-\s]?\d+x\d+[a-z]?|"                    # Mixtral 8x7B
        r"mistral[\-\s]?(?:large|medium|small|nemo|\d)|"
        r"command[\-\s]?r(?:\+|\s?plus)?|"
        r"phi[\-\s]?\d(?:\.\d)?|"
        r"deepseek[\-\s]?(?:v\d|r\d|coder|math)|"          # DeepSeek-V3/R1
        r"qwen[\-\s]?\d+(?:\.\d+)?|"
        r"dall[\-\s]?e[\-\s]?\d|"
        r"sora(?:[\-\s]?\d)?|"
        r"stable\s*diffusion[\-\s]?\d(?:\.\d)?|"
        r"sdxl|sd\d\.\d|"
        r"midjourney[\-\s]?v?\d"
        r")\b",
        re.IGNORECASE,
    ),
]

# 釋出動詞 — 發布相關動作才算 release（否則像 "comparing GPT-4 vs Claude 3" 算不上）
_RELEASE_VERBS = re.compile(
    r"\b("
    r"launch(?:es|ed|ing)?|release(?:s|d)?|announc(?:e|es|ed|ing)|"
    r"unveil(?:s|ed|ing)?|debut(?:s|ed|ing)?|introduc(?:e|es|ed|ing)|"
    r"ship(?:s|ped|ping)?|roll(?:s|ed)?[\-\s]out|"
    r"available|launching|now\s+out"
    r")\b",
    re.IGNORECASE,
)

# 中文動詞
_RELEASE_VERBS_ZH = re.compile(r"(發布|推出|釋出|發佈|上線|亮相|正式推出|開放使用|公開發表)")


_RESEARCH_SOURCES = ("arxiv", "papers with code", "paperswithcode", "huggingface papers")


def detect_model_release(title: str = "", summary: str = "", source_name: str = "") -> int:
    """回傳 1/0。標題或摘要同時命中（模型名 + 版本）+（釋出動詞）。
    研究源（arXiv/PWC/HF Papers）一律視為研究論文而非模型發布（排除）。
    """
    src = (source_name or "").lower()
    if any(r in src for r in _RESEARCH_SOURCES):
        return 0
    # 標題優先命中（發布新聞通常標題就寫「某某 X 發布」）
    if not any(p.search(title or "") for p in _MODEL_PATTERNS):
        return 0
    text = f"{title or ''}\n{summary or ''}"
    if _RELEASE_VERBS.search(text) or _RELEASE_VERBS_ZH.search(text):
        return 1
    return 0
