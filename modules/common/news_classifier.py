"""新聞分類與去重邏輯。"""
from __future__ import annotations

from typing import Any

CATEGORIES = {
    "AI模型/研究": ["gpt", "claude", "gemini", "llm", "大型語言模型", "生成式", "訓練", "模型"],
    "商業/投資": ["融資", "ipo", "收購", "投資", "估值", "營收", "獲利", "股價"],
    "政策/監管": ["監管", "法規", "禁令", "政策", "歐盟", "立法", "合規"],
    "產品/服務": ["發布", "推出", "上線", "產品", "服務", "功能", "版本"],
    "半導體/硬體": ["晶片", "gpu", "nvidia", "台積電", "半導體", "硬體", "算力"],
    "其他": [],
}

# 中文顯示名稱 ↔ 英文 slug（給 DB 存儲與 API 使用）
CATEGORY_SLUGS = {
    "AI模型/研究": "ai_model",
    "商業/投資": "business",
    "政策/監管": "policy",
    "產品/服務": "product",
    "半導體/硬體": "semiconductor",
    "其他": "other",
}
SLUG_TO_DISPLAY = {v: k for k, v in CATEGORY_SLUGS.items()}


def classify(news_item: dict[str, Any]) -> str:
    """依 title + business_angle 的關鍵字分類，找不到則分類為其他。"""
    text = (news_item.get("title") or "") + " " + (news_item.get("business_angle") or "")
    text = text.lower()

    for category, keywords in CATEGORIES.items():
        if category == "其他":
            continue
        if any(kw.lower() in text for kw in keywords):
            return category
    return "其他"


def classify_slug(news_item: dict[str, Any]) -> str:
    """回傳英文 slug（給 DB 存、API 用）。"""
    return CATEGORY_SLUGS[classify(news_item)]


def display_name(slug: str) -> str:
    """slug → 中文顯示名稱。"""
    return SLUG_TO_DISPLAY.get(slug, "其他")


def _extract_key_words(title: str) -> set[str]:
    """簡單的中英文詞彙提取（≥2 字元的詞彙）。"""
    words = set()
    i = 0
    while i < len(title):
        if title[i].isascii():
            j = i
            while j < len(title) and title[j].isascii() and not title[j].isspace():
                j += 1
            word = title[i:j].lower()
            if len(word) >= 2:
                words.add(word)
            i = j
        else:
            j = i + 1
            while j < len(title) and not title[j].isascii() and not title[j].isspace():
                j += 1
            word = title[i:j]
            if len(word) >= 2:
                words.add(word)
            i = j
        while i < len(title) and title[i].isspace():
            i += 1
    return words


def deduplicate_groups(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """在同一分類內標記重複。同類別內若兩則 title 共享 ≥3 個有意義詞彙，後者標記 is_duplicate=True。"""
    result = []
    seen_per_category = {}

    for item in items:
        cat = classify(item)
        if cat not in seen_per_category:
            seen_per_category[cat] = []

        item_copy = dict(item)
        item_copy["is_duplicate"] = False

        title = item.get("title") or ""
        words = _extract_key_words(title)

        is_dup = False
        for prev_item in seen_per_category[cat]:
            prev_title = prev_item.get("title") or ""
            prev_words = _extract_key_words(prev_title)
            common = len(words & prev_words)
            if common >= 3:
                is_dup = True
                break

        if is_dup:
            item_copy["is_duplicate"] = True

        seen_per_category[cat].append(item_copy)
        result.append(item_copy)

    return result


def categorize_all(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """分類並去重，回傳 {category_name: [items]} dict。"""
    deduped = deduplicate_groups(items)
    grouped = {}

    for item in deduped:
        cat = classify(item)
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append(item)

    return grouped
