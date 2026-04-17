"""AI 頻道 Web UI — Flask app（Railway 部署 / 本地）。"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from datetime import timedelta
from flask import Flask, jsonify, redirect, render_template, request, url_for

from modules.brief.brief_generator import generate, load_today
from modules.common.logging_setup import setup_logger
from modules.database import db_manager
from modules.database.models import init_db
from web_ui.auth import login_required, register_auth_routes

setup_logger()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "ai-channel-webui-2026")
app.permanent_session_lifetime = timedelta(days=30)

register_auth_routes(app)


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
@login_required
def index():
    date = request.args.get("date")   # None = 今日, "" = 全部, "YYYY-MM-DD" = 指定日期
    if date is None:
        brief = load_today() or generate()
    else:
        brief = generate(fetched_date=date)
    return render_template("index.html",
                           brief=brief,
                           status=db_manager.get_pipeline_status(),
                           stats=_stats(),
                           active="index")


@app.route("/refresh")
@login_required
def refresh():
    date = request.args.get("date")
    generate(fetched_date=date)
    redirect_url = url_for("index") + (f"?date={date}" if date is not None else "")
    return redirect(redirect_url)


@app.route("/news-dates")
@login_required
def news_dates():
    summary = db_manager.get_fetch_date_summary()
    return render_template("news_dates.html",
                           summary=summary,
                           stats=_stats(),
                           active="news_dates")


@app.route("/clear-date", methods=["POST"])
@login_required
def clear_date():
    date = request.form.get("date", "").strip()
    if date:
        db_manager.delete_news_by_date(date)
    return redirect(url_for("news_dates"))


@app.route("/select", methods=["POST"])
@login_required
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
@login_required
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
@login_required
def script_review():
    rec = db_manager.load_latest_script()
    script = rec["script"] if rec else None
    return render_template("script_review.html",
                           script=script,
                           stats=_stats(),
                           active="script")


@app.route("/script/approve", methods=["POST"])
@login_required
def script_approve():
    rec = db_manager.load_latest_script()
    if rec:
        db_manager.approve_script(rec["id"])
    db_manager.set_pipeline_status("tts", error_msg=None)
    return redirect(url_for("status_page"))


@app.route("/script/regenerate", methods=["POST"])
@login_required
def script_regenerate():
    """重置 pipeline 到 scripting，讓 watcher 知道需要重新生成。"""
    db_manager.set_pipeline_status("scripting", error_msg=None)
    return redirect(url_for("script_review"))


@app.route("/report")
@login_required
def report_page():
    return render_template("report.html",
                           report_md=_latest_report(),
                           stats=_stats(),
                           active="report")


@app.route("/setup")
@login_required
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


# ─────────── CoWork 研究 / 腳本 ───────────

@app.route("/cowork/research")
@login_required
def cowork_research():
    """顯示研究 Prompt，讓使用者複製給 Claude Code。"""
    prompt = None
    # 找最新的 research_prompt.md
    prompts = sorted(
        (PROJECT_ROOT / "data" / "scripts").glob("*/research_prompt.md"), reverse=True
    )
    if prompts:
        prompt = prompts[0].read_text(encoding="utf-8")
    return render_template("cowork_research.html",
                           prompt=prompt,
                           stats=_stats(),
                           active="status")


@app.route("/api/cowork/research", methods=["POST"])
@login_required
def api_cowork_research():
    """接收研究結果，存成 research.json，生成腳本 Prompt，推進 pipeline。"""
    data = request.get_json(silent=True) or {}
    research_text = (data.get("research_text") or "").strip()
    if not research_text:
        return jsonify({"error": "research_text 不能為空"}), 400

    status = db_manager.get_pipeline_status()
    news_id = status.get("selected_id")
    if not news_id:
        return jsonify({"error": "找不到已選題的 news_id，請先選題"}), 400

    try:
        from modules.script.researcher import save_research
        from modules.script.script_writer import export_prompt
        research_path = save_research(news_id, research_text)
        export_prompt(research_path)  # 同時生成 script_prompt.md
        db_manager.set_pipeline_status("scripting",
                                       date=status.get("date"), error_msg=None)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 500


@app.route("/cowork/script")
@login_required
def cowork_script():
    """顯示腳本生成 Prompt，讓使用者複製給 Claude Code。"""
    prompt = None
    prompts = sorted(
        (PROJECT_ROOT / "data" / "scripts").glob("*/script_prompt.md"), reverse=True
    )
    if prompts:
        prompt = prompts[0].read_text(encoding="utf-8")
    return render_template("cowork_script.html",
                           prompt=prompt,
                           stats=_stats(),
                           active="status")


@app.route("/api/cowork/script", methods=["POST"])
@login_required
def api_cowork_script():
    """接收腳本 JSON，存檔並推進 pipeline 到 script_ready。"""
    data = request.get_json(silent=True) or {}
    script_json = (data.get("script_json") or "").strip()
    if not script_json:
        return jsonify({"error": "script_json 不能為空"}), 400

    status  = db_manager.get_pipeline_status()
    news_id = status.get("selected_id")

    try:
        from modules.script.script_writer import save_script
        # 找最新的研究資料夾
        research_files = sorted(
            (PROJECT_ROOT / "data" / "scripts").glob("*/research.json"), reverse=True
        )
        if not research_files:
            return jsonify({"error": "找不到 research.json，請先完成研究步驟"}), 400
        out_dir = research_files[0].parent
        save_script(script_json, out_dir, news_id)
        db_manager.set_pipeline_status("script_ready",
                                       date=status.get("date"), error_msg=None)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 500


# ─────────── 評分 Web 入口 ───────────

@app.route("/scoring")
@login_required
def scoring_page():
    """顯示待評分新聞，並提供匯入評分結果的介面。"""
    pending = db_manager.fetch_news_to_score(limit=50)
    return render_template("scoring.html",
                           pending=pending,
                           stats=_stats(),
                           active="scoring")


@app.route("/api/scoring/export")
@login_required
def api_scoring_export():
    """匯出待評分新聞 JSON（供貼給 Claude Code）。"""
    pending = db_manager.fetch_news_to_score(limit=50)
    items = [
        {
            "id": r["id"],
            "title": r["title"],
            "source_name": r["source_name"],
            "source_priority": r["source_priority"],
            "published_at": r.get("published_at"),
            "summary": (r.get("summary") or "")[:400],
        }
        for r in pending
    ]
    return jsonify({"count": len(items), "items": items})


@app.route("/api/scoring/import", methods=["POST"])
@login_required
def api_scoring_import():
    """接收評分結果 JSON 並寫入 DB。"""
    data = request.get_json(silent=True)
    if not data or "results" not in data:
        return jsonify({"error": "需要 {results: [...]} 格式"}), 400

    from modules.common.config import settings
    ai_min = settings()["filter"]["ai_score_min"]
    updates = []
    for r in data["results"]:
        try:
            score = float(r.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        updates.append({
            "id": r["id"],
            "ai_score": score,
            "business_angle": r.get("business_angle"),
            "why_audience_cares": r.get("why_audience_cares"),
            "suggested_title": r.get("suggested_title"),
            "skip_reason": r.get("skip_reason"),
            "status": "candidate" if score >= ai_min else "skipped",
        })

    db_manager.update_ai_scores(updates)
    candidates = sum(1 for u in updates if u["status"] == "candidate")
    return jsonify({"ok": True, "scored": len(updates), "candidates": candidates})


# ─────────── 自動模式 API ───────────

_auto_thread: threading.Thread | None = None


@app.route("/api/auto-start", methods=["POST"])
@login_required
def api_auto_start():
    """在背景執行 auto_research + auto_write_script，不需要啟動 watcher.py。"""
    global _auto_thread
    if _auto_thread and _auto_thread.is_alive():
        return jsonify({"error": "自動模式已在執行中，請等待"}), 400

    status  = db_manager.get_pipeline_status()
    news_id = status.get("selected_id")
    date    = status.get("date")
    if not news_id:
        return jsonify({"error": "尚未選題，請先在今日選題頁選定主題"}), 400

    def _run():
        try:
            from modules.script.researcher import auto_research
            from modules.script.script_writer import auto_write_script
            db_manager.set_pipeline_status("researching", date=date, error_msg=None)
            research_path = auto_research(news_id)
            db_manager.set_pipeline_status("scripting", date=date, error_msg=None)
            auto_write_script(research_path)
            db_manager.set_pipeline_status("script_ready", date=date, error_msg=None)
        except Exception as e:
            try:
                from modules.script.researcher import export_prompt
                export_prompt(news_id)
            except Exception:
                pass
            db_manager.set_pipeline_status(
                "researching", date=date,
                error_msg=f"自動模式失敗：{e}，請至 CoWork 頁面手動操作",
            )

    _auto_thread = threading.Thread(target=_run, daemon=True, name="auto-pipeline")
    _auto_thread.start()
    return jsonify({"ok": True})


# ─────────── 連線測試 API ───────────

@app.route("/api/test/db")
@login_required
def api_test_db():
    try:
        stats = db_manager.stats_today()
        return jsonify({"ok": True, "news_total": stats.get("total", 0)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/test/anthropic")
@login_required
def api_test_anthropic():
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({"ok": False, "error": "ANTHROPIC_API_KEY 未設定"}), 400
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": "hi"}],
        )
        return jsonify({"ok": True, "model": resp.model})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@app.route("/api/test/youtube")
@login_required
def api_test_youtube():
    secret = PROJECT_ROOT / "config" / "youtube_client_secret.json"
    token  = PROJECT_ROOT / "config" / "youtube_token.json"
    if not secret.exists():
        return jsonify({"ok": False, "error": "client_secret.json 不存在"})
    if not token.exists():
        return jsonify({"ok": False, "error": "尚未完成 OAuth2 授權"})
    try:
        from modules.publish.youtube_uploader import _build_youtube
        youtube = _build_youtube()
        youtube.channels().list(part="id", mine=True).execute()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]})


# ─────────── SSE 即時狀態 ───────────

@app.route("/api/sse/status")
@login_required
def sse_status():
    """Server-Sent Events — 推送 pipeline 狀態給瀏覽器。"""
    import time

    def generate():
        last = None
        for _ in range(120):   # 最多推 120 次（2 分鐘），讓客端重連
            try:
                status = db_manager.get_pipeline_status()
                payload = {
                    "stage": status.get("stage"),
                    "updated_at": status.get("updated_at"),
                    "error_msg": status.get("error_msg"),
                }
                import json as _json
                data = _json.dumps(payload)
                if data != last:
                    last = data
                    yield f"data: {data}\n\n"
            except Exception:
                yield "data: {}\n\n"
            time.sleep(5)

    from flask import Response
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


# ─────────── 日誌查看 ───────────

@app.route("/logs")
@login_required
def logs_page():
    log_dir = PROJECT_ROOT / "logs"
    today = __import__("modules.common.utils", fromlist=["tw_today"]).tw_today()
    log_file = log_dir / f"{today.replace('-','')}.log"
    lines = []
    if log_file.exists():
        all_lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        lines = all_lines[-200:]   # 最新 200 行
    return render_template("logs.html",
                           lines=lines,
                           log_file=str(log_file),
                           stats=_stats(),
                           active="logs")


# ─────────── API ───────────

@app.route("/api/status")
@login_required
def api_status():
    return jsonify(db_manager.get_pipeline_status())


@app.route("/api/brief")
@login_required
def api_brief():
    return jsonify(load_today() or generate())


@app.route("/api/stats")
@login_required
def api_stats():
    return jsonify(_stats())


@app.route("/api/pipeline/retry", methods=["POST"])
@login_required
def api_pipeline_retry():
    """重試：把 stage 重置回 selected，讓 watcher 重新執行自動流程。"""
    status = db_manager.get_pipeline_status()
    db_manager.set_pipeline_status("selected",
                                   date=status.get("date"),
                                   selected_id=status.get("selected_id"),
                                   selected_angle=status.get("selected_angle"),
                                   custom_note=status.get("custom_note"),
                                   error_msg=None)
    return jsonify({"ok": True})


@app.route("/api/pipeline/stage", methods=["POST"])
@login_required
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
