"""YouTube 數據追蹤 + 週報生成。

每日 22:00 抓昨日影片觀看數；每週一生成 CoWork 週報分析。

執行：
  python -m modules.database.analytics_tracker --update   # 更新影片數據
  python -m modules.database.analytics_tracker --weekly   # 生成週報
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from loguru import logger

from modules.common.config import PROJECT_ROOT
from modules.common.logging_setup import setup_logger
from modules.database import db_manager
from modules.database.models import Episode, SessionLocal

setup_logger()

REPORTS_DIR = PROJECT_ROOT / "data" / "reports"


def update_video_analytics() -> None:
    """從 YouTube API 拉最新觀看數更新 episodes 表。"""
    try:
        from modules.publish.youtube_uploader import _build_youtube
        youtube = _build_youtube()
    except Exception as e:
        logger.warning(f"YouTube API 連線失敗（跳過數據更新）：{e}")
        return

    with db_manager.get_session() as s:
        episodes = s.query(Episode).filter(
            Episode.youtube_id.isnot(None)
        ).all()

        if not episodes:
            logger.info("沒有已上傳的影片，跳過數據更新")
            return

        ids = [ep.youtube_id for ep in episodes]
        resp = youtube.videos().list(
            part="statistics",
            id=",".join(ids)
        ).execute()

        stats_map = {
            item["id"]: item["statistics"]
            for item in resp.get("items", [])
        }

        for ep in episodes:
            st = stats_map.get(ep.youtube_id, {})
            ep.views_24h = int(st.get("viewCount", 0))

        logger.info(f"更新 {len(episodes)} 支影片數據完成")


def generate_weekly_report() -> Path:
    """生成本週分析報告，存為 Markdown（供 CoWork 模式分析）。"""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc)
    week_ago = today - timedelta(days=7)
    report_date = today.strftime("%Y%m%d")

    with db_manager.get_session() as s:
        episodes = s.query(Episode).filter(
            Episode.date >= week_ago.strftime("%Y-%m-%d")
        ).order_by(Episode.views_24h.desc()).all()

    lines = [
        f"# AI 頻道週報 — {today.strftime('%Y-%m-%d')}",
        "",
        f"## 本週影片（{week_ago.strftime('%m/%d')} - {today.strftime('%m/%d')}）",
        "",
    ]

    if not episodes:
        lines.append("本週尚無已上傳影片。")
    else:
        for i, ep in enumerate(episodes, 1):
            lines += [
                f"### {i}. {ep.title or '（無標題）'}",
                f"- 日期：{ep.date}",
                f"- YouTube：{'https://youtu.be/' + ep.youtube_id if ep.youtube_id else '—'}",
                f"- 觀看數：{ep.views_24h or 0:,}",
                f"- CTR：{ep.ctr or '—'}%",
                f"- 平均觀看率：{ep.avg_watch_pct or '—'}%",
                "",
            ]

    # 本週爬取統計
    with db_manager.get_session() as s:
        from modules.database.models import NewsItem
        total_news = s.query(NewsItem).filter(
            NewsItem.fetched_at >= week_ago.isoformat()
        ).count()
        candidates = s.query(NewsItem).filter(
            NewsItem.fetched_at >= week_ago.isoformat(),
            NewsItem.status == "candidate"
        ).count()

    lines += [
        "## 本週新聞統計",
        f"- 抓取總數：{total_news:,} 則",
        f"- 候選（AI 評分 ≥ 6）：{candidates} 則",
        f"- 轉化率：{candidates / max(total_news, 1) * 100:.1f}%",
        "",
        "## 下週建議（待 CoWork 分析填入）",
        "- [ ] 最受歡迎的主題類型：",
        "- [ ] 表現最好的標題格式：",
        "- [ ] 下週重點追蹤公司：",
        "- [ ] 需要調整的腳本風格：",
    ]

    report_path = REPORTS_DIR / f"{report_date}_weekly.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"週報已生成：{report_path}")
    return report_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="更新影片數據")
    ap.add_argument("--weekly", action="store_true", help="生成週報")
    args = ap.parse_args()

    if args.update:
        update_video_analytics()
        print("[OK] 影片數據已更新")

    if args.weekly:
        path = generate_weekly_report()
        print(f"[OK] 週報：{path}")
        print(path.read_text(encoding="utf-8")[:500])


if __name__ == "__main__":
    main()
