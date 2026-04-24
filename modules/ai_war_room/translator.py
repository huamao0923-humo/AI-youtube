"""新聞標題批次翻譯 — 透過本地 Claude CLI 翻譯為繁體中文。

不呼叫 Anthropic API（不需 ANTHROPIC_API_KEY）。走 subprocess 呼叫 node cli.js。

批次策略：一次送 N 條標題（編號列表），CLI 回 JSON array，寫回 title_zh。
防重：translated_at IS NULL 才處理；force=True 全部重翻。

用法：
    python -m modules.ai_war_room.translator               # 翻未翻譯的 AI 新聞
    python -m modules.ai_war_room.translator --limit 200   # 最多 200 筆
    python -m modules.ai_war_room.translator --all         # 忽略 translated_at 全翻
    python -m modules.ai_war_room.translator --stats
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone

from modules.common.claude_cli import run as claude_run
from modules.database.models import NewsItem, SessionLocal


# 每批送多少條標題。太多 context 太長、太少 overhead 高。實測 20 左右最佳。
BATCH_SIZE = 20
# CLI 每批 timeout（秒）
CLI_TIMEOUT = 180


_PROMPT_TEMPLATE = """你是新聞標題翻譯器。把下列英文 AI 新聞標題翻譯成**繁體中文（台灣用語）**，風格要精簡、像科技媒體標題。

規則：
- 保留英文專有名詞不翻（GPT、Claude、Gemini、OpenAI、Anthropic、NVIDIA、AWS、Apple、Meta、Google、LLM、RAG、API、RLHF、GPU、CPU 等）
- 已經是中文的標題原樣回傳
- 每條翻譯控制在 60 字內，不要加多餘說明或標點
- 不要加引號或前後綴

輸入是一個 JSON array，每個元素 {{"i": 編號, "t": 原標題}}。
**只**輸出一個 JSON array，每個元素 {{"i": 同編號, "z": 翻譯}}，不要任何額外文字、markdown、思考過程。

輸入：
{payload}
"""


def _extract_json_array(text: str) -> list | None:
    """從 CLI 回應萃取 JSON array。CLI 可能夾帶 think 痕跡或 markdown。"""
    if not text:
        return None
    # 先嘗試直接 parse
    try:
        data = json.loads(text.strip())
        return data if isinstance(data, list) else None
    except json.JSONDecodeError:
        pass
    # 找 ```json ... ``` 區塊
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 找第一個最大的 [ ... ]
    m = re.search(r"(\[[\s\S]*\])", text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
    return None


def _translate_batch(items: list[dict]) -> dict[int, str]:
    """items = [{"i": idx, "t": title}, ...] → {idx: translation}"""
    if not items:
        return {}
    payload = json.dumps(items, ensure_ascii=False)
    prompt = _PROMPT_TEMPLATE.format(payload=payload)
    try:
        out = claude_run(prompt, timeout=CLI_TIMEOUT)
    except Exception as e:
        from loguru import logger
        logger.warning(f"[translator] CLI 呼叫失敗：{e}")
        return {}
    arr = _extract_json_array(out)
    if not isinstance(arr, list):
        return {}
    result: dict[int, str] = {}
    for el in arr:
        if not isinstance(el, dict):
            continue
        i = el.get("i")
        z = el.get("z")
        if isinstance(i, int) and isinstance(z, str) and z.strip():
            result[i] = z.strip()
    return result


def translate(limit: int = 500, force: bool = False, batch_size: int = BATCH_SIZE) -> dict[str, int]:
    from loguru import logger
    now = datetime.now(timezone.utc).isoformat()
    done = 0
    failed = 0

    with SessionLocal() as s:
        q = s.query(NewsItem).filter(NewsItem.is_ai == 1)
        if not force:
            q = q.filter(NewsItem.translated_at.is_(None))
        q = q.order_by(NewsItem.published_at.desc().nullslast(),
                       NewsItem.fetched_at.desc().nullslast()).limit(limit)
        rows = q.all()
        if not rows:
            return {"processed": 0, "failed": 0}

        logger.info(f"[translator] 待翻譯 {len(rows)} 筆，batch={batch_size}")
        for start in range(0, len(rows), batch_size):
            batch_rows = rows[start:start + batch_size]
            payload = [{"i": r.id, "t": r.title or ""} for r in batch_rows if r.title]
            if not payload:
                continue
            result = _translate_batch(payload)
            if not result:
                failed += len(payload)
                logger.warning(f"[translator] 批次 {start // batch_size + 1} 全失敗")
                continue
            for r in batch_rows:
                z = result.get(r.id)
                if z:
                    r.title_zh = z
                    r.translated_at = now
                    done += 1
            s.commit()
            logger.info(f"[translator] 累計 {done} 筆 / 失敗 {failed}")
    return {"processed": done, "failed": failed}


def stats() -> None:
    with SessionLocal() as s:
        total = s.query(NewsItem).filter(NewsItem.is_ai == 1).count()
        translated = s.query(NewsItem).filter(NewsItem.is_ai == 1,
                                              NewsItem.title_zh.isnot(None)).count()
        print(f"AI 新聞：{total} 筆 | 已翻譯：{translated} | 未翻：{total - translated}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--batch", type=int, default=BATCH_SIZE)
    ap.add_argument("--all", action="store_true", help="忽略 translated_at 全翻")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    if args.stats:
        stats()
        return
    r = translate(limit=args.limit, force=args.all, batch_size=args.batch)
    print(f"[OK] translator: {r}")
    stats()


if __name__ == "__main__":
    main()
