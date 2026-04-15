"""社群貼文發布 — 從 script.json 取出社群貼文，準備或自動發布。

目前支援：
  - 儲存貼文到 data/social/YYYYMMDD/ 資料夾（人工複製）
  - Buffer API 排程發布（需 BUFFER_ACCESS_TOKEN）

執行：
  python -m modules.publish.social_publisher --script data/scripts/xxx/script.json --youtube-id abc123
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
from loguru import logger

from modules.common.config import PROJECT_ROOT, env
from modules.common.logging_setup import setup_logger

setup_logger()

SOCIAL_DIR = PROJECT_ROOT / "data" / "social"


def prepare_posts(script_path: Path, youtube_id: str) -> dict:
    """從 script.json 取出社群貼文，加入 YouTube 連結，存檔。"""
    script = json.loads(script_path.read_text(encoding="utf-8"))
    posts = script.get("social_posts", {})
    title = (script.get("title_options") or ["AI 新聞"])[0]
    yt_url = f"https://youtu.be/{youtube_id}" if youtube_id else ""

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    out_dir = SOCIAL_DIR / today
    out_dir.mkdir(parents=True, exist_ok=True)

    result = {}

    # Twitter / X 推文串
    thread = posts.get("twitter_thread", [])
    if thread:
        # 第一則加 YouTube 連結
        thread_with_url = list(thread)
        if yt_url:
            thread_with_url[-1] = thread_with_url[-1] + f"\n\n{yt_url}"
        tw_path = out_dir / "twitter_thread.txt"
        tw_path.write_text("\n\n---\n\n".join(thread_with_url), encoding="utf-8")
        result["twitter"] = str(tw_path)
        logger.info(f"Twitter 推文串：{tw_path}")

    # LinkedIn
    linkedin = posts.get("linkedin_post", "")
    if linkedin:
        if yt_url:
            linkedin += f"\n\n▶ 完整影片：{yt_url}"
        li_path = out_dir / "linkedin.txt"
        li_path.write_text(linkedin, encoding="utf-8")
        result["linkedin"] = str(li_path)

    # IG
    ig = posts.get("ig_caption", "")
    if ig:
        if yt_url:
            ig += f"\n\n連結在 bio 👆"
        ig_path = out_dir / "instagram.txt"
        ig_path.write_text(ig, encoding="utf-8")
        result["instagram"] = str(ig_path)

    # 彙整檔
    summary = {
        "date": today,
        "youtube_url": yt_url,
        "title": title,
        "files": result,
        "twitter_thread": thread,
        "linkedin": linkedin,
        "ig": ig,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info(f"社群貼文已存至：{out_dir}")
    return summary


def buffer_schedule(posts: dict, profile_ids: list[str]) -> None:
    """透過 Buffer API 排程發布（需 BUFFER_ACCESS_TOKEN）。"""
    token = env("BUFFER_ACCESS_TOKEN")
    if not token:
        logger.info("未設定 BUFFER_ACCESS_TOKEN，跳過 Buffer 發布")
        return

    yt_url = posts.get("youtube_url", "")
    headers = {"Authorization": f"Bearer {token}",
               "Content-Type": "application/json"}

    for profile_id in profile_ids:
        # 只排 Twitter 第一則（含連結）
        thread = posts.get("twitter_thread", [])
        if thread:
            text = thread[0] + (f"\n\n{yt_url}" if yt_url else "")
            payload = {
                "profile_ids": [profile_id],
                "text": text,
                "scheduled_at": "now",
            }
            try:
                resp = httpx.post(
                    "https://api.bufferapp.com/1/updates/create.json",
                    headers=headers, json=payload, timeout=15
                )
                if resp.status_code == 200:
                    logger.info(f"Buffer 排程成功：profile={profile_id}")
                else:
                    logger.warning(f"Buffer 失敗：{resp.status_code} {resp.text[:200]}")
            except httpx.HTTPError as e:
                logger.error(f"Buffer API 錯誤：{e}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", type=Path, required=True)
    ap.add_argument("--youtube-id", default="")
    ap.add_argument("--buffer", nargs="*", default=[], help="Buffer profile IDs")
    args = ap.parse_args()

    posts = prepare_posts(args.script, args.youtube_id)

    if args.buffer:
        buffer_schedule(posts, args.buffer)

    print(f"\n[OK] 社群貼文已準備完成")
    print(f"  YouTube：{posts['youtube_url']}")
    print(f"  Twitter：{len(posts.get('twitter_thread', []))} 則推文")
    print(f"  存放位置：{SOCIAL_DIR}/{datetime.now(timezone.utc).strftime('%Y%m%d')}/")


if __name__ == "__main__":
    main()
