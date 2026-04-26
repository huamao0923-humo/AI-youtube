"""SQLAlchemy ORM 模型 — 同時支援 SQLite（本地）與 PostgreSQL（Railway/Neon）。"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer, String, Text, create_engine, event
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# 優先用環境變數 DATABASE_URL（Neon / Railway），否則落回本地 SQLite
_DEFAULT_SQLITE = "sqlite:///./data/channel.db"
DATABASE_URL = os.getenv("DATABASE_URL", _DEFAULT_SQLITE)

# Neon 的 URL 開頭是 postgres://，SQLAlchemy 需要 postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# PostgreSQL 需要 psycopg2；SQLite 用內建
_is_pg = DATABASE_URL.startswith("postgresql")
_connect_args = {} if _is_pg else {"check_same_thread": False}

engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    pool_pre_ping=True,
    echo=False,
)

# SQLite WAL 模式（並發更好）
if not _is_pg:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA journal_mode=WAL;")
        dbapi_conn.execute("PRAGMA foreign_keys=ON;")

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


class NewsItem(Base):
    __tablename__ = "news_items"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    url             = Column(String(2048), unique=True, nullable=False)
    title           = Column(Text, nullable=False)
    source_name     = Column(String(128), nullable=False)
    source_priority = Column(Integer, default=5)
    published_at    = Column(String(64))
    fetched_at      = Column(String(64), nullable=False)
    summary         = Column(Text)
    full_text       = Column(Text)
    local_score     = Column(Float, default=0)
    ai_score        = Column(Float)
    business_angle  = Column(Text)
    why_audience_cares = Column(Text)
    suggested_title = Column(Text)
    skip_reason     = Column(Text)
    status          = Column(String(32), default="new")
    content_hash    = Column(String(64))
    # 分類與地區（Phase A 新增，nullable 相容舊資料）
    region          = Column(String(16), index=True, default="global")  # "global" | "taiwan"
    category        = Column(String(32), index=True)  # ai_model|business|policy|product|semiconductor|other
    topic_id        = Column(Integer, index=True)     # FK → topics.id（nullable）
    classified_at   = Column(String(64))              # 重分類偵測
    # AI 戰情室欄位
    is_ai           = Column(Integer, index=True, default=0)   # 0/1 快篩
    ai_company      = Column(String(32), index=True)           # openai|anthropic|gdeepmind|meta|xai|...
    model_release   = Column(Integer, default=0)               # 0/1，命中 GPT-\d / Claude \d 等
    title_zh        = Column(Text)                             # 翻譯後的繁體中文標題（本地 CLI 寫入）
    translated_at   = Column(String(64))                       # 標題翻譯時間戳
    summary_zh      = Column(Text)                             # 翻譯後的繁體中文摘要
    summary_translated_at = Column(String(64))                 # 摘要翻譯時間戳


class Episode(Base):
    __tablename__ = "episodes"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    date            = Column(String(16), nullable=False)
    slug            = Column(String(200), index=True)     # 連結檔案系統命名
    news_item_id    = Column(Integer)
    script_path     = Column(Text)
    audio_path      = Column(Text)
    images_dir      = Column(Text)
    thumbnail_path  = Column(Text)
    video_path      = Column(Text)
    youtube_id      = Column(String(64))
    title           = Column(Text)
    published_at    = Column(String(64))
    views_24h       = Column(Integer)
    views_7d        = Column(Integer)
    ctr             = Column(Float)
    avg_watch_pct   = Column(Float)
    notes           = Column(Text)
    drive_folder_id = Column(String(128))                 # 未來 Drive 整合
    drive_manifest  = Column(Text)                        # JSON: {script:id, audio:id, ...}
    status          = Column(String(32), default="draft") # draft|uploaded|published|archived
    # Phase A 新增：Topic 綁定
    topic_id        = Column(Integer, index=True)         # 若此集從 Topic 產生
    source_news_ids = Column(Text)                        # JSON array 快照


class TopicHistory(Base):
    __tablename__ = "topic_history"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    topic_keyword = Column(String(256))
    company       = Column(String(128))
    used_date     = Column(String(16))
    episode_id    = Column(Integer)


class PipelineStatus(Base):
    """[LEGACY] 以 date 為 unique 的舊流水線狀態（保留向後相容，新程式碼請用 EpisodeStatus）。"""
    __tablename__ = "pipeline_status"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    date           = Column(String(16), nullable=False, unique=True)
    stage          = Column(String(32), default="idle")
    selected_id    = Column(Integer)
    selected_angle = Column(String(4))
    custom_note    = Column(Text)
    updated_at      = Column(String(64))
    error_msg       = Column(Text)
    progress_detail = Column(Text)   # 即時進度細節（"生成第 3/10 張…"）


class EpisodeStatus(Base):
    """每集流水線狀態 — 以 slug 為唯一鍵，支援每天多集並行。"""
    __tablename__ = "episode_status"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    slug               = Column(String(200), nullable=False, unique=True, index=True)
    date               = Column(String(16), nullable=False, index=True)
    stage              = Column(String(32), default="idle")
    selected_id        = Column(Integer)
    selected_topic_id  = Column(Integer, index=True)   # Phase A：優先於 selected_id
    selected_angle     = Column(String(4))
    custom_note        = Column(Text)
    priority           = Column(Integer, default=0)
    updated_at         = Column(String(64))
    error_msg          = Column(Text)
    progress_detail    = Column(Text)
    created_at         = Column(String(64))


class DailyBrief(Base):
    """每日 Brief — 內容存 DB，Railway Web UI 可直接讀取。"""
    __tablename__ = "daily_briefs"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    date            = Column(String(16), nullable=False, unique=True)
    content_json    = Column(Text, nullable=False)   # 完整 brief dict 序列化
    candidate_count = Column(Integer, default=0)
    unscored_count  = Column(Integer, default=0)     # 當日未評分新聞數
    created_at      = Column(String(64))


class ScriptRecord(Base):
    """腳本記錄 — 存 DB，Railway 可讀取做審閱。"""
    __tablename__ = "script_records"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    date            = Column(String(16), nullable=False)
    news_item_id    = Column(Integer)
    research_json   = Column(Text)
    script_json     = Column(Text, nullable=False)
    status          = Column(String(32), default="draft")  # draft|approved|rejected
    created_at      = Column(String(64))
    approved_at     = Column(String(64))
    # Phase A：Topic 綁定
    topic_id        = Column(Integer, index=True)
    source_news_ids = Column(Text)  # JSON array


class Topic(Base):
    """主題 — 聚合多則相關新聞，作為一集影片的素材單位。"""
    __tablename__ = "topics"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    slug            = Column(String(200), unique=True, index=True, nullable=False)
    title           = Column(Text, nullable=False)
    summary         = Column(Text)
    category        = Column(String(32), index=True)  # 同 NewsItem.category
    region          = Column(String(16), index=True)  # global | taiwan | mixed
    first_seen_date = Column(String(16), index=True)
    last_seen_date  = Column(String(16), index=True)
    news_count      = Column(Integer, default=0)
    top_news_id     = Column(Integer)
    aggregate_score = Column(Float, default=0)
    status          = Column(String(32), default="open", index=True)  # open|used|archived
    auto_created    = Column(Integer, default=1)  # 0=手動調整過
    notes           = Column(Text)
    created_at      = Column(String(64))
    updated_at      = Column(String(64))
    # worldmonitor 風格：複合熱度指數（heat_calculator 寫入）
    heat_index      = Column(Float, default=0, index=True)
    heat_prev       = Column(Float, default=0)   # 前一次 refresh 的 heat，用於算漲跌
    heat_updated_at = Column(String(64))
    # 戰情室卡片用：主題彙總繁中摘要（topic_summarizer 寫入）
    summary_zh           = Column(Text)
    summary_generated_at = Column(String(64))


class AiUsedMark(Base):
    """AI 戰情室「已用」標記 — 選題時寫入，用於灰化已處理過的新聞 / topic。"""
    __tablename__ = "ai_used_marks"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    entity_type   = Column(String(16), nullable=False, index=True)  # 'news' | 'topic'
    entity_id     = Column(String(200), nullable=False, index=True) # news_id 或 topic_slug
    used_in_slug  = Column(String(200))                             # EpisodeStatus.slug 或 'skip_YYYYMMDD'
    marked_at     = Column(String(64), nullable=False)
    marked_by     = Column(String(64))                              # 多使用者預留


class DailyCategorySummary(Base):
    """每日類別總摘要 — 戰情室「焦點新聞」分節下方顯示。

    一天 × 一個 feed 一筆（feed = product / funding / partnership / research / policy / other）。
    內容是該 feed 當日所有 AI 新聞的 400-600 字繁中總結，由 category_summarizer 寫入。
    """
    __tablename__ = "daily_category_summaries"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    date            = Column(String(16), nullable=False, index=True)
    feed            = Column(String(32), nullable=False, index=True)  # product/funding/...
    summary_zh      = Column(Text, nullable=False)
    news_count      = Column(Integer, default=0)
    top_news_ids    = Column(Text)  # JSON array of news ids that fed into the summary
    word_count      = Column(Integer, default=0)
    generated_at    = Column(String(64))


class SchedulerRun(Base):
    """排程任務執行紀錄 — 健康檢查與「上次執行時間」顯示用。"""
    __tablename__ = "scheduler_runs"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    job_id      = Column(String(50), index=True, nullable=False)
    last_run    = Column(String(64), index=True, nullable=False)  # ISO8601
    success     = Column(Boolean, default=True)
    error_msg   = Column(Text)
    duration_ms = Column(Integer, default=0)


class TopicHeatSnapshot(Base):
    """主題熱度歷史快照 — 一天一列 × topic 數，供時間軸與 7 日趨勢。"""
    __tablename__ = "topic_heat_snapshot"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    topic_id      = Column(Integer, index=True, nullable=False)
    date          = Column(String(16), index=True, nullable=False)  # YYYY-MM-DD
    heat_index    = Column(Float, default=0)
    news_count    = Column(Integer, default=0)
    ai_score_avg  = Column(Float, default=0)
    category      = Column(String(32))  # snapshot 時的分類（便於 radar 聚合）
    created_at    = Column(String(64))


def init_db() -> None:
    """建立所有表格，並對既有 DB 補欄位（ALTER TABLE migration）。"""
    Base.metadata.create_all(engine)
    _migrate_columns()


def _migrate_columns() -> None:
    """對既有 DB 補新欄位（idempotent）。SQLite 用 PRAGMA，Postgres 用 information_schema。"""
    migrations = {
        "pipeline_status": [
            ("error_msg", "TEXT"),
            ("progress_detail", "TEXT"),
        ],
        "episodes": [
            ("slug", "VARCHAR(200)"),
            ("audio_path", "TEXT"),
            ("images_dir", "TEXT"),
            ("thumbnail_path", "TEXT"),
            ("drive_folder_id", "VARCHAR(128)"),
            ("drive_manifest", "TEXT"),
            ("status", "VARCHAR(32)"),
            # Phase A
            ("topic_id", "INTEGER"),
            ("source_news_ids", "TEXT"),
        ],
        # Phase A 新增
        "news_items": [
            ("region", "VARCHAR(16)"),
            ("category", "VARCHAR(32)"),
            ("topic_id", "INTEGER"),
            ("classified_at", "VARCHAR(64)"),
            # AI 戰情室
            ("is_ai", "INTEGER"),
            ("ai_company", "VARCHAR(32)"),
            ("model_release", "INTEGER"),
            ("title_zh", "TEXT"),
            ("translated_at", "VARCHAR(64)"),
            ("summary_zh", "TEXT"),
            ("summary_translated_at", "VARCHAR(64)"),
        ],
        "episode_status": [
            ("selected_topic_id", "INTEGER"),
        ],
        "script_records": [
            ("topic_id", "INTEGER"),
            ("source_news_ids", "TEXT"),
        ],
        "topics": [
            ("heat_index", "FLOAT"),
            ("heat_prev", "FLOAT"),
            ("heat_updated_at", "VARCHAR(64)"),
            ("summary_zh", "TEXT"),
            ("summary_generated_at", "VARCHAR(64)"),
        ],
    }
    from sqlalchemy import text as _sql
    with engine.connect() as conn:
        for table, cols in migrations.items():
            try:
                if _is_pg:
                    rows = conn.execute(_sql(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = :t"
                    ), {"t": table}).fetchall()
                    existing = [r[0] for r in rows]
                else:
                    existing = [row[1] for row in conn.execute(
                        _sql(f"PRAGMA table_info({table})")
                    )]
            except Exception:
                continue
            for col_name, col_type in cols:
                if col_name not in existing:
                    try:
                        conn.execute(_sql(
                            f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"
                        ))
                        conn.commit()
                    except Exception:
                        pass
