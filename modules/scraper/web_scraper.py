"""通用 Web 爬蟲 — 針對沒有 RSS 的來源。

使用 httpx + BeautifulSoup。遇到純 JS 渲染的站才需要 playwright（目前預留入口）。
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup
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


async def _fetch_html(client: httpx.AsyncClient, url: str, retries: int) -> str | None:
    for attempt in range(1, retries + 1):
        try:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            return resp.text
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            if attempt == retries:
                logger.warning(f"GET 失敗：{url} — {e}")
                return None
            await asyncio.sleep(2 * attempt)
    return None


def _extract_article_links(html: str, base_url: str, source_name: str) -> list[dict[str, Any]]:
    """通用擷取邏輯：找所有 <a> 標籤中看起來像文章的連結。

    針對特定站點（OpenAI、Anthropic、Mistral 等）我們在 official_blog_scraper.py
    用專用選擇器；這裡是通用後備。
    """
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 15:
            continue
        if href.startswith("#") or href.startswith("mailto:"):
            continue
        if href.startswith("/"):
            from urllib.parse import urljoin
            href = urljoin(base_url, href)
        if not href.startswith("http"):
            continue
        if href in seen:
            continue
        seen.add(href)
        items.append({
            "url": href,
            "title": title[:300],
            "source_name": source_name,
            "summary": "",
        })
    return items


async def _scrape_one(
    client: httpx.AsyncClient, source: dict[str, Any], cfg: dict[str, Any]
) -> list[dict[str, Any]]:
    name = source["name"]
    url = source.get("url")
    if not url:
        return []

    html = await _fetch_html(client, url, cfg["max_retries"])
    if not html:
        return []

    # 優先用專用解析器
    from modules.scraper.official_blog_scraper import PARSERS
    parser = PARSERS.get(name)
    raw_items: list[dict[str, Any]]
    if parser:
        try:
            raw_items = parser(html, url)
        except Exception as e:
            logger.warning(f"[{name}] 專用解析器失敗，退回通用：{e}")
            raw_items = _extract_article_links(html, url, name)
    else:
        raw_items = _extract_article_links(html, url, name)

    required_kw = source.get("filter_keywords")
    now = datetime.now(timezone.utc).isoformat()
    kept: list[dict[str, Any]] = []

    for it in raw_items:
        title = it.get("title", "").strip()
        summary = (it.get("summary") or "").strip()
        combined = f"{title}\n\n{summary}"

        if not keyword_filter_pass(combined, required_kw):
            continue
        if has_exclude_keyword(combined):
            continue

        kept.append({
            "url": normalize_url(it["url"]),
            "title": title,
            "source_name": name,
            "source_priority": source.get("priority", 5),
            "published_at": it.get("published_at") or now,
            "summary": summary,
            "full_text": None,
            "local_score": local_score(combined),
            "content_hash": content_hash(title, summary),
            "status": "new",
        })

    logger.info(f"[{name}] 通用爬蟲抓到 {len(kept)} 則")
    return kept


def _all_scrape_sources() -> list[dict[str, Any]]:
    src = sources()
    out: list[dict[str, Any]] = []
    for group_name, group in src.items():
        if not isinstance(group, list):
            continue
        for item in group:
            method = item.get("scrape_method", "")
            # rss_or_scrape 由 rss_fetcher 處理 rss 端；這裡只處理純 scrape
            if method == "scrape":
                out.append(item)
    return out


async def run_async(write_db: bool = True) -> dict[str, Any]:
    cfg = settings()["scraper"]
    srcs = _all_scrape_sources()
    logger.info(f"準備 Web 爬 {len(srcs)} 個來源（無 RSS）")

    sem = asyncio.Semaphore(cfg["concurrent_requests"])
    timeout = httpx.Timeout(cfg["http_timeout_seconds"])
    headers = {"User-Agent": cfg["user_agent"]}

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:

        async def _bounded(src):
            async with sem:
                result = await _scrape_one(client, src, cfg)
                await asyncio.sleep(cfg["per_source_delay_seconds"])
                return result

        results = await asyncio.gather(*[_bounded(s) for s in srcs], return_exceptions=True)

    all_items: list[dict[str, Any]] = []
    errors = 0
    for r in results:
        if isinstance(r, Exception):
            errors += 1
            logger.error(f"Web 爬例外：{r}")
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
    print("\n========= Web 爬蟲結果 =========")
    for k, v in stats.items():
        print(f"  {k:<16} : {v}")
    print("==================================")


if __name__ == "__main__":
    main()
