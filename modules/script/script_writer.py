"""腳本生成模組 — CoWork 模式：輸出 prompt 供 Claude Code 生成腳本 JSON，不呼叫 API。

流程：
  1. export_prompt(research_path) → 輸出腳本生成 prompt 給 Claude Code
  2. 使用者將 Claude Code 的回答（JSON）貼回 Web UI
  3. save_script(json_text, out_dir) → 存成 script.json 並寫入 DB

執行：
  python -m modules.script.script_writer --research data/scripts/20260416_xxx/research.json
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from modules.common.config import PROJECT_ROOT
from modules.common.logging_setup import setup_logger

setup_logger()

SCRIPTS_DIR = PROJECT_ROOT / "data" / "scripts"
STYLE_GUIDE = PROJECT_ROOT / "config" / "style_guide.md"


def _load_style_guide() -> str:
    if STYLE_GUIDE.exists():
        return STYLE_GUIDE.read_text(encoding="utf-8")
    return "（風格指南未設定，請填寫 config/style_guide.md）"


SCRIPT_FORMAT = """{
  "title_options": ["標題A（20字內）", "標題B", "標題C", "標題D", "標題E"],
  "estimated_duration": "8分30秒",
  "thumbnail_concept": "縮圖概念：主標關鍵字 + 視覺元素描述",
  "script_sections": [
    {
      "section_id": 1,
      "type": "hook",
      "timestamp": "0:00",
      "narration": "旁白內容（TTS 朗讀）",
      "visual_prompt": "給 ComfyUI 的英文圖片生成 prompt（科技感商業風格）",
      "screen_note": "[畫面：說明]",
      "duration_seconds": 30
    }
  ],
  "youtube_description": "完整 YouTube 描述（含時間軸和標籤）",
  "tags": ["AI", "人工智慧", "其他標籤"],
  "shorts_script": "60秒 Shorts 版本腳本（純旁白文字）",
  "social_posts": {
    "twitter_thread": ["推文1（280字內）", "推文2", "推文3"],
    "linkedin_post": "LinkedIn 長文（繁體中文）",
    "ig_caption": "IG 說明文字 + hashtag"
  }
}"""


def export_prompt(research_path: Path) -> tuple[str, Path]:
    """
    根據 research.json 生成腳本 prompt，存成 script_prompt.md。
    回傳 (prompt_text, script_prompt_path)。
    """
    research_data = json.loads(research_path.read_text(encoding="utf-8"))
    style_guide = _load_style_guide()

    prompt = f"""你是一個繁體中文科技 YouTube 頻道的金牌腳本作家。

{style_guide}

=== 腳本格式規範 ===
- 每個段落後標記預估秒數 [0:00]
- 旁白（TTS 要念的）直接寫
- 畫面說明用 [畫面：...] 標記
- 開頭 30 秒必須有強烈 Hook
- 每 90 秒要有轉折或新資訊
- 結尾要有明確觀點總結和 CTA
- section type 可以是：hook | background | main_point | data_deep_dive | impact | counterpoint | conclusion | cta
- 每集目標 8-12 分鐘，約 8-12 個 sections

=== 輸出格式 ===
請輸出合法 JSON，不要包在 markdown code block 裡，直接輸出 {{ 開頭的 JSON：

{SCRIPT_FORMAT}

---

請根據以下研究摘要，生成完整的 YouTube 腳本：

**主題**：{research_data['title']}
**來源**：{research_data['source']}

**研究摘要**：
{research_data['research_text']}

**商業意義**：{research_data.get('business_angle', '')}
**台灣觀眾關注點**：{research_data.get('why_audience_cares', '')}

請生成完整腳本 JSON（直接輸出 JSON，不要加任何說明文字或 markdown）。"""

    prompt_file = research_path.parent / "script_prompt.md"
    prompt_file.write_text(prompt, encoding="utf-8")
    logger.info(f"腳本 prompt 已存至：{prompt_file}")

    return prompt, prompt_file


def save_script(json_text: str, out_dir: Path, news_id: int | None = None) -> Path:
    """
    解析 Claude Code 回傳的 JSON，加入 _meta 後存成 script.json，
    同時寫入 DB（ScriptRecord）。回傳 script.json 路徑。
    """
    script = _extract_json(json_text)

    # 補充 _meta
    if news_id is None:
        meta_file = out_dir / "news_meta.json"
        if meta_file.exists():
            news_id = json.loads(meta_file.read_text(encoding="utf-8")).get("news_id")

    script["_meta"] = {
        "news_id": news_id,
        "title": script.get("title_options", [""])[0],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    out_path = out_dir / "script.json"
    out_path.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"腳本已存至：{out_path}")

    # 寫入 DB
    try:
        from modules.database import db_manager
        db_manager.save_script(script)
        logger.info("腳本已同步寫入 DB")
    except Exception as e:
        logger.warning(f"寫入 DB 失敗（不影響本地檔案）：{e}")

    return out_path


def _extract_json(text: str) -> dict:
    text = text.strip()
    # 去除 markdown code block
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    # 找 JSON 邊界
    if not text.startswith("{"):
        start = text.find("{")
        end   = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start : end + 1]
    return json.loads(text)


def run(research_path: Path | None = None, news_id: int | None = None) -> Path:
    """主入口：生成腳本 prompt 並等待 CoWork 輸入。"""
    if research_path is None and news_id is not None:
        matches = sorted(SCRIPTS_DIR.glob("**/research.json"), reverse=True)
        candidates = [
            p for p in matches
            if f'"news_id": {news_id}' in p.read_text(encoding="utf-8")
        ]
        if not candidates:
            raise FileNotFoundError(
                f"找不到 news_id={news_id} 的研究摘要，請先在 Web UI 完成「研究」步驟"
            )
        research_path = candidates[0]

    if research_path is None:
        raise ValueError("需要指定 --research 或 --news-id")

    prompt, prompt_file = export_prompt(research_path)
    return prompt_file


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--research", type=Path, help="research.json 路徑")
    ap.add_argument("--news-id", type=int, help="自動找對應的 research.json")
    ap.add_argument("--import-json", type=str, help="直接匯入腳本 JSON 文字（測試用）")
    args = ap.parse_args()

    if args.import_json:
        research_path = args.research or next(
            iter(sorted(SCRIPTS_DIR.glob("**/research.json"), reverse=True)), None
        )
        if not research_path:
            print("[ERROR] 找不到 research.json")
            return
        out = save_script(args.import_json, research_path.parent, args.news_id)
        print(f"[OK] 腳本已存：{out}")
        return

    prompt_file = run(research_path=args.research, news_id=args.news_id)
    print(f"\n{'='*60}")
    print(f"腳本 prompt 已存至：{prompt_file}")
    print("請複製 prompt 貼給 Claude Code，取得 JSON 後在 Web UI /cowork/script 頁面匯入")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
