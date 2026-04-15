"""深度研究模組 — 收到選題後自動補充背景資料。

流程：
  1. 從 DB 讀取選定的新聞
  2. 用 Claude 搜尋相關背景、數據、前因後果
  3. 輸出結構化「研究摘要」供 script_writer 使用

執行：
  python -m modules.script.researcher --news-id 393
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic
from loguru import logger

from modules.common.config import PROJECT_ROOT, env, settings
from modules.common.logging_setup import setup_logger
from modules.database import db_manager

setup_logger()

RESEARCH_DIR = PROJECT_ROOT / "data" / "scripts"

RESEARCH_SYSTEM = """你是一個專業的 AI 商業新聞研究員，負責為繁體中文 YouTube 頻道準備深度報導素材。

收到一則新聞後，你要整理出以下結構化研究摘要：

1. **核心事實**：這件事到底發生了什麼（5W1H）
2. **背景脈絡**：這家公司/這個技術的歷史，觀眾需要知道什麼才能理解這則新聞
3. **關鍵數字**：所有具體的財務、技術、市場數據（金額、比例、時間）
4. **為什麼現在**：這個時間點發生這件事的原因
5. **影響分析**：
   - 對業界的影響
   - 對競爭對手的影響
   - 對台灣用戶/企業的影響
6. **可能的反方觀點**：這件事有沒有值得質疑或批評的角度
7. **3個精彩的開場 Hook 選項**：用一句話抓住觀眾注意力

請用繁體中文輸出，格式清楚、條列分明。"""


def research(news_id: int) -> dict:
    """對指定新聞做深度研究，回傳研究摘要 dict。"""
    news = db_manager.get_news_by_id(news_id)
    if not news:
        raise ValueError(f"找不到 news_id={news_id}")

    api_key = env("ANTHROPIC_API_KEY", required=True)
    client = Anthropic(api_key=api_key)
    cfg = settings()["claude"]

    title = news.get("suggested_title") or news["title"]
    summary = news.get("summary") or ""
    source = news.get("source_name", "")
    biz_angle = news.get("business_angle") or ""
    why_care = news.get("why_audience_cares") or ""

    user_msg = f"""請為以下新聞準備深度研究摘要：

**標題**：{title}
**原始標題**：{news['title']}
**來源**：{source}
**發布時間**：{news.get('published_at', '')[:10]}

**新聞摘要**：
{summary}

**已知的商業意義**：{biz_angle}
**台灣觀眾關注點**：{why_care}

請整理完整的研究摘要，讓腳本作家可以直接用這份資料撰寫 8-10 分鐘的影片腳本。"""

    logger.info(f"開始深度研究：{title}")

    resp = client.messages.create(
        model=cfg["model"],
        max_tokens=3000,
        temperature=0.4,
        system=RESEARCH_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )

    research_text = "".join(b.text for b in resp.content if b.type == "text")

    result = {
        "news_id": news_id,
        "title": title,
        "source": source,
        "researched_at": datetime.now(timezone.utc).isoformat(),
        "research_text": research_text,
        "news_summary": summary,
        "business_angle": biz_angle,
        "why_audience_cares": why_care,
    }

    # 存檔
    slug = _slugify(title)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    out_dir = RESEARCH_DIR / f"{today}_{slug}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "research.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"研究摘要已存至：{out_dir}/research.json")

    return result


def _slugify(text: str) -> str:
    import re
    text = re.sub(r"[^\w\u4e00-\u9fff]", "_", text)
    return text[:40].strip("_")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--news-id", type=int, required=True)
    ap.add_argument("--cowork", action="store_true", help="輸出給 Claude Code 做研究（不呼叫 API）")
    args = ap.parse_args()

    if args.cowork:
        # CoWork 模式：只印出新聞資訊，讓使用者貼給 Claude Code
        news = db_manager.get_news_by_id(args.news_id)
        if not news:
            print(f"找不到 news_id={args.news_id}")
            return
        print("\n=== 請將以下內容貼給 Claude Code 請它做深度研究 ===\n")
        print(f"標題：{news.get('suggested_title') or news['title']}")
        print(f"來源：{news.get('source_name')}")
        print(f"摘要：{(news.get('summary') or '')[:500]}")
        print(f"商業意義：{news.get('business_angle') or '—'}")
        print(f"觀眾關注：{news.get('why_audience_cares') or '—'}")
        print("\n請 Claude Code 整理：核心事實、背景脈絡、關鍵數字、影響分析、3個 Hook 選項")
        return

    result = research(args.news_id)
    print(f"\n[OK] 研究完成：{result['title']}")
    print("\n--- 研究摘要前段 ---")
    print(result["research_text"][:600])
    print("...")


if __name__ == "__main__":
    main()
