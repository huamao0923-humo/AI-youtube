"""TTS 配音引擎 — 優先使用 edge-tts（台灣男聲），可升級為 XTTS-v2。

配音策略（優先順序）：
  1. edge-tts  — zh-TW-YunJheNeural（微軟 Azure Neural，免費，無需 API Key）
  2. XTTS-v2   — 若有聲音樣本 + TTS 套件，複製自訂聲音
  3. 靜音佔位  — 以上皆失敗時，生成靜音 WAV 讓 pipeline 繼續

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

# edge-tts 台灣男聲（可改為 zh-TW-HsiaoChenNeural 女聲）
EDGE_TTS_VOICE = "zh-TW-YunJheNeural"


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
        communicate = edge_tts.Communicate(text, EDGE_TTS_VOICE)
        await communicate.save(str(out_mp3))
        return True
    except ImportError:
        logger.warning("edge-tts 未安裝。請執行：pip install edge-tts")
        return False
    except Exception as e:
        logger.warning(f"edge-tts 合成失敗：{e}")
        return False


def _generate_with_edge_tts(sections: list[dict], out_dir: Path) -> list[Path]:
    """用 edge-tts 逐段合成，回傳 WAV 路徑清單。失敗的段落用靜音佔位。"""

    async def _run_all():
        results = []
        for sec in sections:
            sid = sec["section_id"]
            narration = sec.get("narration", "").strip()
            duration = sec.get("duration_seconds", 5)
            wav_path = out_dir / f"section_{sid:03d}.wav"

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
                if not converted:
                    logger.warning(f"段落 {sid} MP3→WAV 轉換失敗，改用靜音")
                    _make_silence(duration, wav_path)
            else:
                _make_silence(duration, wav_path)

            logger.info(f"段落 {sid} 配音完成（edge-tts {EDGE_TTS_VOICE}）")
            results.append(wav_path)

        return results

    return asyncio.run(_run_all())


# ─── XTTS-v2 合成（個人聲音，選用）────────────────────────

def _generate_with_xtts(sections: list[dict], out_dir: Path,
                         voice_sample: Path) -> list[Path] | None:
    """嘗試用 XTTS-v2 合成。若套件未安裝或失敗回傳 None。"""
    try:
        from TTS.api import TTS as CoquiTTS
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info(f"載入 XTTS-v2（device={device}，樣本：{voice_sample}）")
        tts = CoquiTTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
    except ImportError:
        return None
    except Exception as e:
        logger.warning(f"XTTS-v2 載入失敗：{e}")
        return None

    results = []
    for sec in sections:
        sid = sec["section_id"]
        narration = sec.get("narration", "").strip()
        duration = sec.get("duration_seconds", 5)
        wav_path = out_dir / f"section_{sid:03d}.wav"

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

def generate_audio(script_path: Path) -> Path:
    """
    讀取 script.json，逐段生成配音，合併為 audio_full.wav。

    配音優先順序：XTTS-v2（有樣本）> edge-tts > 靜音佔位
    """
    import json
    script = json.loads(script_path.read_text(encoding="utf-8"))
    sections = script.get("script_sections", [])
    slug = script_path.parent.name
    out_dir = AUDIO_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    seg_paths: list[Path] | None = None

    # 嘗試 XTTS-v2（有個人聲音樣本時）
    voice_sample = _find_voice_sample()
    if voice_sample:
        logger.info("找到聲音樣本，嘗試 XTTS-v2...")
        seg_paths = _generate_with_xtts(sections, out_dir, voice_sample)
        if seg_paths:
            mode = "XTTS-v2（個人聲音）"

    # Fallback：edge-tts 台灣男聲
    if seg_paths is None:
        logger.info(f"使用 edge-tts（{EDGE_TTS_VOICE}）...")
        seg_paths = _generate_with_edge_tts(sections, out_dir)
        mode = f"edge-tts {EDGE_TTS_VOICE}"

    # 合併所有段落
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
