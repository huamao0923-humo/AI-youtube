"""AI 公司對映 — 把新聞 title+summary 對映到 ai_companies.yaml 的 company key。

策略：按 YAML 順序遍歷；別名分兩類：
  - 純字串：小寫 substring 匹配
  - regex：形如 /pattern/flags（只認 i 旗標）

用法：
    matcher = CompanyMatcher.load()
    key = matcher.match(title='OpenAI releases Sora 2', summary='...')
    # -> 'openai'
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "ai_companies.yaml"


def _parse_alias(raw: str) -> tuple[str, re.Pattern | None]:
    """/pattern/flags → compiled regex；否則視為 lowercase substring。"""
    if isinstance(raw, str) and raw.startswith("/") and raw.rfind("/") > 0:
        end = raw.rfind("/")
        pattern = raw[1:end]
        flags_str = raw[end + 1:]
        flags = re.IGNORECASE if "i" in flags_str else 0
        try:
            return raw, re.compile(pattern, flags)
        except re.error:
            return raw, None
    return (raw or "").lower(), None


class CompanyMatcher:
    def __init__(self, companies: list[dict[str, Any]]):
        self._companies = companies
        # 預編譯：每家 [(lower_str_or_none, regex_or_none), ...]
        self._compiled: list[tuple[str, list[tuple[str, re.Pattern | None]]]] = []
        for c in companies:
            aliases = c.get("aliases") or []
            compiled = [_parse_alias(a) for a in aliases if a]
            self._compiled.append((c["key"], compiled))

    def match(self, title: str = "", summary: str = "") -> str | None:
        """回傳首個命中的 company key，沒有就 None。"""
        text = f"{title or ''}\n{summary or ''}"
        text_lower = text.lower()
        for key, aliases in self._compiled:
            for raw, regex in aliases:
                if regex is not None:
                    if regex.search(text):
                        return key
                elif raw and raw in text_lower:
                    return key
        return None

    def all_companies(self) -> list[dict[str, Any]]:
        return self._companies

    @classmethod
    @lru_cache(maxsize=1)
    def load(cls, path: str | None = None) -> "CompanyMatcher":
        import yaml
        p = Path(path) if path else _CONFIG_PATH
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(data.get("companies") or [])
