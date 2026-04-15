"""Claude 新聞評分器 — 批次送 Claude API，產出 JSON 評分。

輸出欄位：
  score, business_angle, why_audience_cares, suggested_title, skip_reason

流程：
  1. 從 DB 撈 ai_score IS NULL 的新聞
  2. 每 N 則打包成一個 user message
  3. Claude 回傳 JSON 陣列
  4. 寫回 DB，同時把 >= 門檻的標記為 candidate
"""
from __future__ import annotations

import argparse
import json
import re
from typing import Any

from anthropic import Anthropic
from loguru import logger

from modules.common.config import env, settings
from modules.common.logging_setup import setup_logger
from modules.database import db_manager

setup_logger()

SYSTEM_PROMPT = """你是一個 AI 商業新聞編輯，專門為繁體中文 YouTube 頻道篩選每日最有價值的 AI 新聞。

評分標準（總分 10 分）：
- 商業影響力（0-3）：對企業、產業的實際影響有多大？
- 新鮮度（0-2）：是否是最新發布的資訊？
- 故事性（0-2）：是否有清晰的「為什麼重要」的角度？
- 觀眾興趣（0-2）：台灣繁體中文觀眾是否會感興趣？
- 獨家性（0-1）：是否是官方第一手消息？

回傳 JSON 格式（務必是合法 JSON 陣列，每個元素對應輸入的一則新聞，順序一致）：
[
  {
    "id": 原始編號（整數）,
    "score": 0-10 的整數或一位小數,
    "business_angle": "這則新聞最重要的商業意義（繁體中文，30-80字）",
    "why_audience_cares": "台灣觀眾為什麼要看這則（繁體中文，30-80字）",
    "suggested_title": "建議的影片標題（繁體中文，20字內）",
    "skip_reason": null 或 "如果分數低於5，說明為什麼跳過"
  }
]

若分數 < 5 則 skip_reason 必填，其他欄位仍需簡短填寫。
務必回傳純 JSON 陣列，不要包 markdown code block。"""


def _build_user_message(batch: list[Any]) -> str:
    lines = ["請為以下 AI 新聞評分：\n"]
    for row in batch:
        lines.append(f"--- 編號 {row['id']} ---")
        lines.append(f"標題：{row['title']}")
        lines.append(f"來源：{row['source_name']} (優先度 {row['source_priority']}/10)")
        if row.get("published_at"):
            lines.append(f"發布時間：{row['published_at']}")
        if row.get("summary"):
            lines.append(f"摘要：{row['summary'][:500]}")
        lines.append("")
    return "\n".join(lines)


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    """盡力從 Claude 回傳擷取 JSON 陣列。"""
    text = text.strip()
    # 嘗試移除 markdown code fence
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    # 直接找第一個 [ 到最後一個 ]
    if not text.startswith("["):
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1:
            text = text[start:end + 1]
    return json.loads(text)


def score_batch(client: Anthropic, batch: list[Any], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    user_msg = _build_user_message(batch)

    resp = client.messages.create(
        model=cfg["model"],
        max_tokens=cfg["max_tokens"],
        temperature=cfg["temperature"],
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    text = "".join(block.text for block in resp.content if block.type == "text")
    try:
        parsed = _extract_json_array(text)
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Claude 回傳非合法 JSON：{e}\n原文前 500 字：{text[:500]}")
        return []

    return parsed


def run(limit: int = 100) -> dict[str, int]:
    api_key = env("ANTHROPIC_API_KEY", required=True)
    client = Anthropic(api_key=api_key)

    cfg_claude = settings()["claude"]
    cfg_filter = settings()["filter"]
    batch_size = cfg_filter["batch_size"]
    ai_min = cfg_filter["ai_score_min"]

    pending = db_manager.fetch_news_to_score(limit=limit)
    logger.info(f"取出 {len(pending)} 則待評分")
    if not pending:
        return {"scored": 0, "candidates": 0, "skipped": 0}

    all_updates: list[dict[str, Any]] = []

    for i in range(0, len(pending), batch_size):
        batch = pending[i:i + batch_size]
        batch_ids = {row["id"] for row in batch}
        logger.info(f"送 Claude 評分：batch {i // batch_size + 1}（{len(batch)} 則）")

        try:
            results = score_batch(client, batch, cfg_claude)
        except Exception as e:
            logger.error(f"Claude API 例外：{e}")
            continue

        # 建 id -> 原始 row 的 map 以驗證 Claude 回傳
        by_id = {row["id"]: row for row in batch}

        for r in results:
            nid = r.get("id")
            if nid not in by_id:
                logger.warning(f"Claude 回傳了非預期的 id：{nid}")
                continue
            try:
                score = float(r.get("score", 0))
            except (TypeError, ValueError):
                score = 0.0
            status = "candidate" if score >= ai_min else "skipped"
            all_updates.append({
                "id": nid,
                "ai_score": score,
                "business_angle": r.get("business_angle"),
                "why_audience_cares": r.get("why_audience_cares"),
                "suggested_title": r.get("suggested_title"),
                "skip_reason": r.get("skip_reason"),
                "status": status,
            })

    if all_updates:
        db_manager.update_ai_scores(all_updates)

    candidates = sum(1 for u in all_updates if u["status"] == "candidate")
    skipped = len(all_updates) - candidates
    logger.info(f"評分完成：候選 {candidates} / 跳過 {skipped}")

    return {"scored": len(all_updates), "candidates": candidates, "skipped": skipped}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100, help="最多評分幾則")
    args = ap.parse_args()
    stats = run(limit=args.limit)
    print("\n========= Claude 評分結果 =========")
    for k, v in stats.items():
        print(f"  {k:<12} : {v}")
    print("====================================")


if __name__ == "__main__":
    main()
