"""XTTS-v2 配音引擎 — Module 4（需要聲音樣本 + CUDA）。

目前為佔位模組：
  - 若 TTS 套件未安裝，自動 fallback 到靜音模式
  - 等錄音樣本就位後，移除 fallback 即可啟用

安裝（需 CUDA 環境，獨立安裝避免衝突）：
  pip install TTS torch torchaudio --index-url https://download.pytorch.org/whl/cu121

執行：
  python -m modules.tts.xtts_engine --script data/scripts/xxx/script.json
"""
from __future__ import annotations

import argparse
import wave
import struct
from pathlib import Path

from loguru import logger

from modules.common.config import PROJECT_ROOT, settings
from modules.common.logging_setup import setup_logger

setup_logger()

AUDIO_DIR = PROJECT_ROOT / "data" / "audio"
VOICE_DIR = PROJECT_ROOT / "voice_samples" / "processed"


def _find_voice_sample() -> Path | None:
    """找最佳聲音樣本。"""
    samples = list(VOICE_DIR.glob("*.wav"))
    if not samples:
        return None
    # 優先選 best_sample.wav，否則取第一個
    best = VOICE_DIR / "best_sample.wav"
    return best if best.exists() else samples[0]


def _make_silence(duration_sec: float, out_path: Path) -> None:
    """生成靜音 WAV（佔位用）。"""
    sample_rate = 24000
    n_samples = int(sample_rate * duration_sec)
    with wave.open(str(out_path), "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(struct.pack("<" + "h" * n_samples, *([0] * n_samples)))


def generate_audio(script_path: Path) -> Path:
    """
    讀取 script.json，逐段生成配音，合併為 audio_full.wav。

    若 TTS 未安裝或無聲音樣本，生成靜音佔位檔案（pipeline 不中斷）。
    """
    import json
    script = json.loads(script_path.read_text(encoding="utf-8"))
    sections = script.get("script_sections", [])
    slug = script_path.parent.name
    out_dir = AUDIO_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    voice_sample = _find_voice_sample()
    tts_available = False

    # 嘗試載入 TTS
    tts = None
    if voice_sample:
        try:
            from TTS.api import TTS as CoquiTTS
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info(f"載入 XTTS-v2（device={device}）")
            tts = CoquiTTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
            tts_available = True
            logger.info(f"使用聲音樣本：{voice_sample}")
        except ImportError:
            logger.warning("TTS 套件未安裝，使用靜音模式。安裝：pip install TTS")
        except Exception as e:
            logger.warning(f"XTTS-v2 載入失敗：{e}，使用靜音模式")
    else:
        logger.warning(
            "找不到聲音樣本，使用靜音模式。\n"
            "請將 .wav 錄音放至：voice_samples/processed/best_sample.wav"
        )

    seg_paths: list[Path] = []

    for sec in sections:
        sid = sec["section_id"]
        narration = sec.get("narration", "").strip()
        duration = sec.get("duration_seconds", 5)
        seg_path = out_dir / f"section_{sid:03d}.wav"

        if seg_path.exists():
            logger.info(f"段落 {sid} 音訊已存在，跳過")
            seg_paths.append(seg_path)
            continue

        if tts_available and narration:
            try:
                tts.tts_to_file(
                    text=narration,
                    speaker_wav=str(voice_sample),
                    language="zh-cn",  # XTTS-v2 繁中用 zh-cn
                    file_path=str(seg_path),
                )
                logger.info(f"段落 {sid} 配音完成")
            except Exception as e:
                logger.warning(f"段落 {sid} TTS 失敗：{e}，改用靜音")
                _make_silence(duration, seg_path)
        else:
            _make_silence(duration, seg_path)

        seg_paths.append(seg_path)

    # 合併所有段落
    full_path = out_dir / "audio_full.wav"
    _concat_wavs(seg_paths, full_path)
    mode = "XTTS-v2" if tts_available else "靜音佔位"
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
        with wv.open(str(p), "rb") as f:
            if params is None:
                params = f.getparams()
            data.append(f.readframes(f.getnframes()))

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
