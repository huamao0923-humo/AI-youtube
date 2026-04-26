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
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for

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


@app.context_processor
def inject_globals():
    """全域注入：CoWork 錯誤提示 + 登入使用者。"""
    cowork_alert = False
    cowork_alert_msg = ""
    try:
        for row in db_manager.list_episode_statuses() or []:
            err = (row.get("error_msg") or "").strip()
            stage = row.get("stage") or ""
            if err and stage not in ("done", "cancelled"):
                cowork_alert = True
                slug = row.get("slug") or ""
                cowork_alert_msg = f"{slug}：{err[:40]}"
                break
    except Exception:
        pass
    return {
        "cowork_alert": cowork_alert,
        "cowork_alert_msg": cowork_alert_msg,
        "user_email": "已登入" if session.get("authenticated") else None,
    }


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
def dashboard():
    """專案儀表板 — 今日 KPI + pipeline board + 熱門主題。"""
    ds = db_manager.dashboard_stats()
    topics = db_manager.list_topics(
        status="open", sort="aggregate_score", limit=12,
    )
    active_rows = db_manager.list_episode_statuses()
    return render_template(
        "dashboard.html",
        ds=ds,
        topics=topics,
        episode_statuses=active_rows,
        stats=_merged_stats(ds),
        active="dashboard",
    )


@app.route("/today")
@login_required
def index():
    """今日候選新聞（舊首頁）。"""
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


@app.route("/topics")
@login_required
def topics_page():
    """主題瀏覽 — filter by category / region / status / date。"""
    category = request.args.get("category") or None
    region = request.args.get("region") or None
    status = request.args.get("status") or "open"
    sort = request.args.get("sort", "aggregate_score")
    date = request.args.get("date") or None

    topics = db_manager.list_topics(
        date=date, category=category, region=region,
        status=status, sort=sort, limit=80,
    )

    # 蒐集 category chip 統計（用於 filter UI）
    from collections import Counter
    all_open = db_manager.list_topics(limit=200)
    cat_counts = Counter(t.get("category") or "other" for t in all_open)

    return render_template(
        "topics.html",
        topics=topics, cat_counts=dict(cat_counts),
        filter_category=category, filter_region=region,
        filter_status=status, filter_sort=sort, filter_date=date,
        stats=_stats(),
        active="topics",
    )


@app.route("/topic/<slug>")
@login_required
def topic_detail(slug: str):
    """單一主題詳情 — 顯示成員新聞，能推進到選題。"""
    topic = db_manager.get_topic_by_slug(slug)
    if not topic:
        flash(f"找不到主題：{slug}", "error")
        return redirect(url_for("topics_page"))
    news_rows = db_manager.list_news_by_topic(topic["id"])
    # 查是否有對應 Episode
    ep = db_manager.get_episode_by_slug(slug)
    return render_template(
        "topic_detail.html",
        topic=topic, news_rows=news_rows, episode=ep,
        stats=_stats(),
        active="topics",
    )


def _merged_stats(ds: dict) -> dict:
    """把 dashboard_stats 壓成 base.html sidebar 期望的 stats 格式。"""
    basic = _stats() or {}
    basic["open_topics"] = ds.get("open_topics", 0)
    return basic


@app.route("/refresh")
@login_required
def refresh():
    date = request.args.get("date")
    try:
        generate(fetched_date=date)
        flash(f"已重新爬蟲" + (f"（{date}）" if date else ""), "success")
    except Exception as e:
        flash(f"爬蟲失敗：{e}", "error")
    redirect_url = url_for("index") + (f"?date={date}" if date is not None else "")
    return redirect(redirect_url)


@app.route("/news")
@login_required
def news_workbench():
    """新聞工作台 — 從爬蟲到選題的一站式入口。"""
    ds = db_manager.dashboard_stats()
    summary = db_manager.get_fetch_date_summary() or []
    # 最近爬蟲日期與待評分統計
    total_days = len(summary)
    latest_fetch_date = summary[0]["date"] if summary else None
    # 候選新聞（有分數且 >=6）
    candidates_count = ds.get("candidates", 0)
    # 近 7 日未分類
    return render_template("news.html",
                           ds=ds,
                           total_days=total_days,
                           latest_fetch_date=latest_fetch_date,
                           candidates_count=candidates_count,
                           stats=_stats(),
                           active="news")


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
    if not date:
        flash("未指定日期", "warn")
        return redirect(url_for("news_dates"))
    try:
        n = db_manager.delete_news_by_date(date)
        # delete 可能回傳刪除筆數或 None；做寬鬆處理
        if isinstance(n, int):
            flash(f"已刪除 {date} 的 {n} 筆新聞", "success")
        else:
            flash(f"已刪除 {date} 的新聞", "success")
    except Exception as e:
        flash(f"刪除失敗：{e}", "error")
    return redirect(url_for("news_dates"))


@app.route("/select-topic", methods=["POST"])
@login_required
def select_topic_from_topic():
    """從 Topic 開集：把整個 topic 下的 news 綁進 EpisodeStatus + Episode。"""
    import json
    topic_id = int(request.form["topic_id"])
    topic = db_manager.get_topic(topic_id)
    if not topic:
        flash("找不到該主題", "error")
        return redirect(url_for("dashboard"))

    news_rows = db_manager.list_news_by_topic(topic_id)
    if not news_rows:
        flash("此主題沒有成員新聞", "error")
        return redirect(url_for("topic_detail", slug=topic["slug"]))

    news_ids = [r["id"] for r in news_rows]
    primary_id = news_ids[0]
    slug = topic["slug"]

    from modules.common.utils import tw_today
    date = tw_today()

    # 主要新聞標記 selected
    db_manager.mark_selected(primary_id)

    db_manager.set_episode_status(
        slug=slug, stage="selected", date=date,
        selected_id=primary_id,
        selected_topic_id=topic_id,
        selected_angle=request.form.get("angle", "A"),
        custom_note=request.form.get("custom_note", ""),
        error_msg=None,
    )
    db_manager.upsert_episode(
        slug=slug, date=date,
        title=topic["title"],
        news_item_id=primary_id,
        topic_id=topic_id,
        source_news_ids=json.dumps(news_ids, ensure_ascii=False),
        status="draft",
    )
    # 狀態更新：Topic 標 used
    db_manager.update_topic(topic_id, status="used")

    # AI 戰情室：同步標記已用
    try:
        from modules.ai_war_room.used_marks import mark_news_used, mark_topic_used
        mark_topic_used(topic_id, slug)
        for nid in news_ids:
            mark_news_used(nid, slug)
    except Exception:
        pass

    flash(f"已開集：{topic['title']}", "success")
    return redirect(url_for("episode_page", slug=slug))


@app.route("/select", methods=["POST"])
@login_required
def select_topic():
    news_id = int(request.form["news_id"])
    angle   = request.form.get("angle", "A")
    note    = request.form.get("custom_note", "")

    # 計算 slug（與 researcher._out_dir 一致）
    from modules.common.utils import build_slug, tw_today
    news = db_manager.get_news_by_id(news_id) or {}
    title = news.get("suggested_title") or news.get("title") or f"news_{news_id}"
    slug = build_slug(title)
    date = tw_today()

    db_manager.mark_selected(news_id)

    # 新架構：EpisodeStatus（slug-based）
    db_manager.set_episode_status(slug=slug, stage="selected", date=date,
                                  selected_id=news_id, selected_angle=angle,
                                  custom_note=note, error_msg=None)
    # Episode 初始記錄（title + date）
    db_manager.upsert_episode(slug=slug, date=date,
                              title=title, news_item_id=news_id,
                              status="draft")

    # Legacy 相容：也寫 PipelineStatus（舊 /status 頁用）
    db_manager.set_pipeline_status("selected", date=date,
                                   selected_id=news_id,
                                   selected_angle=angle,
                                   custom_note=note)

    # AI 戰情室：同步標記已用
    try:
        from modules.ai_war_room.used_marks import mark_news_used
        mark_news_used(news_id, slug)
    except Exception:
        pass

    return redirect(url_for("episode_page", slug=slug))


@app.route("/status")
@login_required
def status_page():
    """相容舊路由：若有進行中集數則 redirect 到該集管理頁。
    無 active 時顯示舊版 status.html 作為引導。"""
    active = db_manager.get_active_episode()
    if active:
        return redirect(url_for("episode_page", slug=active["slug"]))

    # 無進行中集數 — 顯示引導
    status   = db_manager.get_pipeline_status()
    selected = (db_manager.get_news_by_id(status.get("selected_id"))
                if status.get("selected_id") else None)
    return render_template("status.html",
                           status=status,
                           selected=selected,
                           stats=_stats(),
                           active="status")


def _list_script_slugs():
    """列出所有有 script.json 的 slug，按 mtime 倒序。"""
    scripts = sorted(
        (PROJECT_ROOT / "data" / "scripts").glob("*/script.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [p.parent.name for p in scripts]


def _load_script_for_slug(slug: str):
    """讀取指定 slug 的 script.json（檔案為準）。"""
    p = PROJECT_ROOT / "data" / "scripts" / slug / "script.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


@app.route("/script")
@login_required
def script_review():
    slug = request.args.get("slug")
    all_slugs = _list_script_slugs()
    script = None
    active_slug = None
    if slug and slug in all_slugs:
        script = _load_script_for_slug(slug)
        active_slug = slug
    elif all_slugs:
        active_slug = all_slugs[0]
        script = _load_script_for_slug(active_slug)
    else:
        # 降級：沒有 script.json 檔案就用 DB 的最新 script
        rec = db_manager.load_latest_script()
        if rec:
            script = rec["script"]
    return render_template("script_review.html",
                           script=script,
                           all_slugs=all_slugs,
                           active_slug=active_slug,
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


def _latest_script_path(slug: str | None = None):
    """找 script.json 絕對路徑。指定 slug 時取該集的；否則取最新的。"""
    if slug:
        p = PROJECT_ROOT / "data" / "scripts" / slug / "script.json"
        return p if p.exists() else None
    scripts = sorted(
        (PROJECT_ROOT / "data" / "scripts").glob("*/script.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return scripts[0] if scripts else None


@app.route("/api/script/section/<int:section_id>", methods=["POST"])
@login_required
def api_script_update_section(section_id: int):
    """單段就地編輯 — 更新該段 narration。"""
    data = request.get_json(silent=True) or {}
    narration = (data.get("narration") or "").strip()
    slug = data.get("slug")
    if not narration:
        return jsonify({"error": "narration 不可為空"}), 400
    path = _latest_script_path(slug)
    if not path:
        return jsonify({"error": "找不到 script.json"}), 404
    try:
        from modules.script.reviewer import update_section
        ok = update_section(path, section_id, narration)
        if not ok:
            return jsonify({"error": f"找不到 section_id={section_id}"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 500


@app.route("/api/script/ai-review", methods=["POST"])
@login_required
def api_script_ai_review():
    """觸發 AI 審閱，回傳 diff 結構。"""
    data = request.get_json(silent=True) or {}
    section_ids = data.get("section_ids")
    slug = data.get("slug")
    if section_ids and not isinstance(section_ids, list):
        return jsonify({"error": "section_ids 必須是陣列"}), 400
    path = _latest_script_path(slug)
    if not path:
        return jsonify({"error": "找不到 script.json"}), 404
    try:
        from modules.script.reviewer import review_script
        result = review_script(path, section_ids=section_ids)
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"error": str(e)[:500]}), 500


@app.route("/api/script/apply-changes", methods=["POST"])
@login_required
def api_script_apply_changes():
    """套用使用者接受的變更到 script.json。"""
    data = request.get_json(silent=True) or {}
    accepted = data.get("accepted") or []
    slug = data.get("slug")
    if not isinstance(accepted, list) or not accepted:
        return jsonify({"error": "accepted 為空"}), 400
    path = _latest_script_path(slug)
    if not path:
        return jsonify({"error": "找不到 script.json"}), 404
    try:
        from modules.script.reviewer import apply_changes
        result = apply_changes(path, accepted)
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 500


@app.route("/api/script/meta", methods=["POST"])
@login_required
def api_script_update_meta():
    """更新腳本 meta：chosen_title、tags、youtube_description、thumbnail_concept。"""
    data = request.get_json(silent=True) or {}
    slug = data.get("slug")
    path = _latest_script_path(slug)
    if not path:
        return jsonify({"error": "找不到 script.json"}), 404
    try:
        from modules.script.reviewer import update_meta
        result = update_meta(path, data)
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 500


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

@app.route("/all-news")
@login_required
def all_news_page():
    """所有新聞頁面 — 按分類、去重、評分呈現。"""
    date = request.args.get("date")
    from modules.common.utils import tw_today
    if date is None:
        date = tw_today()
    items = db_manager.get_news_by_date(date)
    from modules.common.news_classifier import categorize_all
    grouped = categorize_all(items)
    dates = db_manager.get_fetch_date_summary()
    return render_template("all_news.html",
                           grouped=grouped, date=date, dates=dates,
                           stats=_stats(), active="all_news")


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


@app.route("/api/recommend-brief", methods=["POST"])
@login_required
def api_recommend_brief():
    """重新執行 brief_generator.generate() 推薦 10 則到今日嚴選。"""
    data = request.get_json(silent=True) or {}
    from modules.common.utils import tw_today
    date = data.get("date") or tw_today()
    try:
        generate(fetched_date=date)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 500


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
    """Server-Sent Events — 推送 pipeline 狀態給瀏覽器。

    支援 `?slug=` 參數：指定時推該集 EpisodeStatus，不指定時推 legacy PipelineStatus。
    """
    import time
    slug = request.args.get("slug")

    def generate():
        last = None
        for _ in range(120):   # 最多推 120 次（2 分鐘），讓客端重連
            try:
                if slug:
                    status = db_manager.get_episode_status(slug) or {}
                else:
                    status = db_manager.get_pipeline_status()
                payload = {
                    "stage": status.get("stage"),
                    "updated_at": status.get("updated_at"),
                    "error_msg": status.get("error_msg"),
                    "progress_detail": status.get("progress_detail"),
                }
                import json as _json
                data = _json.dumps(payload)
                if data != last:
                    last = data
                    yield f"data: {data}\n\n"
            except Exception:
                yield "data: {}\n\n"
            time.sleep(2)

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
    """重試：若在 tts/images/compositing/uploading 失敗，只清除 error_msg 原地重試；
    若在 researching/scripting 失敗，才重置回 selected 讓 watcher 重跑研究+腳本。"""
    status = db_manager.get_pipeline_status()
    stage = status.get("stage", "idle")
    # 這些 stage 可以原地重試（不需要從頭跑）
    retry_in_place = {"tts", "prefetch", "images", "compositing", "upload_ready", "uploading"}
    if stage in retry_in_place:
        db_manager.set_pipeline_status(stage,
                                       date=status.get("date"),
                                       error_msg=None)
    else:
        db_manager.set_pipeline_status("selected",
                                       date=status.get("date"),
                                       selected_id=status.get("selected_id"),
                                       selected_angle=status.get("selected_angle"),
                                       custom_note=status.get("custom_note"),
                                       error_msg=None)
    return jsonify({"ok": True, "stage": stage})


@app.route("/video/preview")
@login_required
def video_preview():
    """相容舊路由，redirect 到最新集數的影片。"""
    videos = sorted(
        (PROJECT_ROOT / "data" / "videos").glob("*/final.mp4"), reverse=True
    )
    if not videos:
        return "找不到影片", 404
    latest_slug = videos[0].parent.name
    return redirect(url_for("episode_video", slug=latest_slug))


# ─────────── Episode 管理（slug-based） ───────────

@app.route("/episodes")
@login_required
def episodes_page():
    """所有集數列表（歷史 + 進行中）。"""
    from modules.storage.local_storage import get_episode_paths

    date_filter = request.args.get("date") or None
    statuses = db_manager.list_episode_statuses(date=date_filter)
    episodes = {e["slug"]: e for e in db_manager.list_episodes(limit=100)}

    rows = []
    for st in statuses:
        slug = st["slug"]
        paths = get_episode_paths(slug)
        ep = episodes.get(slug, {})
        rows.append({
            "slug":        slug,
            "date":        st.get("date"),
            "title":       ep.get("title"),
            "stage":       st.get("stage"),
            "error_msg":   st.get("error_msg"),
            "updated_at":  st.get("updated_at"),
            "youtube_id":  ep.get("youtube_id"),
            "has_audio":   paths["audio_full"]["exists"],
            "n_images":    len(paths["section_images"]),
            "has_video":   paths["video"]["exists"],
        })

    return render_template("episodes.html",
                           rows=rows,
                           date_filter=date_filter,
                           stats=_stats(),
                           active="episodes")


@app.route("/episode/<path:slug>")
@login_required
def episode_page(slug):
    """單集管理頁。"""
    from modules.storage.local_storage import get_episode_paths

    status = db_manager.get_episode_status(slug)
    if not status:
        # DB 沒記錄但檔案可能存在 — 產生虛擬狀態
        from modules.common.utils import parse_date_from_slug
        paths = get_episode_paths(slug)
        if not any(paths[k]["exists"] for k in ("script", "audio_full", "video")):
            return f"找不到集數：{slug}", 404
        status = {
            "slug": slug,
            "date": parse_date_from_slug(slug),
            "stage": "idle",
            "error_msg": None,
            "progress_detail": None,
            "updated_at": None,
        }

    episode = db_manager.get_episode_by_slug(slug) or {}
    paths = get_episode_paths(slug)

    return render_template("episode_detail.html",
                           slug=slug,
                           status=status,
                           episode=episode,
                           paths=paths,
                           stats=_stats(),
                           active="episodes")


@app.route("/episode/<path:slug>/video")
@login_required
def episode_video(slug):
    """串流該集影片。"""
    from flask import send_file
    video_path = PROJECT_ROOT / "data" / "videos" / slug / "final.mp4"
    if not video_path.exists():
        return f"影片不存在：{slug}", 404
    return send_file(video_path, mimetype="video/mp4", conditional=True)


@app.route("/episode/<path:slug>/research.json")
@login_required
def episode_research_json(slug):
    p = PROJECT_ROOT / "data" / "scripts" / slug / "research.json"
    if not p.exists():
        return jsonify({"error": "not found"}), 404
    return jsonify(json.loads(p.read_text(encoding="utf-8")))


@app.route("/episode/<path:slug>/script.json")
@login_required
def episode_script_json(slug):
    p = PROJECT_ROOT / "data" / "scripts" / slug / "script.json"
    if not p.exists():
        return jsonify({"error": "not found"}), 404
    return jsonify(json.loads(p.read_text(encoding="utf-8")))


@app.route("/episode/<path:slug>/thumbnail")
@login_required
def episode_thumbnail(slug):
    """串流該集縮圖。"""
    from flask import send_file
    thumb = PROJECT_ROOT / "data" / "images" / slug / "thumbnail.png"
    if not thumb.exists():
        return "no thumbnail", 404
    return send_file(thumb, mimetype="image/png", conditional=True)


@app.route("/episode/<path:slug>/audio")
@login_required
def episode_audio(slug):
    """串流該集完整配音。"""
    from flask import send_file
    audio = PROJECT_ROOT / "data" / "audio" / slug / "audio_full.wav"
    if not audio.exists():
        return "no audio", 404
    return send_file(audio, mimetype="audio/wav", conditional=True)


@app.route("/episode/<path:slug>/retry-step", methods=["POST"])
@login_required
def episode_retry_step(slug):
    """單項重做：把 pipeline 拉回指定 stage。"""
    data = request.get_json(silent=True) or {}
    target = data.get("stage")
    valid = {"researching", "scripting", "tts", "prefetch", "images", "compositing", "uploading"}
    if target not in valid:
        return jsonify({"error": f"stage 不合法：{target}"}), 400
    # 舊 "images" alias 到新 "prefetch"
    if target == "images":
        target = "prefetch"
    try:
        db_manager.set_episode_status(slug=slug, stage=target, error_msg=None)
        return jsonify({"ok": True, "stage": target})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/episode/<path:slug>/image/<int:idx>")
@login_required
def episode_image(slug, idx):
    """串流該集第 idx 張場景圖。"""
    from flask import send_file
    from modules.storage.local_storage import get_episode_paths
    paths = get_episode_paths(slug)
    imgs = paths["section_images"]
    if idx < 0 or idx >= len(imgs):
        return "not found", 404
    return send_file(imgs[idx]["path"], mimetype="image/png", conditional=True)


@app.route("/episode/<path:slug>/broll-manifest")
@login_required
def episode_broll_manifest(slug):
    """回傳該集的 B-roll manifest（供腳本 Tab 預覽）。"""
    import json as _json
    manifest_path = PROJECT_ROOT / "data" / "broll_cache" / slug / "manifest.json"
    if not manifest_path.exists():
        return jsonify({"sections": {}, "available": False}), 200
    try:
        data = _json.loads(manifest_path.read_text(encoding="utf-8"))
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/episode/<path:slug>/broll/<int:section_id>/<int:idx>")
@login_required
def episode_broll_clip(slug, section_id, idx):
    """串流 manifest 內指定段落的 B-roll 本地 mp4 檔。"""
    import json as _json
    from flask import send_file
    manifest_path = PROJECT_ROOT / "data" / "broll_cache" / slug / "manifest.json"
    if not manifest_path.exists():
        return "manifest missing", 404
    try:
        data = _json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return "manifest parse error", 500
    items = (data.get("sections") or {}).get(str(section_id)) or []
    if idx < 0 or idx >= len(items):
        return "not found", 404
    local = items[idx].get("local_path") or ""
    if not local or not Path(local).exists():
        # 沒有本地檔，302 重導到 Pexels 原 URL（瀏覽器直接取）
        remote = items[idx].get("url") or ""
        if remote:
            return redirect(remote)
        return "clip not downloaded", 404
    return send_file(local, mimetype="video/mp4", conditional=True)


@app.route("/episode/<path:slug>/retry", methods=["POST"])
@login_required
def episode_retry(slug):
    """該集重試：清 error_msg，讓 watcher 原地重跑。"""
    st = db_manager.get_episode_status(slug)
    if not st:
        return jsonify({"error": f"找不到 {slug}"}), 404
    db_manager.set_episode_status(slug=slug, stage=st["stage"],
                                  date=st.get("date"), error_msg=None)
    return jsonify({"ok": True, "stage": st["stage"]})


@app.route("/episode/<path:slug>/upload-confirm", methods=["POST"])
@login_required
def episode_upload_confirm(slug):
    """使用者確認上傳：upload_ready → uploading。"""
    st = db_manager.get_episode_status(slug)
    if not st:
        return jsonify({"error": f"找不到 {slug}"}), 404
    if st["stage"] != "upload_ready":
        return jsonify({"error": f"目前 stage={st['stage']}，非 upload_ready"}), 400
    db_manager.set_episode_status(slug=slug, stage="uploading",
                                  date=st.get("date"), error_msg=None)
    return jsonify({"ok": True})


@app.route("/api/episodes")
@login_required
def api_episodes():
    statuses = db_manager.list_episode_statuses()
    return jsonify({"count": len(statuses), "episodes": statuses})


@app.route("/api/episode/<path:slug>")
@login_required
def api_episode(slug):
    st = db_manager.get_episode_status(slug) or {}
    ep = db_manager.get_episode_by_slug(slug) or {}
    from modules.storage.local_storage import get_episode_paths
    return jsonify({
        "slug": slug,
        "status": st,
        "episode": ep,
        "paths": get_episode_paths(slug),
    })


@app.route("/api/upload-confirm", methods=["POST"])
@login_required
def api_upload_confirm():
    """使用者手動確認上傳 YouTube，把 stage 推進到 uploading。"""
    status = db_manager.get_pipeline_status()
    if status.get("stage") != "upload_ready":
        return jsonify({"error": "目前非 upload_ready 狀態"}), 400
    db_manager.set_pipeline_status("uploading",
                                   date=status.get("date"),
                                   error_msg=None)
    return jsonify({"ok": True})


@app.route("/api/pipeline/stage", methods=["POST"])
@login_required
def api_set_stage():
    """手動設定 pipeline stage（除錯 / CoWork 用）。"""
    data = request.get_json(silent=True) or {}
    stage = data.get("stage")
    if not stage:
        return jsonify({"error": "missing stage"}), 400
    extra = {k: v for k, v in data.items() if k != "stage"}
    db_manager.set_pipeline_status(stage, **extra)
    return jsonify({"ok": True, "stage": stage})


# ─── watcher 背景執行緒 ────────────────────────────────────────
_watcher_started = False


def start_watcher_thread() -> None:
    """把 watcher loop 丟到背景 daemon thread，Web UI 同 process 代管。

    支援兩種啟動方式：
      - python web_ui/app.py          → __main__ 區塊呼叫一次
      - gunicorn web_ui.app:app       → 模組載入時呼叫一次

    `_watcher_started` 保護防止同一 process 重複啟動。
    WATCHER_ENABLED=0 可關閉（多 worker 或獨立 watcher process 情境）。
    """
    global _watcher_started
    import threading

    if _watcher_started:
        return
    if os.getenv("WATCHER_ENABLED", "1") == "0":
        print("[Watcher] WATCHER_ENABLED=0，略過背景 watcher")
        _watcher_started = True
        return

    import watcher
    t = threading.Thread(target=watcher.run_loop, daemon=True, name="pipeline-watcher")
    t.start()
    _watcher_started = True
    print(f"[Watcher] 已在背景 thread 啟動（pid={os.getpid()}）")


# ─────────── worldmonitor 風格儀表板 API ───────────

_heat_api_cache: dict = {}
_HEAT_API_TTL = 300  # 秒


def _cached(key: str, producer):
    import time
    now = time.time()
    entry = _heat_api_cache.get(key)
    if entry and now - entry[0] < _HEAT_API_TTL:
        return entry[1]
    data = producer()
    _heat_api_cache[key] = (now, data)
    return data


def _compute_arrow(curr: float, prev: float) -> tuple[float, str]:
    if not prev or prev < 1e-6:
        return (0.0 if not curr else 100.0, "up" if curr > 0 else "flat")
    pct = (curr - prev) / prev * 100.0
    arrow = "up" if pct > 15 else ("down" if pct < -15 else "flat")
    return round(pct, 1), arrow


@app.route("/api/trending")
@login_required
def api_trending():
    """Trending Topics 榜 — 依 heat_index 排序，含漲跌箭頭與樣本標題。"""
    try:
        limit = max(1, min(30, int(request.args.get("limit", 10))))
    except ValueError:
        limit = 10

    def _produce():
        from modules.database.models import SessionLocal, Topic, NewsItem
        with SessionLocal() as s:
            topics = (
                s.query(Topic)
                .filter(Topic.status == "open")
                .order_by(Topic.heat_index.desc().nullslast())
                .limit(limit)
                .all()
            )
            result = []
            for t in topics:
                pct, arrow = _compute_arrow(t.heat_index or 0, t.heat_prev or 0)
                samples = (
                    s.query(NewsItem.title)
                    .filter(NewsItem.topic_id == t.id)
                    .order_by(NewsItem.ai_score.desc().nullslast())
                    .limit(3).all()
                )
                result.append({
                    "topic_id": t.id,
                    "slug": t.slug,
                    "title": t.title,
                    "heat": round(t.heat_index or 0, 3),
                    "heat_delta_pct": pct,
                    "arrow": arrow,
                    "news_count": t.news_count or 0,
                    "category": t.category,
                    "region": t.region,
                    "sample_titles": [row[0] for row in samples],
                })
            return result

    return jsonify(_cached(f"trending:{limit}", _produce))


@app.route("/api/timeline")
@login_required
def api_timeline():
    """30 日事件時間軸 — snapshot 散點資料。可按 category 過濾。"""
    try:
        days = max(1, min(90, int(request.args.get("days", 30))))
    except ValueError:
        days = 30
    category = (request.args.get("category") or "").strip() or None

    def _produce():
        from datetime import timedelta
        from modules.common.utils import tw_now
        from modules.database.models import SessionLocal, Topic, TopicHeatSnapshot
        cutoff = (tw_now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with SessionLocal() as s:
            q = (
                s.query(TopicHeatSnapshot, Topic)
                .join(Topic, Topic.id == TopicHeatSnapshot.topic_id)
                .filter(TopicHeatSnapshot.date >= cutoff)
            )
            if category:
                q = q.filter(TopicHeatSnapshot.category == category)
            rows = q.order_by(TopicHeatSnapshot.date.asc()).all()
            return [{
                "date": snap.date,
                "category": snap.category or "other",
                "topic_id": t.id,
                "slug": t.slug,
                "title": t.title,
                "heat": round(snap.heat_index or 0, 3),
                "news_count": snap.news_count or 0,
            } for snap, t in rows]

    return jsonify(_cached(f"timeline:{days}:{category or '*'}", _produce))


@app.route("/api/radar")
@login_required
def api_radar():
    """6 分類信號雷達 — 今日 vs 7 日均。"""
    CATEGORIES = ["ai_model", "business", "policy", "product", "semiconductor", "other"]

    def _produce():
        from datetime import timedelta
        from modules.common.utils import tw_now, tw_today
        from modules.database.models import SessionLocal, TopicHeatSnapshot
        today = tw_today()
        cutoff = (tw_now() - timedelta(days=7)).strftime("%Y-%m-%d")

        today_heat = {c: 0.0 for c in CATEGORIES}
        avg7_heat = {c: 0.0 for c in CATEGORIES}

        with SessionLocal() as s:
            # 今日：各分類 heat 總和
            rows = s.query(TopicHeatSnapshot).filter(TopicHeatSnapshot.date == today).all()
            for r in rows:
                cat = r.category if r.category in CATEGORIES else "other"
                today_heat[cat] += r.heat_index or 0

            # 7 日均：按 date + category 取總和，再除以天數
            rows_7d = s.query(TopicHeatSnapshot).filter(TopicHeatSnapshot.date >= cutoff).all()
            per_day: dict = {}
            for r in rows_7d:
                cat = r.category if r.category in CATEGORIES else "other"
                per_day.setdefault(r.date, {c: 0.0 for c in CATEGORIES})
                per_day[r.date][cat] += r.heat_index or 0
            if per_day:
                for cat in CATEGORIES:
                    avg7_heat[cat] = round(
                        sum(d[cat] for d in per_day.values()) / len(per_day), 3
                    )

        return {
            "today": {c: round(v, 3) for c, v in today_heat.items()},
            "avg_7d": avg7_heat,
            "date": today,
            "categories": CATEGORIES,
        }

    return jsonify(_cached("radar:today", _produce))


# ─────────── AI 戰情室 ───────────

@app.route("/ai")
def ai_war_room_public():
    """AI 新聞戰情室（公開版） — 不需登入。"""
    return render_template("ai_war_room_public.html")


@app.route("/admin/ai-war-room")
@login_required
def ai_war_room_admin():
    """AI 新聞戰情室（內部版） — 選題工具。"""
    return render_template("ai_war_room_admin.html", active="ai_war_room")


def _ai_parse_published(dt_str: str | None):
    """把 published_at 轉為 datetime；失敗回 None。"""
    from datetime import datetime
    if not dt_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    return None


def _ai_feed_tag(row) -> str:
    """把 NewsItem 分到 product / funding / partnership / research / policy / other。"""
    title = (row.title or "").lower()
    title_zh = row.title_zh or ""
    text = title + " " + title_zh
    cat = (row.category or "").lower()
    src = (row.source_name or "").lower()

    # research：arXiv / PWC / HF Papers / GitHub Trending / Reddit r/ML
    if any(k in src for k in ("arxiv", "papers", "huggingface", "github trending", "r/machinelearning", "r/locallm", "r/localllama")):
        return "research"
    if cat == "policy" or any(k in text for k in (
        "regulat", "lawmaker", "senate", "congress", "fcc", "ftc", "eu ai act",
        "監管", "法案", "法規", "立法", "政策", "禁令", "制裁",
    )):
        return "policy"
    if cat == "product" or any(k in text for k in (
        "launch", "release", "announce", "unveil", "introduc", "debut",
        "ship", "roll out", "rolling out", "now available", "now live",
        "goes live", "open beta", "open-source", "open source",
        "發布", "推出", "上線", "上架", "開放", "開源", "公布", "正式", "亮相",
    )):
        return "product"
    if any(k in text for k in (
        "raise", "funding", "series ", "valuation", "valued at", "ipo",
        "acquire", "acquisition", "buyout", "invest", "round",
        "融資", "併購", "收購", "估值", "投資", "入股",
    )):
        return "funding"
    if any(k in text for k in (
        "partner", "partnership", "teams up", "team up", "joint ", "joins forces",
        "sign", "deal with", "collaborat", "alliance",
        "合作", "聯手", "攜手", "結盟", "簽約", "共同",
    )):
        return "partnership"
    return "other"


_SUMMARY_NOISE = __import__("re").compile(
    r"^(HN\s*points?:\s*\d+\s*(?:\|\s*comments?:\s*\d+)?|↑\d+\s*💬\s*\d+\s*[—\-:]?\s*)",
    __import__("re").IGNORECASE,
)

def _clean_summary(raw: str | None, max_len: int = 180) -> str:
    """去除 HN/Reddit 前綴雜訊，截短到 max_len 字元。"""
    if not raw:
        return ""
    s = _SUMMARY_NOISE.sub("", raw.strip())
    s = " ".join(s.split())
    if len(s) > max_len:
        s = s[:max_len].rstrip() + "…"
    return s


@app.route("/api/ai/latest-news")
def api_ai_latest_news():
    """最新 AI 新聞流。可依 feed 過濾。"""
    from modules.database.models import NewsItem, SessionLocal
    try:
        limit = max(1, min(100, int(request.args.get("limit", 40))))
    except ValueError:
        limit = 40
    feed = (request.args.get("feed") or "all").strip().lower()

    def _produce():
        with SessionLocal() as s:
            rows = (
                s.query(NewsItem)
                .filter(NewsItem.is_ai == 1)
                .order_by(NewsItem.published_at.desc().nullslast(),
                          NewsItem.fetched_at.desc().nullslast())
                .limit(400)  # 取較多以便 feed 過濾後還夠
                .all()
            )
            out = []
            for r in rows:
                tag = _ai_feed_tag(r)
                if feed != "all" and tag != feed:
                    continue
                out.append({
                    "id": r.id,
                    "title": r.title_zh or r.title,
                    "title_en": r.title,
                    "summary": r.summary_zh or _clean_summary(r.summary),
                    "url": r.url,
                    "source": r.source_name,
                    "published_at": r.published_at,
                    "company": r.ai_company,
                    "category": r.category,
                    "feed_tag": tag,
                    "ai_score": r.ai_score,
                })
                if len(out) >= limit:
                    break
            return out

    return jsonify(_cached(f"ai:news:{feed}:{limit}", _produce))


@app.route("/api/ai/company-activity")
def api_ai_company_activity():
    """各公司最新動作 + 24h / 7d 計數。"""
    from datetime import datetime, timedelta, timezone
    from modules.ai_war_room.company_matcher import CompanyMatcher
    from modules.database.models import NewsItem, SessionLocal
    from sqlalchemy import func

    def _produce():
        matcher = CompanyMatcher.load()
        meta = {c["key"]: c for c in matcher.all_companies()}
        now = datetime.now(timezone.utc)
        cut_24h = (now - timedelta(hours=24)).isoformat()
        cut_7d = (now - timedelta(days=7)).isoformat()

        with SessionLocal() as s:
            # 各公司最新一則
            rows = (
                s.query(NewsItem)
                .filter(NewsItem.is_ai == 1, NewsItem.ai_company.isnot(None))
                .order_by(NewsItem.published_at.desc().nullslast(),
                          NewsItem.fetched_at.desc().nullslast())
                .all()
            )
            latest_by_company: dict = {}
            count_24h: dict = {}
            count_7d: dict = {}
            for r in rows:
                k = r.ai_company
                if k not in latest_by_company:
                    latest_by_company[k] = {
                        "title": r.title_zh or r.title,
                        "url": r.url,
                        "published_at": r.published_at,
                        "source": r.source_name,
                    }
                anchor = r.published_at or r.fetched_at or ""
                if anchor >= cut_7d:
                    count_7d[k] = count_7d.get(k, 0) + 1
                if anchor >= cut_24h:
                    count_24h[k] = count_24h.get(k, 0) + 1

            result = []
            for key, info in meta.items():
                result.append({
                    "company_key": key,
                    "name": info.get("name"),
                    "logo": info.get("logo"),
                    "group": info.get("group"),
                    "latest": latest_by_company.get(key),
                    "count_24h": count_24h.get(key, 0),
                    "count_7d": count_7d.get(key, 0),
                })
            # 按 count_7d 排序
            result.sort(key=lambda x: (-x["count_7d"], -x["count_24h"], x["name"] or ""))
            return result

    return jsonify(_cached("ai:companies", _produce))


@app.route("/api/ai/kpi")
def api_ai_kpi():
    """頂部 KPI Strip — 四個指標：今日新聞 / 活躍公司 / 模型發布 30d / 最熱主題。"""
    from datetime import datetime, timedelta, timezone
    from modules.database.models import NewsItem, SessionLocal, Topic

    def _produce():
        from modules.ai_war_room.company_matcher import CompanyMatcher
        from sqlalchemy import func

        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        y_start = (now - timedelta(days=1)).date().isoformat()
        cut_24h = (now - timedelta(hours=24)).isoformat()
        cut_30d = (now - timedelta(days=30)).isoformat()

        with SessionLocal() as s:
            # 今日 AI 新聞
            def _count_between(lo: str, hi: str | None = None) -> int:
                q = s.query(func.count(NewsItem.id)).filter(NewsItem.is_ai == 1)
                q = q.filter(NewsItem.published_at >= lo)
                if hi:
                    q = q.filter(NewsItem.published_at < hi)
                return q.scalar() or 0

            today_n = _count_between(today + "T00:00:00")
            yesterday_n = _count_between(y_start + "T00:00:00", today + "T00:00:00")

            # 活躍公司（24h 有 ≥1 則）
            active_rows = (
                s.query(NewsItem.ai_company, func.count(NewsItem.id))
                .filter(NewsItem.is_ai == 1, NewsItem.ai_company.isnot(None),
                        NewsItem.published_at >= cut_24h)
                .group_by(NewsItem.ai_company)
                .all()
            )
            active_count = len([k for k, _ in active_rows])
            total_tracked = len(CompanyMatcher.load().all_companies())

            # 模型發布 30 天
            model_n = (
                s.query(func.count(NewsItem.id))
                .filter(NewsItem.is_ai == 1, NewsItem.model_release == 1,
                        NewsItem.published_at >= cut_30d)
                .scalar() or 0
            )

            # 最熱主題（24h 內 AI 新聞命中最多的 open topic）
            hot_row = (
                s.query(Topic, func.count(NewsItem.id).label("cnt"))
                .join(NewsItem, NewsItem.topic_id == Topic.id)
                .filter(NewsItem.is_ai == 1, NewsItem.published_at >= cut_24h)
                .group_by(Topic.id)
                .order_by(func.count(NewsItem.id).desc())
                .first()
            )
            hot_topic = None
            if hot_row:
                t, cnt = hot_row
                pct, arrow = _compute_arrow(t.heat_index or 0, t.heat_prev or 0)
                hot_topic = {
                    "title": t.title, "slug": t.slug,
                    "heat": round(t.heat_index or 0, 2),
                    "delta_pct": pct, "arrow": arrow, "news_count": int(cnt),
                }

        # 漲跌 %（今日 vs 昨日）
        delta_pct = 0.0
        if yesterday_n > 0:
            delta_pct = round((today_n - yesterday_n) / yesterday_n * 100, 1)
        elif today_n > 0:
            delta_pct = 100.0

        return {
            "today_news":    {"value": today_n, "yesterday": yesterday_n, "delta_pct": delta_pct},
            "active_cos":    {"value": active_count, "total": total_tracked},
            "model_release": {"value": int(model_n), "window_days": 30},
            "hot_topic":     hot_topic,
        }

    return jsonify(_cached("ai:kpi", _produce))


@app.route("/api/ai/company-news/<company_key>")
def api_ai_company_news(company_key: str):
    """某公司近期新聞（給動態牆 modal 使用）。"""
    from modules.database.models import NewsItem, SessionLocal
    try:
        limit = max(1, min(50, int(request.args.get("limit", 20))))
    except ValueError:
        limit = 20

    def _produce():
        with SessionLocal() as s:
            rows = (
                s.query(NewsItem)
                .filter(NewsItem.is_ai == 1, NewsItem.ai_company == company_key)
                .order_by(NewsItem.published_at.desc().nullslast(),
                          NewsItem.fetched_at.desc().nullslast())
                .limit(limit)
                .all()
            )
            return [{
                "id": r.id,
                "title": r.title_zh or r.title,
                "title_en": r.title,
                "summary": r.summary_zh or _clean_summary(r.summary),
                "url": r.url,
                "source": r.source_name,
                "published_at": r.published_at,
                "category": r.category,
            } for r in rows]

    return jsonify(_cached(f"ai:company:{company_key}:{limit}", _produce))


@app.route("/api/ai/trending-topics")
def api_ai_trending_topics():
    """AI 熱門 topic + title tokenize wordcloud。"""
    import re
    from datetime import datetime, timedelta, timezone
    from collections import Counter
    from modules.database.models import NewsItem, SessionLocal, Topic
    from sqlalchemy import func

    window = (request.args.get("window") or "24h").strip().lower()
    hours = 24 if window == "24h" else (24 * 7)

    def _produce():
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        fallback = False

        with SessionLocal() as s:
            # Tier 1：window 內有新 AI 新聞的 topic
            topic_rows = (
                s.query(Topic, func.count(NewsItem.id).label("cnt"))
                .join(NewsItem, NewsItem.topic_id == Topic.id)
                .filter(NewsItem.is_ai == 1,
                        (NewsItem.published_at >= cutoff) | (NewsItem.fetched_at >= cutoff))
                .group_by(Topic.id)
                .order_by(func.count(NewsItem.id).desc())
                .limit(10)
                .all()
            )
            # Tier 2 fallback：window 內無資料時，列出 heat_index 最高的 AI topic
            if not topic_rows:
                fallback = True
                topic_rows = (
                    s.query(Topic, func.count(NewsItem.id).label("cnt"))
                    .join(NewsItem, NewsItem.topic_id == Topic.id)
                    .filter(NewsItem.is_ai == 1)
                    .group_by(Topic.id)
                    .order_by(Topic.heat_index.desc().nullslast(),
                              func.count(NewsItem.id).desc())
                    .limit(10)
                    .all()
                )
            topics = []
            for t, cnt in topic_rows:
                pct, arrow = _compute_arrow(t.heat_index or 0, t.heat_prev or 0)

                # 取該 Topic 內 ai_score 最高的 5 則新聞（戰情室卡片展開用）
                top_news_rows = (
                    s.query(NewsItem)
                    .filter(NewsItem.topic_id == t.id, NewsItem.is_ai == 1)
                    .order_by(NewsItem.ai_score.desc().nullslast(),
                              NewsItem.published_at.desc().nullslast())
                    .limit(5)
                    .all()
                )

                # 主新聞（最高分）的 angle / suggested_title
                top = top_news_rows[0] if top_news_rows else None
                top_score = float(top.ai_score) if top and top.ai_score is not None else None

                # 收集相關公司 / model_release flag
                companies = sorted({n.ai_company for n in top_news_rows if n.ai_company})
                model_release = any((n.model_release or 0) == 1 for n in top_news_rows)

                news_items = [{
                    "id": n.id,
                    "title": n.title_zh or n.title,
                    "title_en": n.title,
                    "summary_zh": n.summary_zh,
                    "source_name": n.source_name,
                    "url": n.url,
                    "ai_score": round(float(n.ai_score), 1) if n.ai_score is not None else None,
                    "published_at": n.published_at,
                } for n in top_news_rows]

                topics.append({
                    "topic_id": t.id, "slug": t.slug, "title": t.title,
                    "heat": round(t.heat_index or 0, 3),
                    "news_count": int(cnt), "arrow": arrow, "heat_delta_pct": pct,
                    "category": t.category,
                    "summary_zh": t.summary_zh,
                    "business_angle": top.business_angle if top else None,
                    "why_audience_cares": top.why_audience_cares if top else None,
                    "suggested_title": top.suggested_title if top else None,
                    "ai_score": top_score,
                    "companies": companies,
                    "model_release": bool(model_release),
                    "first_seen_date": t.first_seen_date,
                    "last_seen_date": t.last_seen_date,
                    "news_items": news_items,
                })

            # WordCloud：從窗口內 AI 新聞 title tokenize；fallback 用全部
            wc_q = s.query(NewsItem.title).filter(NewsItem.is_ai == 1)
            if not fallback:
                wc_q = wc_q.filter(
                    (NewsItem.published_at >= cutoff) | (NewsItem.fetched_at >= cutoff)
                )
            titles = wc_q.order_by(NewsItem.published_at.desc().nullslast()).limit(1500).all()

        # 簡易 tokenizer：英文 word ≥ 3 字；中文 2~4 字詞靠既有 keyword yaml 若存在
        stop = {"the","a","an","and","or","of","to","in","for","on","with","at","by","is","are","be",
                "this","that","from","as","it","its","was","were","into","new","more","how","why",
                "will","can","has","have","but","not","you","your","our","their","his","her",
                "也","與","和","或","的","了","在","是","為","及","以","對","從","給","有","會"}
        wc = Counter()
        en_re = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")
        zh_re = re.compile(r"[一-鿿]{2,4}")
        for (t,) in titles:
            if not t:
                continue
            for tok in en_re.findall(t):
                k = tok.lower()
                if k in stop or len(k) < 3:
                    continue
                wc[k] += 1
            for tok in zh_re.findall(t):
                if tok in stop:
                    continue
                wc[tok] += 1

        wordcloud = [{"name": k, "value": v} for k, v in wc.most_common(80)]
        return {"topics": topics, "wordcloud": wordcloud, "window": window, "fallback": fallback}

    return jsonify(_cached(f"ai:trending:{window}", _produce))


@app.route("/api/ai/model-timeline")
def api_ai_model_timeline():
    """模型發布時間軸 + benchmark 排行。"""
    from datetime import datetime, timedelta, timezone
    from modules.database.models import NewsItem, SessionLocal

    try:
        days = max(7, min(365, int(request.args.get("days", 180))))
    except ValueError:
        days = 180

    def _produce():
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with SessionLocal() as s:
            rows = (
                s.query(NewsItem)
                .filter(NewsItem.is_ai == 1, NewsItem.model_release == 1,
                        (NewsItem.published_at >= cutoff) | (NewsItem.fetched_at >= cutoff))
                .order_by(NewsItem.published_at.desc().nullslast())
                .limit(200)
                .all()
            )
            releases = [{
                "date": (r.published_at or r.fetched_at or "")[:10],
                "company": r.ai_company,
                "title": r.title,
                "url": r.url,
                "source": r.source_name,
            } for r in rows]

        # Benchmarks 從 YAML 讀（若不存在給空）
        benchmarks: list[dict] = []
        try:
            import yaml
            bp = PROJECT_ROOT / "config" / "ai_benchmarks.yaml"
            if bp.exists():
                with open(bp, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                benchmarks = data.get("benchmarks") or []
        except Exception:
            benchmarks = []

        return {"releases": releases, "benchmarks": benchmarks, "days": days}

    return jsonify(_cached(f"ai:models:{days}", _produce))


@app.route("/api/ai/used-set")
def api_ai_used_set():
    """某 entity_type 下的已用 id set + used_in_slug map。"""
    entity_type = (request.args.get("type") or "news").strip()
    if entity_type not in ("news", "topic"):
        return jsonify({"error": "invalid type"}), 400

    def _produce():
        from modules.ai_war_room.used_marks import get_used_set, get_used_slug_map
        return {
            "ids": sorted(get_used_set(entity_type)),
            "slug_map": get_used_slug_map(entity_type),
        }
    return jsonify(_cached(f"ai:used:{entity_type}", _produce))


@app.route("/api/ai/mark-used", methods=["POST"])
@login_required
def api_ai_mark_used():
    from modules.ai_war_room.used_marks import mark_news_used, mark_topic_used
    data = request.get_json(silent=True) or {}
    entity_type = (data.get("entity_type") or "").strip()
    entity_id = str(data.get("entity_id") or "").strip()
    used_in_slug = (data.get("used_in_slug") or "").strip()
    if entity_type not in ("news", "topic") or not entity_id:
        return jsonify({"error": "invalid payload"}), 400
    mid = mark_news_used(entity_id, used_in_slug) if entity_type == "news" else mark_topic_used(entity_id, used_in_slug)
    # 清相關 cache
    _heat_api_cache.pop(f"ai:used:{entity_type}", None)
    return jsonify({"id": mid, "ok": True})


@app.route("/api/ai/mark-used/<int:mark_id>", methods=["DELETE"])
@login_required
def api_ai_unmark(mark_id: int):
    from modules.ai_war_room.used_marks import unmark
    ok = unmark(mark_id)
    _heat_api_cache.pop("ai:used:news", None)
    _heat_api_cache.pop("ai:used:topic", None)
    return jsonify({"ok": bool(ok)})


# ─── 手動任務執行器（News 工作台按鈕 + 排程的同時入口）────────────
#
# 每個任務一 thread，全域 dict 記錄狀態供前端輪詢。
# 同一任務若已在 running 則拒絕再觸發（回 409）。

import subprocess
import threading

_task_states: dict[str, dict] = {}   # name → {status, started_at, finished_at, error, tail}
_task_lock = threading.Lock()


def _task_log_path(name: str) -> "Path":
    logs_dir = PROJECT_ROOT / "data" / "logs" / "tasks"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir / f"{name}.log"


def _run_subprocess_task(name: str, cmd: list[str]) -> None:
    """背景 thread 入口：跑 subprocess 並更新狀態。"""
    import datetime as _dt
    log_path = _task_log_path(name)
    started = _dt.datetime.now().isoformat(timespec="seconds")
    with _task_lock:
        _task_states[name] = {"status": "running", "started_at": started,
                              "finished_at": None, "error": None, "tail": ""}
    try:
        with open(log_path, "w", encoding="utf-8") as logf:
            logf.write(f"[{started}] CMD: {' '.join(cmd)}\n\n")
            logf.flush()
            p = subprocess.Popen(
                cmd, cwd=str(PROJECT_ROOT), stdout=logf, stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            rc = p.wait()
        # 讀尾巴 15 行做摘要
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            tail = "".join(lines[-15:])[-1500:]
        except Exception:
            tail = ""
        finished = _dt.datetime.now().isoformat(timespec="seconds")
        with _task_lock:
            _task_states[name].update({
                "status": "ok" if rc == 0 else "failed",
                "finished_at": finished,
                "error": None if rc == 0 else f"exit code {rc}",
                "tail": tail,
            })
    except Exception as e:
        with _task_lock:
            _task_states[name].update({
                "status": "failed",
                "finished_at": _dt.datetime.now().isoformat(timespec="seconds"),
                "error": str(e), "tail": "",
            })


def _run_python_task(name: str, args: list[str]) -> None:
    cmd = [sys.executable, *args]
    _run_subprocess_task(name, cmd)


def _run_refresh_chain() -> None:
    """一鍵：爬 → 分類 → 補 AI 欄位 → 翻譯。任一步失敗後續仍跑（不阻塞）。"""
    import datetime as _dt
    started = _dt.datetime.now().isoformat(timespec="seconds")
    with _task_lock:
        _task_states["refresh_all"] = {"status": "running", "started_at": started,
                                        "finished_at": None, "error": None, "tail": ""}
    sub_specs = [
        ("fetch",            [sys.executable, "daily_pipeline.py", "--fetch"]),
        ("classify",         [sys.executable, "-m", "modules.common.news_pipeline"]),
        ("backfill",         [sys.executable, "-m", "modules.ai_war_room.backfill"]),
        ("translate",        [sys.executable, "-m", "modules.ai_war_room.translator", "--batch", "5"]),
        ("auto_score",       [sys.executable, "-m", "modules.ai_war_room.auto_scorer", "--batch", "5", "--limit", "300"]),
        ("cluster_recover",  [sys.executable, "-m", "modules.brief.topic_clusterer", "--no-candidate", "--min-score", "3"]),
    ]
    failed: list[str] = []
    for sub_name, cmd in sub_specs:
        _run_subprocess_task(sub_name, cmd)
        if _task_states.get(sub_name, {}).get("status") != "ok":
            failed.append(sub_name)
    finished = _dt.datetime.now().isoformat(timespec="seconds")
    with _task_lock:
        _task_states["refresh_all"].update({
            "status": "ok" if not failed else "failed",
            "finished_at": finished,
            "error": None if not failed else f"步驟失敗：{', '.join(failed)}",
            "tail": f"完成 {len(sub_specs) - len(failed)}/{len(sub_specs)} 步驟",
        })


_TASK_SPECS: dict[str, dict] = {
    "fetch":       {"label": "📡 爬取新聞",     "desc": "抓取所有 RSS / 網頁源 → 寫入 DB", "args": ["daily_pipeline.py", "--fetch"]},
    "classify":    {"label": "🏷️ 分類新聞",     "desc": "寫入 category / region / is_ai",  "args": ["-m", "modules.common.news_pipeline"]},
    "backfill":    {"label": "🏢 補公司 / 模型", "desc": "AI 戰情室用 — ai_company / model_release", "args": ["-m", "modules.ai_war_room.backfill"]},
    "translate":   {"label": "🌐 翻譯標題",     "desc": "本地 Claude CLI 翻譯英文標題為繁中", "args": ["-m", "modules.ai_war_room.translator", "--batch", "5"]},
    "translate_summary": {"label": "🌐 翻譯摘要", "desc": "本地 Claude CLI 翻譯英文摘要為繁中", "args": ["-m", "modules.ai_war_room.translator", "--mode", "summary"]},
    "topic_summary": {"label": "📝 主題彙總摘要", "desc": "把同主題多則新聞濃縮成一段繁中摘要（戰情室卡片用）", "args": ["-m", "modules.ai_war_room.topic_summarizer", "--limit", "50"]},
    "auto_score":  {"label": "⭐ AI 自動評分",  "desc": "本地 Claude CLI 為未評分 AI 新聞打分 + 升候選（batch=5 預設 limit=300）", "args": ["-m", "modules.ai_war_room.auto_scorer", "--batch", "5", "--limit", "300"]},
    "cluster_recover": {"label": "🧩 補做主題聚類", "desc": "繞過 candidate 條件，將全期已評分 AI 新聞補進 Topic（min_score=3）", "args": ["-m", "modules.brief.topic_clusterer", "--no-candidate", "--min-score", "3"]},
    "brief":       {"label": "📋 生成 Brief",   "desc": "Daily Brief + 熱度指數刷新",      "args": ["daily_pipeline.py", "--brief"]},
    "score_cowork": {"label": "⭐ 匯出評分佇列(CoWork)", "desc": "產生 scoring_queue.json 供手動評分（備援）", "args": ["daily_pipeline.py", "--score", "--cowork"]},
    "analytics":   {"label": "📊 更新 YouTube 數據", "desc": "刷新已上傳影片的觀看數",    "args": ["-c", "from modules.database.analytics_tracker import update_video_analytics; update_video_analytics()"]},
    "weekly":      {"label": "📰 產生週報",     "desc": "寫入 data/reports/*.md",          "args": ["-c", "from modules.database.analytics_tracker import generate_weekly_report; print(generate_weekly_report())"]},
}


@app.route("/api/tasks")
def api_tasks_status():
    """回目前所有任務狀態 + 規格。"""
    with _task_lock:
        states = dict(_task_states)
    return jsonify({
        "specs": {k: {"label": v["label"], "desc": v["desc"]} for k, v in _TASK_SPECS.items()},
        "states": states,
    })


@app.route("/api/tasks/<name>", methods=["POST"])
@login_required
def api_tasks_run(name: str):
    """觸發任務。同一任務 running 中時回 409。"""
    with _task_lock:
        current = _task_states.get(name, {}).get("status")
        if current == "running":
            return jsonify({"error": "已在執行中", "status": "running"}), 409

    if name == "refresh_all":
        threading.Thread(target=_run_refresh_chain, daemon=True).start()
        return jsonify({"ok": True, "name": name})

    spec = _TASK_SPECS.get(name)
    if not spec:
        return jsonify({"error": "unknown task"}), 404
    threading.Thread(target=_run_python_task, args=(name, spec["args"]), daemon=True).start()
    return jsonify({"ok": True, "name": name})


@app.route("/api/tasks/<name>/log")
@login_required
def api_tasks_log(name: str):
    """下載該任務最近一次的 stdout log。"""
    p = _task_log_path(name)
    if not p.exists():
        return jsonify({"error": "no log"}), 404
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return content, 200, {"Content-Type": "text/plain; charset=utf-8"}


# ─── 每日排程背景 daemon（APScheduler BackgroundScheduler）────────
_scheduler_started = False


def start_scheduler_thread() -> None:
    """把 scheduler.py 裡的 cron job 搬到 Flask 同 process 的背景 daemon。

    這樣使用者只需開 `python web_ui/app.py`，就能自動每日 06:00 抓取。
    SCHEDULER_ENABLED=0 可關閉（例如多 worker 或獨立 scheduler process 情境）。
    """
    global _scheduler_started
    if _scheduler_started:
        return
    if os.getenv("SCHEDULER_ENABLED", "1") == "0":
        print("[Scheduler] SCHEDULER_ENABLED=0，跳過啟動")
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        print("[Scheduler] 未安裝 APScheduler，跳過；pip install APScheduler>=3.10")
        return

    # 重用 scheduler.py 定義的 job 函式
    from scheduler import (
        job_compose_and_upload,
        job_fetch_and_score,
        job_generate_brief,
        job_update_analytics,
        job_weekly_report,
    )

    sched = BackgroundScheduler(timezone="Asia/Taipei", daemon=True)
    sched.add_job(job_fetch_and_score, CronTrigger(hour=6, minute=0), id="fetch", name="抓取新聞")
    sched.add_job(job_generate_brief, CronTrigger(hour=6, minute=30), id="brief", name="生成 Brief")
    sched.add_job(job_compose_and_upload, CronTrigger(hour=14, minute=0), id="compose", name="影片合成上傳")
    sched.add_job(job_update_analytics, CronTrigger(hour=22, minute=0), id="analytics", name="更新數據")
    sched.add_job(job_weekly_report, CronTrigger(day_of_week="mon", hour=9), id="weekly", name="週報")

    # AI 戰情室專用：每次抓取完成 30 分鐘後自動翻譯新增的 AI 新聞標題 + 摘要
    def _job_translate_ai_news():
        from loguru import logger
        try:
            from modules.ai_war_room.translator import translate_titles, translate_summaries
            r1 = translate_titles(limit=500)
            logger.info(f"[Scheduler] 翻譯標題：{r1}")
            r2 = translate_summaries(limit=500)
            logger.info(f"[Scheduler] 翻譯摘要：{r2}")
        except Exception as e:
            logger.warning(f"[Scheduler] 翻譯任務失敗：{e}")

    sched.add_job(_job_translate_ai_news, CronTrigger(hour=7, minute=0),
                  id="translate", name="翻譯 AI 新聞標題")

    # AI 戰情室專用：06:15 自動評分新爬的 AI 新聞、06:45 補做主題聚類
    def _job_auto_score():
        from loguru import logger
        try:
            from modules.ai_war_room.auto_scorer import score
            r = score(limit=500)
            logger.info(f"[Scheduler] 自動評分：{r}")
        except Exception as e:
            logger.warning(f"[Scheduler] 自動評分失敗：{e}")

    def _job_cluster_recover():
        from loguru import logger
        try:
            from modules.brief.topic_clusterer import cluster_and_persist
            r = cluster_and_persist(min_score=3.0, limit=2000, no_candidate=True)
            logger.info(f"[Scheduler] 補做聚類：{r}")
        except Exception as e:
            logger.warning(f"[Scheduler] 補做聚類失敗：{e}")

    sched.add_job(_job_auto_score, CronTrigger(hour=6, minute=15),
                  id="auto_score", name="AI 自動評分")
    sched.add_job(_job_cluster_recover, CronTrigger(hour=6, minute=45),
                  id="cluster_recover", name="補做主題聚類")

    sched.start()
    _scheduler_started = True
    print("[Scheduler] 已啟動（台灣時間）：06:00 爬取、06:15 自動評分、06:30 brief、06:45 補做聚類、07:00 翻譯、14:00 合成、22:00 數據、週一 09:00 週報")


# 模組載入時自動啟動（讓 gunicorn / flask / 直接跑 都能觸發）
init_db()
start_watcher_thread()
start_scheduler_thread()


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"啟動：http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)