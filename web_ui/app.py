"""AI 頻道選題 Web UI — 可部署到 Railway。

本地啟動：
  python web_ui/app.py

Railway 部署：
  已設定 Procfile，push 後自動啟動。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import json

from flask import Flask, jsonify, redirect, render_template, request, url_for

from modules.brief.brief_generator import generate, load_today
from modules.common.logging_setup import setup_logger
from modules.database import db_manager
from modules.database.models import init_db

SCRIPTS_DIR = PROJECT_ROOT / "data" / "scripts"

setup_logger()
app = Flask(__name__)
app.secret_key = "ai-channel-webui-2026"


@app.before_request
def _ensure_db():
    pass  # DB 在 main 已 init


# ─────────── Pages ───────────

@app.route("/")
def index():
    brief = load_today()
    if not brief:
        brief = generate()
    status = db_manager.get_pipeline_status()
    stats = db_manager.stats_today()
    return render_template("index.html", brief=brief, status=status, stats=stats)


@app.route("/refresh")
def refresh():
    generate()
    return redirect(url_for("index"))


@app.route("/select", methods=["POST"])
def select_topic():
    news_id   = int(request.form["news_id"])
    angle     = request.form.get("angle", "A")
    custom_note = request.form.get("custom_note", "")

    db_manager.mark_selected(news_id)
    db_manager.set_pipeline_status(
        "selected",
        selected_id=news_id,
        selected_angle=angle,
        custom_note=custom_note,
    )
    return redirect(url_for("status_page"))


@app.route("/status")
def status_page():
    status   = db_manager.get_pipeline_status()
    selected = db_manager.get_news_by_id(status.get("selected_id")) if status.get("selected_id") else None
    return render_template("status.html", status=status, selected=selected)


# ─────────── 腳本審閱 ───────────

def _load_latest_script() -> dict | None:
    """讀取最新的 script.json（按資料夾名稱排序）。"""
    scripts = sorted(SCRIPTS_DIR.glob("*/script.json"), reverse=True)
    if not scripts:
        return None
    try:
        return json.loads(scripts[0].read_text(encoding="utf-8"))
    except Exception:
        return None


@app.route("/script")
def script_review():
    script = _load_latest_script()
    return render_template("script_review.html", script=script)


@app.route("/script/approve", methods=["POST"])
def script_approve():
    db_manager.set_pipeline_status("tts")
    return redirect(url_for("status_page"))


# ─────────── API ───────────

@app.route("/api/status")
def api_status():
    return jsonify(db_manager.get_pipeline_status())


@app.route("/api/brief")
def api_brief():
    return jsonify(load_today() or generate())


@app.route("/api/stats")
def api_stats():
    return jsonify(db_manager.stats_today())


if __name__ == "__main__":
    init_db()
    port = int(__import__("os").getenv("PORT", 5000))
    print(f"啟動選題介面：http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
