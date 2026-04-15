"""AI 頻道 Web UI — Flask app（Railway 部署 / 本地）。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, jsonify, redirect, render_template, request, url_for

from modules.brief.brief_generator import generate, load_today
from modules.common.logging_setup import setup_logger
from modules.database import db_manager
from modules.database.models import init_db

setup_logger()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "ai-channel-webui-2026")


def _stats():
    try:
        return db_manager.stats_today()
    except Exception:
        return {}


def _latest_report() -> str | None:
    from pathlib import Path
    rdir = PROJECT_ROOT / "data" / "reports"
    if not rdir.exists():
        return None
    reports = sorted(rdir.glob("*_weekly.md"), reverse=True)
    return reports[0].read_text(encoding="utf-8") if reports else None


# ─────────── Pages ───────────

@app.route("/")
def index():
    brief = load_today() or generate()
    return render_template("index.html",
                           brief=brief,
                           status=db_manager.get_pipeline_status(),
                           stats=_stats(),
                           active="index")


@app.route("/refresh")
def refresh():
    generate()
    return redirect(url_for("index"))


@app.route("/select", methods=["POST"])
def select_topic():
    news_id = int(request.form["news_id"])
    angle   = request.form.get("angle", "A")
    note    = request.form.get("custom_note", "")
    db_manager.mark_selected(news_id)
    db_manager.set_pipeline_status("selected",
                                   selected_id=news_id,
                                   selected_angle=angle,
                                   custom_note=note)
    return redirect(url_for("status_page"))


@app.route("/status")
def status_page():
    status   = db_manager.get_pipeline_status()
    selected = (db_manager.get_news_by_id(status.get("selected_id"))
                if status.get("selected_id") else None)
    return render_template("status.html",
                           status=status,
                           selected=selected,
                           stats=_stats(),
                           active="status")


@app.route("/script")
def script_review():
    rec = db_manager.load_latest_script()
    script = rec["script"] if rec else None
    return render_template("script_review.html",
                           script=script,
                           stats=_stats(),
                           active="script")


@app.route("/script/approve", methods=["POST"])
def script_approve():
    rec = db_manager.load_latest_script()
    if rec:
        db_manager.approve_script(rec["id"])
    db_manager.set_pipeline_status("tts")
    return redirect(url_for("status_page"))


@app.route("/report")
def report_page():
    return render_template("report.html",
                           report_md=_latest_report(),
                           stats=_stats(),
                           active="report")


@app.route("/setup")
def setup_page():
    has_yt    = (PROJECT_ROOT / "config" / "youtube_client_secret.json").exists()
    has_token = (PROJECT_ROOT / "config" / "youtube_token.json").exists()
    env_vars  = {}
    env_file  = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                env_vars[k.strip()] = "已設定" if v.strip() else "未設定"
    return render_template("setup.html",
                           has_yt=has_yt,
                           has_token=has_token,
                           env_vars=env_vars,
                           stats=_stats(),
                           active="setup")


# ─────────── API ───────────

@app.route("/api/status")
def api_status():
    return jsonify(db_manager.get_pipeline_status())


@app.route("/api/brief")
def api_brief():
    return jsonify(load_today() or generate())


@app.route("/api/stats")
def api_stats():
    return jsonify(_stats())


@app.route("/api/pipeline/stage", methods=["POST"])
def api_set_stage():
    data  = request.get_json(silent=True) or {}
    stage = data.get("stage")
    if not stage:
        return jsonify({"error": "missing stage"}), 400
    db_manager.set_pipeline_status(stage,
                                   **{k: v for k, v in data.items() if k != "stage"})
    return jsonify({"ok": True, "stage": stage})


if __name__ == "__main__":
    init_db()
    port = int(os.getenv("PORT", 5000))
    print(f"啟動：http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
