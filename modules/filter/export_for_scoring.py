"""匯出待評分新聞供 Claude Code 人工評分。

使用方式：
  1. python -m modules.filter.export_for_scoring          # 匯出到 scoring_queue.json
  2. 把內容貼給 Claude Code，請它評分
  3. python -m modules.filter.import_scores               # 把 Claude Code 給的評分結果寫回 DB
"""
from __future__ import annotations

import json
from pathlib import Path

from modules.common.config import PROJECT_ROOT
from modules.common.logging_setup import setup_logger
from modules.database import db_manager

setup_logger()

OUTPUT = PROJECT_ROOT / "data" / "scoring_queue.json"


def main() -> None:
    db_manager.init_db()
    pending = db_manager.fetch_news_to_score(limit=50)

    if not pending:
        print("沒有待評分的新聞。")
        return

    items = [
        {
            "id": row["id"],
            "title": row["title"],
            "source_name": row["source_name"],
            "source_priority": row["source_priority"],
            "published_at": row["published_at"],
            "summary": (row["summary"] or "")[:400],
        }
        for row in pending
    ]

    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"已匯出 {len(items)} 則新聞到：{OUTPUT}")
    print("\n接下來：把以下內容貼給 Claude Code，請它按格式評分後回傳 JSON。")
    print("-" * 60)
    print(OUTPUT.read_text(encoding="utf-8")[:2000])
    print("...")


if __name__ == "__main__":
    main()
