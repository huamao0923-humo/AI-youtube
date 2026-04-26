"""本地 CLI 自動評分 — 透過 Claude Code 走訂閱模式評 1300+ 筆未評分 AI 新聞。

不呼叫 Anthropic API（不需 ANTHROPIC_API_KEY）。走 subprocess 呼叫 node cli.js。

策略：
- 只評 is_ai=1 AND ai_score IS NULL（除非 --all）
- batch=5（評分輸出較長且需 JSON 結構化）
- 評分後若 score >= 6：status='candidate'；否則 status='skip'
- 寫回 ai_score / business_angle / why_audience_cares / suggested_title / skip_reason

用法：
    python -m modules.ai_war_room.auto_scorer                  # 預設 limit=200
    python -m modules.ai_war_room.auto_scorer --limit 500
    python -m modules.ai_war_room.auto_scorer --batch 5
    python -m modules.ai_war_room.auto_scorer --all
    python -m modules.ai_war_room.auto_scorer --stats
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone

from loguru import logger

from modules.common.claude_cli import run as claude_run
from modules.database.models import NewsItem, SessionLocal


BATCH_SIZE = 5
CLI_TIMEOUT = 300
CANDIDATE_THRESHOLD = 6.0


_SYSTEM = """你是一個 AI 商業新聞編輯，專門為繁體中文 YouTube 頻道篩選每日最有價值的 AI 新聞。

評分標準（總分 10 分）：
- 商業影響力（0-3）：對企業、產業的實際影響有多大？
- 新鮮度（0-2）：是否是最新發布的資訊？
- 故事性（0-2）：是否有清晰的「為什麼重要」的角度？
- 觀眾興趣（0-2）：台灣繁體中文觀眾是否會感興趣？
- 獨家性（0-1）：是否是官方第一手消息？

回傳 JSON 格式（務必是合法 JSON 陣列，每個元素對應輸入的一則新聞，順序一致）：
[
  {
    "id": 原始編號（整數）,
    "score": 0-10 的整數或一位小數,
    "business_angle": "這則新聞最重要的商業意義（繁體中文，30-80字）",
    "why_audience_cares": "台灣觀眾為什麼要看這則（繁體中文，30-80字）",
    "suggested_title": "建議的影片標題（繁體中文，20字內）",
    "skip_reason": null 或 "如果分數低於5，說明為什麼跳過"
  }
]

若分數 < 5 則 skip_reason 必填，其他欄位仍需簡短填寫。
務必回傳純 JSON 陣列，不要包 markdown code block，不要任何前後文字。"""


_PROMPT_TEMPLATE = """{system}

請為以下 AI 新聞評分：

{news_block}
"""


def _build_news_block(rows: list[NewsItem]) -> str:
    lines = []
    for r in rows:
        lines.append(f"--- 編號 {r.id} ---")
        title = r.title_zh or r.title or ""
        lines.append(f"標題：{title}")
        lines.append(f"來源：{r.source_name} (優先度 {r.source_priority or 5}/10)")
        if r.published_at:
            lines.append(f"發布時間：{r.published_at}")
        summary = r.summary_zh or r.summary or ""
        if summary:
            lines.append(f"摘要：{summary[:400]}")
        lines.append("")
    return "\n".join(lines)


def _extract_json_array(text: str) -> list | None:
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


def _score_batch(rows: list[NewsItem]) -> dict[int, dict]:
    """rows -> {news_id: {score, business_angle, why_audience_cares, suggested_title, skip_reason}}"""
    if not rows:
        return {}
    block = _build_news_block(rows)
    prompt = _PROMPT_TEMPLATE.format(system=_SYSTEM, news_block=block)
    try:
        out = claude_run(prompt, timeout=CLI_TIMEOUT)
    except Exception as e:
        logger.warning(f"[auto_scorer] CLI 失敗：{e}")
        return {}
    arr = _extract_json_array(out)
    if not isinstance(arr, list):
        logger.warning(f"[auto_scorer] 無法解析 JSON，CLI stdout 前 200 字：{(out or '')[:200]}")
        return {}
    result: dict[int, dict] = {}
    for el in arr:
        if not isinstance(el, dict):
            continue
        i = el.get("id")
        sc = el.get("score")
        if not isinstance(i, int):
            continue
        try:
            sc = float(sc) if sc is not None else None
        except (ValueError, TypeError):
            sc = None
        if sc is None:
            continue
        result[i] = {
            "score": max(0.0, min(10.0, sc)),
            "business_angle": (el.get("business_angle") or "").strip()[:500] or None,
            "why_audience_cares": (el.get("why_audience_cares") or "").strip()[:500] or None,
            "suggested_title": (el.get("suggested_title") or "").strip()[:200] or None,
            "skip_reason": (el.get("skip_reason") or "").strip()[:500] or None,
        }
    return result


def score(limit: int = 200, force: bool = False, batch_size: int = BATCH_SIZE) -> dict[str, int]:
    done = 0
    failed = 0
    candidates = 0

    with SessionLocal() as s:
        q = s.query(NewsItem).filter(NewsItem.is_ai == 1)
        if not force:
            q = q.filter(NewsItem.ai_score.is_(None))
        # 優先評最新發布、其次來源優先度
        q = q.order_by(NewsItem.published_at.desc().nullslast(),
                       NewsItem.source_priority.desc().nullslast()).limit(limit)
        rows = q.all()
        if not rows:
            return {"processed": 0, "failed": 0, "candidates": 0}

        logger.info(f"[auto_scorer] 待評分 {len(rows)} 筆，batch={batch_size}")
        for start in range(0, len(rows), batch_size):
            batch_rows = rows[start:start + batch_size]
            result = _score_batch(batch_rows)
            if not result:
                failed += len(batch_rows)
                logger.warning(f"[auto_scorer] 批次 {start // batch_size + 1} 全失敗")
                continue
            for r in batch_rows:
                rec = result.get(r.id)
                if not rec:
                    continue
                r.ai_score = rec["score"]
                if rec.get("business_angle"):
                    r.business_angle = rec["business_angle"]
                if rec.get("why_audience_cares"):
                    r.why_audience_cares = rec["why_audience_cares"]
                if rec.get("suggested_title"):
                    r.suggested_title = rec["suggested_title"]
                if rec.get("skip_reason"):
                    r.skip_reason = rec["skip_reason"]
                # 升 status
                if rec["score"] >= CANDIDATE_THRESHOLD:
                    if r.status not in ("selected", "used"):
                        r.status = "candidate"
                    candidates += 1
                else:
                    if r.status not in ("selected", "used", "candidate"):
                        r.status = "skip"
                done += 1
            s.commit()
            logger.info(f"[auto_scorer] 累計評 {done} / 失敗 {failed} / 候選 {candidates}")
    return {"processed": done, "failed": failed, "candidates": candidates}


def stats() -> None:
    from sqlalchemy import func as _f
    with SessionLocal() as s:
        total = s.query(NewsItem).filter(NewsItem.is_ai == 1).count()
        scored = s.query(NewsItem).filter(NewsItem.is_ai == 1, NewsItem.ai_score.isnot(None)).count()
        cand = s.query(NewsItem).filter(NewsItem.is_ai == 1,
                                         NewsItem.ai_score >= CANDIDATE_THRESHOLD).count()
        avg = s.query(_f.avg(NewsItem.ai_score)).filter(NewsItem.is_ai == 1,
                                                         NewsItem.ai_score.isnot(None)).scalar()
        avg = round(float(avg or 0), 2)
    print(f"AI 新聞 {total} | 已評分 {scored} | 未評 {total - scored} | 候選 (>= {CANDIDATE_THRESHOLD}) {cand} | 平均分 {avg}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--batch", type=int, default=BATCH_SIZE)
    ap.add_argument("--all", action="store_true", help="忽略 ai_score IS NULL，全部重評")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    if args.stats:
        stats()
        return
    r = score(limit=args.limit, force=args.all, batch_size=args.batch)
    print(f"[OK] auto_scorer: {r}")
    stats()


if __name__ == "__main__":
    main()
