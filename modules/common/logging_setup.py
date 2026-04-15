"""統一 logger 設定（所有模組共用）。"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

from .config import PROJECT_ROOT

# 修正 Windows 終端機亂碼（CP950 → UTF-8）
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_configured = False


def setup_logger() -> None:
    global _configured
    if _configured:
        return
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | "
               "<cyan>{name}:{function}</cyan> - <level>{message}</level>",
    )
    log_file = log_dir / f"{datetime.now():%Y%m%d}.log"
    logger.add(
        log_file,
        level="DEBUG",
        rotation="50 MB",
        retention="30 days",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {name}:{function}:{line} - {message}",
    )
    _configured = True


setup_logger()
