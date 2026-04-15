"""本地粗分：根據 keywords.yaml 規則對標題+摘要算分。

這是 Claude 評分前的過濾層，目的：
  1. 把明顯不相關的東西刷掉（省 API 費）
  2. 對 RSS 來源做關鍵字白名單（如 Ars Technica AI 過濾）
"""
from __future__ import annotations

import re
from typing import Iterable

from .config import keywords


def _tokenize(text: str) -> str:
    return (text or "").lower()


def keyword_filter_pass(text: str, required: list[str] | None) -> bool:
    """來源若設 filter_keywords，需有至少一個關鍵字出現才保留。"""
    if not required:
        return True
    low = _tokenize(text)
    return any(kw.lower() in low for kw in required)


def has_exclude_keyword(text: str) -> bool:
    low = _tokenize(text)
    return any(kw.lower() in low for kw in keywords().get("exclude_keywords", []))


def local_score(text: str) -> float:
    """對單一文字（標題+摘要）計算本地粗分。分數可為負（被扣）。"""
    if not text:
        return 0.0
    low = _tokenize(text)
    kw = keywords()
    score = 0.0

    high = kw.get("high_value_keywords", {})
    for k in high.get("tier1_score_5", []):
        if k.lower() in low:
            score += 5
    for k in high.get("tier2_score_3", []):
        if k.lower() in low:
            score += 3
    for k in high.get("tier3_score_1", []):
        if k.lower() in low:
            score += 1

    companies = kw.get("target_companies", {})
    for c in companies.get("score_5", []):
        if c.lower() in low:
            score += 5
    for c in companies.get("score_3", []):
        if c.lower() in low:
            score += 3
    for c in companies.get("score_2", []):
        if c.lower() in low:
            score += 2

    biz = kw.get("business_signals", {})
    for s in biz.get("score_3", []):
        if s.lower() in low:
            score += 3
    for s in biz.get("score_2", []):
        if s.lower() in low:
            score += 2

    for ex in kw.get("exclude_keywords", []):
        if ex.lower() in low:
            score -= 5

    return score
