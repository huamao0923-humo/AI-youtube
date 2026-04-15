"""資料庫 CRUD — 相容 SQLite（本地）與 PostgreSQL（Railway/Neon）。

所有模組透過此檔存取資料，不直接操作 ORM Session。
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from modules.common.utils import tw_today
from typing import Any, Iterable

from .models import (
    NewsItem, Episode, PipelineStatus, DailyBrief, ScriptRecord,
    SessionLocal, init_db
)


@contextmanager
def get_session():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ───────────── 新聞相關 ─────────────

def insert_news_batch(items: Iterable[dict[str, Any]]) -> int:
    """批次寫入，遇 url 重複跳過。回傳實際新增筆數。"""
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    with get_session() as s:
        for it in items:
            exists = s.query(NewsItem).filter_by(url=it["url"]).first()
            if exists:
                continue
            row = NewsItem(
                url=it["url"],
                title=it["title"],
                source_name=it["source_name"],
                source_priority=it.get("source_priority", 5),
                published_at=it.get("published_at"),
                fetched_at=now,
                summary=it.get("summary"),
                full_text=it.get("full_text"),
                local_score=it.get("local_score", 0),
                content_hash=it.get("content_hash"),
                status=it.get("status", "new"),
            )
            s.add(row)
            inserted += 1
    return inserted


def fetch_news_to_score(limit: int = 100) -> list[dict[str, Any]]:
    with get_session() as s:
        rows = (
            s.query(NewsItem)
            .filter(NewsItem.ai_score.is_(None), NewsItem.status == "new")
            .order_by(NewsItem.source_priority.desc(), NewsItem.local_score.desc())
            .limit(limit)
            .all()
        )
        return [_row_to_dict(r) for r in rows]


def update_ai_scores(updates: list[dict[str, Any]]) -> None:
    with get_session() as s:
        for u in updates:
            row = s.query(NewsItem).filter_by(id=u["id"]).first()
            if not row:
                continue
            row.ai_score = u.get("ai_score")
            row.business_angle = u.get("business_angle")
            row.why_audience_cares = u.get("why_audience_cares")
            row.suggested_title = u.get("suggested_title")
            row.skip_reason = u.get("skip_reason")
            row.status = u.get("status", "new")


def fetch_candidates(min_score: float = 6.0, limit: int = 5) -> list[dict[str, Any]]:
    with get_session() as s:
        rows = (
            s.query(NewsItem)
            .filter(NewsItem.status == "candidate", NewsItem.ai_score >= min_score)
            .order_by(NewsItem.ai_score.desc(), NewsItem.source_priority.desc())
            .limit(limit)
            .all()
        )
        return [_row_to_dict(r) for r in rows]


def get_news_by_id(news_id: int) -> dict[str, Any] | None:
    with get_session() as s:
        row = s.query(NewsItem).filter_by(id=news_id).first()
        return _row_to_dict(row) if row else None


def mark_selected(news_id: int) -> None:
    with get_session() as s:
        row = s.query(NewsItem).filter_by(id=news_id).first()
        if row:
            row.status = "selected"


def hash_exists(content_hash: str) -> bool:
    with get_session() as s:
        return s.query(NewsItem).filter_by(content_hash=content_hash).first() is not None


# ───────────── Pipeline 狀態 ─────────────

def get_pipeline_status(date: str | None = None) -> dict[str, Any]:
    if not date:
        date = tw_today()
    with get_session() as s:
        row = s.query(PipelineStatus).filter_by(date=date).first()
        if not row:
            return {"stage": "idle", "selected_id": None,
                    "selected_angle": None, "custom_note": None,
                    "updated_at": None, "date": date}
        return _row_to_dict(row)


def set_pipeline_status(stage: str, date: str | None = None, **kwargs) -> None:
    if not date:
        date = tw_today()
    now = datetime.now(timezone.utc).isoformat()
    with get_session() as s:
        row = s.query(PipelineStatus).filter_by(date=date).first()
        if not row:
            row = PipelineStatus(date=date)
            s.add(row)
        row.stage = stage
        row.updated_at = now
        for k, v in kwargs.items():
            if hasattr(row, k):
                setattr(row, k, v)


# ───────────── Daily Brief ─────────────

def save_brief(date: str, content: dict[str, Any], unscored: int = 0) -> None:
    import json
    with get_session() as s:
        row = s.query(DailyBrief).filter_by(date=date).first()
        if not row:
            row = DailyBrief(date=date)
            s.add(row)
        row.content_json = json.dumps(content, ensure_ascii=False)
        row.candidate_count = len(content.get("candidates", []))
        row.unscored_count = unscored
        row.created_at = datetime.now(timezone.utc).isoformat()


def load_brief(date: str | None = None) -> dict[str, Any] | None:
    import json
    if not date:
        date = tw_today()
    with get_session() as s:
        row = s.query(DailyBrief).filter_by(date=date).first()
        if not row:
            return None
        d = _row_to_dict(row)
        d["content"] = json.loads(row.content_json) if row.content_json else {}
        return d


def get_unscored_count() -> int:
    with get_session() as s:
        return s.query(NewsItem).filter(
            NewsItem.ai_score.is_(None), NewsItem.status == "new"
        ).count()


# ───────────── Script Record ─────────────

def save_script(date: str, news_id: int | None,
                script: dict[str, Any], research: dict[str, Any] | None = None) -> int:
    import json
    with get_session() as s:
        row = ScriptRecord(
            date=date,
            news_item_id=news_id,
            script_json=json.dumps(script, ensure_ascii=False),
            research_json=json.dumps(research, ensure_ascii=False) if research else None,
            status="draft",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        s.add(row)
        s.flush()
        return row.id


def load_latest_script() -> dict[str, Any] | None:
    import json
    with get_session() as s:
        row = s.query(ScriptRecord).order_by(ScriptRecord.id.desc()).first()
        if not row:
            return None
        d = _row_to_dict(row)
        d["script"] = json.loads(row.script_json) if row.script_json else {}
        d["research"] = json.loads(row.research_json) if row.research_json else {}
        return d


def approve_script(script_id: int) -> None:
    with get_session() as s:
        row = s.query(ScriptRecord).filter_by(id=script_id).first()
        if row:
            row.status = "approved"
            row.approved_at = datetime.now(timezone.utc).isoformat()


# ───────────── 統計 ─────────────

def stats_today() -> dict[str, Any]:
    today = tw_today()
    with get_session() as s:
        total = s.query(NewsItem).count()
        candidates = s.query(NewsItem).filter_by(status="candidate").count()
        selected = s.query(NewsItem).filter_by(status="selected").count()
        unscored = s.query(NewsItem).filter(
            NewsItem.ai_score.is_(None), NewsItem.status == "new"
        ).count()
    status = get_pipeline_status(today)
    return {
        "total": total, "candidates": candidates,
        "selected": selected, "unscored": unscored,
        "stage": status.get("stage", "idle"), "date": today,
    }


# ───────────── 工具 ─────────────

def _row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    d = {}
    for col in row.__table__.columns:
        d[col.name] = getattr(row, col.name)
    return d


if __name__ == "__main__":
    init_db()
    print("[OK] 資料庫初始化完成")
