"""主排程器 — 用 APScheduler 定時執行每日任務。

啟動：
  python scheduler.py

環境變數：
  SCHEDULER_MODE=cloud  → 只跑雲端任務（爬蟲/評分/Brief/翻譯/週報/觀看數）
  SCHEDULER_MODE=local  → 只跑本機任務（影片合成上傳；需 GPU/FFmpeg）
  SCHEDULER_MODE=all    → 全部跑（單機開發用，預設）

所有時間為台灣時間（UTC+8）。
"""
from __future__ import annotations

import functools
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger
from modules.common.logging_setup import setup_logger
from modules.database import db_manager
from modules.database.models import init_db

setup_logger()


def _record(job_id: str):
    """Decorator：每次執行寫 scheduler_runs，便於健康檢查與 UI 顯示「上次執行時間」。"""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            start = time.time()
            success = True
            err = None
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                success = False
                err = str(e)
                raise
            finally:
                duration_ms = int((time.time() - start) * 1000)
                try:
                    db_manager.record_scheduler_run(
                        job_id=job_id, success=success,
                        error_msg=err, duration_ms=duration_ms,
                    )
                except Exception as rec_err:
                    logger.warning(f"記錄 scheduler_run 失敗：{rec_err}")
        return wrapper
    return deco


@_record("fetch")
def job_fetch_and_score():
    """06:00 — 抓取新聞 + 自動評分。"""
    logger.info("排程任務：抓取新聞 + 自動評分")
    from daily_pipeline import step_fetch, step_score
    step_fetch()
    step_score(auto=True)


@_record("rescore")
def job_rescore_backlog():
    """06:15 — 補評分 backlog（若 06:00 沒清完）。"""
    logger.info("排程任務：補評分 backlog")
    from daily_pipeline import step_score
    step_score(auto=True)


@_record("brief")
def job_generate_brief():
    """06:30 — 生成 Daily Brief（可在 Web UI 查看）。"""
    logger.info("排程任務：生成 Daily Brief")
    from daily_pipeline import step_brief
    step_brief()


@_record("topic_summary")
def job_topic_summary():
    """06:45 — 翻譯新聞摘要 + 生成主題彙總摘要（戰情室卡片用）。"""
    logger.info("排程任務：主題彙總摘要")
    try:
        from modules.ai_war_room.translator import translate_summaries
        translate_summaries(limit=300)
    except Exception as e:
        logger.warning(f"摘要翻譯失敗：{e}")
    try:
        from modules.ai_war_room.topic_summarizer import run as summarize_topics
        summarize_topics(limit=50)
    except Exception as e:
        logger.warning(f"主題彙總失敗：{e}")


@_record("category_summary")
def job_category_summary():
    """06:55 — 生成每日類別總摘要（戰情室焦點新聞分節用，400-600 字）。"""
    logger.info("排程任務：類別總摘要")
    try:
        from modules.ai_war_room.category_summarizer import run as summarize_categories
        summarize_categories()
    except Exception as e:
        logger.warning(f"類別總摘要失敗：{e}")


@_record("compose")
def job_compose_and_upload():
    """14:00 — 前置條件全部通過才執行合成上傳。"""
    from modules.database import db_manager as _db
    from pathlib import Path

    status = _db.get_pipeline_status()
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


@_record("analytics")
def job_update_analytics():
    """22:00 — 更新影片觀看數。"""
    logger.info("排程任務：更新影片數據")
    from modules.database.analytics_tracker import update_video_analytics
    update_video_analytics()


@_record("weekly")
def job_weekly_report():
    """每週一 09:00 — 生成週報。"""
    logger.info("排程任務：生成週報")
    from modules.database.analytics_tracker import generate_weekly_report
    path = generate_weekly_report()
    logger.info(f"週報已生成：{path}")


# job_id → (函式, CronTrigger 參數, 描述, 模式類別)
JOB_REGISTRY = [
    # cloud：不需要 GPU/FFmpeg，可在 Railway worker 跑
    ("fetch",         job_fetch_and_score,    {"hour": 6, "minute": 0},  "抓取新聞 + 自動評分", "cloud"),
    ("rescore",       job_rescore_backlog,    {"hour": 6, "minute": 15}, "補評分 backlog",     "cloud"),
    ("brief",         job_generate_brief,     {"hour": 6, "minute": 30}, "生成 Brief",         "cloud"),
    ("topic_summary", job_topic_summary,      {"hour": 6, "minute": 45}, "主題彙總摘要",       "cloud"),
    ("category_summary", job_category_summary, {"hour": 6, "minute": 55}, "類別總摘要 400-600 字", "cloud"),
    ("analytics",     job_update_analytics,   {"hour": 22, "minute": 0}, "更新數據",           "cloud"),
    ("weekly",        job_weekly_report,      {"day_of_week": "mon", "hour": 9}, "週報",       "cloud"),
    # local：需要 GPU/FFmpeg，跑在本機 NSSM
    ("compose",       job_compose_and_upload, {"hour": 14, "minute": 0}, "影片合成上傳",       "local"),
]


def main():
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        raise RuntimeError("請安裝 APScheduler：pip install APScheduler>=3.10")

    init_db()

    mode = os.getenv("SCHEDULER_MODE", "all").lower().strip()
    if mode not in ("all", "cloud", "local"):
        logger.warning(f"SCHEDULER_MODE={mode!r} 不認識，退回 all")
        mode = "all"

    scheduler = BlockingScheduler(timezone="Asia/Taipei")

    registered = 0
    for job_id, fn, cron, name, category in JOB_REGISTRY:
        if mode != "all" and category != mode:
            continue
        scheduler.add_job(fn, CronTrigger(**cron), id=job_id, name=name)
        registered += 1

    logger.info(f"排程器已啟動（mode={mode}，台灣時間，註冊 {registered} 個任務）：")
    for job in scheduler.get_jobs():
        logger.info(f"  {job.next_run_time.strftime('%m/%d %H:%M')} — {job.name}")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("排程器已停止")


if __name__ == "__main__":
    main()
