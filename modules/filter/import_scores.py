"""把 Claude Code 給的評分結果寫回 DB。

用法：
  python -m modules.filter.import_scores --file data/scored_results.json

scored_results.json 格式（與 scorer.py 的 Claude 回傳格式相同）：
[
  {
    "id": 47,
    "score": 8.5,
    "business_angle": "...",
    "why_audience_cares": "...",
    "suggested_title": "...",
    "skip_reason": null
  },
  ...
]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from modules.common.config import PROJECT_ROOT, settings
from modules.common.logging_setup import setup_logger
from modules.database import db_manager

setup_logger()

DEFAULT_FILE = PROJECT_ROOT / "data" / "scored_results.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(DEFAULT_FILE))
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"找不到評分檔案：{path}")
        return

    results = json.loads(path.read_text(encoding="utf-8"))
    ai_min = settings()["filter"]["ai_score_min"]

    updates = []
    for r in results:
        try:
            score = float(r.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        updates.append({
            "id": r["id"],
            "ai_score": score,
            "business_angle": r.get("business_angle"),
            "why_audience_cares": r.get("why_audience_cares"),
            "suggested_title": r.get("suggested_title"),
            "skip_reason": r.get("skip_reason"),
            "status": "candidate" if score >= ai_min else "skipped",
        })

    db_manager.update_ai_scores(updates)

    candidates = sum(1 for u in updates if u["status"] == "candidate")
    print(f"[OK] 寫入 {len(updates)} 筆評分，其中候選 {candidates} 則（分數 >= {ai_min}）")


if __name__ == "__main__":
    main()
