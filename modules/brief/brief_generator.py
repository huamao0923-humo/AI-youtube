"""Daily Brief 生成器 — 從 DB 候選池產生今日簡報 JSON。

不依賴 Telegram，直接輸出給 Web UI 或 CoWork 模式使用。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from modules.common.config import PROJECT_ROOT, settings
from modules.common.logging_setup import setup_logger
from modules.database import db_manager

setup_logger()

BRIEF_DIR = PROJECT_ROOT / "data" / "briefs"


def generate(top_n: int = 5) -> dict[str, Any]:
    """從候選池取前 N 則，生成結構化 Daily Brief。"""
    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    db_manager.init_db()

    candidates = db_manager.fetch_candidates(
        min_score=settings()["filter"]["ai_score_min"],
        limit=top_n,
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    items = []

    for rank, row in enumerate(candidates, 1):
        # 產生 3 個建議角度
        title = row["suggested_title"] or row["title"]
        angles = _suggest_angles(row)

        items.append({
            "rank": rank,
            "id": row["id"],
            "title": row["title"],
            "source_name": row["source_name"],
            "published_at": row["published_at"],
            "ai_score": row["ai_score"],
            "business_angle": row["business_angle"],
            "why_audience_cares": row["why_audience_cares"],
            "suggested_title": title,
            "angles": angles,
            "url": None,  # 若需要原文連結可另查
        })

    brief = {
        "date": today,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidates": items,
        "quick_news": _fetch_quick_news(exclude_ids=[r["id"] for r in candidates]),
    }

    # 同時存 DB（讓 Railway Web UI 能讀到）和本地檔案（備用）
    unscored = db_manager.get_unscored_count()
    db_manager.save_brief(today, brief, unscored=unscored)

    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    out = BRIEF_DIR / f"{today}_brief.json"
    out.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")

    return brief


def _suggest_angles(row: Any) -> list[dict[str, str]]:
    """根據 business_angle 與 why_audience_cares 生成 3 個切入角度建議。"""
    title = row["suggested_title"] or row["title"]
    biz = row["business_angle"] or ""
    why = row["why_audience_cares"] or ""

    return [
        {
            "label": "A",
            "name": "商業影響角度",
            "description": f"聚焦在：{biz[:80]}" if biz else "從商業影響切入",
        },
        {
            "label": "B",
            "name": "觀眾實用角度",
            "description": f"聚焦在：{why[:80]}" if why else "從觀眾痛點切入",
        },
        {
            "label": "C",
            "name": "趨勢預測角度",
            "description": f"深挖「{title}」背後代表的產業趨勢走向",
        },
    ]


def _fetch_quick_news(exclude_ids: list[int], limit: int = 5) -> list[dict]:
    """抓分數 4-6 的新聞作為快訊補充。"""
    from modules.database.models import NewsItem, SessionLocal
    with SessionLocal() as s:
        q = (
            s.query(NewsItem)
            .filter(
                NewsItem.ai_score.between(4, 5.9),
                NewsItem.status.in_(["candidate", "skipped"]),
            )
        )
        if exclude_ids:
            q = q.filter(NewsItem.id.notin_(exclude_ids))
        rows = q.order_by(NewsItem.ai_score.desc()).limit(limit).all()
    return [{"id": r.id, "title": r.title, "source": r.source_name} for r in rows]


def load_today() -> dict[str, Any] | None:
    """讀取今日 brief — 優先 DB（Railway），fallback 本地檔。"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # DB 優先（Railway 環境無本地檔案）
    row = db_manager.load_brief(today)
    if row:
        return row["content"]
    # fallback 本地
    path = BRIEF_DIR / f"{today}_brief.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


if __name__ == "__main__":
    brief = generate()
    print(f"[OK] Daily Brief 生成完成，候選 {len(brief['candidates'])} 則")
    for item in brief["candidates"]:
        print(f"  [{item['rank']}] {item['ai_score']:.1f} | {item['suggested_title']}")
