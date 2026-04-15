"""腳本生成模組 — 用 Claude 生成完整結構化腳本 JSON。

輸入：research.json（researcher.py 輸出）
輸出：script.json（後續 TTS、ComfyUI、FFmpeg 使用）

執行：
  python -m modules.script.script_writer --research data/scripts/20260416_xxx/research.json
  python -m modules.script.script_writer --news-id 393   # 自動找最新 research
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic
from loguru import logger

from modules.common.config import PROJECT_ROOT, env, settings
from modules.common.logging_setup import setup_logger

setup_logger()

SCRIPTS_DIR = PROJECT_ROOT / "data" / "scripts"
STYLE_GUIDE = PROJECT_ROOT / "config" / "style_guide.md"


def _load_style_guide() -> str:
    if STYLE_GUIDE.exists():
        return STYLE_GUIDE.read_text(encoding="utf-8")
    return "（風格指南未設定，請填寫 config/style_guide.md）"


SYSTEM_PROMPT_TEMPLATE = """你是一個繁體中文科技 YouTube 頻道的金牌腳本作家。

{style_guide}

=== 腳本格式規範 ===
- 每個段落後標記預估秒數 [0:00]
- 旁白（TTS 要念的）直接寫
- 畫面說明用 [畫面：...] 標記
- 開頭 30 秒必須有強烈 Hook
- 每 90 秒要有轉折或新資訊
- 結尾要有明確觀點總結和 CTA

=== 輸出格式 ===
請輸出合法 JSON，結構如下（不要包在 markdown code block 裡）：

{{
  "title_options": ["標題A（20字內）", "標題B", "標題C", "標題D", "標題E"],
  "estimated_duration": "8分30秒",
  "thumbnail_concept": "縮圖概念：主標關鍵字 + 視覺元素描述",
  "script_sections": [
    {{
      "section_id": 1,
      "type": "hook",
      "timestamp": "0:00",
      "narration": "旁白內容（TTS 朗讀）",
      "visual_prompt": "給 ComfyUI 的英文圖片生成 prompt（科技感商業風格）",
      "screen_note": "[畫面：說明]",
      "duration_seconds": 30
    }}
  ],
  "youtube_description": "完整 YouTube 描述（含時間軸和標籤）",
  "tags": ["AI", "人工智慧", "其他標籤"],
  "shorts_script": "60秒 Shorts 版本腳本（純旁白文字）",
  "social_posts": {{
    "twitter_thread": ["推文1（280字內）", "推文2", "推文3"],
    "linkedin_post": "LinkedIn 長文（繁體中文）",
    "ig_caption": "IG 說明文字 + hashtag"
  }}
}}

section type 可以是：hook | background | main_point | data_deep_dive | impact | counterpoint | conclusion | cta
每集目標 8-12 分鐘，約 8-12 個 sections。"""


def write_script(research_data: dict) -> dict:
    """根據研究摘要生成完整腳本。"""
    api_key = env("ANTHROPIC_API_KEY", required=True)
    client = Anthropic(api_key=api_key)
    cfg = settings()["claude"]

    style_guide = _load_style_guide()
    system = SYSTEM_PROMPT_TEMPLATE.format(style_guide=style_guide)

    user_msg = f"""請根據以下研究摘要，生成完整的 YouTube 腳本：

**主題**：{research_data['title']}
**來源**：{research_data['source']}

**研究摘要**：
{research_data['research_text']}

**商業意義**：{research_data.get('business_angle', '')}
**台灣觀眾關注點**：{research_data.get('why_audience_cares', '')}

請生成完整腳本 JSON。"""

    logger.info(f"開始生成腳本：{research_data['title']}")

    resp = client.messages.create(
        model=cfg["model"],
        max_tokens=8000,
        temperature=0.7,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )

    text = "".join(b.text for b in resp.content if b.type == "text")

    try:
        script = _extract_json(text)
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"JSON 解析失敗：{e}")
        # 把原始文字也存下來，方便手動修復
        script = {"_raw": text, "_parse_error": str(e)}

    script["_meta"] = {
        "news_id": research_data["news_id"],
        "title": research_data["title"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    return script


def _extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start:end + 1]
    return json.loads(text)


def run(research_path: Path | None = None, news_id: int | None = None) -> Path:
    """主入口，回傳 script.json 路徑。"""
    if research_path is None and news_id is not None:
        # 找最新的 research.json
        matches = sorted(SCRIPTS_DIR.glob(f"**/research.json"), reverse=True)
        candidates = [p for p in matches if f"news_id\": {news_id}" in p.read_text(encoding="utf-8")]
        if not candidates:
            raise FileNotFoundError(f"找不到 news_id={news_id} 的研究摘要，請先執行 researcher.py")
        research_path = candidates[0]

    if research_path is None:
        raise ValueError("需要指定 --research 或 --news-id")

    research_data = json.loads(research_path.read_text(encoding="utf-8"))
    script = write_script(research_data)

    out = research_path.parent / "script.json"
    out.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"腳本已存至：{out}")

    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--research", type=Path, help="research.json 路徑")
    ap.add_argument("--news-id", type=int, help="自動找對應的 research.json")
    args = ap.parse_args()

    out = run(research_path=args.research, news_id=args.news_id)

    script = json.loads(out.read_text(encoding="utf-8"))
    print(f"\n[OK] 腳本生成完成：{out}")
    print(f"預估長度：{script.get('estimated_duration', '—')}")
    print("標題選項：")
    for t in script.get("title_options", []):
        print(f"  - {t}")
    sections = script.get("script_sections", [])
    print(f"共 {len(sections)} 個段落")


if __name__ == "__main__":
    main()
