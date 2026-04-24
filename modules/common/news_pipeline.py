"""新聞後處理 pipeline — 爬完 / 評分完後補分類與地區欄位。

設計：
  - 只處理 classified_at IS NULL 的 row（idempotent，可重複跑）
  - 先 classify_slug（category），再 detect_region（region）
  - 批次 commit，避免頻繁 flush

掛點：
  - daily_pipeline.py 爬完之後（在評分之前，或之後都可以；此 pipeline 與 AI 評分正交）
  - /api/scoring/import 匯入 Claude 評分結果之後（新增的 business_angle 能讓 classifier 更準）

執行：
  python -m modules.common.news_pipeline         # 跑一次
  python -m modules.common.news_pipeline --all   # 忽略 classified_at，全部重跑
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from loguru import logger

from pathlib import Path

from modules.ai_war_room.filter import is_ai_related, load_ai_source_whitelist
from modules.common.logging_setup import setup_logger
from modules.common.news_classifier import classify_slug
from modules.common.region_detector import detect_region
from modules.database.models import NewsItem, SessionLocal

_SOURCES_YAML = Path(__file__).resolve().parents[2] / "config" / "sources.yaml"
_AI_SOURCE_WHITELIST: set[str] | None = None


def _get_ai_whitelist() -> set[str]:
    global _AI_SOURCE_WHITELIST
    if _AI_SOURCE_WHITELIST is None:
        _AI_SOURCE_WHITELIST = load_ai_source_whitelist(str(_SOURCES_YAML))
    return _AI_SOURCE_WHITELIST

setup_logger()


def classify_and_persist(news_ids: list[int] | None = None,
                          force: bool = False, batch_size: int = 200) -> int:
    """為未分類的 NewsItem 寫入 category + region。

    - news_ids=None：自動選取 classified_at IS NULL 的 row
    - news_ids=[...]：只處理指定 id（force=True 會覆寫）
    - force=True：忽略 classified_at，整批重跑

    回傳：處理筆數
    """
    now = datetime.now(timezone.utc).isoformat()
    processed = 0

    session = SessionLocal()
    try:
        q = session.query(NewsItem)
        if news_ids:
            q = q.filter(NewsItem.id.in_(news_ids))
        elif not force:
            q = q.filter(NewsItem.classified_at.is_(None))
        # 按 id 順序批次處理
        # 用 last_id 游標而非 offset（offset 會因 commit 清掉前段而錯位）
        q = q.order_by(NewsItem.id.asc())
        last_id = 0

        while True:
            rows = q.filter(NewsItem.id > last_id).limit(batch_size).all()
            if not rows:
                break
            ai_whitelist = _get_ai_whitelist()
            for row in rows:
                item = {
                    "title": row.title,
                    "summary": row.summary,
                    "business_angle": row.business_angle,
                    "source_name": row.source_name,
                    "url": row.url,
                    # source_region 不在 NewsItem 上；detect_region 會退回其他路徑
                }
                row.category = classify_slug(item)
                row.region = detect_region(item)
                # AI 戰情室 filter（讀最新 category 再判斷，才吃得到 ai_model / semiconductor）
                item["category"] = row.category
                is_ai, _matched = is_ai_related(item, ai_whitelist)
                row.is_ai = is_ai
                row.classified_at = now
                processed += 1
            last_id = rows[-1].id
            session.commit()
            logger.info(f"分類進度：累計 {processed} 筆寫入 (last_id={last_id})")

        logger.info(f"[分類完成] 共處理 {processed} 筆新聞")
        return processed
    finally:
        session.close()


def _summary_stats() -> dict[str, int]:
    """檢視目前 DB 的分類分佈（debug 用）。"""
    session = SessionLocal()
    try:
        total = session.query(NewsItem).count()
        unclassified = session.query(NewsItem).filter(NewsItem.classified_at.is_(None)).count()
        tw = session.query(NewsItem).filter(NewsItem.region == "taiwan").count()
        glob = session.query(NewsItem).filter(NewsItem.region == "global").count()
        return {"total": total, "unclassified": unclassified, "taiwan": tw, "global": glob}
    finally:
        session.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="忽略 classified_at，全部重跑")
    ap.add_argument("--stats", action="store_true", help="只看統計")
    args = ap.parse_args()

    if args.stats:
        s = _summary_stats()
        print(f"總計 {s['total']} 筆 | 未分類 {s['unclassified']} | 台灣 {s['taiwan']} | 全球 {s['global']}")
        return

    n = classify_and_persist(force=args.all)
    print(f"[OK] 分類 {n} 筆")
    s = _summary_stats()
    print(f"當前分佈：台灣 {s['taiwan']} / 全球 {s['global']} / 未分類 {s['unclassified']}")


if __name__ == "__main__":
    main()
