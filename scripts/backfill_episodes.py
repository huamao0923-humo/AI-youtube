"""回填腳本：掃描 data/scripts/* 寫入 Episode + EpisodeStatus。

用途：
  第一次啟用新架構時，把既有已生成的集數記錄到 DB。

CLI：
  python scripts/backfill_episodes.py [--dry-run] [--slug <slug>] [--force]

  --dry-run  只列印計畫動作，不寫 DB
  --slug     只處理指定 slug
  --force    已有 DB 記錄也覆寫
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger
from modules.common.logging_setup import setup_logger
from modules.common.utils import parse_date_from_slug, tw_today
from modules.database import db_manager
from modules.database.models import init_db
from modules.storage.local_storage import (
    get_episode_paths, infer_stage_from_files, list_slugs_on_disk,
)

setup_logger()


def _extract_title_and_news_id(slug: str) -> tuple[str, int | None]:
    """從 script.json 或 news_meta.json 萃取標題與 news_id。"""
    script_dir = PROJECT_ROOT / "data" / "scripts" / slug
    title = slug
    news_id: int | None = None

    sj = script_dir / "script.json"
    if sj.exists():
        try:
            data = json.loads(sj.read_text(encoding="utf-8"))
            meta = data.get("_meta", {})
            title = (data.get("title_options") or [meta.get("title", slug)])[0]
            news_id = meta.get("news_id") or data.get("news_id")
        except Exception as e:
            logger.warning(f"讀 {sj.name} 失敗：{e}")

    if news_id is None:
        nm = script_dir / "news_meta.json"
        if nm.exists():
            try:
                news_id = json.loads(nm.read_text(encoding="utf-8")).get("news_id")
            except Exception:
                pass

    return title, news_id


def backfill_one(slug: str, dry_run: bool = False, force: bool = False) -> dict:
    """回填單一 slug。回傳 summary dict。"""
    date = parse_date_from_slug(slug) or tw_today()
    title, news_id = _extract_title_and_news_id(slug)

    paths = get_episode_paths(slug)

    # 檢查是否已有 DB 記錄
    existing_ep = db_manager.get_episode_by_slug(slug)
    existing_st = db_manager.get_episode_status(slug)
    has_youtube = bool(existing_ep and existing_ep.get("youtube_id"))

    stage = infer_stage_from_files(slug, has_youtube_id=has_youtube)

    summary = {
        "slug": slug,
        "date": date,
        "title": title[:40],
        "news_id": news_id,
        "stage": stage,
        "has_video": paths["video"]["exists"],
        "has_audio": paths["audio_full"]["exists"],
        "n_images": len(paths["section_images"]),
        "ep_exists": existing_ep is not None,
        "st_exists": existing_st is not None,
    }

    action = "UPDATE" if (existing_ep or existing_st) else "INSERT"
    if (existing_ep or existing_st) and not force:
        action = "SKIP(exists)"
    summary["action"] = action

    if dry_run or action.startswith("SKIP"):
        return summary

    # 寫 Episode
    ep_fields = {
        "date": date,
        "title": title,
        "news_item_id": news_id,
        "script_path": paths["script"]["path"] if paths["script"]["exists"] else None,
        "audio_path":  paths["audio_full"]["path"] if paths["audio_full"]["exists"] else None,
        "images_dir":  paths["images_dir"]["path"] if paths["images_dir"]["exists"] else None,
        "thumbnail_path": paths["thumbnail"]["path"] if paths["thumbnail"]["exists"] else None,
        "video_path":  paths["video"]["path"] if paths["video"]["exists"] else None,
        "status":      "draft" if not has_youtube else "uploaded",
    }
    db_manager.upsert_episode(slug=slug, **ep_fields)

    # 寫 EpisodeStatus
    db_manager.set_episode_status(
        slug=slug, stage=stage, date=date,
        selected_id=news_id,
    )

    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只列印不寫 DB")
    ap.add_argument("--slug", default=None, help="只處理指定 slug")
    ap.add_argument("--force", action="store_true", help="覆寫既有 DB 記錄")
    ap.add_argument("--include-partial", action="store_true",
                    help="包含只做到研究階段的集數（無 script.json）")
    args = ap.parse_args()

    init_db()

    slugs = [args.slug] if args.slug else list_slugs_on_disk(
        require_script=not args.include_partial)
    if not slugs:
        print("找不到任何 slug（data/scripts/ 下無 script.json）")
        return 1

    print(f"{'(DRY-RUN) ' if args.dry_run else ''}回填 {len(slugs)} 個 slug：\n")
    print(f"{'SLUG':<55} {'STAGE':<14} {'TITLE':<30} {'ACTION'}")
    print("-" * 120)

    for slug in slugs:
        s = backfill_one(slug, dry_run=args.dry_run, force=args.force)
        print(f"{s['slug']:<55} {s['stage']:<14} {s['title']:<30} {s['action']}"
              f"  [audio={s['has_audio']}, imgs={s['n_images']}, video={s['has_video']}]")

    print()
    if args.dry_run:
        print("DRY-RUN 完成。未寫入 DB。執行時移除 --dry-run 參數。")
    else:
        print(f"完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
