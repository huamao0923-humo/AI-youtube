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


def slugify(text: str, max_len: int = 40) -> str:
    """把標題轉成檔案系統安全的 slug（保留中英數，其他轉底線）。"""
    import re
    text = re.sub(r"[^\w\u4e00-\u9fff]", "_", text or "")
    return text[:max_len].strip("_") or "untitled"


def build_slug(title: str, date: str | None = None) -> str:
    """生成集數 slug：YYYYMMDD_title。

    - date 為 None 時用今日（台灣時區）
    - date 可接受 `YYYY-MM-DD` 或 `YYYYMMDD`
    - title 會 slugify（僅保留中英數）
    """
    if not date:
        d = tw_now().strftime("%Y%m%d")
    else:
        d = date.replace("-", "")
    return f"{d}_{slugify(title)}"


def parse_date_from_slug(slug: str) -> str | None:
    """從 `YYYYMMDD_xxx` 萃取 `YYYY-MM-DD`，格式不合則 None。"""
    import re
    m = re.match(r"^(\d{4})(\d{2})(\d{2})_", slug or "")
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
