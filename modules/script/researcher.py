"""深度研究模組 — CoWork 模式：輸出 prompt 供 Claude Code 做研究，不呼叫 API。

流程：
  1. export_prompt(news_id) → 輸出研究 prompt 給 Claude Code
  2. 使用者將 Claude Code 的回答貼回 Web UI
  3. save_research(news_id, research_text) → 存成 research.json 供 script_writer 使用

執行：
  python -m modules.script.researcher --news-id 393
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from modules.common.config import PROJECT_ROOT
from modules.common.logging_setup import setup_logger
from modules.database import db_manager

setup_logger()

RESEARCH_DIR = PROJECT_ROOT / "data" / "scripts"

RESEARCH_INSTRUCTION = """你是一個專業的 AI 商業新聞研究員，負責為繁體中文 YouTube 頻道準備深度報導素材。

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
7. **3個精彩的開場 Hook 選項**：用一句話抓住觀眾注意力

請用繁體中文輸出，格式清楚、條列分明。"""


def _slugify(text: str) -> str:
    import re
    text = re.sub(r"[^\w\u4e00-\u9fff]", "_", text)
    return text[:40].strip("_")


def _out_dir(news_id: int, title: str) -> Path:
    slug = _slugify(title)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return RESEARCH_DIR / f"{today}_{slug}"


def export_prompt(news_id: int) -> tuple[str, Path]:
    """
    生成研究 prompt 字串，同時存成 research_prompt.md。
    回傳 (prompt_text, prompt_file_path)。
    """
    news = db_manager.get_news_by_id(news_id)
    if not news:
        raise ValueError(f"找不到 news_id={news_id}")

    title = news.get("suggested_title") or news["title"]
    summary = news.get("summary") or ""
    source  = news.get("source_name", "")
    biz_angle = news.get("business_angle") or ""
    why_care  = news.get("why_audience_cares") or ""

    prompt = f"""{RESEARCH_INSTRUCTION}

---

請為以下新聞準備深度研究摘要：

**標題**：{title}
**原始標題**：{news['title']}
**來源**：{source}
**發布時間**：{str(news.get('published_at', ''))[:10]}

**新聞摘要**：
{summary}

**已知的商業意義**：{biz_angle}
**台灣觀眾關注點**：{why_care}

請整理完整研究摘要，讓腳本作家可以直接用這份資料撰寫 8-10 分鐘的影片腳本。"""

    out_dir = _out_dir(news_id, title)
    out_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = out_dir / "research_prompt.md"
    prompt_file.write_text(prompt, encoding="utf-8")

    # 同時把新聞基本資訊存進去，之後 save_research 會用到
    meta = {
        "news_id": news_id,
        "title": title,
        "source": source,
        "business_angle": biz_angle,
        "why_audience_cares": why_care,
        "news_summary": summary,
        "out_dir": str(out_dir),
    }
    (out_dir / "news_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info(f"研究 prompt 已存至：{prompt_file}")
    return prompt, out_dir


def save_research(news_id: int, research_text: str) -> Path:
    """
    把 Claude Code 回傳的研究內容存成 research.json。
    回傳 research.json 路徑。
    """
    # 找對應的 news_meta.json
    metas = sorted(RESEARCH_DIR.glob("*/news_meta.json"), reverse=True)
    out_dir = None
    for m in metas:
        d = json.loads(m.read_text(encoding="utf-8"))
        if d.get("news_id") == news_id:
            out_dir = m.parent
            meta = d
            break

    if out_dir is None:
        # 沒有 prompt 紀錄，直接建立
        news = db_manager.get_news_by_id(news_id)
        title = (news.get("suggested_title") or news["title"]) if news else f"news_{news_id}"
        out_dir = _out_dir(news_id, title)
        out_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "news_id": news_id,
            "title": title,
            "source": news.get("source_name", "") if news else "",
            "business_angle": news.get("business_angle", "") if news else "",
            "why_audience_cares": news.get("why_audience_cares", "") if news else "",
            "news_summary": news.get("summary", "") if news else "",
            "out_dir": str(out_dir),
        }

    result = {
        "news_id": news_id,
        "title": meta["title"],
        "source": meta["source"],
        "researched_at": datetime.now(timezone.utc).isoformat(),
        "research_text": research_text,
        "news_summary": meta.get("news_summary", ""),
        "business_angle": meta.get("business_angle", ""),
        "why_audience_cares": meta.get("why_audience_cares", ""),
        "out_dir": str(out_dir),
    }

    research_file = out_dir / "research.json"
    research_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"研究摘要已存至：{research_file}")
    return research_file


def auto_research(news_id: int) -> Path:
    """
    用 claude CLI（-p 模式）自動做深度研究，不需要 API Key。
    使用 Claude Max 訂閱，等同於 CoWork。
    回傳 research.json 路徑。
    """
    from modules.common.claude_cli import run as claude_run
    prompt, _ = export_prompt(news_id)
    logger.info(f"呼叫 claude CLI 進行深度研究（news_id={news_id}）…")
    research_text = claude_run(prompt, timeout=300)
    logger.info(f"研究完成（{len(research_text)} 字）")
    return save_research(news_id, research_text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--news-id", type=int, required=True)
    ap.add_argument("--save-research", type=str, help="直接儲存研究文字（測試用）")
    args = ap.parse_args()

    if args.save_research:
        path = save_research(args.news_id, args.save_research)
        print(f"[OK] 研究摘要已存：{path}")
        return

    prompt, out_dir = export_prompt(args.news_id)
    print(f"\n{'='*60}")
    print("請將以下 prompt 貼給 Claude Code 請它做深度研究")
    print(f"{'='*60}\n")
    print(prompt)
    print(f"\n{'='*60}")
    print(f"prompt 也已存至：{out_dir}/research_prompt.md")
    print("取得研究內容後，在 Web UI /cowork/research 頁面匯入")


if __name__ == "__main__":
    main()
