"""本地檔案路徑抽象 — 以 slug 為輸入，回傳一集所有檔案的本地路徑。

用途：
  - Web UI 單集頁統一顯示檔案
  - watcher.handle_* 取檔案路徑
  - backfill 腳本推斷 stage

慣例：
  data/scripts/<slug>/script.json, research.json, research_prompt.md, script_prompt.md
  data/audio/<slug>/audio_full.wav, section_*.wav
  data/images/<slug>/section_*.png, thumbnail.png
  data/videos/<slug>/final.mp4, subtitles.ass, subtitles.srt
"""
from __future__ import annotations

from pathlib import Path

from modules.common.config import PROJECT_ROOT

DATA_DIR    = PROJECT_ROOT / "data"
SCRIPTS_DIR = DATA_DIR / "scripts"
AUDIO_DIR   = DATA_DIR / "audio"
IMAGES_DIR  = DATA_DIR / "images"
VIDEOS_DIR  = DATA_DIR / "videos"


def get_episode_paths(slug: str) -> dict:
    """回傳一集所有預期檔案的路徑 dict。

    每個 key 附帶 exists bool，方便 UI 判斷。
    """
    script_dir = SCRIPTS_DIR / slug
    audio_dir  = AUDIO_DIR / slug
    img_dir    = IMAGES_DIR / slug
    video_dir  = VIDEOS_DIR / slug

    def _pack(p: Path) -> dict:
        return {"path": str(p), "exists": p.exists(),
                "size": p.stat().st_size if p.exists() else 0}

    # 圖片列表（section_001..N + thumbnail）
    section_imgs = sorted(img_dir.glob("section_*.png")) if img_dir.exists() else []

    return {
        "slug":       slug,
        "script":     _pack(script_dir / "script.json"),
        "research":   _pack(script_dir / "research.json"),
        "research_prompt": _pack(script_dir / "research_prompt.md"),
        "script_prompt":   _pack(script_dir / "script_prompt.md"),
        "audio_full": _pack(audio_dir / "audio_full.wav"),
        "audio_dir":  {"path": str(audio_dir), "exists": audio_dir.exists()},
        "images_dir": {"path": str(img_dir),   "exists": img_dir.exists()},
        "thumbnail":  _pack(img_dir / "thumbnail.png"),
        "section_images": [_pack(p) for p in section_imgs],
        "video":      _pack(video_dir / "final.mp4"),
        "subtitle_ass": _pack(video_dir / "subtitles.ass"),
        "subtitle_srt": _pack(video_dir / "subtitles.srt"),
        "videos_dir": {"path": str(video_dir), "exists": video_dir.exists()},
    }


def infer_stage_from_files(slug: str, has_youtube_id: bool = False) -> str:
    """從檔案存在狀態推斷本集目前應該在哪個 stage。

    用於回填腳本。
    規則：
      - 有 youtube_id           → done
      - 有 final.mp4            → upload_ready
      - 有 section_001.png+     → compositing（影片尚未生成）
      - 有 audio_full.wav       → images（圖片尚未生成）
      - 有 script.json          → script_ready 或 tts（有音訊才是 tts）
      - 什麼都沒有               → idle
    """
    if has_youtube_id:
        return "done"

    paths = get_episode_paths(slug)
    if paths["video"]["exists"]:
        return "upload_ready"
    if paths["section_images"]:
        return "compositing"
    if paths["audio_full"]["exists"]:
        return "images"
    if paths["script"]["exists"]:
        return "script_ready"
    return "idle"


def list_slugs_on_disk(require_script: bool = True) -> list[str]:
    """掃描 data/scripts/，回傳所有已存在的 slug。

    - require_script=True（預設）：只列有 script.json 的完整集數
    - False：包含只做到研究階段（有 research.json 或 research_prompt.md）的集數
    """
    if not SCRIPTS_DIR.exists():
        return []

    def _ok(d: Path) -> bool:
        if not d.is_dir():
            return False
        if require_script:
            return (d / "script.json").exists()
        return any((d / name).exists() for name in
                   ("script.json", "research.json", "research_prompt.md"))

    return sorted([d.name for d in SCRIPTS_DIR.iterdir() if _ok(d)], reverse=True)
