"""字幕生成器 — 用 Whisper 對配音做語音識別，輸出 SRT。

無音訊模式：直接從 script.json 的 narration + timestamp 生成 SRT（近似字幕）。

執行：
  python -m modules.video.subtitle_generator --script data/scripts/xxx/script.json
  python -m modules.video.subtitle_generator --audio data/.../audio_full.wav  # 使用 Whisper
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from loguru import logger

from modules.common.config import PROJECT_ROOT
from modules.common.logging_setup import setup_logger

setup_logger()

VIDEOS_DIR = PROJECT_ROOT / "data" / "videos"


def _seconds(ts: str) -> float:
    """'1:35' → 95.0"""
    parts = ts.strip().split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        pass
    return 0.0


def _srt_time(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int((sec - int(sec)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _wrap_chinese(text: str, max_chars: int = 20) -> str:
    """中文換行：每行最多 max_chars 字。"""
    lines = []
    while text:
        lines.append(text[:max_chars])
        text = text[max_chars:]
    return "\n".join(lines)


def from_script(script_path: Path) -> Path:
    """從 script.json 的 narration 直接生成近似 SRT（無需音訊）。"""
    script = json.loads(script_path.read_text(encoding="utf-8"))
    slug = script_path.parent.name
    out_dir = VIDEOS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    srt_path = out_dir / "subtitles.srt"

    sections = script.get("script_sections", [])
    blocks = []
    idx = 1

    for sec in sections:
        start_sec = _seconds(sec.get("timestamp", "0:00"))
        duration = sec.get("duration_seconds", 10)
        narration = sec.get("narration", "").strip()

        if not narration:
            continue

        # 把旁白拆成約 5 秒一段
        sentences = re.split(r"[。！？\n]+", narration)
        sentences = [s.strip() for s in sentences if s.strip()]

        if not sentences:
            continue

        time_per = duration / max(len(sentences), 1)
        t = start_sec

        for sent in sentences:
            end = t + time_per
            blocks.append(
                f"{idx}\n"
                f"{_srt_time(t)} --> {_srt_time(end)}\n"
                f"{_wrap_chinese(sent)}\n"
            )
            t = end
            idx += 1

    srt_path.write_text("\n".join(blocks), encoding="utf-8")
    logger.info(f"SRT 字幕已生成：{srt_path}（{len(blocks)} 段）")
    return srt_path


def from_audio(audio_path: Path, script_path: Path) -> Path:
    """用 Whisper 對音訊做語音識別，生成精確 SRT。"""
    try:
        import whisper
    except ImportError:
        logger.warning("Whisper 未安裝，改用腳本字幕模式。安裝：pip install openai-whisper")
        return from_script(script_path)

    logger.info("載入 Whisper large-v3 模型（首次需下載約 3GB）")
    model = whisper.load_model("large-v3")

    logger.info(f"開始語音識別：{audio_path}")
    result = model.transcribe(str(audio_path), language="zh", word_timestamps=True)

    slug = script_path.parent.name
    out_dir = VIDEOS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    srt_path = out_dir / "subtitles_whisper.srt"

    blocks = []
    for i, seg in enumerate(result["segments"], 1):
        blocks.append(
            f"{i}\n"
            f"{_srt_time(seg['start'])} --> {_srt_time(seg['end'])}\n"
            f"{_wrap_chinese(seg['text'].strip())}\n"
        )

    srt_path.write_text("\n".join(blocks), encoding="utf-8")
    logger.info(f"Whisper SRT 已生成：{srt_path}（{len(blocks)} 段）")
    return srt_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", type=Path, required=True)
    ap.add_argument("--audio", type=Path, default=None, help="若有音訊，用 Whisper 識別")
    args = ap.parse_args()

    if args.audio:
        out = from_audio(args.audio, args.script)
    else:
        out = from_script(args.script)
    print(f"[OK] 字幕：{out}")


if __name__ == "__main__":
    main()
