"""共用設定載入與路徑處理。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"

load_dotenv(PROJECT_ROOT / ".env")


@lru_cache(maxsize=1)
def load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"找不到設定檔：{path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def settings() -> dict[str, Any]:
    return load_yaml("settings.yaml")


def sources() -> dict[str, Any]:
    return load_yaml("sources.yaml")


def keywords() -> dict[str, Any]:
    return load_yaml("keywords.yaml")


def env(key: str, default: str | None = None, *, required: bool = False) -> str | None:
    val = os.getenv(key, default)
    if required and not val:
        raise RuntimeError(f"缺少必要環境變數：{key}")
    return val


def data_path(*parts: str) -> Path:
    base = PROJECT_ROOT / settings()["paths"]["data_root"].lstrip("./")
    p = base.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def project_path(*parts: str) -> Path:
    return PROJECT_ROOT.joinpath(*parts)
