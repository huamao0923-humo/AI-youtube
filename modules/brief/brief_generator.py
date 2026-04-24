"""Daily Brief 生成器 — 從 DB 候選池產生今日簡報 JSON。

不依賴 Telegram，直接輸出給 Web UI 或 CoWork 模式使用。
"""
from __future__ import annotations

import json
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from modules.common.utils import tw_today
from pathlib import Path
from typing import Any

from modules.common.config import PROJECT_ROOT, settings, keywords
from modules.common.logging_setup import setup_logger
from modules.database import db_manager

setup_logger()

BRIEF_DIR = PROJECT_ROOT / "data" / "briefs"

_KNOWLEDGE_KW = [
    "research", "paper", "benchmark", "fine-tuning", "training",
    "open source", "api", "multimodal", "architecture", "inference",
    "model weights", "dataset",
]
_COMMERCIAL_KW = [
    "$", "billion", "million", "funding", "acquisition", "revenue",
    "ipo", "valuation", "enterprise", "partnership", "customers",
    "market share", "competitor",
]
_COUNT_SCALE = {0: 1, 1: 3, 2: 5, 3: 7, 4: 8}


def _scale_count(n: int) -> int:
    return _COUNT_SCALE.get(n, 10)


def _compute_timeliness(published_at: str | None) -> dict[str, Any]:
    fallback = {"timeliness_days": None, "timeliness_label": "fresh", "timeliness_warning": False}
    if not published_at:
        return fallback
    try:
        pub = datetime.fromisoformat(str(published_at))
    except ValueError:
        try:
            from datetime import date
            d = date.fromisoformat(str(published_at)[:10])
            pub = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        except ValueError:
            return fallback
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    days = max(0, (datetime.now(timezone.utc) - pub).days)
    if days < 7:
        label, warning = "fresh", False
    elif days <= 14:
        label, warning = "aging", True
    else:
        label, warning = "stale", True
    return {"timeliness_days": days, "timeliness_label": label, "timeliness_warning": warning}


def _compute_three_scores(row: dict[str, Any], cluster_size: int, timeliness_days: int | None) -> dict[str, int]:
    text = " ".join(filter(None, [
        row.get("title", ""), row.get("summary", ""), row.get("business_angle", ""),
    ])).lower()

    base = round((row.get("ai_score") or 0) * 0.5)
    heat = base + min(cluster_size - 1, 3)
    if timeliness_days is not None:
        heat += 2 if timeliness_days < 3 else (1 if timeliness_days < 7 else 0)
    heat = max(1, min(10, heat))

    k_count = sum(1 for kw in _KNOWLEDGE_KW if kw in text)
    c_count = sum(1 for kw in _COMMERCIAL_KW if kw in text)

    return {
        "heat_score": heat,
        "knowledge_score": _scale_count(k_count),
        "commercial_score": _scale_count(c_count),
    }


def _extract_entities(row: dict[str, Any]) -> set[str]:
    kw = keywords()
    text = " ".join(filter(None, [row.get("title", ""), row.get("summary", "")])).lower()
    entities: set[str] = set()
    for tier in kw.get("target_companies", {}).values():
        for name in tier:
            if name.lower() in text:
                entities.add(name.lower())
    for kw_str in kw.get("high_value_keywords", {}).get("tier1_score_5", []):
        if kw_str.lower() in text:
            entities.add(kw_str.lower())
    return entities


def build_entity_components(items: list[dict[str, Any]]) -> list[set[int]]:
    """BFS 連通分量 — 以共同 entity 為邊的圖分群。供 brief + topic_clusterer 共用。"""
    if not items:
        return []
    ids = [item["id"] for item in items]
    entity_map = {item["id"]: _extract_entities(item) for item in items}

    adj: dict[int, set[int]] = defaultdict(set)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if entity_map[a] & entity_map[b]:
                adj[a].add(b)
                adj[b].add(a)

    visited: set[int] = set()
    components: list[set[int]] = []
    for nid in ids:
        if nid in visited:
            continue
        comp: set[int] = set()
        q = deque([nid])
        while q:
            cur = q.popleft()
            if cur in visited:
                continue
            visited.add(cur)
            comp.add(cur)
            q.extend(adj[cur] - visited)
        components.append(comp)
    return components


def _cluster_candidates(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not items:
        return items, []

    id_to_item = {item["id"]: item for item in items}
    components = build_entity_components(items)

    score_lookup = {item["id"]: (item.get("ai_score") or 0) for item in items}
    topic_clusters = []
    for comp in sorted(components, key=lambda c: (-len(c), -max(score_lookup[i] for i in c))):
        rep_id = max(comp, key=lambda i: score_lookup[i])
        topic_clusters.append({
            "cluster_id": str(rep_id),
            "size": len(comp),
            "representative_id": rep_id,
            "member_ids": sorted(comp),
        })

    comp_map: dict[int, set[int]] = {}
    for comp in components:
        for nid in comp:
            comp_map[nid] = comp

    for item in items:
        comp = comp_map[item["id"]]
        rep_id = max(comp, key=lambda i: score_lookup[i])
        peers = [i for i in comp if i != item["id"]]
        item["cluster_id"] = str(rep_id)
        item["cluster_size"] = len(comp)
        item["cluster_peers"] = peers
        item["cluster_peer_titles"] = [
            id_to_item[i].get("suggested_title") or id_to_item[i].get("title", "")
            for i in peers
        ]

    return items, topic_clusters


def generate(top_n: int = 5, fetched_date: str | None = None) -> dict[str, Any]:
    """從候選池取前 N 則，生成結構化 Daily Brief。

    fetched_date: 限定爬取日期（YYYY-MM-DD），預設今日台灣時間。
                  傳入空字串 "" 表示不篩選（顯示所有日期）。
    """
    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    db_manager.init_db()

    if fetched_date is None:
        # 預設取最新一批爬取資料（fetched_at 為 UTC，避免時區偏移誤判）
        fetched_date = db_manager.get_latest_fetch_date() or ""

    candidates = db_manager.fetch_candidates(
        min_score=settings()["filter"]["ai_score_min"],
        limit=top_n,
        fetched_date=fetched_date or None,
    )

    today = tw_today()
    items = []

    for rank, row in enumerate(candidates, 1):
        title = row["suggested_title"] or row["title"]
        angles = _suggest_angles(row)

        items.append({
            "rank": rank,
            "id": row["id"],
            "title": row["title"],
            "summary": row.get("summary", ""),
            "source_name": row["source_name"],
            "published_at": row["published_at"],
            "ai_score": row["ai_score"],
            "business_angle": row["business_angle"],
            "why_audience_cares": row["why_audience_cares"],
            "suggested_title": title,
            "angles": angles,
            "url": None,
        })

    # 時效（無相依）
    for item in items:
        item.update(_compute_timeliness(item.get("published_at")))

    # 主題聚合（需整個 items list）
    items, topic_clusters = _cluster_candidates(items)

    # 三維評分（需 cluster_size + timeliness_days）
    for item in items:
        item.update(_compute_three_scores(
            row=item,
            cluster_size=item["cluster_size"],
            timeliness_days=item.get("timeliness_days"),
        ))

    brief = {
        "date": today,
        "fetched_date": fetched_date or "all",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidates": items,
        "topic_clusters": topic_clusters,
        "quick_news": _fetch_quick_news(exclude_ids=[r["id"] for r in candidates]),
    }

    # 同時存 DB（讓 Railway Web UI 能讀到）和本地檔案（備用）
    unscored = db_manager.get_unscored_count()
    db_manager.save_brief(today, brief, unscored=unscored)

    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    out = BRIEF_DIR / f"{today}_brief.json"
    out.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")

    return brief


def _suggest_angles(row: Any) -> list[dict[str, str]]:
    """根據 business_angle 與 why_audience_cares 生成 3 個切入角度建議。"""
    title = row["suggested_title"] or row["title"]
    biz = row["business_angle"] or ""
    why = row["why_audience_cares"] or ""

    return [
        {
            "label": "A",
            "name": "商業影響角度",
            "description": f"聚焦在：{biz[:80]}" if biz else "從商業影響切入",
        },
        {
            "label": "B",
            "name": "觀眾實用角度",
            "description": f"聚焦在：{why[:80]}" if why else "從觀眾痛點切入",
        },
        {
            "label": "C",
            "name": "趨勢預測角度",
            "description": f"深挖「{title}」背後代表的產業趨勢走向",
        },
    ]


def _fetch_quick_news(exclude_ids: list[int], limit: int = 5) -> list[dict]:
    """抓分數 4-6 的新聞作為快訊補充。"""
    from modules.database.models import NewsItem, SessionLocal
    with SessionLocal() as s:
        q = (
            s.query(NewsItem)
            .filter(
                NewsItem.ai_score.between(4, 5.9),
                NewsItem.status.in_(["candidate", "skipped"]),
            )
        )
        if exclude_ids:
            q = q.filter(NewsItem.id.notin_(exclude_ids))
        rows = q.order_by(NewsItem.ai_score.desc()).limit(limit).all()
    return [{"id": r.id, "title": r.title, "source": r.source_name} for r in rows]


def load_today() -> dict[str, Any] | None:
    """讀取今日 brief — 優先 DB（Railway），fallback 本地檔。"""
    today = tw_today()
    # DB 優先（Railway 環境無本地檔案）
    row = db_manager.load_brief(today)
    if row:
        return row["content"]
    # fallback 本地
    path = BRIEF_DIR / f"{today}_brief.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


if __name__ == "__main__":
    brief = generate()
    print(f"[OK] Daily Brief 生成完成，候選 {len(brief['candidates'])} 則")
    for item in brief["candidates"]:
        print(f"  [{item['rank']}] {item['ai_score']:.1f} | {item['suggested_title']}")
