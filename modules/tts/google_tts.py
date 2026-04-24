"""Google Cloud TTS — 真正台灣口音（cmn-TW WaveNet）。

免費額度：WaveNet 每月 100 萬字元（日更頻道約可撐 50-70 集）。
超出後 $16/1M 字元。

申請步驟：
  1. https://console.cloud.google.com → 建立專案
  2. 啟用 Cloud Text-to-Speech API
  3. API 與服務 → 憑證 → 建立 API 金鑰 → 限制為 Text-to-Speech API
  4. 在 .env 設 GOOGLE_TTS_API_KEY=AIza...

可用聲音（cmn-TW）：
  cmn-TW-Wavenet-A — 女（清亮）
  cmn-TW-Wavenet-B — 男（沉穩，推薦新聞播報）
  cmn-TW-Wavenet-C — 男（年輕有活力，推薦科技題）★ 預設
  cmn-TW-Standard-A/B/C — 標準版（免費額度 400 萬字元但品質較差）
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from loguru import logger


TTS_ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"
DEFAULT_VOICE = "cmn-TW-Wavenet-C"


def _api_key() -> str | None:
    return os.getenv("GOOGLE_TTS_API_KEY", "").strip() or None


def _voice() -> str:
    return os.getenv("GOOGLE_TTS_VOICE", "").strip() or DEFAULT_VOICE


def is_available() -> bool:
    return bool(_api_key())


def synthesize(text: str, out_mp3: Path, voice: str | None = None,
               speaking_rate: float = 1.0, pitch: float = 0.0) -> bool:
    """合成單段 MP3。回傳是否成功。

    speaking_rate: 0.25 ~ 4.0（1.0 正常）
    pitch: -20.0 ~ 20.0（0.0 正常）
    """
    key = _api_key()
    if not key:
        logger.debug("GOOGLE_TTS_API_KEY 未設定")
        return False

    body = {
        "input": {"text": text},
        "voice": {
            "languageCode": "cmn-TW",
            "name": voice or _voice(),
        },
        "audioConfig": {
            "audioEncoding": "MP3",
            "sampleRateHertz": 24000,
            "speakingRate": speaking_rate,
            "pitch": pitch,
        },
    }

    req = urllib.request.Request(
        f"{TTS_ENDPOINT}?key={key}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:300]
        logger.warning(f"Google TTS HTTP {e.code}：{err_body}")
        return False
    except Exception as e:
        logger.warning(f"Google TTS 請求失敗：{e}")
        return False

    audio_b64 = resp.get("audioContent")
    if not audio_b64:
        logger.warning(f"Google TTS 回應無音訊：{str(resp)[:200]}")
        return False

    out_mp3.write_bytes(base64.b64decode(audio_b64))
    return True


def synthesize_sections(sections: list[dict], out_dir: Path) -> list[Path] | None:
    """逐段合成 → MP3 → 交由外層轉 WAV。失敗的段落回傳 None 讓外層 fallback。

    回傳 MP3 路徑清單（或 None）。
    """
    from modules.tts.text_normalizer import normalize_for_tts

    if not is_available():
        return None

    voice = _voice()
    logger.info(f"使用 Google Cloud TTS（{voice}）")

    results: list[Path] = []
    for sec in sections:
        sid = sec["section_id"]
        narration = sec.get("narration", "").strip()
        mp3_path = out_dir / f"section_{sid:03d}.mp3"

        if not narration:
            results.append(mp3_path)  # 空段，外層判斷會用靜音
            continue

        if mp3_path.exists() and mp3_path.stat().st_size > 0:
            logger.info(f"段落 {sid} MP3 已存，跳過")
            results.append(mp3_path)
            continue

        ok = synthesize(normalize_for_tts(narration), mp3_path, voice=voice)
        if not ok:
            logger.error(f"段落 {sid} Google TTS 失敗，將退回下一層")
            return None
        logger.info(f"段落 {sid} ✅ Google TTS 完成")
        results.append(mp3_path)

    return results


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="大家好，這是 Google Cloud TTS 的台灣口音測試。")
    ap.add_argument("--voice", default=None)
    ap.add_argument("--out", type=Path, default=Path("google_tts_test.mp3"))
    args = ap.parse_args()

    if not is_available():
        print("[ERROR] 請先設定 GOOGLE_TTS_API_KEY")
        return

    ok = synthesize(args.text, args.out, voice=args.voice)
    if ok:
        size_kb = args.out.stat().st_size // 1024
        print(f"[OK] 合成完成：{args.out}（{size_kb} KB，voice={args.voice or _voice()}）")
    else:
        print("[FAIL] 合成失敗，請檢查 API Key / 網路 / 配額")


if __name__ == "__main__":
    main()
