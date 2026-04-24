"""新聞標題 / 摘要批次翻譯 — 透過本地 Claude CLI 翻譯為繁體中文。

不呼叫 Anthropic API（不需 ANTHROPIC_API_KEY）。走 subprocess 呼叫 node cli.js。

批次策略：一次送 N 條標題或摘要（編號列表），CLI 回 JSON array，寫回 DB。
防重：預設只處理未翻過的；--all 全部重翻。

用法：
    python -m modules.ai_war_room.translator                    # title（預設）
    python -m modules.ai_war_room.translator --mode summary     # 翻摘要
    python -m modules.ai_war_room.translator --mode both        # title 再 summary
    python -m modules.ai_war_room.translator --limit 200
    python -m modules.ai_war_room.translator --all
    python -m modules.ai_war_room.translator --stats
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone

from modules.common.claude_cli import run as claude_run
from modules.database.models import NewsItem, SessionLocal


# 每批送多少條。title 可大批；summary 因字數較多用小批。
BATCH_SIZE_TITLE = 20
BATCH_SIZE_SUMMARY = 8
# CLI 每批 timeout（秒）
CLI_TIMEOUT = 240


_PROMPT_TITLE = """你是新聞標題翻譯器。把下列英文 AI 新聞標題翻譯成**繁體中文（台灣用語）**，風格要精簡、像科技媒體標題。

規則：
- 保留英文專有名詞不翻（GPT、Claude、Gemini、OpenAI、Anthropic、NVIDIA、AWS、Apple、Meta、Google、LLM、RAG、API、RLHF、GPU、CPU 等）
- 已經是中文的標題原樣回傳
- 每條翻譯控制在 60 字內，不要加多餘說明或標點
- 不要加引號或前後綴

輸入是一個 JSON array，每個元素 {{"i": 編號, "t": 原標題}}。
**只**輸出一個 JSON array，每個元素 {{"i": 同編號, "z": 翻譯}}，不要任何額外文字、markdown、思考過程。

輸入：
{payload}
"""


_PROMPT_SUMMARY = """你是新聞摘要翻譯器。把下列英文 AI 新聞摘要翻譯成**繁體中文（台灣用語）**。

規則：
- 保留英文專有名詞不翻（GPT、Claude、Gemini、OpenAI、Anthropic、NVIDIA、AWS、Apple、Meta、Google、LLM、RAG、API、RLHF、GPU、CPU 等）
- 已經是中文的摘要原樣回傳
- 每條翻譯控制在 120 字內，寫成通順的一段話；不要分條、不要 emoji、不要引號
- 若原文是 HN / Reddit 短評（如 "↑N 💬N — ..."），只翻主文，不要保留投票符號
- 保留關鍵數字、金額、百分比
- 若原文是論文 abstract，翻成一句介紹此論文主旨與發現的摘要

輸入是一個 JSON array，每個元素 {{"i": 編號, "t": 原摘要}}。
**只**輸出一個 JSON array，每個元素 {{"i": 同編號, "z": 翻譯}}，不要任何額外文字、markdown、思考過程。

輸入：
{payload}
"""


def _extract_json_array(text: str) -> list | None:
    """從 CLI 回應萃取 JSON array。CLI 可能夾帶 think 痕跡或 markdown。"""
    if not text:
        return None
    try:
        data = json.loads(text.strip())
        return data if isinstance(data, list) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"(\[[\s\S]*\])", text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
    return None


def _translate_batch(items: list[dict], mode: str = "title") -> dict[int, str]:
    """items = [{"i": idx, "t": text}, ...] → {idx: translation}"""
    if not items:
        return {}
    payload = json.dumps(items, ensure_ascii=False)
    tpl = _PROMPT_TITLE if mode == "title" else _PROMPT_SUMMARY
    prompt = tpl.format(payload=payload)
    try:
        out = claude_run(prompt, timeout=CLI_TIMEOUT)
    except Exception as e:
        from loguru import logger
        logger.warning(f"[translator:{mode}] CLI 呼叫失敗：{e}")
        return {}
    arr = _extract_json_array(out)
    if not isinstance(arr, list):
        return {}
    result: dict[int, str] = {}
    for el in arr:
        if not isinstance(el, dict):
            continue
        i = el.get("i")
        z = el.get("z")
        if isinstance(i, int) and isinstance(z, str) and z.strip():
            result[i] = z.strip()
    return result


def translate_titles(limit: int = 500, force: bool = False, batch_size: int = BATCH_SIZE_TITLE) -> dict[str, int]:
    from loguru import logger
    now = datetime.now(timezone.utc).isoformat()
    done = 0
    failed = 0

    with SessionLocal() as s:
        q = s.query(NewsItem).filter(NewsItem.is_ai == 1)
        if not force:
            q = q.filter(NewsItem.translated_at.is_(None))
        q = q.order_by(NewsItem.published_at.desc().nullslast(),
                       NewsItem.fetched_at.desc().nullslast()).limit(limit)
        rows = q.all()
        if not rows:
            return {"processed": 0, "failed": 0}

        logger.info(f"[translator:title] 待翻譯 {len(rows)} 筆，batch={batch_size}")
        for start in range(0, len(rows), batch_size):
            batch_rows = rows[start:start + batch_size]
            payload = [{"i": r.id, "t": r.title or ""} for r in batch_rows if r.title]
            if not payload:
                continue
            result = _translate_batch(payload, mode="title")
            if not result:
                failed += len(payload)
                logger.warning(f"[translator:title] 批次 {start // batch_size + 1} 全失敗")
                continue
            for r in batch_rows:
                z = result.get(r.id)
                if z:
                    r.title_zh = z
                    r.translated_at = now
                    done += 1
            s.commit()
            logger.info(f"[translator:title] 累計 {done} / 失敗 {failed}")
    return {"processed": done, "failed": failed}


def translate_summaries(limit: int = 500, force: bool = False, batch_size: int = BATCH_SIZE_SUMMARY,
                        min_summary_len: int = 20) -> dict[str, int]:
    """翻譯摘要 — 只處理 summary 長度 >= min_summary_len 的項目（避免翻空值）。"""
    from loguru import logger
    now = datetime.now(timezone.utc).isoformat()
    done = 0
    failed = 0

    with SessionLocal() as s:
        from sqlalchemy import func as _f
        q = s.query(NewsItem).filter(
            NewsItem.is_ai == 1,
            NewsItem.summary.isnot(None),
            _f.length(NewsItem.summary) >= min_summary_len,
        )
        if not force:
            q = q.filter(NewsItem.summary_translated_at.is_(None))
        q = q.order_by(NewsItem.published_at.desc().nullslast(),
                       NewsItem.fetched_at.desc().nullslast()).limit(limit)
        rows = q.all()
        if not rows:
            return {"processed": 0, "failed": 0}

        logger.info(f"[translator:summary] 待翻譯 {len(rows)} 筆，batch={batch_size}")
        for start in range(0, len(rows), batch_size):
            batch_rows = rows[start:start + batch_size]
            # 截短原摘要避免過長 — 先清理前綴，再截 400 字
            payload = []
            for r in batch_rows:
                raw = (r.summary or "").strip()
                # 粗略去掉 HN/Reddit 前綴讓 CLI 更好翻
                raw = re.sub(r"^(HN\s*points?:\s*\d+\s*(?:\|\s*comments?:\s*\d+)?|↑\d+\s*💬\s*\d+\s*[—\-:]?\s*)", "", raw, flags=re.IGNORECASE)
                if len(raw) > 400:
                    raw = raw[:400]
                if len(raw) >= min_summary_len:
                    payload.append({"i": r.id, "t": raw})
            if not payload:
                continue
            result = _translate_batch(payload, mode="summary")
            if not result:
                failed += len(payload)
                logger.warning(f"[translator:summary] 批次 {start // batch_size + 1} 全失敗")
                continue
            for r in batch_rows:
                z = result.get(r.id)
                if z:
                    r.summary_zh = z
                    r.summary_translated_at = now
                    done += 1
            s.commit()
            logger.info(f"[translator:summary] 累計 {done} / 失敗 {failed}")
    return {"processed": done, "failed": failed}


# 向後相容
def translate(limit: int = 500, force: bool = False, batch_size: int = BATCH_SIZE_TITLE) -> dict[str, int]:
    return translate_titles(limit=limit, force=force, batch_size=batch_size)


def stats() -> None:
    with SessionLocal() as s:
        total = s.query(NewsItem).filter(NewsItem.is_ai == 1).count()
        title_tr = s.query(NewsItem).filter(NewsItem.is_ai == 1,
                                             NewsItem.title_zh.isnot(None)).count()
        sum_tr = s.query(NewsItem).filter(NewsItem.is_ai == 1,
                                           NewsItem.summary_zh.isnot(None)).count()
        print(f"AI 新聞：{total} 筆 | 標題已翻：{title_tr} | 摘要已翻：{sum_tr}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["title", "summary", "both"], default="title")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--batch", type=int, default=0, help="0=用模式預設（title 20 / summary 8）")
    ap.add_argument("--all", action="store_true", help="忽略 translated_at 全翻")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    if args.stats:
        stats()
        return
    if args.mode in ("title", "both"):
        bs = args.batch or BATCH_SIZE_TITLE
        r = translate_titles(limit=args.limit, force=args.all, batch_size=bs)
        print(f"[OK] title: {r}")
    if args.mode in ("summary", "both"):
        bs = args.batch or BATCH_SIZE_SUMMARY
        r = translate_summaries(limit=args.limit, force=args.all, batch_size=bs)
        print(f"[OK] summary: {r}")
    stats()


if __name__ == "__main__":
    main()
