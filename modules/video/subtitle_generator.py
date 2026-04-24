"""字幕生成器 — Whisper 識別後輸出 ASS（動態字幕，可高亮關鍵字）或 SRT。

兩種格式：
  - SRT（舊）：靜態，給不支援 ASS 的平台
  - ASS（新）：word-level，關鍵字自動放大變色，適合「商業本質」風格影片

無音訊時從 script.json 的 narration + timestamp 推算近似時間軸。

執行：
  python -m modules.video.subtitle_generator --script data/scripts/xxx/script.json
  python -m modules.video.subtitle_generator --audio data/.../audio_full.wav --script ... --ass
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

# ASS 色碼 (&HBBGGRR)
ASS_WHITE  = "&H00FFFFFF"
ASS_YELLOW = "&H0000FFFF"   # 關鍵字高亮色
ASS_ORANGE = "&H000080FF"
ASS_BLACK  = "&H00000000"
ASS_SHADOW = "&H64000000"   # 半透明黑


def _seconds(ts: str) -> float:
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


def _ass_time(sec: float) -> str:
    """ASS 時間格式：H:MM:SS.CC（百分秒）。"""
    if sec < 0:
        sec = 0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _wrap_chinese(text: str, max_chars: int = 20) -> str:
    lines = []
    while text:
        lines.append(text[:max_chars])
        text = text[max_chars:]
    return "\n".join(lines)


def _collect_highlight_keywords(script: dict) -> list[str]:
    """從 script.json 收集所有要高亮的關鍵字（去重、去空）。"""
    kws: set[str] = set()
    # 全域欄位
    for kw in script.get("highlight_keywords", []) or []:
        if isinstance(kw, str) and kw.strip():
            kws.add(kw.strip())
    # 每段可自帶 highlight
    for sec in script.get("script_sections", []) or []:
        for kw in sec.get("highlight_keywords", []) or []:
            if isinstance(kw, str) and kw.strip():
                kws.add(kw.strip())
        # 相容：部分 prompt 會輸出 highlights
        for kw in sec.get("highlights", []) or []:
            if isinstance(kw, str) and kw.strip():
                kws.add(kw.strip())
    # 按長度降序：優先匹配長詞，避免短詞先吃掉長詞一部分
    return sorted(kws, key=len, reverse=True)


def _escape_ass(text: str) -> str:
    """ASS 文字轉義。"""
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _highlight_text(text: str, keywords: list[str]) -> str:
    """關鍵字高亮功能已停用，直接回傳 ASS 轉義後文字。

    （保留 keywords 參數與簽名以維持向後相容，但忽略內容）
    """
    return _escape_ass(text)


def _chunk_by_words(words: list[dict], target_secs: float = 2.5) -> list[dict]:
    """將 word 時間戳陣列切成約 target_secs 秒的短句，回傳 [{start,end,text}]。"""
    if not words:
        return []

    chunks: list[dict] = []
    cur_words: list[dict] = []
    cur_start = words[0].get("start", 0.0)

    for w in words:
        cur_words.append(w)
        elapsed = (w.get("end", cur_start) - cur_start)
        # 以標點或時間長度斷句
        txt = (w.get("word") or "").strip()
        is_punct = bool(txt) and txt[-1] in "。！？，、,.!?;:；："
        if elapsed >= target_secs or is_punct:
            text = "".join((x.get("word") or "") for x in cur_words).strip()
            if text:
                chunks.append({
                    "start": cur_start,
                    "end": cur_words[-1].get("end", cur_start + elapsed),
                    "text": text,
                })
            cur_words = []
            if w is not words[-1]:
                cur_start = w.get("end", cur_start)

    if cur_words:
        text = "".join((x.get("word") or "") for x in cur_words).strip()
        if text:
            chunks.append({
                "start": cur_start,
                "end": cur_words[-1].get("end", cur_start + 1),
                "text": text,
            })
    return chunks


def _chunk_segment(seg: dict, target_secs: float = 2.5) -> list[dict]:
    """Whisper segment 切成更短的塊。優先用 word-level，退回按字元估算。"""
    words = seg.get("words") or []
    if words:
        return _chunk_by_words(words, target_secs=target_secs)

    # Fallback：沒 word 就按字數均分
    text = (seg.get("text") or "").strip()
    start = seg.get("start", 0.0)
    end = seg.get("end", start + 1.0)
    total = max(end - start, 0.5)
    if total <= target_secs or len(text) <= 12:
        return [{"start": start, "end": end, "text": text}] if text else []

    n = max(1, int(round(total / target_secs)))
    chunk_len = max(1, len(text) // n)
    chunks = []
    for i in range(n):
        s = start + total * (i / n)
        e = start + total * ((i + 1) / n)
        t = text[i * chunk_len : (i + 1) * chunk_len if i < n - 1 else len(text)]
        if t.strip():
            chunks.append({"start": s, "end": e, "text": t.strip()})
    return chunks


# ─── SRT 舊版（保留相容）──────────────────────────────────

def from_script(script_path: Path) -> Path:
    """從 script.json 直接生成近似 SRT。"""
    script = json.loads(script_path.read_text(encoding="utf-8"))
    slug = script_path.parent.name
    out_dir = VIDEOS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    srt_path = out_dir / "subtitles.srt"

    sections = script.get("script_sections", [])
    blocks: list[str] = []
    idx = 1
    for sec in sections:
        start_sec = _seconds(sec.get("timestamp", "0:00"))
        duration = sec.get("duration_seconds", 10)
        narration = sec.get("narration", "").strip()
        if not narration:
            continue
        sentences = [s.strip() for s in re.split(r"[。！？\n]+", narration) if s.strip()]
        if not sentences:
            continue
        time_per = duration / max(len(sentences), 1)
        t = start_sec
        for sent in sentences:
            end = t + time_per
            blocks.append(
                f"{idx}\n{_srt_time(t)} --> {_srt_time(end)}\n{_wrap_chinese(sent)}\n"
            )
            t = end
            idx += 1

    srt_path.write_text("\n".join(blocks), encoding="utf-8")
    logger.info(f"SRT 字幕已生成：{srt_path}（{len(blocks)} 段）")
    return srt_path


def from_audio(audio_path: Path, script_path: Path) -> Path:
    """Whisper 識別後輸出 SRT。"""
    try:
        import whisper
    except ImportError:
        logger.warning("Whisper 未安裝，改用腳本字幕模式")
        return from_script(script_path)

    logger.info("載入 Whisper large-v3 模型")
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
            f"{i}\n{_srt_time(seg['start'])} --> {_srt_time(seg['end'])}\n"
            f"{_wrap_chinese(seg['text'].strip())}\n"
        )
    srt_path.write_text("\n".join(blocks), encoding="utf-8")
    logger.info(f"Whisper SRT 已生成：{srt_path}（{len(blocks)} 段）")
    return srt_path


# ─── ASS 新版（動態字幕）────────────────────────────────

ASS_HEADER_TEMPLATE = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{fontname},{fontsize},{primary},&H000000FF,{outline_color},{shadow_color},1,0,0,0,100,100,0,0,1,{outline_w},2,2,80,80,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _build_ass_header(fontname: str = "Microsoft JhengHei",
                      fontsize: int = 64,
                      margin_v: int = 120,
                      outline_w: int = 4) -> str:
    return ASS_HEADER_TEMPLATE.format(
        fontname=fontname,
        fontsize=fontsize,
        primary=ASS_WHITE,
        outline_color=ASS_BLACK,
        shadow_color=ASS_SHADOW,
        outline_w=outline_w,
        margin_v=margin_v,
    )


def _build_dialogue(start: float, end: float, text: str,
                    keywords: list[str], pop_in: bool = True) -> str:
    """產一行 Dialogue。pop_in 會加入淡入 + 輕微放大動畫。"""
    body = _highlight_text(text, keywords)
    prefix = r"{\fad(120,80)\blur0.4}" if pop_in else ""
    return f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{prefix}{body}"


def _write_ass(out_path: Path, chunks: list[dict], keywords: list[str],
               fontsize: int, margin_v: int) -> None:
    header = _build_ass_header(fontsize=fontsize, margin_v=margin_v)
    lines: list[str] = []
    for c in chunks:
        text = (c.get("text") or "").strip()
        if not text:
            continue
        lines.append(_build_dialogue(c["start"], c["end"], text, keywords))
    out_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def from_audio_ass(audio_path: Path, script_path: Path,
                   fontsize: int = 72, margin_v: int = 140,
                   target_chunk_secs: float = 2.2, **_unused) -> Path:
    """Whisper 識別 → ASS 動態字幕（關鍵字放大變色）。"""
    try:
        import whisper
    except ImportError:
        logger.warning("Whisper 未安裝，改用 script ASS 模式")
        return from_script_ass(script_path, fontsize=fontsize, margin_v=margin_v)

    script = json.loads(script_path.read_text(encoding="utf-8"))
    keywords = _collect_highlight_keywords(script)
    logger.info(f"高亮關鍵字（{len(keywords)}）：{keywords[:10]}")

    logger.info("載入 Whisper large-v3…")
    model = whisper.load_model("large-v3")
    logger.info(f"語音識別：{audio_path}")
    result = model.transcribe(
        str(audio_path),
        language="zh",
        word_timestamps=True,
        condition_on_previous_text=False,
    )

    chunks: list[dict] = []
    for seg in result["segments"]:
        chunks.extend(_chunk_segment(seg, target_secs=target_chunk_secs))

    slug = script_path.parent.name
    out_dir = VIDEOS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    ass_path = out_dir / "subtitles.ass"
    _write_ass(ass_path, chunks, keywords, fontsize=fontsize, margin_v=margin_v)
    logger.info(f"ASS 動態字幕已生成：{ass_path}（{len(chunks)} 塊，高亮 {len(keywords)} 個關鍵字）")
    return ass_path


_PUNCT_SPLIT = re.compile(r"(?<=[。！？\n])|(?<=[，、；：,])")
_ASCII_RUN = re.compile(r"[A-Za-z0-9%$.\-]+")


def _smart_break_points(text: str, max_chars: int) -> list[int]:
    """回傳「可以斷行的字元索引」清單。不會落在 ASCII run 中間（保護 AI/ChatGPT/393% 這種詞）。"""
    protected: set[int] = set()
    for m in _ASCII_RUN.finditer(text):
        # run 內部每個位置都不能斷
        for i in range(m.start() + 1, m.end()):
            protected.add(i)
    breaks: list[int] = []
    for i in range(1, len(text)):
        if i not in protected:
            breaks.append(i)
    return breaks


def _smart_chunk(text: str, max_chars: int) -> list[str]:
    """把文字切成每塊 ≤ max_chars，優先在標點後斷，絕不切斷 ASCII 詞。"""
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []

    pieces: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_chars:
            pieces.append(remaining)
            break

        # 先找 max_chars 範圍內最靠後的標點位置
        window = remaining[:max_chars + 1]
        cut = -1
        for idx, ch in enumerate(window):
            if ch in "。！？，、；：,.!?;:":
                cut = idx + 1  # 標點後斷
        if cut > 0 and cut <= max_chars + 1:
            pieces.append(remaining[:cut].strip())
            remaining = remaining[cut:].lstrip()
            continue

        # 無標點 → 找最靠近 max_chars 的「安全斷點」（不在 ASCII run 中間）
        breaks = _smart_break_points(remaining[:max_chars + 3], max_chars)
        safe_cut = None
        for b in reversed(breaks):
            if b <= max_chars:
                safe_cut = b
                break
            # 若 ASCII 詞跨過 max_chars，稍微放寬到 max_chars+3
            if b <= max_chars + 3:
                safe_cut = b
                break
        if safe_cut is None:
            safe_cut = max_chars  # 保底
        pieces.append(remaining[:safe_cut].strip())
        remaining = remaining[safe_cut:].lstrip()

    return [p for p in pieces if p]


def _section_audio_duration(slug: str, section_id: int) -> float | None:
    """讀 data/audio/<slug>/section_NNN.wav 的實際時長。失敗回 None。"""
    import wave
    audio_file = PROJECT_ROOT / "data" / "audio" / slug / f"section_{section_id:03d}.wav"
    if not audio_file.exists():
        return None
    try:
        with wave.open(str(audio_file), "rb") as f:
            return f.getnframes() / f.getframerate()
    except Exception:
        return None


def from_script_ass(script_path: Path, fontsize: int = 72, margin_v: int = 140,
                    max_chars_per_chunk: int = 14, **_unused) -> Path:
    """從 script.json + 各段音訊實際長度 生成 ASS 字幕。

    不需要 Whisper：
    - 用各段 `section_NNN.wav` 的真實時長對齊時間軸（而非 script 估計值）
    - 依標點切短句，ASCII 詞不會被腰斬

    **_unused 吸收已移除的舊 kwargs（例如早期的 pop_in），避免殘留 caller 爆掉。
    """
    if _unused:
        logger.debug(f"from_script_ass 忽略過時參數：{list(_unused)}")
    script = json.loads(script_path.read_text(encoding="utf-8"))
    keywords = _collect_highlight_keywords(script)
    slug = script_path.parent.name

    chunks: list[dict] = []
    cursor = 0.0
    for sec in script.get("script_sections", []):
        sid = sec["section_id"]
        narration = sec.get("narration", "").strip()
        if not narration:
            continue

        # 段落實際時長：優先讀音訊檔，讀不到 fallback 到 script 估計值
        real_dur = _section_audio_duration(slug, sid)
        duration = real_dur if real_dur else float(sec.get("duration_seconds", 10))

        # 先按強標點切成自然句，再用 smart_chunk 限制長度
        pieces: list[str] = []
        for sent in re.split(r"(?<=[。！？\n])", narration):
            sent = sent.strip()
            if not sent:
                continue
            pieces.extend(_smart_chunk(sent, max_chars_per_chunk))

        if not pieces:
            cursor += duration
            continue

        # 依字數比例分配段內時長
        total_chars = sum(len(p) for p in pieces) or 1
        t = cursor
        for p in pieces:
            p_dur = duration * (len(p) / total_chars)
            chunks.append({"start": t, "end": t + p_dur, "text": p})
            t += p_dur
        cursor += duration

    out_dir = VIDEOS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    ass_path = out_dir / "subtitles.ass"
    _write_ass(ass_path, chunks, keywords, fontsize=fontsize, margin_v=margin_v)
    logger.info(f"ASS 字幕已生成：{ass_path}（{len(chunks)} 塊，總長 {cursor:.1f}s）")
    return ass_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", type=Path, required=True)
    ap.add_argument("--audio", type=Path, default=None)
    ap.add_argument("--ass", action="store_true", help="輸出 ASS 動態字幕（預設 SRT）")
    ap.add_argument("--fontsize", type=int, default=72)
    ap.add_argument("--margin-v", type=int, default=140)
    args = ap.parse_args()

    if args.ass:
        if args.audio:
            out = from_audio_ass(args.audio, args.script,
                                  fontsize=args.fontsize, margin_v=args.margin_v)
        else:
            out = from_script_ass(args.script,
                                   fontsize=args.fontsize, margin_v=args.margin_v)
    else:
        if args.audio:
            out = from_audio(args.audio, args.script)
        else:
            out = from_script(args.script)
    print(f"[OK] 字幕：{out}")


if __name__ == "__main__":
    main()
