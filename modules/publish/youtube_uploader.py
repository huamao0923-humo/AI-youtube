"""YouTube API 上傳模組。

首次使用需手動 OAuth2 授權（會開瀏覽器），之後 token 自動刷新。

執行：
  python -m modules.publish.youtube_uploader --script data/scripts/xxx/script.json
  python -m modules.publish.youtube_uploader --script ... --video data/videos/xxx/final.mp4
  python -m modules.publish.youtube_uploader --auth-only   # 只做授權，不上傳
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from modules.common.utils import tw_today
from pathlib import Path

from loguru import logger

from modules.common.config import PROJECT_ROOT, settings
from modules.common.logging_setup import setup_logger

setup_logger()

TOKEN_PATH = PROJECT_ROOT / "config" / "youtube_token.json"
SECRET_PATH = PROJECT_ROOT / "config" / "youtube_client_secret.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _get_credentials():
    """取得 OAuth2 憑證（首次需瀏覽器授權）。"""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
    except ImportError:
        raise RuntimeError(
            "請安裝 YouTube API 套件：\n"
            "pip install google-api-python-client google-auth-oauthlib"
        )

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
        else:
            if not SECRET_PATH.exists():
                raise FileNotFoundError(
                    f"找不到 YouTube 憑證檔：{SECRET_PATH}\n"
                    "請至 Google Cloud Console 建立 OAuth2 Desktop 憑證並下載。\n"
                    "教學：https://developers.google.com/youtube/v3/quickstart/python"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(SECRET_PATH), SCOPES)
            creds = flow.run_local_server(port=0)

        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        logger.info(f"OAuth2 Token 已儲存：{TOKEN_PATH}")

    return creds


def _build_youtube():
    from googleapiclient.discovery import build
    creds = _get_credentials()
    return build("youtube", "v3", credentials=creds)


def _schedule_time(hour: int = 20, tz_offset: int = 8) -> str:
    """計算今日台灣時間 hour 點的 UTC ISO8601（YouTube scheduledStartTime 格式）。"""
    now_utc = datetime.now(timezone.utc)
    tw_now = now_utc + timedelta(hours=tz_offset)
    publish_tw = tw_now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if publish_tw <= tw_now:  # 已過今日發布時間，改明日
        publish_tw += timedelta(days=1)
    publish_utc = publish_tw - timedelta(hours=tz_offset)
    return publish_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def upload(
    video_path: Path,
    script_path: Path,
    title_index: int = 0,
    privacy: str | None = None,
    schedule: bool = True,
    *,
    slug: str | None = None,
) -> str:
    """上傳影片，回傳 YouTube video_id。slug 有值時進度寫進 EpisodeStatus。"""
    from googleapiclient.http import MediaFileUpload

    def _p(msg: str) -> None:
        logger.info(msg)
        if slug:
            try:
                from modules.database import db_manager
                db_manager.update_episode_progress(slug, msg)
            except Exception:
                pass

    script = json.loads(script_path.read_text(encoding="utf-8"))
    cfg = settings()["youtube"]

    title = (script.get("title_options") or ["AI 每日新聞"])[title_index]
    description = script.get("youtube_description", "")
    tags = script.get("tags", ["AI", "人工智慧"])
    category_id = str(cfg.get("default_category", 28))
    privacy_status = privacy or cfg.get("default_privacy", "private")

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:500],
            "categoryId": category_id,
            "defaultLanguage": "zh-TW",
        },
        "status": {"privacyStatus": privacy_status},
    }

    if schedule and privacy_status == "private":
        pub_time = settings()["video"].get("publish_time", "20:00")
        hour = int(pub_time.split(":")[0])
        body["status"]["publishAt"] = _schedule_time(hour=hour)
        body["status"]["privacyStatus"] = "private"
        logger.info(f"排程發布時間：台灣 {pub_time}（{body['status']['publishAt']} UTC）")

    youtube = _build_youtube()

    size_mb = video_path.stat().st_size / 1024 / 1024
    _p(f"📤 開始上傳 {video_path.name}（{size_mb:.1f} MB）")

    media = MediaFileUpload(str(video_path), mimetype="video/mp4",
                            chunksize=10 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(part=",".join(body.keys()), body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            _p(f"📤 YouTube 上傳 {pct}%（{size_mb * status.progress():.1f}/{size_mb:.1f} MB）")

    video_id = response["id"]
    _p(f"✅ 上傳完成：youtu.be/{video_id}")
    return video_id


def upload_thumbnail(video_id: str, thumbnail_path: Path) -> None:
    from googleapiclient.http import MediaFileUpload
    youtube = _build_youtube()
    media = MediaFileUpload(str(thumbnail_path), mimetype="image/png")
    youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
    logger.info(f"縮圖已上傳：video_id={video_id}")


def save_episode(video_id: str, script_path: Path, video_path: Path) -> None:
    """把這集記錄寫入 DB（upsert by slug，支援多集並行）。"""
    from modules.database import db_manager
    from modules.database.models import TopicHistory
    from modules.storage.local_storage import get_episode_paths
    import re

    script = json.loads(script_path.read_text(encoding="utf-8"))
    title = (script.get("title_options") or [""])[0]
    today = tw_today()
    news_id = script.get("_meta", {}).get("news_id")
    slug = script_path.parent.name
    paths = get_episode_paths(slug)

    # upsert Episode by slug
    db_manager.upsert_episode(
        slug=slug,
        date=today,
        news_item_id=news_id,
        script_path=str(script_path),
        audio_path=paths["audio_full"]["path"] if paths["audio_full"]["exists"] else None,
        images_dir=paths["images_dir"]["path"] if paths["images_dir"]["exists"] else None,
        thumbnail_path=paths["thumbnail"]["path"] if paths["thumbnail"]["exists"] else None,
        video_path=str(video_path),
        youtube_id=video_id,
        title=title,
        published_at=datetime.now(timezone.utc).isoformat(),
        status="uploaded",
    )

    # topic_history：記錄本次主題，避免未來重複
    ep = db_manager.get_episode_by_slug(slug) or {}
    ep_id = ep.get("id")
    with db_manager.get_session() as s:
        keywords = re.findall(r'[A-Z][a-zA-Z]+|[\u4e00-\u9fff]{2,6}', title)[:3]
        for kw in keywords:
            s.add(TopicHistory(topic_keyword=kw, used_date=today, episode_id=ep_id))

    # 新架構：更新 EpisodeStatus
    db_manager.set_episode_status(slug=slug, stage="done", date=today, error_msg=None)
    # Legacy 相容
    db_manager.set_pipeline_status("done")
    logger.info(f"[{slug}] Episode 記錄已存入 DB：{title}")


def upload_shorts(script_path: Path, shorts_video: Path | None = None) -> str | None:
    """上傳 Shorts 版本（60 秒）。shorts_video 為 None 時跳過。"""
    if shorts_video and not shorts_video.exists():
        logger.warning(f"Shorts 影片不存在：{shorts_video}，跳過")
        return None
    if not shorts_video:
        logger.info("未提供 Shorts 影片，跳過 Shorts 上傳")
        return None

    script = json.loads(script_path.read_text(encoding="utf-8"))
    title_base = (script.get("title_options") or ["AI Shorts"])[0]
    shorts_title = f"{title_base[:85]} #Shorts"
    shorts_script = script.get("shorts_script", "")

    body = {
        "snippet": {
            "title": shorts_title[:100],
            "description": shorts_script[:300] + "\n\n#Shorts #AI #人工智慧",
            "tags": script.get("tags", []) + ["Shorts"],
            "categoryId": "28",
        },
        "status": {"privacyStatus": "public"},
    }

    from googleapiclient.http import MediaFileUpload
    youtube = _build_youtube()
    media = MediaFileUpload(str(shorts_video), mimetype="video/mp4",
                            chunksize=5 * 1024 * 1024, resumable=True)
    req = youtube.videos().insert(part=",".join(body.keys()), body=body, media_body=media)
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    vid = resp["id"]
    logger.info(f"Shorts 上傳完成：https://youtu.be/{vid}")
    return vid


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", type=Path)
    ap.add_argument("--video", type=Path)
    ap.add_argument("--title-index", type=int, default=0)
    ap.add_argument("--privacy", choices=["private", "unlisted", "public"], default=None)
    ap.add_argument("--no-schedule", action="store_true")
    ap.add_argument("--auth-only", action="store_true", help="只做 OAuth2 授權")
    ap.add_argument("--thumbnail", type=Path, default=None)
    args = ap.parse_args()

    if args.auth_only:
        _get_credentials()
        print("[OK] OAuth2 授權完成")
        return

    if not args.script:
        ap.error("請指定 --script")

    # 自動找影片
    video = args.video
    if not video:
        slug = args.script.parent.name
        video = PROJECT_ROOT / "data" / "videos" / slug / "final.mp4"
        if not video.exists():
            ap.error(f"找不到影片：{video}，請先執行 compositor.py 或用 --video 指定")

    video_id = upload(
        video_path=video,
        script_path=args.script,
        title_index=args.title_index,
        privacy=args.privacy,
        schedule=not args.no_schedule,
    )

    # 上傳縮圖
    thumb = args.thumbnail
    if not thumb:
        slug = args.script.parent.name
        thumb = PROJECT_ROOT / "data" / "images" / slug / "thumbnail.png"
    if thumb and thumb.exists():
        upload_thumbnail(video_id, thumb)

    save_episode(video_id, args.script, video)
    print(f"[OK] 上傳完成：https://youtu.be/{video_id}")


if __name__ == "__main__":
    main()
