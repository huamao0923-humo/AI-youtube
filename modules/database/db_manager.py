"""資料庫 CRUD — 相容 SQLite（本地）與 PostgreSQL（Railway/Neon）。

所有模組透過此檔存取資料，不直接操作 ORM Session。
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from modules.common.utils import tw_today
from typing import Any, Iterable

from .models import (
    NewsItem, Episode, PipelineStatus, EpisodeStatus, DailyBrief, ScriptRecord,
    Topic, SchedulerRun, DailyCategorySummary, SessionLocal, init_db
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
            # region：優先用 scraper 傳進來的 source_region，否則留空讓 news_pipeline 之後補
            region = (it.get("source_region") or "").strip().lower() or None
            if region not in (None, "taiwan", "global"):
                region = None
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
                region=region,
            )
            s.add(row)
            inserted += 1
    return inserted


def fetch_news_to_score(limit: int = 100, only_ai: bool = True) -> list[dict[str, Any]]:
    """撈待評分新聞。

    Args:
      only_ai: 預設 True，只撈 is_ai=1（AI 相關新聞）。
               非 AI 新聞不會做 YouTube 影片，所以預設不評。
               傳 False 可繞過該過濾（特殊情境如全量重評）。
    """
    with get_session() as s:
        q = s.query(NewsItem).filter(
            NewsItem.ai_score.is_(None), NewsItem.status == "new"
        )
        if only_ai:
            q = q.filter(NewsItem.is_ai == 1)
        rows = (
            q.order_by(NewsItem.source_priority.desc(), NewsItem.local_score.desc())
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


def fetch_candidates(min_score: float = 6.0, limit: int = 5,
                     fetched_date: str | None = None,
                     status_filter: str | None = "candidate") -> list[dict[str, Any]]:
    """撈候選新聞。

    Args:
      status_filter: 預設 'candidate'（向後相容）；傳 None 可繞過 status 過濾，
                     供 topic_clusterer --no-candidate 與其他補救流程使用。
    """
    with get_session() as s:
        q = s.query(NewsItem).filter(NewsItem.ai_score >= min_score)
        if status_filter:
            q = q.filter(NewsItem.status == status_filter)
        if fetched_date:
            q = q.filter(NewsItem.fetched_at.like(f"{fetched_date}%"))
        rows = q.order_by(NewsItem.ai_score.desc(), NewsItem.source_priority.desc()).limit(limit).all()
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


def update_progress(detail: str, date: str | None = None) -> None:
    """更新 progress_detail（不改 stage）。同時寫 PipelineStatus（legacy）與 active EpisodeStatus。"""
    if not date:
        date = tw_today()
    with get_session() as s:
        row = s.query(PipelineStatus).filter_by(date=date).first()
        if row and hasattr(row, "progress_detail"):
            row.progress_detail = (detail or "")[:200]
        # 同步寫到 active EpisodeStatus（若有）
        mid_stages = ["researching", "scripting", "tts", "images",
                      "compositing", "uploading"]
        ep = (
            s.query(EpisodeStatus)
            .filter(EpisodeStatus.stage.in_(mid_stages))
            .filter((EpisodeStatus.error_msg.is_(None)) | (EpisodeStatus.error_msg == ""))
            .order_by(EpisodeStatus.priority.desc(),
                      EpisodeStatus.created_at.asc())
            .first()
        )
        if ep:
            ep.progress_detail = (detail or "")[:200]


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
        if hasattr(row, "progress_detail") and "progress_detail" not in kwargs:
            row.progress_detail = None  # 切換 stage 時清空
        for k, v in kwargs.items():
            if hasattr(row, k):
                setattr(row, k, v)


# ───────────── Episode Status（slug-based，支援每天多集） ─────────────

def get_episode_status(slug: str) -> dict[str, Any] | None:
    """查詢指定 slug 的流水線狀態，找不到回傳 None。"""
    with get_session() as s:
        row = s.query(EpisodeStatus).filter_by(slug=slug).first()
        return _row_to_dict(row) if row else None


def set_episode_status(slug: str, stage: str, date: str | None = None, **kwargs) -> None:
    """寫入 / 更新 EpisodeStatus。切換 stage 時自動清空 progress_detail。"""
    if not date:
        date = tw_today()
    now = datetime.now(timezone.utc).isoformat()
    with get_session() as s:
        row = s.query(EpisodeStatus).filter_by(slug=slug).first()
        created = False
        if not row:
            row = EpisodeStatus(slug=slug, date=date, created_at=now)
            s.add(row)
            created = True
        row.stage = stage
        row.updated_at = now
        if created or ("progress_detail" not in kwargs):
            row.progress_detail = None  # 新建或切換 stage 清空
        for k, v in kwargs.items():
            if hasattr(row, k):
                setattr(row, k, v)


def update_episode_progress(slug: str, detail: str) -> None:
    """只更新 progress_detail（不改 stage）。"""
    with get_session() as s:
        row = s.query(EpisodeStatus).filter_by(slug=slug).first()
        if row:
            row.progress_detail = (detail or "")[:200]


def list_episode_statuses(
    date: str | None = None,
    stages: list[str] | None = None,
) -> list[dict[str, Any]]:
    """列出所有 EpisodeStatus（可按日期/stage 過濾）。"""
    with get_session() as s:
        q = s.query(EpisodeStatus)
        if date:
            q = q.filter_by(date=date)
        if stages:
            q = q.filter(EpisodeStatus.stage.in_(stages))
        q = q.order_by(EpisodeStatus.updated_at.desc().nullslast(),
                       EpisodeStatus.id.desc())
        return [_row_to_dict(r) for r in q.all()]


def get_active_episode() -> dict[str, Any] | None:
    """回傳下一個 watcher 要處理的 slug。

    規則：
    - stage 屬於 pipeline 中段（researching/scripting/tts/images/compositing/uploading/selected）
    - error_msg 為空
    - 優先順序：中段 pipeline（非 selected）> selected；同類內 priority DESC, created_at ASC
    """
    mid_stages = ["researching", "scripting", "tts", "images",
                  "compositing", "uploading"]
    with get_session() as s:
        # 先找中段 pipeline
        row = (
            s.query(EpisodeStatus)
            .filter(EpisodeStatus.stage.in_(mid_stages))
            .filter((EpisodeStatus.error_msg.is_(None)) | (EpisodeStatus.error_msg == ""))
            .order_by(EpisodeStatus.priority.desc(),
                      EpisodeStatus.created_at.asc())
            .first()
        )
        if row:
            return _row_to_dict(row)
        # 無中段 → 找 selected
        row = (
            s.query(EpisodeStatus)
            .filter_by(stage="selected")
            .filter((EpisodeStatus.error_msg.is_(None)) | (EpisodeStatus.error_msg == ""))
            .order_by(EpisodeStatus.priority.desc(),
                      EpisodeStatus.created_at.asc())
            .first()
        )
        return _row_to_dict(row) if row else None


# ───────────── Episode（成品記錄） ─────────────

def upsert_episode(slug: str, **fields) -> int:
    """依 slug 新增或更新 Episode，回傳 id。"""
    with get_session() as s:
        row = s.query(Episode).filter_by(slug=slug).first()
        if not row:
            row = Episode(slug=slug)
            s.add(row)
        for k, v in fields.items():
            if hasattr(row, k) and v is not None:
                setattr(row, k, v)
        s.flush()
        return row.id


def get_episode_by_slug(slug: str) -> dict[str, Any] | None:
    with get_session() as s:
        row = s.query(Episode).filter_by(slug=slug).first()
        return _row_to_dict(row) if row else None


def list_episodes(limit: int = 50, date: str | None = None) -> list[dict[str, Any]]:
    """列出所有 Episode（成品記錄）。"""
    with get_session() as s:
        q = s.query(Episode)
        if date:
            q = q.filter_by(date=date)
        q = q.order_by(Episode.date.desc(), Episode.id.desc()).limit(limit)
        return [_row_to_dict(r) for r in q.all()]


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


def get_latest_fetch_date() -> str | None:
    """回傳最新一次爬取的 UTC 日期字串（YYYY-MM-DD），找不到則回傳 None。"""
    from sqlalchemy import func
    with get_session() as s:
        row = s.query(func.substr(NewsItem.fetched_at, 1, 10)).order_by(NewsItem.fetched_at.desc()).first()
        return row[0] if row else None


def get_fetch_date_summary() -> list[dict[str, Any]]:
    """回傳各爬取日期的新聞統計（fetched_at 日期部分，YYYY-MM-DD）。"""
    from sqlalchemy import func, case
    with get_session() as s:
        date_col = func.substr(NewsItem.fetched_at, 1, 10)
        rows = (
            s.query(
                date_col.label("date"),
                func.count(NewsItem.id).label("total"),
                func.sum(case((NewsItem.status == "candidate", 1), else_=0)).label("candidates"),
                func.sum(case((NewsItem.ai_score.is_(None), 1), else_=0)).label("unscored"),
            )
            .group_by(date_col)
            .order_by(date_col.desc())
            .all()
        )
        return [{"date": r.date, "total": r.total,
                 "candidates": r.candidates, "unscored": r.unscored} for r in rows]


def delete_news_by_date(date_str: str) -> int:
    """刪除指定爬取日期（YYYY-MM-DD）的所有新聞。回傳刪除筆數。"""
    with get_session() as s:
        rows = s.query(NewsItem).filter(NewsItem.fetched_at.like(f"{date_str}%")).all()
        count = len(rows)
        for r in rows:
            s.delete(r)
        return count


def get_unscored_count(only_ai: bool = True) -> int:
    """未評分新聞數。預設只算 is_ai=1（給 sidebar 徽章用）。"""
    with get_session() as s:
        q = s.query(NewsItem).filter(
            NewsItem.ai_score.is_(None), NewsItem.status == "new"
        )
        if only_ai:
            q = q.filter(NewsItem.is_ai == 1)
        return q.count()


def get_news_by_date(date_str: str, status_filter: list[str] | None = None) -> list[dict[str, Any]]:
    """回傳指定爬取日期的所有新聞，可選 status 過濾。"""
    with get_session() as s:
        q = s.query(NewsItem).filter(NewsItem.fetched_at.like(f"{date_str}%"))
        if status_filter:
            q = q.filter(NewsItem.status.in_(status_filter))
        rows = q.order_by(NewsItem.ai_score.desc(), NewsItem.source_priority.desc()).all()
        return [_row_to_dict(r) for r in rows]


def get_today_unprocessed_count(only_ai: bool = True) -> int:
    """今日抓取且 status='new'（未評分）的數量，用於徽章。預設只算 is_ai=1。"""
    today = tw_today()
    with get_session() as s:
        q = s.query(NewsItem).filter(
            NewsItem.fetched_at.like(f"{today}%"),
            NewsItem.status == "new",
        )
        if only_ai:
            q = q.filter(NewsItem.is_ai == 1)
        return q.count()


# ───────────── Script Record ─────────────

def save_script(date: str, news_id: int | None,
                script: dict[str, Any], research: dict[str, Any] | None = None,
                *, topic_id: int | None = None,
                source_news_ids: list[int] | None = None) -> int:
    import json
    with get_session() as s:
        # 若 research 帶了多篇資料就一起寫進 ScriptRecord
        if research and not source_news_ids:
            source_news_ids = research.get("news_ids") or None
        if research and topic_id is None:
            topic_id = research.get("topic_id")

        row = ScriptRecord(
            date=date,
            news_item_id=news_id,
            script_json=json.dumps(script, ensure_ascii=False),
            research_json=json.dumps(research, ensure_ascii=False) if research else None,
            status="draft",
            created_at=datetime.now(timezone.utc).isoformat(),
            topic_id=topic_id,
            source_news_ids=json.dumps(source_news_ids, ensure_ascii=False) if source_news_ids else None,
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
        # 待評分只算 AI 新聞（非 AI 新聞不做 YouTube 影片）
        today_unprocessed = s.query(NewsItem).filter(
            NewsItem.fetched_at.like(f"{today}%"),
            NewsItem.status == "new",
            NewsItem.is_ai == 1,
        ).count()
    status = get_pipeline_status(today)
    return {
        "total": total, "candidates": candidates,
        "selected": selected, "unscored": today_unprocessed,
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


# ───────────── Topic（Phase A 新增） ─────────────

def create_topic(*, slug: str, title: str, summary: str | None = None,
                  category: str | None = None, region: str | None = None,
                  first_seen_date: str | None = None, last_seen_date: str | None = None,
                  news_count: int = 0, top_news_id: int | None = None,
                  aggregate_score: float = 0, status: str = "open",
                  auto_created: int = 1, notes: str | None = None) -> int:
    """新增 Topic，回傳 id。slug 唯一；若已存在拋 ValueError。"""
    now = datetime.now(timezone.utc).isoformat()
    with get_session() as s:
        exists = s.query(Topic).filter_by(slug=slug).first()
        if exists:
            raise ValueError(f"Topic slug 已存在：{slug}")
        row = Topic(
            slug=slug, title=title, summary=summary,
            category=category, region=region,
            first_seen_date=first_seen_date, last_seen_date=last_seen_date,
            news_count=news_count, top_news_id=top_news_id,
            aggregate_score=aggregate_score, status=status,
            auto_created=auto_created, notes=notes,
            created_at=now, updated_at=now,
        )
        s.add(row)
        s.flush()
        return row.id


def update_topic(topic_id: int, **fields) -> None:
    """部分欄位更新。"""
    with get_session() as s:
        row = s.query(Topic).filter_by(id=topic_id).first()
        if not row:
            return
        for k, v in fields.items():
            if hasattr(row, k) and v is not None:
                setattr(row, k, v)
        row.updated_at = datetime.now(timezone.utc).isoformat()


def get_topic(topic_id: int) -> dict[str, Any] | None:
    with get_session() as s:
        row = s.query(Topic).filter_by(id=topic_id).first()
        return _row_to_dict(row) if row else None


def get_topic_by_slug(slug: str) -> dict[str, Any] | None:
    with get_session() as s:
        row = s.query(Topic).filter_by(slug=slug).first()
        return _row_to_dict(row) if row else None


def list_topics(date: str | None = None, category: str | None = None,
                 region: str | None = None, status: str | None = None,
                 sort: str = "aggregate_score", limit: int = 50) -> list[dict[str, Any]]:
    """列出主題，可依 date（last_seen_date）/ category / region / status 篩選。

    sort: "aggregate_score" | "last_seen" | "news_count"
    """
    with get_session() as s:
        q = s.query(Topic)
        if date:
            q = q.filter(Topic.last_seen_date == date)
        if category:
            q = q.filter(Topic.category == category)
        if region:
            q = q.filter(Topic.region == region)
        if status:
            q = q.filter(Topic.status == status)

        if sort == "last_seen":
            q = q.order_by(Topic.last_seen_date.desc(), Topic.aggregate_score.desc())
        elif sort == "news_count":
            q = q.order_by(Topic.news_count.desc(), Topic.aggregate_score.desc())
        else:
            q = q.order_by(Topic.aggregate_score.desc(), Topic.last_seen_date.desc())
        return [_row_to_dict(r) for r in q.limit(limit).all()]


def list_news_by_topic(topic_id: int) -> list[dict[str, Any]]:
    with get_session() as s:
        rows = (
            s.query(NewsItem)
            .filter(NewsItem.topic_id == topic_id)
            .order_by(NewsItem.ai_score.desc(), NewsItem.published_at.desc())
            .all()
        )
        return [_row_to_dict(r) for r in rows]


def attach_news_to_topic(news_ids: list[int], topic_id: int) -> int:
    """把多則新聞綁到指定 topic。回傳實際變更數。同時刷新 topic 的 news_count/last_seen。"""
    if not news_ids:
        return 0
    now_date = tw_today()
    changed = 0
    with get_session() as s:
        rows = s.query(NewsItem).filter(NewsItem.id.in_(news_ids)).all()
        for r in rows:
            if r.topic_id != topic_id:
                r.topic_id = topic_id
                changed += 1
        s.flush()  # 先 flush 讓下面的 count 看到最新值
        # 更新 topic 統計
        t = s.query(Topic).filter_by(id=topic_id).first()
        if t:
            t.news_count = s.query(NewsItem).filter_by(topic_id=topic_id).count()
            t.last_seen_date = now_date
            t.updated_at = datetime.now(timezone.utc).isoformat()
    return changed


def detach_news_from_topic(news_ids: list[int]) -> int:
    """把新聞從目前主題解開（topic_id = NULL）。回傳變更數。"""
    if not news_ids:
        return 0
    changed = 0
    with get_session() as s:
        rows = s.query(NewsItem).filter(NewsItem.id.in_(news_ids)).all()
        affected_topics = set()
        for r in rows:
            if r.topic_id is not None:
                affected_topics.add(r.topic_id)
                r.topic_id = None
                changed += 1
        # 重算受影響主題的 news_count
        for tid in affected_topics:
            t = s.query(Topic).filter_by(id=tid).first()
            if t:
                t.news_count = s.query(NewsItem).filter_by(topic_id=tid).count()
                t.updated_at = datetime.now(timezone.utc).isoformat()
    return changed


def merge_topics(source_ids: list[int], target_id: int) -> int:
    """把 source 主題的新聞全部併入 target，source 狀態設為 archived。回傳搬移數。"""
    if not source_ids or target_id in source_ids:
        return 0
    moved = 0
    now = datetime.now(timezone.utc).isoformat()
    with get_session() as s:
        for sid in source_ids:
            rows = s.query(NewsItem).filter_by(topic_id=sid).all()
            for r in rows:
                r.topic_id = target_id
                moved += 1
            src = s.query(Topic).filter_by(id=sid).first()
            if src:
                src.status = "archived"
                src.auto_created = 0
                src.news_count = 0
                src.notes = (src.notes or "") + f"\n[merged into topic #{target_id} at {now}]"
                src.updated_at = now
        # 刷新 target 統計
        t = s.query(Topic).filter_by(id=target_id).first()
        if t:
            t.news_count = s.query(NewsItem).filter_by(topic_id=target_id).count()
            t.auto_created = 0
            t.last_seen_date = tw_today()
            t.updated_at = now
    return moved


def split_topic(topic_id: int, keep_news_ids: list[int], *,
                 new_title: str, new_slug: str) -> int:
    """把指定 news 從目前主題拆出到新主題，回傳新 topic_id。"""
    now = datetime.now(timezone.utc).isoformat()
    with get_session() as s:
        src = s.query(Topic).filter_by(id=topic_id).first()
        if not src:
            raise ValueError(f"找不到 topic #{topic_id}")
        # 建新 Topic
        new_topic = Topic(
            slug=new_slug, title=new_title,
            category=src.category, region=src.region,
            first_seen_date=tw_today(), last_seen_date=tw_today(),
            aggregate_score=src.aggregate_score,
            status="open", auto_created=0,
            created_at=now, updated_at=now,
        )
        s.add(new_topic)
        s.flush()
        new_id = new_topic.id

        rows = s.query(NewsItem).filter(
            NewsItem.topic_id == topic_id,
            NewsItem.id.in_(keep_news_ids),
        ).all()
        for r in rows:
            r.topic_id = new_id
        new_topic.news_count = len(rows)

        # 刷新 source
        src.news_count = s.query(NewsItem).filter_by(topic_id=topic_id).count()
        src.auto_created = 0
        src.updated_at = now
        return new_id


def update_news_classification(updates: list[dict[str, Any]]) -> int:
    """批次更新 NewsItem 的 category / region / topic_id。

    updates: [{"id":int, "category":str?, "region":str?, "topic_id":int?}]
    """
    now = datetime.now(timezone.utc).isoformat()
    changed = 0
    with get_session() as s:
        for u in updates:
            row = s.query(NewsItem).filter_by(id=u["id"]).first()
            if not row:
                continue
            if "category" in u:
                row.category = u["category"]
            if "region" in u:
                row.region = u["region"]
            if "topic_id" in u:
                row.topic_id = u["topic_id"]
            row.classified_at = now
            changed += 1
    return changed


def dashboard_stats(date: str | None = None) -> dict[str, Any]:
    """首頁儀表板用：今日爬量 / 未評分 / 候選 / 進行中集數 / 地區分佈。"""
    target_date = date or tw_today()
    with get_session() as s:
        total_today = s.query(NewsItem).filter(
            NewsItem.fetched_at.like(f"{target_date}%")
        ).count()
        # 待評分只算 AI 新聞
        unscored = s.query(NewsItem).filter(
            NewsItem.ai_score.is_(None),
            NewsItem.is_ai == 1,
        ).count()
        candidates = s.query(NewsItem).filter(NewsItem.status == "candidate").count()
        active_episodes = s.query(EpisodeStatus).filter(
            EpisodeStatus.stage.notin_(["idle", "done"])
        ).count()
        tw_count = s.query(NewsItem).filter(
            NewsItem.region == "taiwan",
            NewsItem.fetched_at.like(f"{target_date}%"),
        ).count()
        global_count = s.query(NewsItem).filter(
            NewsItem.region == "global",
            NewsItem.fetched_at.like(f"{target_date}%"),
        ).count()
        open_topics = s.query(Topic).filter(Topic.status == "open").count()

        # 每個 stage 的 episode 數量
        from sqlalchemy import func
        stage_rows = (
            s.query(EpisodeStatus.stage, func.count(EpisodeStatus.id))
            .group_by(EpisodeStatus.stage).all()
        )
        stage_counts = {stage: cnt for stage, cnt in stage_rows}

        # 今日分類分佈
        cat_rows = (
            s.query(NewsItem.category, func.count(NewsItem.id))
            .filter(NewsItem.fetched_at.like(f"{target_date}%"))
            .group_by(NewsItem.category).all()
        )
        category_counts = {cat or "unknown": cnt for cat, cnt in cat_rows}

    return {
        "date": target_date,
        "total_today": total_today,
        "unscored": unscored,
        "candidates": candidates,
        "active_episodes": active_episodes,
        "open_topics": open_topics,
        "taiwan_today": tw_count,
        "global_today": global_count,
        "stage_counts": stage_counts,
        "category_counts": category_counts,
    }


# ───────────── Scheduler 健康檢查（Phase B 新增） ─────────────

def record_scheduler_run(job_id: str, success: bool = True,
                          error_msg: str | None = None,
                          duration_ms: int = 0) -> None:
    """記錄一次排程任務的執行結果（每 job 每次執行寫一筆）。"""
    now = datetime.now(timezone.utc).isoformat()
    with get_session() as s:
        row = SchedulerRun(
            job_id=job_id,
            last_run=now,
            success=bool(success),
            error_msg=(error_msg or "")[:500] if error_msg else None,
            duration_ms=int(duration_ms or 0),
        )
        s.add(row)


def get_scheduler_runs() -> dict[str, dict[str, Any]]:
    """回傳每個 job 最近一次的執行紀錄，鍵為 job_id。"""
    from sqlalchemy import func
    with get_session() as s:
        # 先取每個 job_id 的最大 id（= 最新一筆）
        sub = (
            s.query(SchedulerRun.job_id, func.max(SchedulerRun.id).label("max_id"))
            .group_by(SchedulerRun.job_id)
            .subquery()
        )
        rows = (
            s.query(SchedulerRun)
            .join(sub, SchedulerRun.id == sub.c.max_id)
            .all()
        )
        return {r.job_id: {
            "last_run": r.last_run,
            "success": bool(r.success),
            "error_msg": r.error_msg,
            "duration_ms": r.duration_ms or 0,
        } for r in rows}


# ───────────── 每日類別總摘要（Phase B 新增） ─────────────

def save_category_summary(date: str, feed: str, summary_zh: str,
                           news_count: int = 0,
                           top_news_ids: list[int] | None = None) -> None:
    """寫入或覆寫指定 (date, feed) 的類別摘要。"""
    import json as _json
    now = datetime.now(timezone.utc).isoformat()
    word_count = len(summary_zh or "")
    with get_session() as s:
        row = (
            s.query(DailyCategorySummary)
            .filter_by(date=date, feed=feed)
            .first()
        )
        if not row:
            row = DailyCategorySummary(date=date, feed=feed)
            s.add(row)
        row.summary_zh = summary_zh
        row.news_count = int(news_count or 0)
        row.top_news_ids = _json.dumps(top_news_ids or [], ensure_ascii=False)
        row.word_count = word_count
        row.generated_at = now


def load_category_summaries(date: str | None = None) -> dict[str, dict[str, Any]]:
    """取指定日期的全部類別摘要，鍵為 feed。"""
    import json as _json
    if not date:
        date = tw_today()
    out: dict[str, dict[str, Any]] = {}
    with get_session() as s:
        rows = s.query(DailyCategorySummary).filter_by(date=date).all()
        for r in rows:
            try:
                top_ids = _json.loads(r.top_news_ids or "[]")
            except Exception:
                top_ids = []
            out[r.feed] = {
                "feed": r.feed,
                "summary_zh": r.summary_zh,
                "news_count": r.news_count or 0,
                "word_count": r.word_count or 0,
                "top_news_ids": top_ids,
                "generated_at": r.generated_at,
            }
    return out


if __name__ == "__main__":
    init_db()
    print("[OK] 資料庫初始化完成")
