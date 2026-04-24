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
  "estimated_duration": "22分30秒",
  "thumbnail_concept": "縮圖概念：主標關鍵字 + 視覺元素描述",
  "thumbnail_punchline": "縮圖正中央大字（4-8 字，衝擊性口語）",
  "thumbnail_kicker": "縮圖左上小標（≤10 字，選填）",
  "thumbnail_highlight": true,
  "highlight_keywords": ["全域字幕要高亮的關鍵字（5-15 個，繁體中文 2-6 字的名詞或形容詞）"],
  "script_sections": [
    {
      "section_id": 1,
      "type": "hook",
      "timestamp": "0:00",
      "narration": "旁白內容（TTS 朗讀，繁體中文口語）",
      "visual_prompt": "給 AI 生圖的英文 prompt（traditional Chinese colored ink painting 彩墨風格、vibrant saturated colors 鮮豔飽和、golden sunset glow 夕陽金光、flowing watercolor washes 流動色彩、high contrast、16:9；避免 photorealistic/dark/corporate 等字眼）",
      "broll_keywords": ["Pexels 英文搜尋關鍵字（2-4 個，排序：具體→抽象，例如 'Elon Musk speech' → 'rocket launch' → 'futuristic city'）"],
      "highlight_keywords": ["本段字幕要高亮的 1-3 個繁體中文關鍵字"],
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


def _format_sources(research_data: dict) -> str:
    """多來源時列出所有來源，單來源時顯示單一來源名稱。"""
    articles = research_data.get("articles") or []
    if len(articles) > 1:
        lines = [f"  - {a.get('source', '')}：{a.get('title', '')[:50]}" for a in articles]
        return f"{len(articles)} 則報導整合\n" + "\n".join(lines)
    return research_data.get("source") or (articles[0].get("source") if articles else "")


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
- 每 5-6 個 sections 要有明顯轉折或新子題（像章節感）
- 結尾要有明確觀點總結和 CTA
- section type 可以是：hook | background | main_point | data_deep_dive | impact | counterpoint | conclusion | cta
- **每集目標 18-28 分鐘，約 25-30 個 sections**
- **總旁白字數目標 3000-4500 字繁體中文**（約 150 字 / 分鐘）
- main_point / data_deep_dive / impact 可以重複使用多次，對應不同子題
- 禁止空泛填充；每個 section 必須傳遞新資訊或推進論述
- 如果研究資料含多篇來源，必須在適當 sections 提到來源比對（例：「Bloomberg 說 X、Reuters 說 Y」）

=== B-roll 素材規範（重要）===
每個 section 必須提供 `broll_keywords`（英文，給 Pexels 搜尋用）：
- 2-4 個關鍵字，從具體到抽象排序（找不到具體的會退回抽象）
- 優先真實事物（人物、地點、產品）而非概念詞
- 範例：
  - 聊 Elon Musk：["Elon Musk interview", "SpaceX rocket", "Tesla factory"]
  - 聊 AI 晶片：["Nvidia GPU closeup", "semiconductor chip", "data center server"]
  - 聊股價崩跌：["stock market crash", "trader screen red", "falling chart"]
- 避免難搜尋的組合詞（如 "AI business disruption"），拆成單一可搜的畫面

=== 字幕高亮規範 ===
- `highlight_keywords`（全域 + 各段）：字幕畫面中要放大變色的關鍵字
- 挑選原則：最有資訊量、最能打中觀眾情緒的名詞或短語（2-6 字）
- 範例：「殘忍」「暴跌 40%」「全世界第一」「秒殺」「馬斯克」

=== 縮圖規範 ===
- `thumbnail_punchline`：正中央大字，4-8 字，用最口語最衝擊的句子（不是標題）
  - 好：「全被騙了」「真相曝光」「一夕崩盤」
  - 不好：「探討馬斯克的商業策略」
- `thumbnail_kicker`：選填的左上小標（例：「獨家解析」「深度」）

=== 輸出格式 ===
請輸出合法 JSON，不要包在 markdown code block 裡，直接輸出 {{ 開頭的 JSON：

{SCRIPT_FORMAT}

---

請根據以下研究摘要，生成完整的 YouTube 腳本：

**主題**：{research_data.get('title', '')}
**來源**：{_format_sources(research_data)}

**研究摘要**：
{research_data['research_text']}

**商業意義**：{research_data.get('business_angle', '')}
**台灣觀眾關注點**：{research_data.get('why_audience_cares', '')}

請生成完整腳本 JSON（直接輸出 JSON，不要加任何說明文字或 markdown）。"""

    prompt_file = research_path.parent / "script_prompt.md"
    prompt_file.write_text(prompt, encoding="utf-8")
    logger.info(f"腳本 prompt 已存至：{prompt_file}")

    return prompt, prompt_file


def save_script(json_text: str, out_dir: Path, news_id: int | None = None,
                 *, topic_id: int | None = None,
                 source_news_ids: list[int] | None = None) -> Path:
    """
    解析 Claude Code 回傳的 JSON，加入 _meta 後存成 script.json，
    同時寫入 DB（ScriptRecord）。回傳 script.json 路徑。
    """
    script = _extract_json(json_text)

    # 讀 news_meta 補齊 news_id/topic_id
    meta_file = out_dir / "news_meta.json"
    meta = {}
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    if news_id is None:
        news_id = meta.get("news_id")
    if topic_id is None:
        topic_id = meta.get("topic_id")
    if source_news_ids is None:
        source_news_ids = meta.get("news_ids")

    script["_meta"] = {
        "news_id": news_id,
        "topic_id": topic_id,
        "news_ids": source_news_ids,
        "title": script.get("title_options", [""])[0],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    out_path = out_dir / "script.json"
    out_path.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"腳本已存至：{out_path}")

    # 寫入 DB
    try:
        from modules.database import db_manager
        research = None
        research_file = out_dir / "research.json"
        if research_file.exists():
            research = json.loads(research_file.read_text(encoding="utf-8"))
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        db_manager.save_script(
            date_str, news_id, script, research,
            topic_id=topic_id, source_news_ids=source_news_ids,
        )
        logger.info("腳本已同步寫入 DB")
    except Exception as e:
        logger.warning(f"寫入 DB 失敗（不影響本地檔案）：{e}")

    return out_path


def _extract_json(text: str) -> dict:
    text = text.strip()
    # 去除 markdown code block
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    # 找 JSON 邊界
    if not text.startswith("{"):
        start = text.find("{")
        end   = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start : end + 1]
    return json.loads(text)


def auto_write_script(research_path: Path, *, slug: str | None = None) -> Path:
    """
    用 claude CLI（-p 模式）自動生成腳本 JSON，不需要 API Key。
    使用 Claude Max 訂閱，等同於 CoWork。slug 有值時寫進度。
    回傳 script.json 路徑。
    """
    from modules.common.claude_cli import run as claude_run

    def _p(msg: str) -> None:
        logger.info(msg)
        if slug:
            try:
                from modules.database import db_manager
                db_manager.update_episode_progress(slug, msg)
            except Exception:
                pass

    prompt, _ = export_prompt(research_path)
    cli_prompt = prompt + "\n\n重要：只輸出 JSON 本身，不要任何說明文字、markdown 或 code block。"

    # 多篇研究需要更長的 timeout（Claude 要寫 25-30 sections）
    research_data = json.loads(research_path.read_text(encoding="utf-8"))
    is_multi = len(research_data.get("news_ids") or []) > 1
    timeout = 900 if is_multi else 600
    mode_label = '長片（25-30 段）' if is_multi else '短片'
    _p(f"✍️ 呼叫 claude CLI（{mode_label}，timeout={timeout}s）…")
    raw = claude_run(
        cli_prompt, timeout=timeout,
        slug=slug, heartbeat_msg=f"✍️ Claude 生成腳本中（{mode_label}）",
    )
    _p(f"✍️ 已收到 claude 輸出（{len(raw)} 字），解析 JSON…")

    news_id = research_data.get("news_id")
    topic_id = research_data.get("topic_id")
    news_ids = research_data.get("news_ids")
    try:
        out_path = save_script(
            raw, research_path.parent, news_id,
            topic_id=topic_id, source_news_ids=news_ids,
        )
    except Exception as e:
        raise RuntimeError(f"JSON 解析失敗，claude 輸出前500字：{raw[:500]}") from e
    _p(f"✅ 腳本已存：{out_path.name}")
    return out_path


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
