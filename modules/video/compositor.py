"""FFmpeg 影片合成器 — B-roll 優先，退回 AI 生圖 + Ken Burns。

視覺策略（每個 section 會做以下之一）：
  1. B-roll 片段模式：有 Pexels 下載的 MP4 → 縮放 + 輕微 zoom-pan，可切成多個短片段（2-4 秒）
  2. 靜態圖模式：只有 AI 生圖 → Ken Burns 緩慢推拉

字幕：
  - .ass：subtitles= filter（FFmpeg 會自動解析 ASS override tag，支援動態變色放大）
  - .srt：subtitles= filter 外加 force_style

執行：
  python -m modules.video.compositor --script data/scripts/xxx/script.json \
      --audio data/audio/xxx/audio_full.wav --subtitle data/videos/xxx/subtitles.ass
"""
from __future__ import annotations

import argparse
import json
import platform
import random
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
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    candidates = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        str(PROJECT_ROOT / "tools" / "ffmpeg.exe"),
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    raise RuntimeError("找不到 ffmpeg，請安裝後加入 PATH")


def _ffprobe() -> str:
    if shutil.which("ffprobe"):
        return "ffprobe"
    candidates = [
        r"C:\ffmpeg\bin\ffprobe.exe",
        r"C:\Program Files\ffmpeg\bin\ffprobe.exe",
        str(PROJECT_ROOT / "tools" / "ffprobe.exe"),
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return "ffprobe"  # 讓它失敗也沒關係，會 fallback


def _probe_duration(path: Path) -> float:
    try:
        result = subprocess.run(
            [_ffprobe(), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip())
    except Exception as e:
        logger.debug(f"ffprobe 失敗（{path.name}）：{e}")
        return 0.0


def _run(cmd: list[str], timeout: int = 1800) -> None:
    logger.debug("FFmpeg: " + " ".join(str(c) for c in cmd))
    try:
        result = subprocess.run(cmd, timeout=timeout)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg 失敗（exit {result.returncode}）")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"FFmpeg 超時（>{timeout}s）")


def _prepare_subtitle_filter(subtitle_path: Path | None, tmp_dir: Path) -> str | None:
    """把字幕檔複製到純 ASCII 路徑，產生 subtitles/ass filter 字串。"""
    if not subtitle_path or not subtitle_path.exists():
        return None

    suffix = subtitle_path.suffix.lower()
    is_ass = suffix == ".ass"
    safe_path = tmp_dir / f"sub{suffix}"
    shutil.copy(subtitle_path, safe_path)

    path_str = str(safe_path)
    if platform.system() == "Windows":
        path_str = path_str.replace("\\", "/").replace(":", "\\:")

    if is_ass:
        # ASS 內建樣式，不覆寫 force_style
        return f"ass='{path_str}'"

    # SRT：強制樣式讓字大一點
    style = "FontName=Microsoft JhengHei,FontSize=28,PrimaryColour=&Hffffff,OutlineColour=&H000000,Outline=3,Shadow=1,Alignment=2,MarginV=60"
    return f"subtitles='{path_str}':force_style='{style}'"


def _build_scene_clip(
    ff: str,
    src_clip: Path,
    out: Path,
    duration: float,
    w: int,
    h: int,
    fps: int,
) -> None:
    """把一支 Pexels clip 剪出 duration 秒、scale+crop 成 1920x1080。

    乾淨的硬切輸出（無 fade in/out），讓後續 concat 時場景之間是自然跳切。
    """
    clip_dur = _probe_duration(src_clip)
    if clip_dur <= 0:
        clip_dur = duration + 1

    # 從 clip 中段取素材（避開開頭 0.3s 可能的片頭）
    max_start = max(0.0, clip_dur - duration - 0.2)
    if max_start <= 0.1:
        start = 0.0
    else:
        start = max(0.2, (clip_dur - duration) / 2)

    # 若 clip 比 duration 短，循環使用：用 loop filter
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h}:(in_w-{w})/2:(in_h-{h})/2,"
        f"setsar=1,setpts=PTS-STARTPTS"
    )

    cmd = [
        ff, "-y",
        "-ss", f"{start:.2f}",
        "-stream_loop", "-1",  # 不夠長就循環
        "-i", str(src_clip),
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-an",
        "-c:v", "libx264", "-preset", "fast", "-crf", "21",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        str(out),
    ]
    _run(cmd)


def _build_broll_segment(
    ff: str,
    clips: list[Path],
    out: Path,
    duration: float,
    w: int,
    h: int,
    fps: int,
    scene_secs: float,
) -> None:
    """從 clip pool 拼出 duration 秒的片段，每 scene_secs 換一支不同素材。

    - 無 fade in/out（硬切，避免閃爍）
    - 每段都走 libx264 CRF 21，編碼參數一致讓 concat 穩定
    """
    import math

    if not clips:
        raise RuntimeError("clip pool 為空")

    n_scenes = max(1, math.ceil(duration / scene_secs))
    scene_dur = duration / n_scenes

    tmp_parts: list[Path] = []
    for i in range(n_scenes):
        src = clips[i % len(clips)]
        part = out.with_name(f"{out.stem}_scene{i}.mp4")
        tmp_parts.append(part)
        _build_scene_clip(ff, src, part, scene_dur, w, h, fps)

    # 串接所有場景：重新編碼確保時戳連續（copy 會有 PTS jump → 閃爍）
    if len(tmp_parts) == 1:
        shutil.move(str(tmp_parts[0]), str(out))
    else:
        concat_list = out.with_name(f"{out.stem}_concat.txt")
        # 用絕對路徑，避免 ffmpeg 以 concat 檔所在目錄為基準產生雙重路徑
        concat_list.write_text(
            "\n".join(f"file '{p.resolve().as_posix()}'" for p in tmp_parts),
            encoding="utf-8",
        )
        _run([
            ff, "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c:v", "libx264", "-preset", "fast", "-crf", "21",
            "-pix_fmt", "yuv420p", "-r", str(fps),
            str(out),
        ])
        concat_list.unlink(missing_ok=True)
        for p in tmp_parts:
            p.unlink(missing_ok=True)


def _build_image_segment(
    ff: str,
    img: Path,
    out: Path,
    duration: float,
    w: int,
    h: int,
    fps: int,
) -> None:
    """單張圖片 + Ken Burns。"""
    if not img.exists():
        _run([ff, "-y", "-f", "lavfi",
              "-i", f"color=c=black:s={w}x{h}:r={fps}:d={duration}",
              "-c:v", "libx264", "-preset", "fast", "-crf", "23",
              "-pix_fmt", "yuv420p", str(out)])
        return

    d_frames = max(1, int(duration * fps))
    # 避開 zoompan 像素抖動：先上採樣 8× 讓 zoompan 內部的整數 x/y 取整誤差縮到 1/8 像素
    # 參考：https://trac.ffmpeg.org/ticket/4298
    zoom_inc = 0.05 / d_frames  # 整段線性放大 5%
    # 末端 eq 後製：提高飽和、亮度、gamma，解決彩墨太重太濁問題
    zoom_filter = (
        f"scale=8000:-2,"
        f"zoompan=z='min(zoom+{zoom_inc:.8f},1.05)':x='iw/2-(iw/zoom/2)'"
        f":y='ih/2-(ih/zoom/2)':d={d_frames}:s={w}x{h}:fps={fps},"
        f"eq=saturation=1.25:brightness=0.05:gamma=1.08"
    )
    _run([ff, "-y", "-loop", "1", "-i", str(img),
          "-vf", zoom_filter,
          "-t", str(duration), "-c:v", "libx264",
          "-preset", "fast", "-crf", "23",
          "-pix_fmt", "yuv420p", str(out)])


def compose(
    script_path: Path,
    audio_path: Path | None = None,
    subtitle_path: Path | None = None,
    use_broll: bool = True,
    *,
    slug: str | None = None,
) -> Path:
    """主合成函式，回傳最終 MP4 路徑。

    slug 有值時進度寫到 EpisodeStatus.progress_detail（精確到該集），
    無值時 fallback 到 legacy PipelineStatus（daily_pipeline 直接呼叫時）。
    """
    script = json.loads(script_path.read_text(encoding="utf-8"))
    if slug is None:
        slug = script_path.parent.name
    sections = script.get("script_sections", [])

    img_dir = IMAGES_DIR / slug
    out_dir = VIDEOS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    final_mp4 = out_dir / "final.mp4"

    cfg = settings()
    cfg_video = cfg["video"]
    fps = cfg_video.get("fps", 30)
    resolution = cfg_video.get("resolution", "1920x1080")
    w, h = map(int, resolution.split("x"))
    scene_secs = float(cfg_video.get("scene_secs", cfg_video.get("cut_every_secs", 10.0)))

    ff = _ffmpeg()

    try:
        from modules.database import db_manager as _db
    except Exception:
        _db = None

    def _progress(msg: str) -> None:
        logger.info(msg)
        if _db:
            try:
                if slug:
                    _db.update_episode_progress(slug, msg)
                else:
                    _db.update_progress(msg)
            except Exception:
                pass

    # ── B-roll pool 預抓（每 scene_secs 秒一支不同 clip）──
    broll_pool: dict[int, list[Path]] = {}
    if use_broll:
        try:
            from modules.video.broll_fetcher import fetch_broll_pool_for_script, is_available
            if is_available():
                _progress(f"抓取 B-roll pool（每 {scene_secs:.0f}s 一支不同素材）…")
                broll_pool = fetch_broll_pool_for_script(script_path, scene_secs=scene_secs)
            else:
                logger.info("無 PEXELS_API_KEY，跳過 B-roll，改用 AI 生圖")
        except Exception as e:
            logger.warning(f"B-roll 抓取失敗，退回 AI 生圖：{e}")

    # ── 逐段生成片段 ──
    _progress(
        f"步驟 1/3：生成影片片段（共 {len(sections)} 段，"
        f"B-roll 覆蓋 {len(broll_pool)} 段，"
        f"共 {sum(len(v) for v in broll_pool.values())} 支素材）"
    )
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        segment_paths: list[Path] = []

        # 讀每段實際音訊時長，讓視覺與旁白對齊（比 script 的估計值準）
        def _real_section_duration(section_id: int, fallback: float) -> float:
            import wave
            af = PROJECT_ROOT / "data" / "audio" / slug / f"section_{section_id:03d}.wav"
            if af.exists():
                try:
                    with wave.open(str(af), "rb") as f:
                        d = f.getnframes() / f.getframerate()
                    if d > 0.3:
                        return d
                except Exception:
                    pass
            return fallback

        min_img_dur = float(cfg_video.get("image_duration_seconds", 0))
        for sec in sections:
            sid = sec["section_id"]
            dur = _real_section_duration(sid, float(sec.get("duration_seconds", 5)))
            if min_img_dur > 0:
                dur = max(dur, min_img_dur)
            seg = tmp / f"seg_{sid:03d}.mp4"

            clips = broll_pool.get(sid, [])
            if clips:
                _progress(
                    f"步驟 1/3：段 {sid}/{len(sections)} "
                    f"B-roll 剪輯（{dur:.1f}s，{len(clips)} 支素材輪播）"
                )
                try:
                    _build_broll_segment(ff, clips, seg, dur, w, h, fps, scene_secs)
                except Exception as e:
                    logger.warning(f"B-roll 段 {sid} 失敗，退回靜圖：{e}")
                    img = img_dir / f"section_{sid:03d}.png"
                    _build_image_segment(ff, img, seg, dur, w, h, fps)
            else:
                _progress(f"步驟 1/3：段 {sid}/{len(sections)} 靜圖 Ken Burns（{dur:.1f}s）")
                img = img_dir / f"section_{sid:03d}.png"
                _build_image_segment(ff, img, seg, dur, w, h, fps)

            segment_paths.append(seg)

        # ── 步驟 2：串接 ──
        _progress("步驟 2/3：串接所有片段…")
        concat_list = tmp / "concat.txt"
        concat_list.write_text(
            "\n".join(f"file '{p.as_posix()}'" for p in segment_paths),
            encoding="utf-8",
        )
        raw_video = tmp / "raw.mp4"
        # 用 re-encode 確保時間戳正確（不同來源 mp4 concat 用 copy 會飄）
        _run([ff, "-y", "-f", "concat", "-safe", "0",
              "-i", str(concat_list),
              "-c:v", "libx264", "-preset", "fast", "-crf", "21",
              "-pix_fmt", "yuv420p", "-r", str(fps),
              str(raw_video)])

        # ── 步驟 3：音訊 + 字幕 ──
        _progress("步驟 3/3：加音訊與字幕…")
        cmd = [ff, "-y", "-i", str(raw_video)]

        if audio_path and audio_path.exists():
            cmd += ["-i", str(audio_path), "-c:a", "aac", "-b:a", "192k", "-shortest"]
        else:
            cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-c:a", "aac", "-b:a", "128k", "-shortest"]

        sub_filter = _prepare_subtitle_filter(subtitle_path, tmp)
        if sub_filter:
            cmd += ["-vf", sub_filter, "-c:v", "libx264", "-preset", "fast", "-crf", "20"]
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
    ap.add_argument("--no-broll", action="store_true", help="停用 B-roll（只用 AI 生圖）")
    args = ap.parse_args()

    out = compose(args.script, args.audio, args.subtitle, use_broll=not args.no_broll)
    print(f"[OK] 影片輸出：{out}")


if __name__ == "__main__":
    main()
