"""縮圖生成器 — 用 Pillow 生成 YouTube 1280x720 縮圖。

執行：
  python -m modules.image.thumbnail_generator --script data/scripts/xxx/script.json
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from loguru import logger

from modules.common.config import PROJECT_ROOT, settings
from modules.common.logging_setup import setup_logger

setup_logger()

IMAGES_DIR = PROJECT_ROOT / "data" / "images"
THUMB_W, THUMB_H = 1280, 720


def generate_thumbnail(script_path: Path, main_image: Path | None = None) -> Path:
    """生成縮圖，回傳 thumbnail.png 路徑。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise RuntimeError("請安裝 Pillow：pip install Pillow")

    script = json.loads(script_path.read_text(encoding="utf-8"))
    slug = script_path.parent.name
    out_dir = IMAGES_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "thumbnail.png"

    # 取最有衝擊力的標題（第一個）
    title = (script.get("title_options") or ["AI 新聞"])[0]
    # 縮圖只顯示前 15 字
    title_short = title[:15] + ("…" if len(title) > 15 else "")

    # 背景
    bg = Image.new("RGB", (THUMB_W, THUMB_H), (9, 9, 11))  # 深黑背景

    # 主圖（若有）
    if main_image and main_image.exists():
        try:
            hero = Image.open(main_image).convert("RGB")
            # 右半邊放主圖
            hero = hero.resize((700, 720), Image.LANCZOS)
            bg.paste(hero, (580, 0))
            # 漸層遮罩（讓左側文字清晰）
            from PIL import ImageFilter
            mask = Image.new("L", (THUMB_W, THUMB_H), 0)
            md = ImageDraw.Draw(mask)
            for x in range(400, 750):
                alpha = int(255 * (x - 400) / 350)
                md.line([(x, 0), (x, THUMB_H)], fill=alpha)
            bg_overlay = Image.new("RGB", (THUMB_W, THUMB_H), (9, 9, 11))
            bg.paste(bg_overlay, mask=mask)
        except Exception as e:
            logger.warning(f"主圖載入失敗：{e}")

    draw = ImageDraw.Draw(bg)

    # 嘗試載入中文字型，失敗用預設
    font_title = _load_font(72)
    font_sub = _load_font(28)

    # 左側裝飾線
    draw.rectangle([48, 80, 56, 200], fill=(124, 58, 237))  # 紫色豎線

    # 主標題（最多兩行，每行 8 字）
    lines = _wrap_text(title_short, 8)
    y = 100
    for line in lines[:2]:
        draw.text((76, y), line, font=font_title, fill=(250, 250, 250))
        y += 90

    # 副標（來源或 AI 標籤）
    source = script.get("_meta", {}).get("title", "")[:20]
    draw.text((76, y + 20), "AI 商業觀察", font=font_sub, fill=(124, 58, 237))

    # 右下角 logo 區塊
    draw.rectangle([THUMB_W - 180, THUMB_H - 56, THUMB_W - 20, THUMB_H - 16],
                   fill=(124, 58, 237), outline=None)
    draw.text((THUMB_W - 100, THUMB_H - 36), "AI 頻道",
              font=_load_font(22), fill=(255, 255, 255), anchor="mm")

    bg.save(out, "PNG", quality=95)
    logger.info(f"縮圖已存：{out}")
    return out


def _load_font(size: int):
    from PIL import ImageFont
    # Windows 中文字型候選
    candidates = [
        "C:/Windows/Fonts/msjh.ttc",       # 微軟正黑
        "C:/Windows/Fonts/msjhbd.ttc",
        "C:/Windows/Fonts/kaiu.ttf",        # 標楷體
        "C:/Windows/Fonts/mingliu.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",  # Linux
        "/System/Library/Fonts/PingFang.ttc",  # macOS
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap_text(text: str, chars_per_line: int) -> list[str]:
    lines = []
    while text:
        lines.append(text[:chars_per_line])
        text = text[chars_per_line:]
    return lines


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", type=Path, required=True)
    ap.add_argument("--image", type=Path, default=None)
    args = ap.parse_args()
    out = generate_thumbnail(args.script, args.image)
    print(f"[OK] 縮圖：{out}")
