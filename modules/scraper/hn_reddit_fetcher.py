"""Hacker News + Reddit JSON API 爬蟲。

- HN：Firebase public API（無需認證）
- Reddit：公開 .json 端點（需設 User-Agent，避免 429）
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx
from loguru import logger

from modules.common.config import settings, sources
from modules.common.logging_setup import setup_logger
from modules.common.scoring import (
    has_exclude_keyword,
    keyword_filter_pass,
    local_score,
)
from modules.database import db_manager
from modules.filter.deduplicator import content_hash, dedupe_in_memory, normalize_url

setup_logger()

HN_TOP = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{id}.json"

# 只抓提到 AI/LLM 相關關鍵字的 HN 頭條
_HN_AI_KEYWORDS = [
    "ai", "artificial intelligence", "gpt", "llm", "claude",
    "openai", "anthropic", "gemini", "mistral", "transformer",
    "neural", "deep learning", "machine learning",
]


async def _fetch_hn(client: httpx.AsyncClient, source: dict[str, Any]) -> list[dict[str, Any]]:
    name = source["name"]
    min_score = source.get("min_score", 100)
    try:
        resp = await client.get(HN_TOP)
        resp.raise_for_status()
        top_ids = resp.json()[:100]
    except httpx.HTTPError as e:
        logger.warning(f"[{name}] 取頂部清單失敗：{e}")
        return []

    async def _get_item(item_id: int) -> dict | None:
        try:
            r = await client.get(HN_ITEM.format(id=item_id))
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError:
            return None

    items_raw = await asyncio.gather(*[_get_item(i) for i in top_ids])
    kept: list[dict[str, Any]] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for it in items_raw:
        if not it:
            continue
        if it.get("type") != "story":
            continue
        if it.get("score", 0) < min_score:
            continue
        title = it.get("title", "")
        url = it.get("url") or f"https://news.ycombinator.com/item?id={it.get('id')}"
        low = title.lower()
        if not any(k in low for k in _HN_AI_KEYWORDS):
            continue
        if has_exclude_keyword(title):
            continue

        published_iso = None
        if it.get("time"):
            published_iso = datetime.fromtimestamp(it["time"], tz=timezone.utc).isoformat()

        kept.append({
            "url": normalize_url(url),
            "title": title,
            "source_name": name,
            "source_priority": source.get("priority", 8),
            "published_at": published_iso or now_iso,
            "summary": f"HN points: {it.get('score', 0)} | comments: {it.get('descendants', 0)}",
            "full_text": None,
            "local_score": local_score(title) + min(it.get("score", 0) / 100, 5),
            "content_hash": content_hash(title),
            "status": "new",
        })

    logger.info(f"[{name}] HN AI 頭條 {len(kept)} 則")
    return kept


async def _fetch_reddit(client: httpx.AsyncClient, source: dict[str, Any]) -> list[dict[str, Any]]:
    name = source["name"]
    url = source["url"]
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"[{name}] Reddit API 失敗：{e}")
        return []

    posts = data.get("data", {}).get("children", [])
    kept: list[dict[str, Any]] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for post in posts:
        d = post.get("data", {})
        title = d.get("title", "")
        url = d.get("url_overridden_by_dest") or d.get("url") or \
              f"https://reddit.com{d.get('permalink', '')}"
        selftext = (d.get("selftext") or "")[:500]
        combined = f"{title}\n\n{selftext}"

        if has_exclude_keyword(combined):
            continue
        if d.get("score", 0) < 50:
            continue

        published_iso = None
        if d.get("created_utc"):
            published_iso = datetime.fromtimestamp(d["created_utc"], tz=timezone.utc).isoformat()

        kept.append({
            "url": normalize_url(url),
            "title": title,
            "source_name": name,
            "source_priority": source.get("priority", 7),
            "published_at": published_iso or now_iso,
            "summary": f"↑{d.get('score', 0)} 💬{d.get('num_comments', 0)} — {selftext}",
            "full_text": None,
            "local_score": local_score(combined) + min(d.get("score", 0) / 200, 3),
            "content_hash": content_hash(title, selftext),
            "status": "new",
        })

    logger.info(f"[{name}] Reddit {len(kept)} 則")
    return kept


def _api_sources() -> list[dict[str, Any]]:
    src = sources()
    out: list[dict[str, Any]] = []
    for group in src.values():
        if not isinstance(group, list):
            continue
        for item in group:
            if item.get("scrape_method") in ("hn_api", "reddit_api"):
                out.append(item)
    return out


async def run_async(write_db: bool = True) -> dict[str, Any]:
    cfg = settings()["scraper"]
    srcs = _api_sources()
    logger.info(f"準備 API 爬 {len(srcs)} 個來源（HN/Reddit）")

    timeout = httpx.Timeout(cfg["http_timeout_seconds"])
    headers = {"User-Agent": cfg["user_agent"]}

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        tasks = []
        for s in srcs:
            if s["scrape_method"] == "hn_api":
                tasks.append(_fetch_hn(client, s))
            elif s["scrape_method"] == "reddit_api":
                tasks.append(_fetch_reddit(client, s))
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_items: list[dict[str, Any]] = []
    errors = 0
    for r in results:
        if isinstance(r, Exception):
            errors += 1
            logger.error(f"API 爬例外：{r}")
            continue
        all_items.extend(r)

    deduped = dedupe_in_memory(all_items)
    min_score = settings()["filter"]["local_score_min"]
    filtered = [i for i in deduped if i["local_score"] >= min_score]

    inserted = 0
    if write_db:
        db_manager.init_db()
        inserted = db_manager.insert_news_batch(filtered)

    return {
        "sources_total": len(srcs),
        "sources_failed": errors,
        "raw_items": len(all_items),
        "deduped": len(deduped),
        "score_passed": len(filtered),
        "inserted": inserted,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args()
    stats = asyncio.run(run_async(write_db=not args.test))
    print("\n========= HN/Reddit 結果 =========")
    for k, v in stats.items():
        print(f"  {k:<16} : {v}")
    print("===================================")


if __name__ == "__main__":
    main()
