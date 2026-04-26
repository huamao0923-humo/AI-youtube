"""每日類別總摘要 — 把每個 feed（product / funding / partnership / research /
policy / other）當日所有 AI 新聞濃縮成 400-600 字繁中總結。

用 claude_cli（CoWork 訂閱模式，不需 ANTHROPIC_API_KEY）。

寫入 DailyCategorySummary（date + feed → summary_zh + word_count）。

策略：
- 預設只跑今日，每個 feed 取當日 is_ai=1 的新聞（最多 12 則，按 ai_score / published_at 排序）
- 不跑沒有新聞的 feed
- 每個 feed 一次 CLI 呼叫
- --force：忽略已生成，強制重跑

用法：
    python -m modules.ai_war_room.category_summarizer
    python -m modules.ai_war_room.category_summarizer --feed product
    python -m modules.ai_war_room.category_summarizer --date 2026-04-26
    python -m modules.ai_war_room.category_summarizer --force
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone

from loguru import logger

from modules.ai_war_room.feed_tag import FEED_LABELS, ai_feed_tag
from modules.common.claude_cli import run as claude_run
from modules.common.utils import tw_today
from modules.database import db_manager
from modules.database.models import NewsItem, SessionLocal


CLI_TIMEOUT = 240
TOP_NEWS_PER_FEED = 12     # 每個 feed 最多丟幾則新聞給 Claude
MIN_NEWS_PER_FEED = 1      # 少於這個數就跳過（沒料）
TARGET_LEN_MIN = 380       # 容忍下限
TARGET_LEN_MAX = 700       # 容忍上限


_PROMPT = """你是一個 AI 商業新聞編輯，正在為繁體中文 YouTube 頻道撰寫每日類別總結。

下面是「{label}」這個分類今天（{date}）的所有 AI 新聞。請寫**一段 400~600 字的繁體中文總結摘要**，內容要：

1. **開頭一句**點出今天這個分類的核心主題或趨勢（不要寫「今天的 XX 類有以下新聞」這種廢話）
2. **中段** 3~5 句濃縮最重要的事件 / 進展，提及關鍵公司、模型、產品、數字
3. **結尾一句**點出商業意義或對台灣觀眾的影響

規則：
- 一段話寫完，不要分段、不要分點、不要 emoji、不要 markdown、不要引號
- 保留英文專有名詞（GPT、Claude、OpenAI、NVIDIA、AWS、Anthropic、Google、Meta、xAI 等）
- 用台灣繁體中文用語（軟體、影片、檔案、伺服器…）
- 如果新聞之間有矛盾或競爭關係，要點出來（例：「Anthropic 與 OpenAI 同日發表新模型……」）
- 不要堆砌每一則新聞，要篩選與綜合

當日新聞清單（已按重要性排序）：
{news_block}

直接輸出總結文字，不要任何前綴、思考過程或結尾說明。"""


def _build_news_block(items: list[NewsItem]) -> str:
    lines = []
    for i, n in enumerate(items, 1):
        title = (n.title_zh or n.title or "").strip()
        summary = (n.summary_zh or n.summary or "").strip()
        if len(summary) > 320:
            summary = summary[:320] + "…"
        score = f" [{n.ai_score:.1f}]" if n.ai_score is not None else ""
        company = f" ({n.ai_company})" if n.ai_company else ""
        lines.append(f"{i}. {title}{company}{score}")
        if summary:
            lines.append(f"   {summary}")
    return "\n".join(lines)


def _clean(text: str) -> str:
    """移除 CLI 可能夾帶的 markdown / 前綴，回傳乾淨的單段文字。"""
    s = (text or "").strip()
    # 移除 ```...``` code fence
    m = re.search(r"```(?:\w+)?\s*(.+?)\s*```", s, re.DOTALL)
    if m:
        s = m.group(1).strip()
    # 移除常見前綴
    s = re.sub(r"^(總結[:：]\s*|摘要[:：]\s*|彙總摘要[:：]\s*|今日總結[:：]\s*)", "", s)
    # 多段 → 取最長的那段（CLI 有時會多輸出說明文字）
    parts = [p.strip() for p in s.split("\n\n") if p.strip()]
    if parts:
        s = max(parts, key=len)
    # 取代多重空白
    s = " ".join(s.split())
    return s.strip()


def _summarize_feed(date: str, feed: str, items: list[NewsItem]) -> str | None:
    if len(items) < MIN_NEWS_PER_FEED:
        return None
    label = FEED_LABELS.get(feed, ("📰", feed))[1]
    news_block = _build_news_block(items)
    prompt = _PROMPT.format(label=label, date=date, news_block=news_block)

    try:
        out = claude_run(prompt, timeout=CLI_TIMEOUT,
                         slug=f"category-{feed}-{date}",
                         heartbeat_msg=f"📝 彙總類別摘要 [{label}]")
    except Exception as e:
        logger.warning(f"[category_summarizer] feed={feed} CLI 失敗：{e}")
        return None

    cleaned = _clean(out)
    if len(cleaned) < TARGET_LEN_MIN:
        logger.warning(
            f"[category_summarizer] feed={feed} 摘要過短（{len(cleaned)} 字），"
            f"前 80 字：{cleaned[:80]!r}"
        )
        # 太短直接捨棄（避免用一行字蓋掉好摘要）
        if len(cleaned) < 200:
            return None
    if len(cleaned) > TARGET_LEN_MAX + 200:
        logger.info(
            f"[category_summarizer] feed={feed} 摘要過長（{len(cleaned)} 字），保留原樣（讓使用者看完整內容）"
        )
    return cleaned


def _collect_news_for_feed(date: str, feed: str, top_n: int = TOP_NEWS_PER_FEED) -> list[NewsItem]:
    """撈當日 is_ai=1 的新聞，依共用 ai_feed_tag 分類後挑出該 feed。"""
    with SessionLocal() as s:
        rows = (
            s.query(NewsItem)
            .filter(
                NewsItem.is_ai == 1,
                NewsItem.fetched_at.like(f"{date}%"),
            )
            .order_by(
                NewsItem.ai_score.desc().nullslast(),
                NewsItem.published_at.desc().nullslast(),
            )
            .limit(400)
            .all()
        )
        # 為了避免 detached（ORM session 結束後不能用），這裡先 expunge
        for r in rows:
            s.expunge(r)
    matched = [r for r in rows if ai_feed_tag(r) == feed]
    return matched[:top_n]


def run(date: str | None = None, feed: str | None = None,
        force: bool = False) -> dict[str, int]:
    """跑類別摘要。

    Args:
      date: 預設今天（台灣時間）
      feed: None = 全部 feed；指定就只跑那一個
      force: 強制重跑（即使今天已生成過）
    """
    target_date = date or tw_today()
    feeds = [feed] if feed else list(FEED_LABELS.keys())

    existing = db_manager.load_category_summaries(target_date) or {}

    done = 0
    skipped = 0
    failed = 0

    logger.info(f"[category_summarizer] 開始：date={target_date}, feeds={feeds}, force={force}")

    for f in feeds:
        if f in existing and not force:
            logger.info(f"[category_summarizer] feed={f} 已有摘要（{existing[f]['word_count']} 字），跳過（用 --force 重跑）")
            skipped += 1
            continue

        items = _collect_news_for_feed(target_date, f)
        if not items:
            logger.info(f"[category_summarizer] feed={f} 當日無新聞，跳過")
            skipped += 1
            continue

        text = _summarize_feed(target_date, f, items)
        if not text:
            failed += 1
            continue

        db_manager.save_category_summary(
            date=target_date, feed=f,
            summary_zh=text,
            news_count=len(items),
            top_news_ids=[it.id for it in items],
        )
        done += 1
        logger.info(
            f"[category_summarizer] ✓ feed={f}（{len(items)} 則新聞 → {len(text)} 字摘要）"
        )

    logger.info(
        f"[category_summarizer] 完成：成功 {done} / 跳過 {skipped} / 失敗 {failed}"
    )
    return {"processed": done, "skipped": skipped, "failed": failed}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="目標日期 YYYY-MM-DD（預設今日）")
    ap.add_argument("--feed", choices=list(FEED_LABELS.keys()),
                    help="只處理指定 feed")
    ap.add_argument("--force", action="store_true",
                    help="忽略已生成的，強制重跑")
    args = ap.parse_args()
    stats = run(date=args.date, feed=args.feed, force=args.force)
    print(f"[OK] {stats}")


if __name__ == "__main__":
    main()
