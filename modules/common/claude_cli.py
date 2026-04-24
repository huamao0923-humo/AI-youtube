"""Claude CLI 呼叫工具 — 透過 node cli.js -p（非互動模式）呼叫本地 Claude Code。

使用 Claude Max 訂閱，不需要 ANTHROPIC_API_KEY。

為何直接呼叫 node cli.js：
  Windows 的 claude.cmd wrapper 會經過 cmd.exe，中文 prompt 會被
  codepage（cp950）轉成亂碼。直接用 node 跑 cli.js 繞過這個問題。

為何超長 prompt 寫到暫存檔再讀：
  Windows CreateProcess 指令列長度上限 ~32KB，
  script prompt 加上研究摘要可能超過。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# 保守一點，低於 Windows CreateProcess 上限 32KB
ARG_LEN_THRESHOLD = 8000


def _find_node() -> str:
    node = shutil.which("node")
    if not node:
        raise FileNotFoundError("找不到 node，請安裝 Node.js：https://nodejs.org")
    return node


def _find_cli_js() -> str:
    """找 @anthropic-ai/claude-code 的 cli.js。"""
    candidates: list[Path] = []
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", "")
        candidates.append(
            Path(appdata) / "npm" / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
        )
    else:
        for prefix in ("/usr/local/lib", "/usr/lib", str(Path.home() / ".npm")):
            candidates.append(
                Path(prefix) / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
            )

    for cli_js in candidates:
        if cli_js.exists():
            return str(cli_js)

    raise FileNotFoundError(
        "找不到 @anthropic-ai/claude-code cli.js，"
        "請執行：npm install -g @anthropic-ai/claude-code"
    )


def _env() -> dict:
    env = os.environ.copy()
    env["FORCE_COLOR"] = "0"            # 關 ANSI 色碼，避免污染 stdout
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run(
    prompt: str,
    timeout: int = 300,
    *,
    slug: str | None = None,
    heartbeat_msg: str | None = None,
) -> str:
    """呼叫 claude -p PROMPT（非互動模式），回傳回應文字。

    slug + heartbeat_msg：若給定，會在 claude CLI 執行期間每 15 秒更新
    該集數的 progress_detail，讓前端 UI 不會卡在「準備中…」。
    """
    from loguru import logger
    from modules.common.progress_heartbeat import Heartbeat

    node = _find_node()
    cli_js = _find_cli_js()
    logger.debug(f"[claude_cli] node={node}")
    logger.debug(f"[claude_cli] cli_js={cli_js}")
    logger.debug(f"[claude_cli] prompt 長度={len(prompt)} 字元")

    use_stdin = len(prompt) > ARG_LEN_THRESHOLD
    logger.info(f"[claude_cli] 呼叫模式：{'stdin' if use_stdin else 'argument'}，timeout={timeout}s")

    # 關鍵：Python 3.13 + Windows 上 text=True+encoding="utf-8" 不會
    # 正確傳給 subprocess reader thread（會用系統 cp950 解碼，Claude 的
    # UTF-8 中文輸出即炸 UnicodeDecodeError）。改拿 bytes 自己解碼。
    prompt_bytes = prompt.encode("utf-8")
    hb_msg = heartbeat_msg or "⏳ Claude CLI 執行中"
    with Heartbeat(slug=slug, base_msg=hb_msg, expected_sec=timeout):
        if not use_stdin:
            result = subprocess.run(
                [node, cli_js, "-p", prompt],
                capture_output=True,
                timeout=timeout, env=_env(),
            )
        else:
            # 用 stdin=PIPE + input= 傳 bytes，避免 Windows shell redirect 造成 EOF 問題
            result = subprocess.run(
                [node, cli_js, "-p"],
                input=prompt_bytes,
                capture_output=True,
                timeout=timeout, env=_env(),
            )

    stdout = (result.stdout or b"").decode("utf-8", errors="replace")
    stderr = (result.stderr or b"").decode("utf-8", errors="replace")
    logger.info(f"[claude_cli] returncode={result.returncode}, stdout={len(stdout)} 字, stderr={len(stderr)} 字")
    if stderr.strip():
        logger.warning(f"[claude_cli] stderr 前500字：{stderr[:500]}")
    if stdout.strip():
        logger.info(f"[claude_cli] stdout 前500字：{stdout[:500]}")

    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI 失敗（code={result.returncode}）：{stderr[:300]}"
        )

    output = stdout.strip()
    if not output:
        raise RuntimeError(f"claude CLI 回傳空白內容（stderr: {stderr[:200]}）")

    return output
