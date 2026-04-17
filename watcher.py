"""Pipeline Watcher — 在本機背景執行，自動偵測 DB 狀態並觸發下一步。

當使用者在 Railway Web UI 點「確認腳本，開始製作」後，
本機的 watcher 會自動接手執行：配音 → 圖片 → 合成 → 上傳。

啟動：
  python watcher.py

建議：用 tmux/screen 背景執行，或設為開機自啟。
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

POLL_INTERVAL  = 30    # 秒
MAX_RETRIES    = 3     # 同一 stage 最多重試次數
STAGE_TIMEOUT  = 3600  # 秒 — 超過此時間仍在同一 stage 視為卡住

_retry_count: dict[str, int] = {}   # "date:stage" → retry 次數
_stage_start: dict[str, float] = {} # "date:stage" → 開始時間


def _latest_script_path() -> Path | None:
    """找最新的 script.json（按日期資料夾排序）。"""
    scripts = sorted(
        (PROJECT_ROOT / "data" / "scripts").glob("*/script.json"), reverse=True
    )
    return scripts[0] if scripts else None


def _fail(stage: str, msg: str, date: str) -> None:
    """標記失敗，寫 error_msg，不推進 stage。"""
    logger.error(f"[{stage}] 失敗：{msg}")
    db_manager.set_pipeline_status(stage, date=date, error_msg=msg[:500])


def handle_selected(status: dict) -> None:
    """
    已選題 → 自動執行：深度研究 + 腳本生成（claude CLI）。
    若 claude CLI 不可用或失敗，退回 CoWork 模式讓使用者手動操作。
    """
    news_id = status.get("selected_id")
    date    = status.get("date")
    if not news_id:
        logger.warning("selected 狀態缺少 selected_id，跳過")
        return

    from modules.script.researcher import auto_research, export_prompt
    from modules.script.script_writer import auto_write_script

    # ── 步驟 1：深度研究 ──
    research_path = None
    try:
        logger.info("▶ 自動模式：呼叫 claude CLI 進行深度研究…")
        db_manager.set_pipeline_status("researching", date=date, error_msg=None)
        research_path = auto_research(news_id)
        logger.info("✅ 深度研究完成")
    except Exception as e:
        logger.warning(f"深度研究失敗（{e}），退回 CoWork")
        try:
            export_prompt(news_id)
        except Exception:
            pass
        db_manager.set_pipeline_status(
            "researching", date=date,
            error_msg=f"自動研究失敗：{e}｜請點「前往 CoWork 研究」手動操作"
        )
        return

    # ── 步驟 2：腳本生成 ──
    try:
        logger.info("▶ 自動模式：呼叫 claude CLI 生成腳本 JSON…")
        db_manager.set_pipeline_status("scripting", date=date, error_msg=None)
        auto_write_script(research_path)
        logger.info("✅ 腳本生成完成，進入審閱")
        db_manager.set_pipeline_status("script_ready", date=date, error_msg=None)
    except Exception as e:
        logger.warning(f"腳本生成失敗（{e}），退回 CoWork")
        db_manager.set_pipeline_status(
            "scripting", date=date,
            error_msg=f"自動腳本失敗：{e}｜請點「前往 CoWork 腳本」手動操作"
        )


def handle_researching(status: dict, date: str) -> None:
    """
    研究中 — 若有 error_msg 表示自動模式已失敗，等使用者在 Web UI CoWork 頁面操作。
    若無 error_msg 表示仍在自動執行中，watcher 不介入。
    """
    if status.get("error_msg"):
        logger.debug("researching：等待 CoWork 手動輸入…")
    else:
        logger.debug("researching：自動執行中，watcher 不介入")


def handle_scripting(status: dict, date: str) -> None:
    """
    腳本生成中 — 同 handle_researching 邏輯。
    """
    if status.get("error_msg"):
        logger.debug("scripting：等待 CoWork 手動輸入…")
    else:
        logger.debug("scripting：自動執行中，watcher 不介入")


def handle_tts(status: dict, date: str) -> None:
    """腳本已確認 → 執行配音（靜音 fallback）。"""
    logger.info("開始配音生成")
    script = _latest_script_path()
    if not script:
        _fail("tts", "找不到 script.json，請先執行腳本生成", date)
        return
    try:
        from modules.tts.xtts_engine import generate_audio
        generate_audio(script)
        logger.info("配音完成")
        db_manager.set_pipeline_status("images", date=date, error_msg=None)
    except Exception as e:
        _fail("tts", str(e), date)


def handle_images(status: dict, date: str) -> None:
    """生成圖片（ComfyUI 或佔位圖）。"""
    logger.info("開始圖片生成")
    script = _latest_script_path()
    if not script:
        db_manager.set_pipeline_status("compositing", date=date, error_msg=None)
        return
    try:
        from modules.image.comfyui_client import generate_images
        generate_images(script)
        logger.info("圖片生成完成")
        db_manager.set_pipeline_status("compositing", date=date, error_msg=None)
    except Exception as e:
        _fail("images", str(e), date)


def handle_compositing(status: dict, date: str) -> None:
    """合成影片。"""
    logger.info("開始影片合成")
    script = _latest_script_path()
    if not script:
        _fail("compositing", "找不到 script.json", date)
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
        db_manager.set_pipeline_status("uploading", date=date, error_msg=None)
    except Exception as e:
        _fail("compositing", str(e), date)


def handle_uploading(status: dict, date: str) -> None:
    """上傳 YouTube。"""
    logger.info("開始上傳 YouTube")
    script = _latest_script_path()
    if not script:
        _fail("uploading", "找不到 script.json", date)
        return
    slug = script.parent.name
    video = PROJECT_ROOT / "data" / "videos" / slug / "final.mp4"
    if not video.exists():
        _fail("uploading", f"找不到影片：{video}", date)
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
        db_manager.set_pipeline_status("done", date=date, error_msg=None)
    except Exception as e:
        _fail("uploading", str(e), date)


HANDLERS = {
    "selected":    handle_selected,
    "researching": handle_researching,
    "scripting":   handle_scripting,
    "tts":         handle_tts,
    "images":      handle_images,
    "compositing": handle_compositing,
    "uploading":   handle_uploading,
}


def tick() -> None:
    status = db_manager.get_pipeline_status()
    stage  = status.get("stage", "idle")
    date   = status.get("date", "")

    if stage not in HANDLERS:
        return

    key = f"{date}:{stage}"
    retries = _retry_count.get(key, 0)

    # 超時檢查
    start = _stage_start.get(key)
    if start and (time.monotonic() - start) > STAGE_TIMEOUT:
        logger.error(f"[Watcher] {stage} 超過 {STAGE_TIMEOUT}s，標記失敗")
        _fail(stage, f"超時（>{STAGE_TIMEOUT}s），請手動排查", date)
        _retry_count[key] = MAX_RETRIES  # 不再重試
        return

    # 超過重試次數
    if retries >= MAX_RETRIES:
        return  # 靜默等待人工介入

    # 有 error_msg 且還在同 stage → 重試
    if status.get("error_msg") and retries > 0:
        logger.warning(f"[Watcher] {stage} 重試（第 {retries} 次）")

    if key not in _stage_start:
        _stage_start[key] = time.monotonic()

    _retry_count[key] = retries + 1
    try:
        if stage in ("selected",):
            HANDLERS[stage](status)
        else:
            HANDLERS[stage](status, date)
    except Exception as e:
        logger.error(f"[Watcher] {stage} 例外：{e}")
        _fail(stage, str(e), date)


def main() -> None:
    init_db()
    logger.info(f"Pipeline Watcher 啟動，輪詢間隔 {POLL_INTERVAL}s，最大重試 {MAX_RETRIES} 次")
    logger.info("按 Ctrl+C 停止")
    while True:
        try:
            tick()
        except Exception as e:
            logger.error(f"Watcher tick 例外：{e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
