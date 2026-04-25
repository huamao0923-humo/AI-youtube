"""主題彙總摘要 — 把同一 Topic 下多則新聞濃縮成一段繁中摘要。

用 claude_cli（CoWork 模式，不需 ANTHROPIC_API_KEY）。

寫回 Topic.summary_zh + summary_generated_at。

策略：
- 拉取 status='open' 的 Topic
- 預設只處理「未生成」或「last_seen_date 比 summary_generated_at 還新」（有新進新聞）
- 每個 topic 取前 5 則 ai_score 最高的 NewsItem，提供 title_zh + summary_zh（沒中文用英文 fallback）

用法：
    python -m modules.ai_war_room.topic_summarizer
    python -m modules.ai_war_room.topic_summarizer --limit 30
    python -m modules.ai_war_room.topic_summarizer --all
    python -m modules.ai_war_room.topic_summarizer --slug some-topic-slug
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import or_

from modules.common.claude_cli import run as claude_run
from modules.database.models import NewsItem, SessionLocal, Topic


CLI_TIMEOUT = 180
TOP_NEWS_PER_TOPIC = 5


_PROMPT = """你是一個 AI 商業新聞編輯。下面是同一個主題下的多則相關新聞，請把它們濃縮成**一段 100-150 字的繁體中文摘要**，聚焦於：
- 這個主題的核心事件 / 進展是什麼
- 最重要的商業意義與產業影響
- 涉及的關鍵公司、模型或數字

規則：
- 只輸出一段話，不要分點、不要 emoji、不要引號、不要 markdown
- 保留英文專有名詞（GPT、Claude、OpenAI、NVIDIA、AWS 等）
- 用台灣繁體中文用語

主題：{title}

相關新聞：
{news_block}

直接輸出摘要文字，不要任何前綴或思考過程。"""


def _build_news_block(items: list[NewsItem]) -> str:
    lines = []
    for i, n in enumerate(items, 1):
        title = (n.title_zh or n.title or "").strip()
        summary = (n.summary_zh or n.summary or "").strip()
        if len(summary) > 350:
            summary = summary[:350]
        lines.append(f"{i}. {title}")
        if summary:
            lines.append(f"   {summary}")
    return "\n".join(lines)


def _clean(text: str) -> str:
    """移除 CLI 可能夾帶的 markdown / 前綴。"""
    s = text.strip()
    # 移除 ```...``` code fence
    m = re.search(r"```(?:\w+)?\s*(.+?)\s*```", s, re.DOTALL)
    if m:
        s = m.group(1).strip()
    # 移除常見前綴
    s = re.sub(r"^(摘要[:：]\s*|彙總摘要[:：]\s*)", "", s)
    # 取第一段（避免 CLI 多輸出說明）
    parts = [p.strip() for p in s.split("\n\n") if p.strip()]
    if parts:
        s = parts[0]
    return s.strip()


def _summarize_one(topic: Topic, items: list[NewsItem]) -> str | None:
    if not items:
        return None
    news_block = _build_news_block(items)
    prompt = _PROMPT.format(title=topic.title or topic.slug, news_block=news_block)
    try:
        out = claude_run(prompt, timeout=CLI_TIMEOUT,
                         slug=f"topic-{topic.id}",
                         heartbeat_msg="📝 彙總主題摘要")
    except Exception as e:
        logger.warning(f"[topic_summarizer] topic_id={topic.id} CLI 失敗：{e}")
        return None
    cleaned = _clean(out)
    if len(cleaned) < 20:
        logger.warning(f"[topic_summarizer] topic_id={topic.id} 摘要過短，捨棄：{cleaned!r}")
        return None
    return cleaned


def run(limit: int = 50, force: bool = False, slug: str | None = None) -> dict[str, int]:
    now = datetime.now(timezone.utc).isoformat()
    done = 0
    failed = 0

    with SessionLocal() as s:
        q = s.query(Topic).filter(Topic.status == "open")
        if slug:
            q = q.filter(Topic.slug == slug)
        elif not force:
            # 未生成 OR 有新進新聞（last_seen_date > summary_generated_at）
            q = q.filter(or_(
                Topic.summary_generated_at.is_(None),
                Topic.last_seen_date > Topic.summary_generated_at,
            ))
        q = q.order_by(Topic.heat_index.desc().nullslast(),
                       Topic.aggregate_score.desc().nullslast()).limit(limit)
        topics = q.all()
        if not topics:
            logger.info("[topic_summarizer] 無待摘要 Topic")
            return {"processed": 0, "failed": 0}

        logger.info(f"[topic_summarizer] 待處理 {len(topics)} 個 Topic")

        for t in topics:
            items = (
                s.query(NewsItem)
                .filter(NewsItem.topic_id == t.id, NewsItem.is_ai == 1)
                .order_by(NewsItem.ai_score.desc().nullslast(),
                          NewsItem.published_at.desc().nullslast())
                .limit(TOP_NEWS_PER_TOPIC)
                .all()
            )
            if not items:
                logger.debug(f"[topic_summarizer] topic_id={t.id} 無新聞，跳過")
                continue

            text = _summarize_one(t, items)
            if not text:
                failed += 1
                continue

            t.summary_zh = text
            t.summary_generated_at = now
            s.commit()
            done += 1
            logger.info(f"[topic_summarizer] {done}/{len(topics)} ✓ {t.slug}：{text[:50]}…")

    logger.info(f"[topic_summarizer] 完成：成功 {done} / 失敗 {failed}")
    return {"processed": done, "failed": failed}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--all", action="store_true", help="忽略 generated_at，全部重生")
    ap.add_argument("--slug", help="只處理指定 slug 的 Topic")
    args = ap.parse_args()
    stats = run(limit=args.limit, force=args.all, slug=args.slug)
    print(f"[OK] {stats}")


if __name__ == "__main__":
    main()
