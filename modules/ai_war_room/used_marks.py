"""AiUsedMark CRUD — 選題時寫入，供戰情室前端灰化已用內容。"""
from __future__ import annotations

from datetime import datetime, timezone

from modules.database.models import AiUsedMark, SessionLocal


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mark(entity_type: str, entity_id: str, used_in_slug: str = "", marked_by: str | None = None) -> int | None:
    """寫入一筆標記，回傳 id。若同 (entity_type, entity_id) 已存在則跳過並回傳既有 id。"""
    s = SessionLocal()
    try:
        exist = s.query(AiUsedMark).filter(
            AiUsedMark.entity_type == entity_type,
            AiUsedMark.entity_id == str(entity_id),
        ).first()
        if exist:
            # 更新 used_in_slug（從 skip 變實際 slug 之類）
            if used_in_slug and exist.used_in_slug != used_in_slug:
                exist.used_in_slug = used_in_slug
                s.commit()
            return exist.id
        mk = AiUsedMark(
            entity_type=entity_type,
            entity_id=str(entity_id),
            used_in_slug=used_in_slug or "",
            marked_at=_now(),
            marked_by=marked_by or "",
        )
        s.add(mk)
        s.commit()
        return mk.id
    finally:
        s.close()


def mark_news_used(news_id: int | str, used_in_slug: str = "", marked_by: str | None = None) -> int | None:
    return _mark("news", str(news_id), used_in_slug, marked_by)


def mark_topic_used(topic_id: int | str, used_in_slug: str = "", marked_by: str | None = None) -> int | None:
    return _mark("topic", str(topic_id), used_in_slug, marked_by)


def unmark(mark_id: int) -> bool:
    s = SessionLocal()
    try:
        row = s.get(AiUsedMark, mark_id)
        if not row:
            return False
        s.delete(row)
        s.commit()
        return True
    finally:
        s.close()


def get_used_set(entity_type: str = "news") -> set[str]:
    """回傳該 entity_type 下所有 entity_id 的 set（供前端 O(1) 灰化）。"""
    s = SessionLocal()
    try:
        rows = s.query(AiUsedMark.entity_id).filter(AiUsedMark.entity_type == entity_type).all()
        return {r[0] for r in rows}
    finally:
        s.close()


def get_used_slug_map(entity_type: str = "news") -> dict[str, str]:
    """entity_id → used_in_slug 的 dict（公開版要連結到對應影片集）。"""
    s = SessionLocal()
    try:
        rows = s.query(AiUsedMark.entity_id, AiUsedMark.used_in_slug).filter(
            AiUsedMark.entity_type == entity_type
        ).all()
        return {eid: slug for eid, slug in rows if slug and not slug.startswith("skip_")}
    finally:
        s.close()
