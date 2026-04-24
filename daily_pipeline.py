"""每日流程主程式 — 手動觸發或排程器呼叫。

執行：
  python daily_pipeline.py --dry-run       # 測試串接，不實際送 API
  python daily_pipeline.py --fetch         # 只抓新聞
  python daily_pipeline.py --score         # 只評分
  python daily_pipeline.py --brief         # 只生成 Brief
  python daily_pipeline.py --tts           # 只跑配音（無樣本則靜音）
  python daily_pipeline.py --compose       # 只合成影片
  python daily_pipeline.py --upload        # 只上傳 YouTube
  python daily_pipeline.py --all           # 全自動
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger
from modules.common.logging_setup import setup_logger
from modules.database import db_manager
from modules.database.models import init_db

setup_logger()


def step_fetch(dry_run: bool = False) -> dict:
    logger.info("═══ 步驟 1：抓取新聞 ═══")
    from modules.scraper.fetch_all import run_all
    return asyncio.run(run_all(write_db=not dry_run))


def step_score(dry_run: bool = False) -> dict:
    logger.info("═══ 步驟 2：CoWork 評分 ═══")
    from modules.filter.export_for_scoring import main as export
    if dry_run:
        logger.info("[dry-run] 跳過評分，使用現有候選")
        return {"mode": "dry-run"}
    export()
    logger.info("請將 data/scoring_queue.json 交給 Claude Code 評分後執行：")
    logger.info("  python -m modules.filter.import_scores")
    return {"mode": "cowork"}


def step_brief() -> dict:
    logger.info("═══ 步驟 3：生成 Daily Brief ═══")
    from modules.brief.brief_generator import generate
    from modules.brief.heat_calculator import refresh_all as refresh_heat
    brief = generate()
    logger.info(f"Brief 生成完成，候選 {len(brief['candidates'])} 則")
    # 熱度指數刷新（worldmonitor 風格儀表板用）
    try:
        r = refresh_heat()
        logger.info(f"熱度刷新：{r['topics_refreshed']} 主題、snapshot {r['snapshots_written']}")
    except Exception as e:
        logger.warning(f"熱度刷新失敗（不影響 brief）：{e}")
    logger.info("請開啟選題介面：http://localhost:5000")
    return brief


def step_tts(script_path: Path | None = None) -> Path | None:
    logger.info("═══ 步驟 3b：配音生成（TTS）═══")
    if not script_path:
        scripts = sorted((PROJECT_ROOT / "data" / "scripts").glob("*/script.json"), reverse=True)
        if not scripts:
            logger.warning("找不到 script.json")
            return None
        script_path = scripts[0]
    from modules.tts.xtts_engine import generate_audio
    audio = generate_audio(script_path)
    db_manager.set_pipeline_status("images")
    return audio


def step_compose(script_path: Path | None = None) -> Path | None:
    logger.info("═══ 步驟 4：影片合成 ═══")
    if not script_path:
        scripts = sorted((PROJECT_ROOT / "data" / "scripts").glob("*/script.json"), reverse=True)
        if not scripts:
            logger.warning("找不到 script.json，請先生成腳本")
            return None
        script_path = scripts[0]

    from modules.video.subtitle_generator import from_script
    from modules.video.compositor import compose
    from modules.image.thumbnail_generator import generate_thumbnail
    from modules.image.comfyui_client import generate_images

    # 圖片生成（ComfyUI 或佔位圖）
    generate_images(script_path)

    # 配音路徑（若 TTS 已跑過）
    slug = script_path.parent.name
    audio_path = PROJECT_ROOT / "data" / "audio" / slug / "audio_full.wav"
    audio = audio_path if audio_path.exists() else None

    srt = from_script(script_path)
    thumb = generate_thumbnail(script_path)
    video = compose(script_path, audio_path=audio, subtitle_path=srt)

    logger.info(f"影片合成完成：{video}")
    return video


def step_upload(script_path: Path | None = None, video_path: Path | None = None) -> str | None:
    logger.info("═══ 步驟 5：上傳 YouTube ═══")
    from modules.publish.youtube_uploader import upload, upload_thumbnail, save_episode
    from modules.publish.social_publisher import prepare_posts

    if not script_path:
        scripts = sorted((PROJECT_ROOT / "data" / "scripts").glob("*/script.json"), reverse=True)
        if not scripts:
            logger.warning("找不到 script.json")
            return None
        script_path = scripts[0]

    slug = script_path.parent.name
    if not video_path:
        video_path = PROJECT_ROOT / "data" / "videos" / slug / "final.mp4"

    if not video_path.exists():
        logger.warning(f"影片不存在：{video_path}")
        return None

    try:
        video_id = upload(video_path, script_path)
        thumb = PROJECT_ROOT / "data" / "images" / slug / "thumbnail.png"
        if thumb.exists():
            upload_thumbnail(video_id, thumb)
        save_episode(video_id, script_path, video_path)
        prepare_posts(script_path, video_id)
        return video_id
    except Exception as e:
        logger.error(f"上傳失敗：{e}")
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="AI 頻道每日流水線")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--brief", action="store_true")
    ap.add_argument("--tts", action="store_true")
    ap.add_argument("--compose", action="store_true")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    init_db()
    start = datetime.now()

    if args.dry_run or args.all:
        logger.info("🔁 每日流水線開始")
        step_fetch(dry_run=args.dry_run)
        step_score(dry_run=args.dry_run)
        step_brief()
        logger.info("✅ dry-run 完成，等待人工選題後繼續")

    if args.fetch:
        step_fetch()
    if args.score:
        step_score()
    if args.brief:
        step_brief()
    if args.tts:
        step_tts()
    if args.compose:
        step_compose()
    if args.upload:
        step_upload()

    elapsed = (datetime.now() - start).total_seconds()
    logger.info(f"完成，耗時 {elapsed:.1f}s")


if __name__ == "__main__":
    main()
