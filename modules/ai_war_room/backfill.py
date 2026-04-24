"""Backfill is_ai / ai_company / model_release 到既有 news_items。

用法：
    python -m modules.ai_war_room.backfill            # 只補 NULL
    python -m modules.ai_war_room.backfill --all      # 全部重算
    python -m modules.ai_war_room.backfill --stats    # 只看統計

冪等：沒 --all 時只處理 is_ai IS NULL 的 row；跑完後重算 ai_company 只在 is_ai=1 的 row。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from modules.ai_war_room.company_matcher import CompanyMatcher
from modules.ai_war_room.filter import is_ai_related, load_ai_source_whitelist
from modules.ai_war_room.model_registry import detect_model_release
from modules.database.models import NewsItem, SessionLocal


_SOURCES = Path(__file__).resolve().parents[2] / "config" / "sources.yaml"


def _company_stats(s) -> dict[str, int]:
    from sqlalchemy import func
    rows = s.query(NewsItem.ai_company, func.count(NewsItem.id)).filter(
        NewsItem.ai_company.isnot(None)
    ).group_by(NewsItem.ai_company).all()
    return {k: v for k, v in rows}


def backfill(force: bool = False) -> dict[str, int]:
    wl = load_ai_source_whitelist(str(_SOURCES))
    matcher = CompanyMatcher.load()
    s = SessionLocal()
    try:
        q = s.query(NewsItem)
        if not force:
            q = q.filter(NewsItem.is_ai.is_(None))
        rows = q.all()
        ai_count = model_count = company_count = 0
        for r in rows:
            item = {
                "title": r.title,
                "summary": r.summary,
                "source_name": r.source_name,
                "category": r.category,
            }
            r.is_ai, _ = is_ai_related(item, wl)
            if r.is_ai:
                ai_count += 1
                key = matcher.match(r.title or "", r.summary or "")
                if key:
                    r.ai_company = key
                    company_count += 1
                r.model_release = detect_model_release(r.title or "", r.summary or "", r.source_name or "")
                if r.model_release:
                    model_count += 1
            else:
                r.ai_company = None
                r.model_release = 0
        s.commit()
        return {
            "processed": len(rows),
            "is_ai": ai_count,
            "with_company": company_count,
            "model_release": model_count,
        }
    finally:
        s.close()


def stats() -> None:
    from sqlalchemy import func
    s = SessionLocal()
    try:
        total = s.query(NewsItem).count()
        ai = s.query(NewsItem).filter(NewsItem.is_ai == 1).count()
        mr = s.query(NewsItem).filter(NewsItem.model_release == 1).count()
        print(f"總計 {total} | is_ai=1: {ai} | model_release=1: {mr}")
        print("公司分佈：")
        for k, v in sorted(_company_stats(s).items(), key=lambda x: -x[1]):
            print(f"  {k:<20} {v}")
    finally:
        s.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="忽略 is_ai IS NULL，全部重算")
    ap.add_argument("--stats", action="store_true", help="只印統計")
    args = ap.parse_args()
    if args.stats:
        stats()
        return
    res = backfill(force=args.all)
    print(f"[OK] backfill: {res}")
    stats()


if __name__ == "__main__":
    main()
