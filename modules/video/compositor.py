"""FFmpeg 影片合成器 — 把圖片序列 + 音訊 + 字幕合成為最終 MP4。

無音訊模式：若 audio_path 為 None，生成靜音影片（方便測試）。
字幕：從 subtitle_path（SRT）燒錄進影片。

執行：
  python -m modules.video.compositor --script data/scripts/xxx/script.json
  python -m modules.video.compositor --script ... --audio data/.../audio_full.wav
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from loguru import logger

from modules.common.config import PROJECT_ROOT, settings
from modules.common.logging_setup import setup_logger

setup_logger()

VIDEOS_DIR = PROJECT_ROOT / "data" / "videos"
IMAGES_DIR = PROJECT_ROOT / "data" / "images"


def _ffmpeg() -> str:
    """找 ffmpeg 執行檔。"""
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    # Windows 常見路徑
    candidates = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        str(PROJECT_ROOT / "tools" / "ffmpeg.exe"),
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    raise RuntimeError("找不到 ffmpeg，請安裝後加入 PATH：https://ffmpeg.org/download.html")


def _run(cmd: list[str]) -> None:
    logger.debug("FFmpeg: " + " ".join(str(c) for c in cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"FFmpeg 錯誤：\n{result.stderr[-2000:]}")
        raise RuntimeError(f"FFmpeg 失敗（exit {result.returncode}）")


def compose(
    script_path: Path,
    audio_path: Path | None = None,
    subtitle_path: Path | None = None,
) -> Path:
    """主合成函式，回傳最終 MP4 路徑。"""
    script = json.loads(script_path.read_text(encoding="utf-8"))
    slug = script_path.parent.name
    sections = script.get("script_sections", [])

    img_dir = IMAGES_DIR / slug
    out_dir = VIDEOS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    final_mp4 = out_dir / "final.mp4"

    cfg_video = settings()["video"]
    fps = cfg_video.get("fps", 30)
    resolution = cfg_video.get("resolution", "1920x1080")
    w, h = map(int, resolution.split("x"))

    ff = _ffmpeg()

    # ── 步驟 1：每張圖片依時長轉成短片段 ──
    logger.info("步驟 1/3：圖片轉影片片段")
    segment_paths: list[Path] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        for sec in sections:
            sid = sec["section_id"]
            dur = sec.get("duration_seconds", 5)
            img = img_dir / f"section_{sid:03d}.png"
            seg = tmp / f"seg_{sid:03d}.mp4"

            if not img.exists():
                # 圖片不存在：用黑畫面
                _run([ff, "-y", "-f", "lavfi",
                      "-i", f"color=c=black:s={w}x{h}:r={fps}:d={dur}",
                      "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                      str(seg)])
            else:
                # Ken Burns 輕微縮放動畫
                zoom_filter = (
                    f"zoompan=z='min(zoom+0.0005,1.05)':x='iw/2-(iw/zoom/2)'"
                    f":y='ih/2-(ih/zoom/2)':d={dur * fps}:s={w}x{h}:fps={fps}"
                )
                _run([ff, "-y", "-loop", "1", "-i", str(img),
                      "-vf", zoom_filter,
                      "-t", str(dur), "-c:v", "libx264",
                      "-preset", "fast", "-crf", "23", str(seg)])

            segment_paths.append(seg)

        # ── 步驟 2：串接所有片段 ──
        logger.info("步驟 2/3：串接片段")
        concat_list = tmp / "concat.txt"
        concat_list.write_text(
            "\n".join(f"file '{p}'" for p in segment_paths), encoding="utf-8"
        )
        raw_video = tmp / "raw.mp4"
        _run([ff, "-y", "-f", "concat", "-safe", "0",
              "-i", str(concat_list), "-c", "copy", str(raw_video)])

        # ── 步驟 3：加音訊 + 字幕 → 最終輸出 ──
        logger.info("步驟 3/3：加音訊與字幕")
        cmd = [ff, "-y", "-i", str(raw_video)]

        if audio_path and audio_path.exists():
            cmd += ["-i", str(audio_path), "-c:a", "aac", "-b:a", "192k",
                    "-shortest"]
        else:
            # 無音訊：生成靜音
            cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-c:a", "aac", "-b:a", "128k", "-shortest"]

        if subtitle_path and subtitle_path.exists():
            cmd += ["-vf", f"subtitles={subtitle_path}:force_style='FontSize=20,"
                    "PrimaryColour=&H00FFFF00,OutlineColour=&H00000000,Outline=2,"
                    "Alignment=2'"]
            cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "20"]
        else:
            cmd += ["-c:v", "copy"]

        cmd += ["-movflags", "+faststart", str(final_mp4)]
        _run(cmd)

    size_mb = final_mp4.stat().st_size / 1024 / 1024
    logger.info(f"影片合成完成：{final_mp4}（{size_mb:.1f} MB）")
    return final_mp4


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", type=Path, required=True)
    ap.add_argument("--audio", type=Path, default=None)
    ap.add_argument("--subtitle", type=Path, default=None)
    args = ap.parse_args()

    out = compose(args.script, args.audio, args.subtitle)
    print(f"[OK] 影片輸出：{out}")


if __name__ == "__main__":
    main()
