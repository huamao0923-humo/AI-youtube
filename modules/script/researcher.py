"""深度研究模組 — 支援單篇 (legacy) 與多篇（Topic）兩種模式。

流程：
  1. export_prompt(news_id) / export_prompt_for_topic(topic_id) / export_prompt_for_news_ids(ids)
     → 輸出研究 prompt 給 Claude Code / claude CLI
  2. 取得研究內容後，save_research() 寫入 research.json
  3. script_writer 讀 research.json 產腳本

研究 prompt 的長度目標：
  - 單篇 legacy：1500-2500 字（對應 8-10 分鐘腳本）
  - 多篇 topic：2500-4500 字（對應 18-28 分鐘腳本，需交叉比對）

執行：
  python -m modules.script.researcher --news-id 393
  python -m modules.script.researcher --topic-id 42
  python -m modules.script.researcher --news-ids 393,401,412
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from modules.common.config import PROJECT_ROOT
from modules.common.logging_setup import setup_logger
from modules.common.utils import build_slug
from modules.database import db_manager

setup_logger()

RESEARCH_DIR = PROJECT_ROOT / "data" / "scripts"


_RESEARCH_INSTRUCTION_SINGLE = """你是一個專業的 AI 商業新聞研究員，負責為繁體中文 YouTube 頻道準備深度報導素材。

請整理出以下結構化研究摘要：

1. **核心事實**：這件事到底發生了什麼（5W1H）
2. **背景脈絡**：這家公司/這個技術的歷史，觀眾需要知道什麼才能理解這則新聞
3. **關鍵數字**：所有具體的財務、技術、市場數據（金額、比例、時間）
4. **為什麼現在**：這個時間點發生這件事的原因
5. **影響分析**
   - 對業界的影響
   - 對競爭對手的影響
   - 對台灣用戶/企業的影響
6. **可能的反方觀點**：這件事有沒有值得質疑或批評的角度
7. **3 個精彩的開場 Hook 選項**：用一句話抓住觀眾注意力

請用繁體中文輸出，格式清楚、條列分明。"""


_RESEARCH_INSTRUCTION_MULTI = """你是一個專業的 AI 商業新聞研究員，負責為繁體中文 YouTube 頻道準備 **18-28 分鐘** 長片的深度研究素材。

你現在會拿到 **多則相關新聞報導**（同一主題的不同來源），請 **合併、比對、深化** 後產出整合研究，而不是逐篇復述。

=== 必須產出的結構 ===

**0. 主題概覽（200-300 字）**
- 把所有來源匯整成一個統一敘事；一段講清楚這個主題的核心事件
- 這是整集的定錨點，腳本會以此為開場

**1. 核心事實（5W1H）**
- 把分散在各來源的事實點合併列出
- 標明資料出處（「來源 #1 指出…」「Bloomberg 報導…」）

**2. 各來源對照表**
- 明確指出：哪些事實多個來源一致；哪裡分歧或有矛盾
- 如果某個關鍵數字只有一個來源提到，要標示

**3. 背景脈絡（500-800 字）**
- 這家公司/這個技術的過去；觀眾要理解這件事需要的前置知識
- 可以追溯 1-3 年的相關里程碑

**4. 關鍵數字匯總**
- 把所有出現過的數字（金額、比例、日期、用戶數、股價、輪數估值等）彙整成一張表或清單
- 每個數字標明出處

**5. 為什麼現在（200-400 字）**
- 這個時間點發生這件事的因果鏈

**6. 深度影響分析（800-1200 字）**
- 對業界：市場結構、商業模式、競爭格局
- 對競爭對手：誰得利、誰受害、可能反擊
- 對台灣用戶/企業：具體到產業鏈或消費端影響
- 對產業未來：3-6 個月可能的後續發展

**7. 多角度觀點（400-600 字）**
- 看多派（bull case）的論點 + 數據支撐
- 看空派（bear case）的質疑 + 數據支撐
- 中性第三方學者/分析師的說法

**8. 5 個開場 Hook 選項**
- 每個不超過 25 字，衝擊性強、有數字或懸念
- 避免「大家好歡迎來到 ...」這種弱起頭

=== 輸出規範 ===
- **繁體中文**（台灣用語為主，不要用「視頻、網友、芯片」等詞）
- 總字數目標 **2500-4500 字**
- 格式清楚、條列分明；每個 section 用 Markdown 標題
- 事實性資訊 100% 以提供的新聞為依據；不要加入訓練資料的推測（若要推論請明確標示「推估」）"""


def _out_dir_from_slug(slug: str) -> Path:
    return RESEARCH_DIR / slug


def _slug_for_topic(topic: dict) -> str:
    """優先用 topic.slug；沒有就由 title 生成。"""
    if topic.get("slug"):
        return topic["slug"]
    return build_slug(topic.get("title") or f"topic_{topic['id']}",
                       date=topic.get("last_seen_date"))


def _slug_for_news_ids(news_ids: list[int], title_hint: str | None = None) -> str:
    """沒有 Topic 時的 slug：用 title_hint 或 第一則 news 的標題。"""
    if title_hint:
        return build_slug(title_hint)
    first = db_manager.get_news_by_id(news_ids[0])
    if not first:
        return f"multi_{news_ids[0]}"
    return build_slug(first.get("suggested_title") or first["title"])


def _build_article_brief(news: dict) -> dict:
    """把 NewsItem 壓成 prompt + meta 用的精簡結構。"""
    return {
        "id": news["id"],
        "title": news["title"],
        "suggested_title": news.get("suggested_title") or news["title"],
        "source": news.get("source_name", ""),
        "published_at": str(news.get("published_at", ""))[:10],
        "region": news.get("region") or "global",
        "category": news.get("category"),
        "summary": news.get("summary") or "",
        "business_angle": news.get("business_angle") or "",
        "why_audience_cares": news.get("why_audience_cares") or "",
        "url": news.get("url", ""),
    }


# ───────────── Multi-article core ─────────────

def export_prompt_for_news_ids(
    news_ids: list[int],
    *,
    title_hint: str | None = None,
    topic_id: int | None = None,
    topic_title: str | None = None,
    slug: str | None = None,
    force_single_format: bool = False,
) -> tuple[str, Path]:
    """從 news_ids 產出 prompt + 寫 news_meta.json。"""
    if not news_ids:
        raise ValueError("news_ids 不可為空")

    articles = []
    for nid in news_ids:
        n = db_manager.get_news_by_id(nid)
        if n:
            articles.append(_build_article_brief(n))
    if not articles:
        raise ValueError(f"找不到任何 news：{news_ids}")

    # 決定 slug
    if not slug:
        if topic_id:
            topic = db_manager.get_topic(topic_id)
            slug = _slug_for_topic(topic) if topic else _slug_for_news_ids(news_ids, title_hint)
        else:
            slug = _slug_for_news_ids(news_ids, title_hint)

    out_dir = _out_dir_from_slug(slug)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 決定 prompt body
    use_multi = (len(articles) > 1) and not force_single_format
    instruction = _RESEARCH_INSTRUCTION_MULTI if use_multi else _RESEARCH_INSTRUCTION_SINGLE

    if use_multi:
        body = f"""以下是 {len(articles)} 則相關新聞報導，請合併整合為單一研究摘要（供 18-28 分鐘長片使用）：

**主題**：{topic_title or title_hint or articles[0]['suggested_title']}

"""
        for i, a in enumerate(articles, 1):
            body += (
                f"---\n\n**來源 #{i}**：{a['source']}（{a['published_at']}）\n"
                f"**標題**：{a['title']}\n"
                f"**摘要**：{a['summary']}\n"
                f"**商業意義**：{a['business_angle']}\n"
                f"**台灣觀眾關注點**：{a['why_audience_cares']}\n"
                f"**URL**：{a['url']}\n\n"
            )
    else:
        a = articles[0]
        body = f"""請為以下新聞準備深度研究摘要：

**標題**：{a['suggested_title']}
**原始標題**：{a['title']}
**來源**：{a['source']}
**發布時間**：{a['published_at']}

**新聞摘要**：
{a['summary']}

**已知的商業意義**：{a['business_angle']}
**台灣觀眾關注點**：{a['why_audience_cares']}

請整理完整研究摘要，讓腳本作家可以直接用這份資料撰寫 8-10 分鐘的影片腳本。"""

    prompt = f"{instruction}\n\n---\n\n{body}"

    prompt_file = out_dir / "research_prompt.md"
    prompt_file.write_text(prompt, encoding="utf-8")

    # news_meta.json：保留單篇相容 + 多篇新結構
    meta = {
        "slug": slug,
        "topic_id": topic_id,
        "topic_title": topic_title or title_hint,
        "news_ids": [a["id"] for a in articles],
        "news_id": articles[0]["id"],  # legacy 相容
        "title": topic_title or title_hint or articles[0]["suggested_title"],
        "source": articles[0]["source"],  # legacy
        "business_angle": articles[0]["business_angle"],  # legacy
        "why_audience_cares": articles[0]["why_audience_cares"],  # legacy
        "news_summary": articles[0]["summary"],  # legacy
        "articles": articles,
        "out_dir": str(out_dir),
    }
    (out_dir / "news_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    mode = "multi" if use_multi else "single"
    logger.info(f"[{mode}] 研究 prompt 已存至：{prompt_file}（{len(articles)} 篇）")
    return prompt, out_dir


def export_prompt_for_topic(topic_id: int) -> tuple[str, Path]:
    """從 Topic 產出 prompt（讀 topic 下所有 news）。"""
    topic = db_manager.get_topic(topic_id)
    if not topic:
        raise ValueError(f"找不到 topic_id={topic_id}")
    news_rows = db_manager.list_news_by_topic(topic_id)
    news_ids = [r["id"] for r in news_rows]
    if not news_ids:
        raise ValueError(f"topic #{topic_id} 沒有成員新聞")

    return export_prompt_for_news_ids(
        news_ids,
        topic_id=topic_id,
        topic_title=topic.get("title"),
        slug=topic.get("slug"),
    )


def export_prompt(news_id: int) -> tuple[str, Path]:
    """[LEGACY] 單篇 prompt — 內部導到多篇版本（單篇模式）。"""
    return export_prompt_for_news_ids([news_id])


# ───────────── Save research ─────────────

def _find_meta_by_any(ids: list[int] | None = None, topic_id: int | None = None) -> tuple[Path, dict] | None:
    """依 news_id 清單或 topic_id 找到對應的 news_meta.json 所在資料夾。"""
    for m in sorted(RESEARCH_DIR.glob("*/news_meta.json"), reverse=True):
        try:
            d = json.loads(m.read_text(encoding="utf-8"))
        except Exception:
            continue
        if topic_id and d.get("topic_id") == topic_id:
            return m.parent, d
        meta_ids = set(d.get("news_ids") or [d.get("news_id")])
        meta_ids.discard(None)
        if ids and set(ids) & meta_ids:
            return m.parent, d
    return None


def save_research(news_id_or_ids, research_text: str,
                   *, topic_id: int | None = None) -> Path:
    """
    存 research.json；支援單篇（news_id int）或多篇（news_ids list）。

    舊用法：save_research(news_id, text) 仍相容
    新用法：save_research([393, 401], text, topic_id=42)
    """
    if isinstance(news_id_or_ids, int):
        news_ids = [news_id_or_ids]
    else:
        news_ids = list(news_id_or_ids)

    found = _find_meta_by_any(ids=news_ids, topic_id=topic_id)
    if found:
        out_dir, meta = found
    else:
        # 沒有預先跑過 export_prompt，直接 build 一份
        _, out_dir = export_prompt_for_news_ids(news_ids, topic_id=topic_id)
        meta = json.loads((out_dir / "news_meta.json").read_text(encoding="utf-8"))

    result = {
        "news_id": news_ids[0],         # legacy
        "news_ids": news_ids,
        "topic_id": meta.get("topic_id") or topic_id,
        "title": meta.get("title"),
        "source": meta.get("source"),   # legacy（第一篇）
        "researched_at": datetime.now(timezone.utc).isoformat(),
        "research_text": research_text,
        "news_summary": meta.get("news_summary", ""),    # legacy
        "business_angle": meta.get("business_angle", ""),
        "why_audience_cares": meta.get("why_audience_cares", ""),
        "articles": meta.get("articles", []),
        "out_dir": str(out_dir),
    }

    research_file = out_dir / "research.json"
    research_file.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"研究摘要已存至：{research_file}（{len(news_ids)} 篇）")
    return research_file


# ───────────── Auto research（呼叫 claude CLI）─────────────

def auto_research_news_ids(news_ids: list[int], *, topic_id: int | None = None,
                            topic_title: str | None = None,
                            timeout: int = 600,
                            slug: str | None = None) -> Path:
    """多篇 auto research — Claude CLI 回傳整合研究，存進 research.json。

    slug：若給定，會透過 claude_cli 的 heartbeat 機制在研究期間更新 progress_detail。
    """
    from modules.common.claude_cli import run as claude_run
    prompt, _ = export_prompt_for_news_ids(
        news_ids, topic_id=topic_id, topic_title=topic_title,
    )
    logger.info(f"呼叫 claude CLI 做多篇研究（{len(news_ids)} 篇）…")
    research_text = claude_run(
        prompt, timeout=timeout,
        slug=slug,
        heartbeat_msg=f"🔎 Claude 深度研究中（{len(news_ids)} 篇）",
    )
    logger.info(f"研究完成（{len(research_text)} 字）")
    return save_research(news_ids, research_text, topic_id=topic_id)


def auto_research_topic(topic_id: int, timeout: int = 600, *, slug: str | None = None) -> Path:
    """從 Topic 跑 auto research。"""
    topic = db_manager.get_topic(topic_id)
    if not topic:
        raise ValueError(f"找不到 topic #{topic_id}")
    news_rows = db_manager.list_news_by_topic(topic_id)
    news_ids = [r["id"] for r in news_rows]
    return auto_research_news_ids(
        news_ids, topic_id=topic_id, topic_title=topic.get("title"),
        timeout=timeout, slug=slug,
    )


def auto_research(news_id: int, timeout: int = 600, *, slug: str | None = None) -> Path:
    """[LEGACY] 單篇 auto research → 內部走 multi 路徑。"""
    return auto_research_news_ids([news_id], timeout=timeout, slug=slug)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--news-id", type=int)
    ap.add_argument("--topic-id", type=int)
    ap.add_argument("--news-ids", type=str, help="逗號分隔的 news_id 清單")
    ap.add_argument("--save-research", type=str, help="直接儲存研究文字（測試用）")
    args = ap.parse_args()

    # 解析目標
    if args.topic_id:
        prompt, out_dir = export_prompt_for_topic(args.topic_id)
        target_desc = f"topic #{args.topic_id}"
    elif args.news_ids:
        ids = [int(x) for x in args.news_ids.split(",") if x.strip()]
        prompt, out_dir = export_prompt_for_news_ids(ids)
        target_desc = f"news_ids={ids}"
    elif args.news_id:
        prompt, out_dir = export_prompt(args.news_id)
        target_desc = f"news_id={args.news_id}"
    else:
        print("請指定 --news-id / --topic-id / --news-ids")
        return

    if args.save_research:
        if args.topic_id:
            # 找出 topic 下的 news_ids
            rows = db_manager.list_news_by_topic(args.topic_id)
            ids = [r["id"] for r in rows]
            path = save_research(ids, args.save_research, topic_id=args.topic_id)
        elif args.news_ids:
            ids = [int(x) for x in args.news_ids.split(",")]
            path = save_research(ids, args.save_research)
        else:
            path = save_research(args.news_id, args.save_research)
        print(f"[OK] 研究摘要已存：{path}")
        return

    print(f"\n{'='*60}\n目標：{target_desc}")
    print(f"Prompt 已存至：{out_dir}/research_prompt.md")
    print(f"Prompt 長度：{len(prompt)} 字")
    print("取得研究內容後，在 Web UI /cowork/research 頁面匯入")


if __name__ == "__main__":
    main()
