"""針對官方部落格的專用解析器。

每個函式 signature: (html: str, base_url: str) -> list[dict]
每筆 dict 必含 title, url；可選 summary, published_at。

註：這些站的 HTML 結構會隨時間變化 —— 如果某個站點解析失敗，
web_scraper 會自動 fallback 到通用 _extract_article_links。
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup


def _absolute(base: str, href: str) -> str:
    return urljoin(base, href) if href else href


def parse_openai_blog(html: str, base_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    for a in soup.select('a[href*="/index/"], a[href*="/blog/"]'):
        href = a.get("href", "")
        if not href:
            continue
        # 標題通常在 a 裡的 h3/h2/span
        title_el = a.find(["h1", "h2", "h3", "h4", "span"]) or a
        title = title_el.get_text(" ", strip=True)
        if not title or len(title) < 10:
            continue
        items.append({
            "title": title[:300],
            "url": _absolute(base_url, href),
            "summary": "",
            "source_name": "OpenAI Blog",
        })
    return _dedupe(items)


def parse_anthropic_news(html: str, base_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    for a in soup.select('a[href*="/news/"]'):
        href = a.get("href", "")
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 10:
            continue
        items.append({
            "title": title[:300],
            "url": _absolute(base_url, href),
            "summary": "",
            "source_name": "Anthropic News",
        })
    return _dedupe(items)


def parse_mistral_news(html: str, base_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    for a in soup.select('a[href*="/news/"]'):
        href = a.get("href", "")
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 10:
            continue
        items.append({
            "title": title[:300],
            "url": _absolute(base_url, href),
            "summary": "",
            "source_name": "Mistral AI News",
        })
    return _dedupe(items)


def parse_xai_news(html: str, base_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    for a in soup.select('a[href*="/news/"]'):
        href = a.get("href", "")
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 10:
            continue
        items.append({
            "title": title[:300],
            "url": _absolute(base_url, href),
            "summary": "",
            "source_name": "xAI News",
        })
    return _dedupe(items)


def parse_stability_news(html: str, base_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    for a in soup.select('a[href*="/news/"]'):
        href = a.get("href", "")
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 10:
            continue
        items.append({
            "title": title[:300],
            "url": _absolute(base_url, href),
            "summary": "",
            "source_name": "Stability AI Blog",
        })
    return _dedupe(items)


def parse_perplexity_hub(html: str, base_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    for a in soup.select('a[href*="/hub/blog/"]'):
        href = a.get("href", "")
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 10:
            continue
        items.append({
            "title": title[:300],
            "url": _absolute(base_url, href),
            "summary": "",
            "source_name": "Perplexity AI Blog",
        })
    return _dedupe(items)


def parse_scale_blog(html: str, base_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    for a in soup.select('a[href*="/blog/"]'):
        href = a.get("href", "")
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 10:
            continue
        items.append({
            "title": title[:300],
            "url": _absolute(base_url, href),
            "summary": "",
            "source_name": "Scale AI Blog",
        })
    return _dedupe(items)


def parse_runway_research(html: str, base_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    for a in soup.select('a[href*="/research/"]'):
        href = a.get("href", "")
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 10:
            continue
        items.append({
            "title": title[:300],
            "url": _absolute(base_url, href),
            "summary": "",
            "source_name": "Runway Research",
        })
    return _dedupe(items)


def parse_theinformation_ai(html: str, base_url: str) -> list[dict[str, Any]]:
    """The Information 有 paywall，只抓標題。"""
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    for a in soup.select('a[href*="/articles/"]'):
        href = a.get("href", "")
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 15:
            continue
        items.append({
            "title": title[:300],
            "url": _absolute(base_url, href),
            "summary": "(paywall — headline only)",
            "source_name": "The Information AI",
        })
    return _dedupe(items)


def parse_paperswithcode(html: str, base_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    for a in soup.select('a[href*="/paper/"]'):
        href = a.get("href", "")
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 10:
            continue
        items.append({
            "title": title[:300],
            "url": _absolute(base_url, href),
            "summary": "",
            "source_name": "Papers With Code Latest",
        })
    return _dedupe(items)


def parse_pitchbook_ai(html: str, base_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    for a in soup.select("a[href*='/news/articles/']"):
        href = a.get("href", "")
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 10:
            continue
        items.append({
            "title": title[:300],
            "url": _absolute(base_url, href),
            "summary": "",
            "source_name": "PitchBook AI deals",
        })
    return _dedupe(items)


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for it in items:
        key = it.get("url", "")
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


# 對應 sources.yaml 的 name 欄位
PARSERS = {
    "OpenAI Blog": parse_openai_blog,
    "Anthropic News": parse_anthropic_news,
    "Mistral AI News": parse_mistral_news,
    "xAI News": parse_xai_news,
    "Stability AI Blog": parse_stability_news,
    "Perplexity AI Blog": parse_perplexity_hub,
    "Scale AI Blog": parse_scale_blog,
    "Runway Research": parse_runway_research,
    "The Information AI": parse_theinformation_ai,
    "Papers With Code Latest": parse_paperswithcode,
    "PitchBook AI deals": parse_pitchbook_ai,
}
