"""Web UI 身份驗證 — 簡單密碼保護。

設定方式：在 .env 或 Railway Variables 加入：
  WEB_PASSWORD=你的密碼

未設定時，本機開發模式（無需密碼）。
Railway 部署請務必設定 WEB_PASSWORD。
"""
from __future__ import annotations

import os
import functools
from flask import session, redirect, url_for, request, render_template_string

_LOGIN_HTML = """
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>登入 — AI 頻道</title>
<style>
:root{--bg:#09090b;--surface:#111114;--border:#27272d;--text:#f4f4f5;--accent:#7c3aed;--accent2:#8b5cf6;--muted:#71717a;}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--text);font-family:-apple-system,"Microsoft JhengHei",sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;}
.card{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:36px 40px;width:100%;max-width:380px;}
.logo{text-align:center;margin-bottom:28px;}
.logo h1{font-size:18px;font-weight:800;color:var(--accent2);}
.logo p{font-size:12px;color:var(--muted);margin-top:4px;}
label{display:block;font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;}
input{width:100%;background:transparent;border:1px solid var(--border);border-radius:8px;padding:10px 14px;font-size:14px;color:var(--text);outline:none;}
input:focus{border-color:var(--accent);}
.btn{display:block;width:100%;background:var(--accent);color:#fff;border:none;border-radius:8px;padding:11px;font-size:14px;font-weight:700;cursor:pointer;margin-top:16px;}
.btn:hover{background:#6d28d9;}
.err{background:#450a0a;border:1px solid #7f1d1d;border-radius:8px;padding:10px 14px;font-size:12px;color:#fca5a5;margin-bottom:14px;}
</style>
</head>
<body>
<div class="card">
  <div class="logo">
    <h1>🎬 AI 頻道</h1>
    <p>製作系統</p>
  </div>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  <form method="POST" action="/login">
    <div style="margin-bottom:14px">
      <label>密碼</label>
      <input type="password" name="password" autofocus placeholder="輸入存取密碼">
    </div>
    <button class="btn" type="submit">進入系統</button>
  </form>
</div>
</body>
</html>
"""


def _get_password() -> str | None:
    return os.getenv("WEB_PASSWORD")


def _dev_mode() -> bool:
    """未設定 WEB_PASSWORD 且非 Railway 環境時為開發模式（免密）。"""
    return not _get_password() and not os.getenv("RAILWAY_ENVIRONMENT")


def login_required(f):
    """Route decorator — 需要登入。"""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if _dev_mode():
            return f(*args, **kwargs)
        if session.get("authenticated"):
            return f(*args, **kwargs)
        return redirect(url_for("login_page") + f"?next={request.path}")
    return decorated


def register_auth_routes(app):
    """把登入 / 登出路由注入 Flask app。"""

    @app.route("/login", methods=["GET", "POST"])
    def login_page():
        error = None
        if request.method == "POST":
            pw = request.form.get("password", "")
            if pw == _get_password():
                session.permanent = True
                session["authenticated"] = True
                next_url = request.args.get("next", "/")
                return redirect(next_url)
            error = "密碼錯誤，請再試一次"
        return render_template_string(_LOGIN_HTML, error=error)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login_page"))
