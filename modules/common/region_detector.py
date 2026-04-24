"""新聞地區偵測 — 判別是否為台灣新聞。

策略優先序：
  1. source.region（sources.yaml 裡標註的，最可靠）
  2. URL domain（.tw / .com.tw / .org.tw 等）
  3. 標題 + summary 關鍵字命中
  4. fallback → "global"

用於 Phase A 新聞分類持久化。純關鍵字 + domain，不呼叫 API。
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# 台灣域名 suffix（小寫比對）
_TW_DOMAIN_SUFFIXES = (".tw", ".com.tw", ".org.tw", ".gov.tw", ".edu.tw", ".net.tw")

# 台灣來源名稱白名單（比對 source_name）
_TW_SOURCE_NAMES = {
    "ithome", "i thome", "數位時代", "business next", "bnext",
    "科技新報", "technews", "inside", "硬塞", "聯合新聞網", "udn",
    "經濟日報", "中央社", "cna", "中時", "自由時報", "工商時報",
    "天下雜誌", "商周", "商業週刊", "蘋果日報",
}

# 文字關鍵字（中文 + 英文 Taiwan/TSMC 等）
_TW_KEYWORDS = [
    "台灣", "臺灣", "台北", "新竹", "台中", "高雄", "桃園",
    "台積電", "聯發科", "鴻海", "台達電", "華碩", "宏碁", "微星", "廣達",
    "台泥", "國泰", "富邦", "新光", "元大", "台新", "中信",
    "金管會", "科技部", "數位部", "通傳會", "NCC",
    "Taiwan", "TSMC", "MediaTek", "Foxconn", "ASUS", "Acer",
]


def _normalize_source_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _domain_is_tw(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return any(host.endswith(s) for s in _TW_DOMAIN_SUFFIXES)


def detect_region(news_item: dict[str, Any]) -> str:
    """回傳 "taiwan" 或 "global"。

    優先序：source.region > source_name 白名單 > domain > 關鍵字 > global
    """
    # 1. 來源直接標註
    src_region = (news_item.get("source_region") or "").strip().lower()
    if src_region in ("taiwan", "tw"):
        return "taiwan"
    if src_region == "global":
        return "global"

    # 2. source_name 白名單
    name = _normalize_source_name(news_item.get("source_name") or "")
    if name:
        for tw_name in _TW_SOURCE_NAMES:
            if tw_name in name:
                return "taiwan"

    # 3. URL domain
    if _domain_is_tw(news_item.get("url") or ""):
        return "taiwan"

    # 4. 關鍵字命中（門檻：任一台灣專屬詞）
    text = " ".join([
        news_item.get("title") or "",
        news_item.get("summary") or "",
        news_item.get("business_angle") or "",
    ])
    # 不直接 .lower()，中文不受大小寫影響；英文關鍵字另外 lower 比
    text_lower = text.lower()
    for kw in _TW_KEYWORDS:
        if kw.isascii():
            if kw.lower() in text_lower:
                return "taiwan"
        else:
            if kw in text:
                return "taiwan"

    return "global"


if __name__ == "__main__":
    cases = [
        {"title": "TSMC 2nm 量產進度", "source_name": "TechCrunch", "url": "https://tc.com/x"},
        {"title": "OpenAI 發表新模型", "source_name": "The Verge", "url": "https://theverge.com/x"},
        {"title": "AI 晶片新動向", "source_name": "iThome", "url": "https://www.ithome.com.tw/news/123"},
        {"title": "聯發科推出 Dimensity", "source_name": "Reuters", "url": "https://reuters.com/x"},
        {"title": "AI 政策白皮書", "source_region": "taiwan", "source_name": "數位部", "url": "https://moda.gov.tw"},
    ]
    for c in cases:
        print(f"{detect_region(c)}: {c['title']}")
