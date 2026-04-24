"""Topic 複合熱度指數 — worldmonitor 風格儀表板的核心指標。

公式：
  heat = ai_score_avg × log2(news_count + 1) × source_auth_avg × time_decay
  time_decay      = 0.5 ** (hours_since_latest_news / 48)   # 半衰期 48h
  source_auth_avg = mean(source_priority / 10) over topic's news_items

執行：
  python -m modules.brief.heat_calculator            # 今日 refresh
  python -m modules.brief.heat_calculator --dry-run  # 只印結果不寫入

由 daily_pipeline.step_brief() 於 brief 生成後自動呼叫。
"""
from __future__ import annotations

import argparse
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger

from modules.common.logging_setup import setup_logger
from modules.common.utils import tw_isonow, tw_now, tw_today
from modules.database.models import (
    NewsItem, Topic, TopicHeatSnapshot, SessionLocal, init_db
)

setup_logger()

HALF_LIFE_HOURS = 48.0
SNAPSHOT_RETENTION_DAYS = 7


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        s = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _latest_news_time(news_items: list[NewsItem]) -> datetime | None:
    """取該 topic 最新一則新聞時間。優先 published_at，fallback fetched_at。"""
    best = None
    for n in news_items:
        for candidate in (n.published_at, n.fetched_at):
            dt = _parse_iso(candidate)
            if dt and (best is None or dt > best):
                best = dt
                break
    return best


def compute_topic_heat(news_items: list[NewsItem], *,
                       now: datetime | None = None) -> dict[str, float]:
    """對一組 news_items 計算熱度。

    回傳 {heat, ai_score_avg, news_count, source_auth_avg, time_decay, hours_since_latest}
    """
    if not news_items:
        return {
            "heat": 0.0, "ai_score_avg": 0.0, "news_count": 0,
            "source_auth_avg": 0.0, "time_decay": 0.0, "hours_since_latest": None,
        }

    scored = [n for n in news_items if n.ai_score is not None]
    if scored:
        ai_score_avg = sum(n.ai_score for n in scored) / len(scored)
    else:
        ai_score_avg = 0.0

    source_auth_avg = sum(
        max(1, min(10, n.source_priority or 5)) / 10.0 for n in news_items
    ) / len(news_items)

    news_count = len(news_items)

    now = now or datetime.now(timezone.utc)
    latest = _latest_news_time(news_items)
    if latest is None:
        hours_since = None
        time_decay = 0.5  # 無時間資訊 → 中性衰減
    else:
        hours_since = max(0.0, (now - latest).total_seconds() / 3600.0)
        time_decay = 0.5 ** (hours_since / HALF_LIFE_HOURS)

    heat = ai_score_avg * math.log2(news_count + 1) * source_auth_avg * time_decay

    return {
        "heat": round(heat, 4),
        "ai_score_avg": round(ai_score_avg, 3),
        "news_count": news_count,
        "source_auth_avg": round(source_auth_avg, 3),
        "time_decay": round(time_decay, 4),
        "hours_since_latest": round(hours_since, 2) if hours_since is not None else None,
    }


def refresh_all(*, dry_run: bool = False) -> dict[str, Any]:
    """重算所有 open/used 主題的熱度，寫入 Topic.heat_* 與 TopicHeatSnapshot。

    - heat_prev 取「前一日 snapshot」的 heat_index（找不到時用目前 heat_index）
    - 當日 snapshot 以 (topic_id, date) upsert，一天一列
    - 超過 SNAPSHOT_RETENTION_DAYS 的舊 snapshot 會被清除
    """
    today = tw_today()
    now_iso = tw_isonow()
    now_utc = datetime.now(timezone.utc)

    stats = {"topics_refreshed": 0, "snapshots_written": 0,
             "snapshots_pruned": 0, "date": today}

    with SessionLocal() as s:
        topics = s.query(Topic).filter(Topic.status.in_(["open", "used"])).all()

        for t in topics:
            news_items = s.query(NewsItem).filter(NewsItem.topic_id == t.id).all()
            metrics = compute_topic_heat(news_items, now=now_utc)
            new_heat = metrics["heat"]

            # heat_prev 來源：前一日（今日之前最近）snapshot
            prev_snap = (
                s.query(TopicHeatSnapshot)
                .filter(TopicHeatSnapshot.topic_id == t.id,
                        TopicHeatSnapshot.date < today)
                .order_by(TopicHeatSnapshot.date.desc())
                .first()
            )
            prev_heat = prev_snap.heat_index if prev_snap else (t.heat_index or 0.0)

            if not dry_run:
                t.heat_prev = prev_heat
                t.heat_index = new_heat
                t.heat_updated_at = now_iso

                # Upsert 今日 snapshot
                snap = (
                    s.query(TopicHeatSnapshot)
                    .filter_by(topic_id=t.id, date=today)
                    .first()
                )
                if not snap:
                    snap = TopicHeatSnapshot(
                        topic_id=t.id, date=today, created_at=now_iso,
                    )
                    s.add(snap)
                    stats["snapshots_written"] += 1
                snap.heat_index = new_heat
                snap.news_count = metrics["news_count"]
                snap.ai_score_avg = metrics["ai_score_avg"]
                snap.category = t.category

            stats["topics_refreshed"] += 1

        # 清理舊 snapshot
        if not dry_run:
            cutoff = (tw_now() - timedelta(days=SNAPSHOT_RETENTION_DAYS)).strftime("%Y-%m-%d")
            pruned = (
                s.query(TopicHeatSnapshot)
                .filter(TopicHeatSnapshot.date < cutoff)
                .delete(synchronize_session=False)
            )
            stats["snapshots_pruned"] = pruned

        if not dry_run:
            s.commit()

    logger.info(f"熱度 refresh 完成：{stats}")
    return stats


def _selftest() -> None:
    """快速 smoke test — 不寫 DB。"""
    class FakeNews:
        def __init__(self, ai_score, source_priority, published_at=None, fetched_at=None):
            self.ai_score = ai_score
            self.source_priority = source_priority
            self.published_at = published_at
            self.fetched_at = fetched_at or "2026-04-19T00:00:00+00:00"

    # 空 topic
    assert compute_topic_heat([])["heat"] == 0.0

    # 單則新聞、剛發布
    r = compute_topic_heat(
        [FakeNews(7.0, 10, published_at="2026-04-19T00:00:00+00:00")],
        now=datetime(2026, 4, 19, 0, 0, tzinfo=timezone.utc),
    )
    assert r["heat"] > 0
    assert r["time_decay"] > 0.99  # 0 小時 → 1.0

    # 48 小時前 → time_decay 應為 0.5
    r = compute_topic_heat(
        [FakeNews(7.0, 10, published_at="2026-04-17T00:00:00+00:00")],
        now=datetime(2026, 4, 19, 0, 0, tzinfo=timezone.utc),
    )
    assert abs(r["time_decay"] - 0.5) < 1e-6, f"time_decay={r['time_decay']}"

    # 多則新聞 → news_count 影響 log2 項
    r2 = compute_topic_heat(
        [FakeNews(7.0, 10) for _ in range(7)],  # log2(8) = 3
    )
    r1 = compute_topic_heat([FakeNews(7.0, 10)])
    assert r2["heat"] > r1["heat"]

    logger.info("[OK] heat_calculator selftest 通過")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        _selftest()
        return

    init_db()
    r = refresh_all(dry_run=args.dry_run)
    print(f"[OK] 主題數 {r['topics_refreshed']}，snapshot {r['snapshots_written']}，清理 {r['snapshots_pruned']}")


if __name__ == "__main__":
    main()
