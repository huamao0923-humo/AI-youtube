"""Pipeline Watcher — 在本機背景執行，自動偵測 DB 狀態並觸發下一步。

當使用者在 Railway Web UI 點「確認腳本，開始製作」後，
本機的 watcher 會自動接手執行：配音 → 圖片 → 合成 → 上傳。

啟動（與 scheduler.py 分開，單獨跑）：
  python watcher.py

建議：開機自啟或用 tmux/screen 背景執行。
"""
from __future__ import annotations

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

POLL_INTERVAL = 30   # 秒
STAGES_TO_HANDLE = ("selected", "tts", "images", "compositing", "uploading")


def _latest_script_path() -> Path | None:
    scripts = sorted((PROJECT_ROOT / "data" / "scripts").glob("*/script.json"), reverse=True)
    return scripts[0] if scripts else None


def handle_selected(status: dict) -> None:
    """已選題 → 觸發研究 + 腳本生成（CoWork 模式只做提醒）。"""
    logger.info("偵測到選題完成，請執行腳本生成後在 Web UI 確認")
    # 自動切到 scripting 以顯示正確進度
    db_manager.set_pipeline_status("scripting")


def handle_tts(status: dict) -> None:
    """腳本已確認 → 執行配音（靜音 fallback）。"""
    logger.info("開始配音生成")
    script = _latest_script_path()
    if not script:
        logger.error("找不到 script.json，跳過 TTS")
        db_manager.set_pipeline_status("images")
        return
    try:
        from modules.tts.xtts_engine import generate_audio
        generate_audio(script)
        logger.info("配音完成")
    except Exception as e:
        logger.error(f"TTS 失敗：{e}（繼續合成）")
    db_manager.set_pipeline_status("images")


def handle_images(status: dict) -> None:
    """生成圖片（ComfyUI 或佔位圖）。"""
    logger.info("開始圖片生成")
    script = _latest_script_path()
    if not script:
        db_manager.set_pipeline_status("compositing")
        return
    try:
        from modules.image.comfyui_client import generate_images
        generate_images(script)
        logger.info("圖片生成完成")
    except Exception as e:
        logger.error(f"圖片生成失敗：{e}（繼續合成）")
    db_manager.set_pipeline_status("compositing")


def handle_compositing(status: dict) -> None:
    """合成影片。"""
    logger.info("開始影片合成")
    script = _latest_script_path()
    if not script:
        db_manager.set_pipeline_status("uploading")
        return
    try:
        from modules.video.subtitle_generator import from_script
        from modules.video.compositor import compose
        from modules.image.thumbnail_generator import generate_thumbnail

        slug = script.parent.name
        audio = PROJECT_ROOT / "data" / "audio" / slug / "audio_full.wav"

        srt = from_script(script)
        generate_thumbnail(script)
        compose(script, audio_path=audio if audio.exists() else None, subtitle_path=srt)
        logger.info("影片合成完成")
    except Exception as e:
        logger.error(f"合成失敗：{e}")
        db_manager.set_pipeline_status("compositing", error_msg=str(e))
        return
    db_manager.set_pipeline_status("uploading")


def handle_uploading(status: dict) -> None:
    """上傳 YouTube。"""
    logger.info("開始上傳 YouTube")
    script = _latest_script_path()
    if not script:
        db_manager.set_pipeline_status("done")
        return
    slug = script.parent.name
    video = PROJECT_ROOT / "data" / "videos" / slug / "final.mp4"
    if not video.exists():
        logger.error(f"找不到影片：{video}")
        db_manager.set_pipeline_status("done")
        return
    try:
        from modules.publish.youtube_uploader import upload, upload_thumbnail, save_episode
        from modules.publish.social_publisher import prepare_posts

        video_id = upload(video, script)
        thumb = PROJECT_ROOT / "data" / "images" / slug / "thumbnail.png"
        if thumb.exists():
            upload_thumbnail(video_id, thumb)
        save_episode(video_id, script, video)
        prepare_posts(script, video_id)
        logger.info(f"上傳完成：https://youtu.be/{video_id}")
        db_manager.set_pipeline_status("done")
    except Exception as e:
        logger.error(f"上傳失敗：{e}")
        db_manager.set_pipeline_status("uploading", error_msg=str(e))


HANDLERS = {
    "selected": handle_selected,
    "tts": handle_tts,
    "images": handle_images,
    "compositing": handle_compositing,
    "uploading": handle_uploading,
}

_last_handled: dict[str, str] = {}   # date → stage（避免重複觸發）


def tick() -> None:
    status = db_manager.get_pipeline_status()
    stage = status.get("stage", "idle")
    date  = status.get("date", "")

    if stage not in HANDLERS:
        return

    key = f"{date}:{stage}"
    if _last_handled.get(date) == key:
        return  # 這個 stage 今天已處理過

    logger.info(f"[Watcher] 偵測到新階段：{stage}")
    _last_handled[date] = key
    try:
        HANDLERS[stage](status)
    except Exception as e:
        logger.error(f"[Watcher] 處理 {stage} 時例外：{e}")
        db_manager.set_pipeline_status(stage, error_msg=str(e))


def main() -> None:
    init_db()
    logger.info(f"Pipeline Watcher 啟動，輪詢間隔 {POLL_INTERVAL}s")
    logger.info("Ctrl+C 停止")
    while True:
        try:
            tick()
        except Exception as e:
            logger.error(f"Watcher tick 例外：{e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
