"""一鍵執行所有爬蟲（RSS + Web + HN/Reddit）。

執行：
  python -m modules.scraper.fetch_all          # 抓並寫 DB
  python -m modules.scraper.fetch_all --test   # 只抓，不寫 DB
  python -m modules.scraper.fetch_all --score  # 抓完後續跑 Claude 評分
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

from loguru import logger

from modules.common.logging_setup import setup_logger
from modules.scraper import hn_reddit_fetcher, rss_fetcher, web_scraper

setup_logger()


async def run_all(write_db: bool) -> dict:
    logger.info("====== 開始全來源抓取 ======")
    results = await asyncio.gather(
        rss_fetcher.run_async(write_db=write_db),
        web_scraper.run_async(write_db=write_db),
        hn_reddit_fetcher.run_async(write_db=write_db),
        return_exceptions=True,
    )

    combined = {
        "rss": results[0] if not isinstance(results[0], Exception) else {"error": str(results[0])},
        "web": results[1] if not isinstance(results[1], Exception) else {"error": str(results[1])},
        "api": results[2] if not isinstance(results[2], Exception) else {"error": str(results[2])},
    }
    total_inserted = sum(
        v.get("inserted", 0) for v in combined.values() if isinstance(v, dict)
    )
    combined["total_inserted"] = total_inserted
    return combined


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="只抓，不寫 DB")
    ap.add_argument("--score", action="store_true", help="抓完後用 Claude 評分")
    args = ap.parse_args()

    start = datetime.now()
    stats = asyncio.run(run_all(write_db=not args.test))
    elapsed = (datetime.now() - start).total_seconds()

    print("\n============ 全來源抓取總結 ============")
    for group, data in stats.items():
        if isinstance(data, dict) and "error" not in data:
            print(f"\n[{group.upper()}]")
            for k, v in data.items():
                print(f"  {k:<16} : {v}")
        else:
            print(f"\n[{group.upper()}] {data}")
    print(f"\n  elapsed: {elapsed:.1f}s")
    print("========================================\n")

    if args.score and not args.test:
        print(">>> 開始 Claude 評分...")
        from modules.filter import scorer
        scorer.run()


if __name__ == "__main__":
    main()
