"""TTS 配音引擎 — 多層 fallback，確保 pipeline 永不中斷。

配音策略（優先順序）：
  1. Google Cloud TTS — cmn-TW-Wavenet-C（真正台灣口音，需 GOOGLE_TTS_API_KEY）
  2. XTTS-v2   — 若有個人聲音樣本 + TTS 套件，複製自訂聲音
  3. edge-tts  — zh-TW-YunJheNeural（微軟 Azure Neural，免費，無需 API Key）
  4. 靜音佔位  — 以上皆失敗時，生成靜音 WAV 讓 pipeline 繼續

安裝 edge-tts：
  pip install edge-tts

升級為個人聲音（之後補）：
  pip install TTS torch torchaudio --index-url https://download.pytorch.org/whl/cu121
  將錄音放至 voice_samples/processed/best_sample.wav

執行：
  python -m modules.tts.xtts_engine --script data/scripts/xxx/script.json
"""
from __future__ import annotations

import argparse
import asyncio
import subprocess
import wave
import struct
from pathlib import Path

from loguru import logger

from modules.common.config import PROJECT_ROOT, settings
from modules.common.logging_setup import setup_logger

setup_logger()

AUDIO_DIR = PROJECT_ROOT / "data" / "audio"
VOICE_DIR = PROJECT_ROOT / "voice_samples" / "processed"

# edge-tts 聲音選項（依優先序查 env → settings.yaml → default）
# 可選：
#   zh-TW-YunJheNeural   — 男（台灣，穩重，預設）
#   zh-TW-HsiaoChenNeural — 女（台灣，清亮）
#   zh-TW-HsiaoYuNeural   — 女（台灣，溫柔）
#   zh-CN-YunxiNeural     — 男（情緒起伏大，電影感）
#   zh-CN-YunjianNeural   — 男（體育播報風，有力道）
DEFAULT_EDGE_TTS_VOICE = "zh-TW-YunJheNeural"


def _resolve_voice() -> str:
    import os
    v = os.getenv("EDGE_TTS_VOICE", "").strip()
    if v:
        return v
    try:
        cfg = settings().get("tts", {}) or {}
        return (cfg.get("edge_tts_voice") or DEFAULT_EDGE_TTS_VOICE).strip()
    except Exception:
        return DEFAULT_EDGE_TTS_VOICE


EDGE_TTS_VOICE = _resolve_voice()


def _progress(slug: str | None, msg: str) -> None:
    """寫進度到 EpisodeStatus.progress_detail（slug 為 None 時只 log）。"""
    logger.info(msg)
    if slug:
        try:
            from modules.database import db_manager
            db_manager.update_episode_progress(slug, msg)
        except Exception:
            pass


# ─── 工具函式 ────────────────────────────────────────────

def _make_silence(duration_sec: float, out_path: Path) -> None:
    """生成靜音 WAV（pipeline 佔位用）。"""
    sample_rate = 24000
    n_samples = int(sample_rate * duration_sec)
    with wave.open(str(out_path), "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(struct.pack("<" + "h" * n_samples, *([0] * n_samples)))


def _mp3_to_wav(mp3_path: Path, wav_path: Path) -> bool:
    """用 ffmpeg 把 MP3 轉成 WAV（24000Hz mono）。回傳是否成功。"""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(mp3_path),
                "-ar", "24000",
                "-ac", "1",
                str(wav_path),
            ],
            capture_output=True,
            timeout=60,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning(f"ffmpeg 轉換失敗：{e}")
        return False


def _find_voice_sample() -> Path | None:
    """找 XTTS-v2 聲音樣本。"""
    samples = list(VOICE_DIR.glob("*.wav"))
    if not samples:
        return None
    best = VOICE_DIR / "best_sample.wav"
    return best if best.exists() else samples[0]


# ─── edge-tts 合成 ───────────────────────────────────────

async def _edge_tts_section(text: str, out_mp3: Path) -> bool:
    """用 edge-tts 合成單段台灣男聲，輸出 MP3。"""
    try:
        import edge_tts
        from modules.tts.text_normalizer import normalize_for_tts
        normalized = normalize_for_tts(text)
        communicate = edge_tts.Communicate(normalized, EDGE_TTS_VOICE)
        await communicate.save(str(out_mp3))
        return True
    except ImportError:
        logger.warning("edge-tts 未安裝。請執行：pip install edge-tts")
        return False
    except Exception as e:
        logger.warning(f"edge-tts 合成失敗：{e}")
        return False


def _generate_with_edge_tts(sections: list[dict], out_dir: Path,
                              slug: str | None = None) -> list[Path]:
    """用 edge-tts 逐段合成，回傳 WAV 路徑清單。失敗的段落用靜音佔位。"""

    total = len(sections)

    async def _run_all():
        results = []
        for i, sec in enumerate(sections, 1):
            sid = sec["section_id"]
            narration = sec.get("narration", "").strip()
            duration = sec.get("duration_seconds", 5)
            wav_path = out_dir / f"section_{sid:03d}.wav"

            _progress(slug, f"🎙️ 配音 {i}/{total}（edge-tts {EDGE_TTS_VOICE}）")

            if wav_path.exists():
                logger.info(f"段落 {sid} 音訊已存在，跳過")
                results.append(wav_path)
                continue

            if not narration:
                _make_silence(duration, wav_path)
                results.append(wav_path)
                continue

            mp3_path = out_dir / f"section_{sid:03d}.mp3"
            success = await _edge_tts_section(narration, mp3_path)

            if success and mp3_path.exists():
                converted = _mp3_to_wav(mp3_path, wav_path)
                mp3_path.unlink(missing_ok=True)   # 清除暫存 MP3
                if converted:
                    logger.info(f"段落 {sid} ✅ 配音完成（{EDGE_TTS_VOICE}）")
                else:
                    logger.error(f"段落 {sid} ❌ MP3→WAV 轉換失敗，改用靜音")
                    _make_silence(duration, wav_path)
            else:
                logger.error(
                    f"段落 {sid} ❌ edge-tts 合成失敗，改用靜音佔位。"
                    f"可能原因：voice={EDGE_TTS_VOICE} 不可用／網路問題／文字過長"
                )
                _make_silence(duration, wav_path)

            results.append(wav_path)

        return results

    return asyncio.run(_run_all())


# ─── Google Cloud TTS 合成（真台灣口音）──────────────────

def _generate_with_google(sections: list[dict], out_dir: Path,
                            slug: str | None = None) -> list[Path] | None:
    """用 Google Cloud TTS 逐段合成 MP3，再轉 WAV。
    任何一段失敗就回傳 None，讓外層 fallback 到下一層引擎。
    """
    from modules.tts import google_tts

    total = len(sections)
    _progress(slug, f"🎙️ 呼叫 Google TTS（{total} 段）")
    mp3_paths = google_tts.synthesize_sections(sections, out_dir)
    if mp3_paths is None:
        return None

    wav_paths: list[Path] = []
    for i, (sec, mp3) in enumerate(zip(sections, mp3_paths), 1):
        sid = sec["section_id"]
        duration = sec.get("duration_seconds", 5)
        wav = out_dir / f"section_{sid:03d}.wav"
        _progress(slug, f"🎙️ 配音 {i}/{total}（Google TTS，轉 WAV 中）")

        if wav.exists() and wav.stat().st_size > 0:
            wav_paths.append(wav)
            mp3.unlink(missing_ok=True)
            continue

        if not mp3.exists() or mp3.stat().st_size == 0:
            _make_silence(duration, wav)
        else:
            if not _mp3_to_wav(mp3, wav):
                logger.error(f"段落 {sid} Google MP3→WAV 失敗，改靜音")
                _make_silence(duration, wav)
            mp3.unlink(missing_ok=True)

        wav_paths.append(wav)

    return wav_paths


# ─── XTTS-v2 合成（個人聲音，選用）────────────────────────

def _generate_with_xtts(sections: list[dict], out_dir: Path,
                         voice_sample: Path,
                         slug: str | None = None) -> list[Path] | None:
    """嘗試用 XTTS-v2 合成。若套件未安裝或失敗回傳 None。"""
    try:
        from TTS.api import TTS as CoquiTTS
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _progress(slug, f"🎙️ 載入 XTTS-v2（device={device}）…")
        tts = CoquiTTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
    except ImportError:
        return None
    except Exception as e:
        logger.warning(f"XTTS-v2 載入失敗：{e}")
        return None

    total = len(sections)
    results = []
    for i, sec in enumerate(sections, 1):
        sid = sec["section_id"]
        narration = sec.get("narration", "").strip()
        duration = sec.get("duration_seconds", 5)
        wav_path = out_dir / f"section_{sid:03d}.wav"

        _progress(slug, f"🎙️ 配音 {i}/{total}（XTTS-v2 個人聲音）")

        if wav_path.exists():
            results.append(wav_path)
            continue

        if narration:
            try:
                tts.tts_to_file(
                    text=narration,
                    speaker_wav=str(voice_sample),
                    language="zh-cn",
                    file_path=str(wav_path),
                )
                logger.info(f"段落 {sid} XTTS-v2 配音完成")
            except Exception as e:
                logger.warning(f"段落 {sid} XTTS-v2 失敗：{e}，改用靜音")
                _make_silence(duration, wav_path)
        else:
            _make_silence(duration, wav_path)

        results.append(wav_path)

    return results


# ─── 主合成流程 ──────────────────────────────────────────

def generate_audio(script_path: Path, *, slug: str | None = None) -> Path:
    """
    讀取 script.json，逐段生成配音，合併為 audio_full.wav。

    配音優先順序：Google TTS > XTTS-v2 > edge-tts > 靜音佔位
    slug 有值時會把每段進度寫入 EpisodeStatus.progress_detail。
    """
    import json
    script = json.loads(script_path.read_text(encoding="utf-8"))
    sections = script.get("script_sections", [])
    if slug is None:
        slug = script_path.parent.name
    out_dir = AUDIO_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    seg_paths: list[Path] | None = None
    mode = "unknown"

    # 第一優先：Google Cloud TTS（真台灣口音）
    try:
        from modules.tts import google_tts
        if google_tts.is_available():
            seg_paths = _generate_with_google(sections, out_dir, slug=slug)
            if seg_paths:
                mode = f"Google TTS {google_tts._voice()}"
    except Exception as e:
        logger.warning(f"Google TTS 嘗試失敗：{e}")

    # 第二優先：XTTS-v2（有個人聲音樣本時）
    if seg_paths is None:
        voice_sample = _find_voice_sample()
        if voice_sample:
            _progress(slug, "🎙️ 找到聲音樣本，嘗試 XTTS-v2…")
            seg_paths = _generate_with_xtts(sections, out_dir, voice_sample,
                                            slug=slug)
            if seg_paths:
                mode = "XTTS-v2（個人聲音）"

    # Fallback：edge-tts 台灣男聲
    if seg_paths is None:
        _progress(slug, f"🎙️ 使用 edge-tts（{EDGE_TTS_VOICE}）…")
        seg_paths = _generate_with_edge_tts(sections, out_dir, slug=slug)
        mode = f"edge-tts {EDGE_TTS_VOICE}"

    # 合併所有段落
    _progress(slug, "🎵 合併音檔…")
    full_path = out_dir / "audio_full.wav"
    _concat_wavs(seg_paths, full_path)
    logger.info(f"音訊合併完成（{mode}）：{full_path}")
    return full_path


def _concat_wavs(paths: list[Path], out: Path) -> None:
    """合併多個 WAV 檔案。"""
    import wave as wv
    data = []
    params = None
    for p in paths:
        if not p.exists() or p.stat().st_size == 0:
            continue
        try:
            with wv.open(str(p), "rb") as f:
                if params is None:
                    params = f.getparams()
                data.append(f.readframes(f.getnframes()))
        except Exception as e:
            logger.warning(f"讀取 {p.name} 失敗：{e}，跳過")

    if not data or not params:
        _make_silence(5, out)
        return

    with wv.open(str(out), "wb") as f:
        f.setparams(params)
        for chunk in data:
            f.writeframes(chunk)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", type=Path, required=True)
    args = ap.parse_args()
    out = generate_audio(args.script)
    size_kb = out.stat().st_size // 1024
    print(f"[OK] 音訊：{out}（{size_kb} KB）")


if __name__ == "__main__":
    main()
