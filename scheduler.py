"""主排程器 — 用 APScheduler 定時執行每日任務。

啟動：
  python scheduler.py

所有時間為台灣時間（UTC+8）。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger
from modules.common.logging_setup import setup_logger
from modules.database.models import init_db

setup_logger()


def job_fetch_and_score():
    """06:00 — 抓取新聞 + 輸出評分佇列。"""
    logger.info("排程任務：抓取新聞")
    from daily_pipeline import step_fetch, step_score
    step_fetch()
    step_score()


def job_generate_brief():
    """06:30 — 生成 Daily Brief（可在 Web UI 查看）。"""
    logger.info("排程任務：生成 Daily Brief")
    from daily_pipeline import step_brief
    step_brief()


def job_compose_and_upload():
    """14:00 — 前置條件全部通過才執行合成上傳。"""
    from modules.database import db_manager
    from pathlib import Path

    status = db_manager.get_pipeline_status()
    stage  = status.get("stage", "idle")

    # 前置條件 1：stage 必須在 tts 之後
    if stage not in ("tts", "images", "compositing", "uploading"):
        logger.info(f"排程合成：stage={stage}，前置條件未完成，跳過")
        return

    # 前置條件 2：script.json 必須存在
    scripts = sorted(Path("data/scripts").glob("*/script.json"), reverse=True)
    if not scripts:
        logger.warning("排程合成：找不到 script.json，跳過")
        return

    # 前置條件 3：今日已選題
    if not status.get("selected_id"):
        logger.warning("排程合成：今日尚未選題，跳過")
        return

    logger.info(f"排程任務：影片合成 + 上傳（stage={stage}）")
    from daily_pipeline import step_compose, step_upload
    video = step_compose()
    if video:
        step_upload(video_path=video)


def job_update_analytics():
    """22:00 — 更新影片觀看數。"""
    logger.info("排程任務：更新影片數據")
    from modules.database.analytics_tracker import update_video_analytics
    update_video_analytics()


def job_weekly_report():
    """每週一 09:00 — 生成週報。"""
    logger.info("排程任務：生成週報")
    from modules.database.analytics_tracker import generate_weekly_report
    path = generate_weekly_report()
    logger.info(f"週報已生成：{path}")


def main():
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        raise RuntimeError("請安裝 APScheduler：pip install APScheduler>=3.10")

    init_db()
    scheduler = BlockingScheduler(timezone="Asia/Taipei")

    # 每日 06:00 — 抓取新聞
    scheduler.add_job(job_fetch_and_score, CronTrigger(hour=6, minute=0),
                      id="fetch", name="抓取新聞")

    # 每日 06:30 — 生成 Brief
    scheduler.add_job(job_generate_brief, CronTrigger(hour=6, minute=30),
                      id="brief", name="生成 Brief")

    # 每日 14:00 — 合成 + 上傳（若腳本已確認）
    scheduler.add_job(job_compose_and_upload, CronTrigger(hour=14, minute=0),
                      id="compose", name="影片合成上傳")

    # 每日 22:00 — 更新數據
    scheduler.add_job(job_update_analytics, CronTrigger(hour=22, minute=0),
                      id="analytics", name="更新數據")

    # 每週一 09:00 — 週報
    scheduler.add_job(job_weekly_report, CronTrigger(day_of_week="mon", hour=9),
                      id="weekly", name="週報")

    logger.info("排程器已啟動（台灣時間）：")
    for job in scheduler.get_jobs():
        logger.info(f"  {job.next_run_time.strftime('%m/%d %H:%M')} — {job.name}")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("排程器已停止")


if __name__ == "__main__":
    main()
