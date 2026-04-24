"""主題自動聚類 — 把相關新聞綁成 Topic row（持久化版本）。

復用 brief_generator.build_entity_components 的 BFS 邏輯，
但把結果寫進 `topics` 表 + 更新 NewsItem.topic_id。

執行：
  python -m modules.brief.topic_clusterer          # 今日
  python -m modules.brief.topic_clusterer --date 2026-04-18
  python -m modules.brief.topic_clusterer --min-score 5  # 降門檻
"""
from __future__ import annotations

import argparse
import math
from collections import Counter
from datetime import datetime, timezone

from loguru import logger

from modules.brief.brief_generator import build_entity_components
from modules.common.config import settings
from modules.common.logging_setup import setup_logger
from modules.common.utils import build_slug, tw_today
from modules.database import db_manager
from modules.database.models import NewsItem, Topic, SessionLocal

setup_logger()


def _pick_category(members: list[dict]) -> str | None:
    """多數決：成員最多的 category 當主題 category。"""
    cats = [m.get("category") for m in members if m.get("category")]
    if not cats:
        return None
    return Counter(cats).most_common(1)[0][0]


def _pick_region(members: list[dict]) -> str:
    regions = {m.get("region") or "global" for m in members}
    if regions == {"taiwan"}:
        return "taiwan"
    if regions == {"global"}:
        return "global"
    return "mixed"


def _pick_top(members: list[dict]) -> dict:
    return max(members, key=lambda m: (m.get("ai_score") or 0))


def _pick_title(members: list[dict]) -> str:
    """以最高分那則 suggested_title 或 title 為主題標題。"""
    top = _pick_top(members)
    return (top.get("suggested_title") or top.get("title") or "").strip()[:200]


def _aggregate_score(members: list[dict]) -> float:
    """max(ai_score) + log1p(count) 作為熱度指標。"""
    max_score = max((m.get("ai_score") or 0) for m in members)
    return round(max_score + math.log1p(len(members)), 3)


def cluster_and_persist(date: str | None = None,
                         min_score: float | None = None,
                         limit: int = 100) -> dict[str, int]:
    """對指定日期（或今日）的候選新聞做聚類並寫入 Topic 表。

    - 若成員已有 topic_id → 優先加入該 Topic（群內新聞合併）
    - 否則建立新 Topic
    - 更新 NewsItem.topic_id 與 Topic 統計
    """
    target_date = date or tw_today()
    if min_score is None:
        min_score = float(settings().get("filter", {}).get("ai_score_min", 6))

    items = db_manager.fetch_candidates(
        min_score=min_score, limit=limit, fetched_date=target_date,
    )
    if not items:
        logger.info(f"[{target_date}] 無候選新聞，跳過聚類")
        return {"topics_created": 0, "topics_updated": 0, "news_attached": 0}

    logger.info(f"[{target_date}] 候選 {len(items)} 則，開始聚類…")
    components = build_entity_components(items)
    logger.info(f"分群結果：{len(components)} 個群組")

    id_to_item = {item["id"]: item for item in items}
    topics_created = 0
    topics_updated = 0
    news_attached = 0

    for comp in components:
        members = [id_to_item[nid] for nid in comp]
        if not members:
            continue

        # 檢查成員是否已有 topic_id → 用現有 Topic
        existing_topic_ids = {m.get("topic_id") for m in members if m.get("topic_id")}

        if len(existing_topic_ids) == 1:
            # 所有已歸屬的都指到同一個 Topic → 加入它
            topic_id = existing_topic_ids.pop()
        elif len(existing_topic_ids) > 1:
            # 多個既有主題 → 合併到 aggregate_score 最高的（其他 archive）
            targets = sorted(existing_topic_ids)
            topic_rows = [db_manager.get_topic(t) for t in targets if t]
            topic_rows = [t for t in topic_rows if t]
            if not topic_rows:
                topic_id = None
            else:
                target = max(topic_rows, key=lambda t: t.get("aggregate_score") or 0)
                topic_id = target["id"]
                other_ids = [t["id"] for t in topic_rows if t["id"] != topic_id]
                if other_ids:
                    db_manager.merge_topics(other_ids, topic_id)
                    logger.info(f"合併主題 {other_ids} → {topic_id}")
        else:
            topic_id = None

        if topic_id is None:
            # 建立新 Topic
            title = _pick_title(members)
            if not title:
                continue
            slug_base = build_slug(title, date=target_date)
            slug = slug_base
            suffix = 1
            while db_manager.get_topic_by_slug(slug):
                suffix += 1
                slug = f"{slug_base}-{suffix}"
            topic_id = db_manager.create_topic(
                slug=slug,
                title=title,
                category=_pick_category(members),
                region=_pick_region(members),
                first_seen_date=target_date,
                last_seen_date=target_date,
                news_count=len(members),
                top_news_id=_pick_top(members)["id"],
                aggregate_score=_aggregate_score(members),
                status="open",
                auto_created=1,
            )
            topics_created += 1
            logger.info(f"新主題 #{topic_id}：{title[:50]} ({len(members)} 則)")
        else:
            # 更新既有 Topic 的元資料
            db_manager.update_topic(
                topic_id,
                last_seen_date=target_date,
                category=_pick_category(members),
                region=_pick_region(members),
                top_news_id=_pick_top(members)["id"],
                aggregate_score=_aggregate_score(members),
            )
            topics_updated += 1

        # 綁定所有成員
        news_ids = [m["id"] for m in members]
        changed = db_manager.attach_news_to_topic(news_ids, topic_id)
        news_attached += changed

    summary = {
        "topics_created": topics_created,
        "topics_updated": topics_updated,
        "news_attached": news_attached,
    }
    logger.info(f"聚類完成：{summary}")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=str, default=None, help="YYYY-MM-DD，預設今日")
    ap.add_argument("--min-score", type=float, default=None, help="候選門檻")
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args()

    r = cluster_and_persist(date=args.date, min_score=args.min_score, limit=args.limit)
    print(f"[OK] 新主題 {r['topics_created']}，更新 {r['topics_updated']}，綁定 {r['news_attached']} 則新聞")


if __name__ == "__main__":
    main()
