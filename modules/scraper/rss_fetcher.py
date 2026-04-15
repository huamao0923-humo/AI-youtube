"""RSS 爬蟲 — Module 1 核心。

功能：
  - 並行抓取 sources.yaml 中所有有 rss 欄位的來源
  - 對每篇文章計算本地粗分，過濾 exclude / filter_keywords
  - 去重後寫入 SQLite
  - 回傳統計資訊

執行：
  python -m modules.scraper.rss_fetcher --test     # 只抓，不寫 DB
  python -m modules.scraper.rss_fetcher            # 抓並寫 DB
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import feedparser
import httpx
from dateutil import parser as dtparser
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


def _parse_published(entry: Any) -> str | None:
    """從 feedparser entry 擷取發布時間並轉 ISO8601 UTC。"""
    for field in ("published", "updated", "created"):
        val = entry.get(field)
        if val:
            try:
                dt = dtparser.parse(val)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).isoformat()
            except (ValueError, TypeError):
                continue
    # feedparser 解析好的 struct_time
    for key in ("published_parsed", "updated_parsed"):
        if entry.get(key):
            try:
                dt = datetime(*entry[key][:6], tzinfo=timezone.utc)
                return dt.isoformat()
            except (ValueError, TypeError):
                continue
    return None


def _is_recent(published_iso: str | None, lookback_hours: int) -> bool:
    if not published_iso:
        return True  # 沒時間資訊視為保留（讓 Claude 決定）
    try:
        dt = dtparser.parse(published_iso)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        return dt >= cutoff
    except (ValueError, TypeError):
        return True


async def _fetch_feed(
    client: httpx.AsyncClient, source: dict[str, Any], cfg: dict[str, Any]
) -> list[dict[str, Any]]:
    url = source.get("rss")
    if not url:
        return []

    name = source["name"]
    max_retries = cfg["max_retries"]

    for attempt in range(1, max_retries + 1):
        try:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            content = resp.content
            break
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            if attempt == max_retries:
                logger.warning(f"[{name}] RSS 抓取失敗（{attempt}次重試後放棄）：{e}")
                return []
            await asyncio.sleep(2 * attempt)
    else:
        return []

    feed = feedparser.parse(content)
    if feed.bozo and not feed.entries:
        logger.warning(f"[{name}] feed 解析異常：{feed.bozo_exception}")
        return []

    items: list[dict[str, Any]] = []
    required_kw = source.get("filter_keywords")

    for entry in feed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue

        summary = (entry.get("summary") or entry.get("description") or "").strip()
        # 粗暴移除 HTML tag
        summary = _strip_html(summary)[:1000]

        full_text = f"{title}\n\n{summary}"

        if not keyword_filter_pass(full_text, required_kw):
            continue
        if has_exclude_keyword(full_text):
            continue

        published_iso = _parse_published(entry)
        if not _is_recent(published_iso, cfg["lookback_hours"]):
            continue

        items.append({
            "url": normalize_url(link),
            "title": title,
            "source_name": name,
            "source_priority": source.get("priority", 5),
            "published_at": published_iso,
            "summary": summary,
            "full_text": None,
            "local_score": local_score(full_text),
            "content_hash": content_hash(title, summary),
            "status": "new",
        })

    logger.info(f"[{name}] 抓到 {len(items)} 則有效新聞")
    return items


def _strip_html(text: str) -> str:
    import re
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _all_rss_sources() -> list[dict[str, Any]]:
    src = sources()
    out: list[dict[str, Any]] = []
    for group_name, group in src.items():
        if not isinstance(group, list):
            continue
        for item in group:
            method = item.get("scrape_method", "")
            if method in ("rss", "rss_or_scrape") and item.get("rss"):
                out.append(item)
    return out


async def run_async(write_db: bool = True) -> dict[str, Any]:
    cfg = settings()["scraper"]
    srcs = _all_rss_sources()
    logger.info(f"準備抓取 {len(srcs)} 個 RSS 來源")

    sem = asyncio.Semaphore(cfg["concurrent_requests"])
    timeout = httpx.Timeout(cfg["http_timeout_seconds"])
    headers = {"User-Agent": cfg["user_agent"]}

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:

        async def _bounded(src):
            async with sem:
                result = await _fetch_feed(client, src, cfg)
                await asyncio.sleep(cfg["per_source_delay_seconds"])
                return result

        results = await asyncio.gather(*[_bounded(s) for s in srcs], return_exceptions=True)

    all_items: list[dict[str, Any]] = []
    errors = 0
    for r in results:
        if isinstance(r, Exception):
            errors += 1
            logger.error(f"來源抓取例外：{r}")
            continue
        all_items.extend(r)

    logger.info(f"原始抓到 {len(all_items)} 則，開始去重")
    deduped = dedupe_in_memory(all_items)
    logger.info(f"去重後剩 {len(deduped)} 則")

    # 本地分數過濾
    min_score = settings()["filter"]["local_score_min"]
    filtered = [i for i in deduped if i["local_score"] >= min_score]
    logger.info(f"本地分數 >= {min_score} 後剩 {len(filtered)} 則")

    inserted = 0
    if write_db:
        db_manager.init_db()
        inserted = db_manager.insert_news_batch(filtered)
        logger.info(f"寫入 DB：新增 {inserted} 則（其餘為已存在）")

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
    ap.add_argument("--test", action="store_true", help="只抓，不寫入 DB")
    args = ap.parse_args()

    start = datetime.now()
    stats = asyncio.run(run_async(write_db=not args.test))
    elapsed = (datetime.now() - start).total_seconds()

    print("\n========= RSS 爬蟲結果 =========")
    for k, v in stats.items():
        print(f"  {k:<16} : {v}")
    print(f"  elapsed         : {elapsed:.1f}s")
    print("==================================")


if __name__ == "__main__":
    main()
