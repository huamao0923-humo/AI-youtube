"""AI 相關性過濾 — 判定一則新聞是否為 AI 戰情室需要的內容。

三條件 OR 任一即算 AI：
  1. source_name 在 AI 專用源白名單（sources.yaml 的 tags 含 'ai_official'）
  2. category in ('ai_model', 'semiconductor')
  3. 標題/摘要命中 AI keyword regex（中英雙語）

使用：
    is_ai, matched_by = is_ai_related(news_dict, ai_source_whitelist={'OpenAI Blog', ...})
    # news_dict 需含 title / summary / source_name / category
"""
from __future__ import annotations

import re
from typing import Iterable

# 英文 AI keyword — 邊界匹配避免 'rain' 誤匹 'ai'
_AI_KW_EN = re.compile(
    r"\b("
    r"ai|a\.i\.|artificial intelligence|"
    r"llm|large language model|foundation model|"
    r"gpt|chatgpt|claude|gemini|llama|mistral|grok|phi|"
    r"openai|anthropic|deepmind|huggingface|hugging face|"
    r"transformer|rlhf|multimodal|generative|diffusion model|"
    r"agentic|copilot|neural network|machine learning|"
    r"stable diffusion|midjourney|sora"
    r")\b",
    re.IGNORECASE,
)

# 中文 AI keyword
_AI_KW_ZH = re.compile(
    r"(人工智慧|人工智能|大模型|大語言模型|生成式|生成 AI|"
    r"機器學習|深度學習|大型語言模型|通用人工智慧|神經網路)"
)

_AI_CATEGORIES = {"ai_model", "semiconductor"}


def is_ai_related(
    news: dict,
    ai_source_whitelist: Iterable[str] = (),
) -> tuple[int, str]:
    """判定是否為 AI 新聞。

    Args:
      news: dict，至少含 title / summary / source_name / category
      ai_source_whitelist: AI 專用源名稱集合（從 sources.yaml 有 'ai_official' tag 的源推導）

    Returns:
      (is_ai, matched_by)：is_ai ∈ {0,1}；matched_by 為命中原因（'whitelist' | 'category' | 'keyword' | ''）
    """
    source = (news.get("source_name") or "").strip()
    category = (news.get("category") or "").strip()
    title = news.get("title") or ""
    summary = news.get("summary") or ""

    # 規則 1：AI 專用源白名單
    if ai_source_whitelist and source in set(ai_source_whitelist):
        return 1, "whitelist"

    # 規則 2：既有分類已是 AI / 半導體
    if category in _AI_CATEGORIES:
        return 1, "category"

    # 規則 3：標題 / 摘要關鍵字命中
    text = f"{title}\n{summary}"
    if _AI_KW_EN.search(text) or _AI_KW_ZH.search(text):
        return 1, "keyword"

    return 0, ""


# sources.yaml 中被視為「AI 專用」的 section 名稱（整段白名單）
_AI_SECTIONS = {"official_blogs", "community_research", "ai_business_media"}


def load_ai_source_whitelist(sources_yaml_path: str) -> set[str]:
    """從 config/sources.yaml 萃取 AI 專用源名稱。

    兩種機制：
      1. 整段 section 白名單：_AI_SECTIONS 裡的 section 下所有條目都算 AI 源
      2. 顯式 tags：單一條目含 `tags: [ai_official]` 也算（適合例外補加）

    容錯：yaml 缺失或格式不符，回傳空 set（規則 1 跳過，其他兩條仍可過濾）。
    """
    try:
        import yaml
    except ImportError:
        return set()

    try:
        with open(sources_yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, Exception):
        return set()

    names: set[str] = set()

    # 機制 1：整段 section
    if isinstance(data, dict):
        for section, items in data.items():
            if section in _AI_SECTIONS and isinstance(items, list):
                for it in items:
                    if isinstance(it, dict) and isinstance(it.get("name"), str):
                        names.add(it["name"])

    # 機制 2：顯式 tags
    def _walk(obj):
        if isinstance(obj, dict):
            tags = obj.get("tags")
            name = obj.get("name")
            if isinstance(tags, list) and "ai_official" in tags and isinstance(name, str):
                names.add(name)
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v)

    _walk(data)
    return names
