"""新聞去重：URL 正規化 + 標題相似度 + 內容 hash。"""
from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from urllib.parse import urlparse, urlunparse

_TRACKING_PARAMS = re.compile(r"[?&](utm_[^=&]+|fbclid|gclid|ref|ref_src)=[^&]*")


def normalize_url(url: str) -> str:
    """移除 tracking 參數、統一協定、移除結尾斜線。"""
    if not url:
        return ""
    url = _TRACKING_PARAMS.sub("", url).rstrip("?&")
    parsed = urlparse(url)
    scheme = "https"
    netloc = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def content_hash(title: str, summary: str = "") -> str:
    """對標題（正規化後）+ 摘要前 200 字做 SHA1。"""
    norm_title = re.sub(r"\s+", " ", (title or "").lower()).strip()
    norm_sum = re.sub(r"\s+", " ", (summary or "").lower()).strip()[:200]
    blob = f"{norm_title}||{norm_sum}".encode("utf-8")
    return hashlib.sha1(blob).hexdigest()


def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def dedupe_in_memory(items: list[dict]) -> list[dict]:
    """同一批次內去重：URL 完全相同、或標題相似度 >= 0.9 視為重複。"""
    seen_urls: set[str] = set()
    kept: list[dict] = []
    for it in items:
        url = normalize_url(it.get("url", ""))
        if not url or url in seen_urls:
            continue
        is_dup = False
        for kept_it in kept:
            if title_similarity(it.get("title", ""), kept_it.get("title", "")) >= 0.9:
                is_dup = True
                break
        if is_dup:
            continue
        it["url"] = url
        it["content_hash"] = content_hash(it.get("title", ""), it.get("summary", ""))
        seen_urls.add(url)
        kept.append(it)
    return kept
