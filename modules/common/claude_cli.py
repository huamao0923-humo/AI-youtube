"""Claude CLI 呼叫工具 — 透過 claude -p（非互動模式）呼叫本地 Claude Code。

使用 Claude Max 訂閱，不需要 ANTHROPIC_API_KEY。
透過 node cli.js 直接呼叫，繞過 Windows .cmd wrapper 的編碼問題。
使用 stdin 傳遞 prompt，解決超長 prompt 的 command line 長度限制。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _find_node_and_cli() -> tuple[str, str]:
    """找 node 執行檔和 claude cli.js 路徑。"""
    node = shutil.which("node")
    if not node:
        raise FileNotFoundError("找不到 node，請安裝 Node.js：https://nodejs.org")

    # 找 cli.js（全域 npm 安裝路徑）
    candidates = []
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", "")
        candidates.append(
            Path(appdata) / "npm" / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
        )
    else:
        # Linux / macOS
        for prefix in ("/usr/local/lib", "/usr/lib", str(Path.home() / ".npm")):
            candidates.append(
                Path(prefix) / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
            )

    for cli_js in candidates:
        if cli_js.exists():
            return node, str(cli_js)

    raise FileNotFoundError(
        "找不到 claude CLI，請執行：npm install -g @anthropic-ai/claude-code"
    )


def run(prompt: str, timeout: int = 300) -> str:
    """
    呼叫 claude -p（非互動模式），回傳回應文字。
    使用 stdin 傳遞 prompt，支援超長輸入。
    """
    node, cli_js = _find_node_and_cli()

    env = os.environ.copy()
    env["FORCE_COLOR"] = "0"   # 關閉 ANSI 色碼，避免污染輸出

    result = subprocess.run(
        [node, cli_js, "-p", "-"],          # -p - = 從 stdin 讀取 prompt
        input=prompt.encode("utf-8"),
        capture_output=True,
        timeout=timeout,
        env=env,
    )

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"claude CLI 失敗（code={result.returncode}）：{stderr}")

    output = result.stdout.decode("utf-8", errors="replace").strip()
    if not output:
        raise RuntimeError("claude CLI 回傳空白內容")

    return output
