"""SQLAlchemy ORM 模型 — 同時支援 SQLite（本地）與 PostgreSQL（Railway/Neon）。"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, Float, Integer, String, Text, create_engine, event
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


class Episode(Base):
    __tablename__ = "episodes"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    date            = Column(String(16), nullable=False)
    news_item_id    = Column(Integer)
    script_path     = Column(Text)
    video_path      = Column(Text)
    youtube_id      = Column(String(64))
    title           = Column(Text)
    published_at    = Column(String(64))
    views_24h       = Column(Integer)
    views_7d        = Column(Integer)
    ctr             = Column(Float)
    avg_watch_pct   = Column(Float)
    notes           = Column(Text)


class TopicHistory(Base):
    __tablename__ = "topic_history"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    topic_keyword = Column(String(256))
    company       = Column(String(128))
    used_date     = Column(String(16))
    episode_id    = Column(Integer)


class PipelineStatus(Base):
    """流水線狀態（供 Web UI 讀寫）。"""
    __tablename__ = "pipeline_status"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    date           = Column(String(16), nullable=False, unique=True)
    stage          = Column(String(32), default="idle")
    selected_id    = Column(Integer)
    selected_angle = Column(String(4))
    custom_note    = Column(Text)
    updated_at     = Column(String(64))
    error_msg      = Column(Text)   # 最後一次錯誤訊息


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

    id            = Column(Integer, primary_key=True, autoincrement=True)
    date          = Column(String(16), nullable=False)
    news_item_id  = Column(Integer)
    research_json = Column(Text)
    script_json   = Column(Text, nullable=False)
    status        = Column(String(32), default="draft")  # draft|approved|rejected
    created_at    = Column(String(64))
    approved_at   = Column(String(64))


def init_db() -> None:
    """建立所有表格，並對既有 DB 補欄位（ALTER TABLE migration）。"""
    Base.metadata.create_all(engine)
    _migrate_columns()


def _migrate_columns() -> None:
    """對既有 SQLite DB 補新欄位（idempotent — 欄位已存在則跳過）。"""
    migrations = {
        "pipeline_status": [
            ("error_msg", "TEXT"),
        ],
    }
    with engine.connect() as conn:
        for table, cols in migrations.items():
            try:
                existing = [row[1] for row in conn.execute(
                    __import__("sqlalchemy").text(f"PRAGMA table_info({table})")
                )]
            except Exception:
                continue
            for col_name, col_type in cols:
                if col_name not in existing:
                    try:
                        conn.execute(__import__("sqlalchemy").text(
                            f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"
                        ))
                        conn.commit()
                    except Exception:
                        pass
