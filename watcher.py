"""Pipeline Watcher — 以 slug 為單位自動偵測並推進每集流水線。

新架構（slug-based）：
  - 從 EpisodeStatus 取 `get_active_episode()` 找下一個要處理的 slug
  - 各 handler 直接收 slug + status dict
  - 支援多集並行（同時期可有多個 slug，watcher 串行處理）

啟動：
  python watcher.py   # 獨立跑
  或由 web_ui/app.py 自動以 daemon thread 啟動
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
from modules.storage.local_storage import get_episode_paths

setup_logger()

POLL_INTERVAL  = 30
MAX_RETRIES    = 3
STAGE_TIMEOUT  = 3600

_retry_count: dict[str, int] = {}     # "slug:stage" → retries
_stage_start: dict[str, float] = {}   # "slug:stage" → monotonic start


def _fail(slug: str, stage: str, msg: str, date: str | None = None) -> None:
    """標記失敗，寫 error_msg，不推進 stage。"""
    logger.error(f"[{slug} / {stage}] 失敗：{msg}")
    db_manager.set_episode_status(slug=slug, stage=stage, date=date,
                                  error_msg=msg[:500])


def handle_selected(slug: str, status: dict) -> None:
    """已選題 → 自動執行：深度研究 + 腳本生成（claude CLI）。

    優先順序：selected_topic_id（Phase B 新）> selected_id（legacy 單篇）
    """
    topic_id = status.get("selected_topic_id")
    news_id  = status.get("selected_id")
    date     = status.get("date")
    if not topic_id and not news_id:
        logger.warning(f"[{slug}] selected 缺少 selected_topic_id / selected_id，跳過")
        return

    from modules.script.researcher import (
        auto_research, auto_research_topic, export_prompt, export_prompt_for_topic,
    )
    from modules.script.script_writer import auto_write_script

    # ── 步驟 1：深度研究 ──
    try:
        mode = "topic" if topic_id else "news"
        logger.info(f"[{slug}] ▶ 深度研究（{mode} 模式）…")
        db_manager.set_episode_status(slug=slug, stage="researching",
                                      date=date, error_msg=None)
        db_manager.update_episode_progress(
            slug, f"🔎 深度研究（{mode} 模式）：呼叫 claude CLI…")
        if topic_id:
            research_path = auto_research_topic(topic_id, slug=slug)
        else:
            research_path = auto_research(news_id, slug=slug)
        db_manager.update_episode_progress(slug, "✅ 研究完成")
    except Exception as e:
        logger.exception(f"[{slug}] 深度研究失敗，退回 CoWork")
        try:
            if topic_id:
                export_prompt_for_topic(topic_id)
            else:
                export_prompt(news_id)
        except Exception:
            pass
        db_manager.set_episode_status(
            slug=slug, stage="researching", date=date,
            error_msg=f"自動研究失敗：{e}｜請點「前往 CoWork 研究」手動操作"
        )
        return

    # ── 步驟 2：腳本生成 ──
    try:
        logger.info(f"[{slug}] ▶ 腳本生成…")
        db_manager.set_episode_status(slug=slug, stage="scripting",
                                      date=date, error_msg=None)
        db_manager.update_episode_progress(
            slug, "✍️ 腳本生成：呼叫 claude CLI（長片模式可能 15 分鐘）…")
        auto_write_script(research_path, slug=slug)
        logger.info(f"[{slug}] ✅ 腳本生成完成，進入審閱")
        db_manager.set_episode_status(slug=slug, stage="script_ready",
                                      date=date, error_msg=None)
    except Exception as e:
        logger.exception(f"[{slug}] 腳本生成失敗，退回 CoWork")
        db_manager.set_episode_status(
            slug=slug, stage="scripting", date=date,
            error_msg=f"自動腳本失敗：{e}｜請點「前往 CoWork 腳本」手動操作"
        )


def handle_researching(slug: str, status: dict) -> None:
    """researching 中 — 有 error_msg 表自動失敗，等 CoWork；無則自動跑中不介入。"""
    if status.get("error_msg"):
        logger.debug(f"[{slug}] researching：等待 CoWork 手動輸入…")


def handle_scripting(slug: str, status: dict) -> None:
    if status.get("error_msg"):
        logger.debug(f"[{slug}] scripting：等待 CoWork 手動輸入…")


def handle_script_ready(slug: str, status: dict) -> None:
    """腳本已生成，等使用者在 Web UI 審閱後手動推進到 tts。"""
    logger.debug(f"[{slug}] script_ready：等待使用者審閱腳本")


def handle_tts(slug: str, status: dict) -> None:
    """腳本確認 → 執行配音。"""
    date = status.get("date")
    paths = get_episode_paths(slug)
    if not paths["script"]["exists"]:
        _fail(slug, "tts", "找不到 script.json", date)
        return
    try:
        from modules.tts.xtts_engine import generate_audio
        script_path = Path(paths["script"]["path"])
        db_manager.update_episode_progress(slug, "🎙️ 準備配音…")
        generate_audio(script_path, slug=slug)
        db_manager.update_episode_progress(slug, "✅ 配音完成")
        db_manager.set_episode_status(slug=slug, stage="prefetch",
                                      date=date, error_msg=None)
    except Exception as e:
        _fail(slug, "tts", str(e), date)


def handle_prefetch(slug: str, status: dict) -> None:
    """預抓階段 — 依 script.broll_keywords 抓 Pexels B-roll + 生縮圖。

    取代舊 handle_images：不再 AI 生 section 圖（compositor 在 B-roll miss
    的單段會即時 fallback）。section 預先抓好的 clips 與 thumbnail 會被 compose 使用。
    """
    date = status.get("date")
    paths = get_episode_paths(slug)
    if not paths["script"]["exists"]:
        db_manager.set_episode_status(slug=slug, stage="compositing",
                                      date=date, error_msg=None)
        return
    try:
        from modules.video.broll_fetcher import prefetch_all_sections, is_available
        from modules.image.thumbnail_generator import generate_thumbnail

        script_path = Path(paths["script"]["path"])

        if is_available():
            db_manager.update_episode_progress(slug, "📥 預抓 Pexels B-roll 素材…")
            manifest = prefetch_all_sections(script_path, slug=slug)
            stats = manifest.get("stats") or {}
            db_manager.update_episode_progress(
                slug,
                f"✅ B-roll 預抓完成：{stats.get('clips_downloaded', 0)} 支 clips "
                f"/ {stats.get('sections_with_clips', 0)} 段"
            )
        else:
            db_manager.update_episode_progress(slug, "⚠️ 未設 PEXELS_API_KEY，跳過 B-roll 預抓（compositor 將用 AI 生圖 fallback）")

        db_manager.update_episode_progress(slug, "🎨 生成縮圖…")
        generate_thumbnail(script_path)
        db_manager.update_episode_progress(slug, "✅ 預抓階段完成")

        db_manager.set_episode_status(slug=slug, stage="compositing",
                                      date=date, error_msg=None)
    except Exception as e:
        _fail(slug, "prefetch", str(e), date)


# 向後相容：舊集數 DB stage 為 "images" 時走同一個 handler
handle_images = handle_prefetch


def handle_compositing(slug: str, status: dict) -> None:
    date = status.get("date")
    paths = get_episode_paths(slug)
    if not paths["script"]["exists"]:
        _fail(slug, "compositing", "找不到 script.json", date)
        return
    try:
        from modules.video.subtitle_generator import from_audio_ass, from_script_ass
        from modules.video.compositor import compose
        from modules.image.thumbnail_generator import generate_thumbnail

        script_path = Path(paths["script"]["path"])
        audio = Path(paths["audio_full"]["path"])

        db_manager.update_episode_progress(slug, "📝 生成字幕…")
        if audio.exists():
            subtitle = from_audio_ass(audio, script_path)
        else:
            subtitle = from_script_ass(script_path)

        # 縮圖：prefetch 階段已生成；缺檔時才補
        if not paths["thumbnail"]["exists"]:
            db_manager.update_episode_progress(slug, "🎨 生成縮圖（prefetch 階段缺檔，補生）…")
            generate_thumbnail(script_path)

        db_manager.update_episode_progress(slug, "🎬 開始影片合成…")
        compose(
            script_path,
            audio_path=audio if audio.exists() else None,
            subtitle_path=subtitle,
            slug=slug,
        )
        db_manager.update_episode_progress(slug, "✅ 影片合成完成")
        db_manager.set_episode_status(slug=slug, stage="upload_ready",
                                      date=date, error_msg=None)
    except Exception as e:
        _fail(slug, "compositing", str(e), date)


def handle_upload_ready(slug: str, status: dict) -> None:
    """影片已合成，等使用者在 Web UI 確認後才上傳。"""
    logger.debug(f"[{slug}] upload_ready：等待使用者確認上傳")


def handle_uploading(slug: str, status: dict) -> None:
    date = status.get("date")
    paths = get_episode_paths(slug)
    if not paths["script"]["exists"]:
        _fail(slug, "uploading", "找不到 script.json", date)
        return
    if not paths["video"]["exists"]:
        _fail(slug, "uploading", f"找不到影片：{paths['video']['path']}", date)
        return
    try:
        from modules.publish.youtube_uploader import upload, upload_thumbnail, save_episode
        from modules.publish.social_publisher import prepare_posts

        script_path = Path(paths["script"]["path"])
        video = Path(paths["video"]["path"])
        thumb = Path(paths["thumbnail"]["path"])

        db_manager.update_episode_progress(slug, "📤 準備上傳 YouTube…")
        video_id = upload(video, script_path, slug=slug)
        if thumb.exists():
            db_manager.update_episode_progress(slug, "🖼️ 上傳縮圖…")
            upload_thumbnail(video_id, thumb)
        save_episode(video_id, script_path, video)
        prepare_posts(script_path, video_id)

        # 更新 Episode 表（含 slug）
        db_manager.upsert_episode(slug=slug, youtube_id=video_id,
                                  status="uploaded")

        db_manager.update_episode_progress(slug, f"✅ 已發布：youtu.be/{video_id}")
        db_manager.set_episode_status(slug=slug, stage="done",
                                      date=date, error_msg=None)
    except Exception as e:
        _fail(slug, "uploading", str(e), date)


HANDLERS = {
    "selected":     handle_selected,
    "researching":  handle_researching,
    "scripting":    handle_scripting,
    "script_ready": handle_script_ready,
    "tts":          handle_tts,
    "prefetch":     handle_prefetch,
    "images":       handle_prefetch,  # 舊 stage 名相容
    "compositing":  handle_compositing,
    "upload_ready": handle_upload_ready,
    "uploading":    handle_uploading,
}

# 不需 watcher 介入的 stage
_PASSIVE_STAGES = {"script_ready", "upload_ready", "researching", "scripting",
                   "idle", "done"}


def tick() -> None:
    active = db_manager.get_active_episode()
    if not active:
        return

    slug  = active["slug"]
    stage = active.get("stage", "idle")

    if stage in _PASSIVE_STAGES:
        return
    if stage not in HANDLERS:
        return

    key = f"{slug}:{stage}"

    # 若 error_msg 已被 Web UI 清除 → 手動重試，重置計數器
    if not active.get("error_msg") and _retry_count.get(key, 0) >= MAX_RETRIES:
        logger.info(f"[{slug} / {stage}] 偵測到手動重試，重置計數器")
        _retry_count.pop(key, None)
        _stage_start.pop(key, None)

    retries = _retry_count.get(key, 0)

    # 超時檢查
    start = _stage_start.get(key)
    if start and (time.monotonic() - start) > STAGE_TIMEOUT:
        logger.error(f"[{slug} / {stage}] 超過 {STAGE_TIMEOUT}s，標記失敗")
        _fail(slug, stage, f"超時（>{STAGE_TIMEOUT}s），請手動排查", active.get("date"))
        _retry_count[key] = MAX_RETRIES
        return

    if retries >= MAX_RETRIES:
        return

    if active.get("error_msg") and retries > 0:
        logger.warning(f"[{slug} / {stage}] 重試（第 {retries} 次）")

    if key not in _stage_start:
        _stage_start[key] = time.monotonic()

    _retry_count[key] = retries + 1
    try:
        HANDLERS[stage](slug, active)
    except Exception as e:
        logger.error(f"[{slug} / {stage}] 例外：{e}")
        _fail(slug, stage, str(e), active.get("date"))


def run_loop() -> None:
    """主迴圈（可被 Flask 背景 thread 或獨立 main() 呼叫）。"""
    init_db()
    logger.info(f"Pipeline Watcher（slug-based）啟動，輪詢 {POLL_INTERVAL}s，"
                f"最大重試 {MAX_RETRIES} 次")
    while True:
        try:
            tick()
        except Exception as e:
            logger.error(f"Watcher tick 例外：{e}")
        time.sleep(POLL_INTERVAL)


def main() -> None:
    logger.info("按 Ctrl+C 停止")
    run_loop()


if __name__ == "__main__":
    main()
