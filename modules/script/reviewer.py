"""腳本 AI 審閱模組 — 由 Claude CLI 檢查腳本並輸出結構化 diff。

流程：
  1. review_script(script_path, section_ids=None) → 回傳 diff 結構（dict）
  2. apply_changes(script_path, accepted_changes) → 套用使用者接受的變更，
     同時把歷史寫入 script_revisions.json
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from modules.common.config import PROJECT_ROOT
from modules.common.claude_cli import run as claude_run

STYLE_GUIDE = PROJECT_ROOT / "config" / "style_guide.md"


def _load_style_guide() -> str:
    if STYLE_GUIDE.exists():
        return STYLE_GUIDE.read_text(encoding="utf-8")
    return "（未設定頻道風格指南）"


def _extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            text = text[start:end + 1]
    return json.loads(text)


REVIEW_FORMAT = """{
  "summary": "一句話總結修改內容（例：共修改 8 段，主要為：刪減冗言 5 處、修正事實 1 處、加強轉折 2 處）",
  "changes": [
    {
      "section_id": 7,
      "type": "redundancy | factual_error | logic_gap | pacing | style_drift | clarity",
      "before": "段落原本的 narration 全文",
      "after":  "建議修改後的 narration 全文",
      "reason": "為什麼要改（1-2 句具體說明）"
    }
  ]
}"""


def _build_prompt(script: dict, research: dict | None,
                  section_ids: list[int] | None) -> str:
    style = _load_style_guide()
    sections = script.get("script_sections", []) or []
    if section_ids:
        sections = [s for s in sections if s.get("section_id") in section_ids]

    sec_lines = []
    for s in sections:
        sid = s.get("section_id")
        typ = s.get("type", "")
        dur = s.get("duration_seconds", 0)
        narr = (s.get("narration") or "").strip()
        sec_lines.append(f"[section_id={sid} | type={typ} | {dur}s]\n{narr}")
    script_block = "\n\n".join(sec_lines)

    research_block = ""
    if research:
        research_text = research.get("research_text") or ""
        title = research.get("title") or ""
        research_block = f"""
=== 原始研究資料（事實核對依據）===
**主題**：{title}

{research_text[:3000]}
"""

    scope_hint = (
        f"本次只審閱段落 {section_ids}"
        if section_ids else
        "本次審閱整份腳本的所有段落"
    )

    return f"""你是繁體中文 YouTube 頻道的資深編審。請檢查下方腳本並找出需要修改的段落。

{style}

=== 審閱重點 ===
1. **事實錯誤**：對照研究資料，數字 / 人名 / 日期是否正確
2. **邏輯跳躍**：段與段之間是否銜接順暢，有沒有突兀的轉折
3. **冗言贅字**：口語化是否過度或不足、有無重複論點、節奏是否拖沓
4. **節奏問題**：單段是否過長（超過 40 秒）、資訊密度是否失衡
5. **風格偏差**：是否符合上方頻道風格指南（語氣、用詞、觀點鋒利度）
6. **清晰度**：術語是否有解釋、台灣觀眾能否聽懂

=== 審閱範圍 ===
{scope_hint}
{research_block}
=== 待審閱腳本段落 ===
{script_block}

=== 輸出規範（極重要）===
- 只輸出合法 JSON，**不要**加任何說明文字、markdown、code block
- 若該段無需修改，就不要出現在 changes 陣列中
- `before` 必須精準等於原 narration（不要改空白、標點）
- `after` 必須是完整改寫後的 narration（不是 diff，是完整段落）
- `reason` 要具體（壞：「太冗」；好：「第三句與段 5 重複，建議刪」）
- 保守處理：不確定的段落就別改，寧可少改也不要多改
- 若整體無需改動，回傳 `{{"summary": "整份腳本品質良好，無需修改", "changes": []}}`

JSON 格式：
{REVIEW_FORMAT}

請輸出 JSON："""


def review_script(script_path: Path,
                  section_ids: list[int] | None = None,
                  timeout: int = 600) -> dict:
    """呼叫 Claude 審閱腳本，回傳 diff 結構 dict。

    回傳格式：
        {"summary": str, "changes": [{"section_id", "type", "before", "after", "reason"}]}
    """
    script = json.loads(script_path.read_text(encoding="utf-8"))
    research_file = script_path.parent / "research.json"
    research = None
    if research_file.exists():
        try:
            research = json.loads(research_file.read_text(encoding="utf-8"))
        except Exception:
            research = None

    prompt = _build_prompt(script, research, section_ids)
    logger.info(f"[reviewer] 呼叫 claude 審閱腳本，範圍={section_ids or '全部'}，prompt 長度={len(prompt)}")
    raw = claude_run(prompt, timeout=timeout)
    logger.debug(f"[reviewer] claude 原始輸出前 300 字：{raw[:300]}")

    try:
        result = _extract_json(raw)
    except Exception as e:
        raise RuntimeError(f"審閱回傳非合法 JSON：{raw[:500]}") from e

    # 標準化
    changes = result.get("changes") or []
    for c in changes:
        c.setdefault("type", "clarity")
        c.setdefault("reason", "")
    return {
        "summary": result.get("summary") or f"共 {len(changes)} 處建議",
        "changes": changes,
    }


def apply_changes(script_path: Path, accepted: list[dict]) -> dict:
    """套用使用者接受的變更，寫回 script.json，並記錄到 script_revisions.json。

    `accepted` 格式同 review_script 的 changes：
        [{"section_id", "after", "reason", ...}, ...]
    只用 section_id 和 after；其餘供歷史紀錄。

    回傳 {"applied": int, "skipped": int}
    """
    script = json.loads(script_path.read_text(encoding="utf-8"))
    sections = script.get("script_sections") or []
    by_id = {s.get("section_id"): s for s in sections}

    applied, skipped = 0, 0
    for ch in accepted:
        sid = ch.get("section_id")
        after = ch.get("after")
        if sid is None or after is None or sid not in by_id:
            skipped += 1
            continue
        by_id[sid]["narration"] = after
        applied += 1

    script_path.write_text(
        json.dumps(script, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 寫歷史
    hist_file = script_path.parent / "script_revisions.json"
    history = []
    if hist_file.exists():
        try:
            history = json.loads(hist_file.read_text(encoding="utf-8"))
        except Exception:
            history = []
    history.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "applied_count": applied,
        "changes": accepted,
    })
    hist_file.write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 同步 DB（若有）
    try:
        from modules.database import db_manager
        meta = script.get("_meta") or {}
        news_id = meta.get("news_id")
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        research_file = script_path.parent / "research.json"
        research = None
        if research_file.exists():
            research = json.loads(research_file.read_text(encoding="utf-8"))
        db_manager.save_script(
            date_str, news_id, script, research,
            topic_id=meta.get("topic_id"),
            source_news_ids=meta.get("news_ids"),
        )
    except Exception as e:
        logger.warning(f"[reviewer] 寫回 DB 失敗（不影響本地檔案）：{e}")

    return {"applied": applied, "skipped": skipped}


def update_meta(script_path: Path, patch: dict) -> dict:
    """更新腳本的 meta 欄位：chosen_title, tags, youtube_description, thumbnail_concept。

    `chosen_title` 會把該字串移到 title_options[0]（視為已選）。
    """
    script = json.loads(script_path.read_text(encoding="utf-8"))
    updated = []

    chosen = patch.get("chosen_title")
    if chosen:
        opts = script.get("title_options") or []
        opts = [t for t in opts if t != chosen]
        opts.insert(0, chosen)
        script["title_options"] = opts[:6]
        if "_meta" in script:
            script["_meta"]["title"] = chosen
        updated.append("chosen_title")

    if "tags" in patch and isinstance(patch["tags"], list):
        script["tags"] = [str(t).strip() for t in patch["tags"] if str(t).strip()][:20]
        updated.append("tags")

    if "youtube_description" in patch:
        script["youtube_description"] = str(patch["youtube_description"])[:5000]
        updated.append("youtube_description")

    if "thumbnail_concept" in patch:
        script["thumbnail_concept"] = str(patch["thumbnail_concept"])[:500]
        updated.append("thumbnail_concept")

    if "thumbnail_punchline" in patch:
        script["thumbnail_punchline"] = str(patch["thumbnail_punchline"])[:40]
        updated.append("thumbnail_punchline")

    script_path.write_text(
        json.dumps(script, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 同步 DB
    try:
        from modules.database import db_manager
        meta = script.get("_meta") or {}
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        research_file = script_path.parent / "research.json"
        research = None
        if research_file.exists():
            research = json.loads(research_file.read_text(encoding="utf-8"))
        db_manager.save_script(
            date_str, meta.get("news_id"), script, research,
            topic_id=meta.get("topic_id"),
            source_news_ids=meta.get("news_ids"),
        )
    except Exception as e:
        logger.warning(f"[reviewer] meta 寫回 DB 失敗：{e}")

    return {"updated": updated}


def update_section(script_path: Path, section_id: int, narration: str) -> bool:
    """單段就地編輯 — 只更新指定 section 的 narration。"""
    script = json.loads(script_path.read_text(encoding="utf-8"))
    sections = script.get("script_sections") or []
    for s in sections:
        if s.get("section_id") == section_id:
            s["narration"] = narration
            script_path.write_text(
                json.dumps(script, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # 同步 DB
            try:
                from modules.database import db_manager
                meta = script.get("_meta") or {}
                date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                research_file = script_path.parent / "research.json"
                research = None
                if research_file.exists():
                    research = json.loads(research_file.read_text(encoding="utf-8"))
                db_manager.save_script(
                    date_str, meta.get("news_id"), script, research,
                    topic_id=meta.get("topic_id"),
                    source_news_ids=meta.get("news_ids"),
                )
            except Exception as e:
                logger.warning(f"[reviewer] 單段更新寫回 DB 失敗：{e}")
            return True
    return False
