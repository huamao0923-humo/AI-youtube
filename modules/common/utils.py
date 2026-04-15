"""共用工具函式。"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

TW_TZ = timezone(timedelta(hours=8))


def tw_now() -> datetime:
    """現在的台灣時間。"""
    return datetime.now(TW_TZ)


def tw_today() -> str:
    """台灣時間今日日期字串，格式 YYYY-MM-DD。"""
    return tw_now().strftime("%Y-%m-%d")


def tw_isonow() -> str:
    """台灣時間現在的 ISO8601 字串。"""
    return tw_now().isoformat()
