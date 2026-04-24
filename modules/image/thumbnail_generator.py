"""縮圖生成器 — YouTube 1280x720 點擊率優化縮圖。

設計風格（商業本質 / 老高風格）：
  - 全版人物 / 情境背景圖
  - 左下深色漸層遮罩
  - 巨大黃色粗體標題（6-8 字衝擊句），黑色描邊
  - 紅色圓圈 + 箭頭強調視覺重點（選用）
  - 右上角頻道標籤

Script 可自帶：
  - thumbnail_punchline: "6 字衝擊句"（沒填就從 title 取前 8 字）
  - thumbnail_highlight: true 是否加紅圈

執行：
  python -m modules.image.thumbnail_generator --script data/scripts/xxx/script.json
  python -m modules.image.thumbnail_generator --script ... --image path/to/hero.png
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

from loguru import logger

from modules.common.config import PROJECT_ROOT, settings
from modules.common.logging_setup import setup_logger

setup_logger()

IMAGES_DIR = PROJECT_ROOT / "data" / "images"
THUMB_W, THUMB_H = 1280, 720

# 配色候選（每次隨機選一組，保持頻道多樣性）
PALETTES = [
    {"accent": (255, 214, 0),   "sub": (255, 255, 255), "ring": (230, 30, 30)},  # 黃 + 白 + 紅圈
    {"accent": (255, 235, 59),  "sub": (0, 229, 255),   "ring": (255, 23, 68)},  # 黃 + 青 + 紅
    {"accent": (255, 80, 80),   "sub": (255, 255, 255), "ring": (255, 215, 0)},  # 紅 + 白 + 金圈
    {"accent": (255, 255, 255), "sub": (255, 214, 0),   "ring": (229, 57, 53)},  # 白 + 黃 + 紅
]


def _load_font(size: int):
    from PIL import ImageFont
    candidates = [
        "C:/Windows/Fonts/msjhbd.ttc",       # 微軟正黑粗
        "C:/Windows/Fonts/msjh.ttc",         # 微軟正黑
        "C:/Windows/Fonts/kaiu.ttf",
        "C:/Windows/Fonts/mingliu.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap_chinese(text: str, chars_per_line: int) -> list[str]:
    lines: list[str] = []
    remaining = text
    while remaining:
        lines.append(remaining[:chars_per_line])
        remaining = remaining[chars_per_line:]
    return lines


def _draw_stroked_text(draw, xy, text, font, fill, stroke_fill, stroke_width=6, anchor=None):
    x, y = xy
    # Pillow 9+ 原生支援 stroke_width，直接用
    draw.text((x, y), text, font=font, fill=fill,
              stroke_width=stroke_width, stroke_fill=stroke_fill, anchor=anchor)


def _apply_cover_scale(img, target_w: int, target_h: int):
    """圖片以 cover 模式縮放裁切到 target 尺寸。"""
    iw, ih = img.size
    scale = max(target_w / iw, target_h / ih)
    nw, nh = int(iw * scale), int(ih * scale)
    img = img.resize((nw, nh))
    left = (nw - target_w) // 2
    top = (nh - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def _punchline_from_script(script: dict) -> str:
    """決定縮圖大字。優先序：thumbnail_punchline > title 擷取。"""
    punch = (script.get("thumbnail_punchline") or "").strip()
    if punch:
        return punch[:10]

    titles = script.get("title_options") or []
    title = titles[0] if titles else "AI 新聞"

    # 嘗試從標題中擷取最有衝擊的詞（含標點斷句）
    segments = re.split(r"[，。！？、：:｜\|\-—\s]+", title)
    segments = [s for s in segments if s.strip()]
    if segments:
        # 選最長但 ≤ 10 字的
        candidates = sorted([s for s in segments if 2 <= len(s) <= 10], key=len, reverse=True)
        if candidates:
            return candidates[0]
    return title[:8]


def generate_thumbnail(script_path: Path, main_image: Path | None = None) -> Path:
    """生成縮圖，回傳 thumbnail.png。"""
    try:
        from PIL import Image, ImageDraw, ImageFilter
    except ImportError:
        raise RuntimeError("請安裝 Pillow：pip install Pillow")

    script = json.loads(script_path.read_text(encoding="utf-8"))
    slug = script_path.parent.name
    out_dir = IMAGES_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "thumbnail.png"

    cfg = settings().get("thumbnail", {})
    channel_name = cfg.get("channel_label", settings().get("youtube", {}).get("channel_name", "AI 商業觀察"))
    show_ring = script.get("thumbnail_highlight", True)

    palette = random.choice(PALETTES)
    accent = palette["accent"]
    ring = palette["ring"]

    # 背景：優先 main_image → section 1 → 黑底
    if not main_image:
        candidates = sorted((IMAGES_DIR / slug).glob("section_*.png"))
        main_image = candidates[0] if candidates else None

    bg = Image.new("RGB", (THUMB_W, THUMB_H), (9, 9, 11))
    if main_image and main_image.exists():
        try:
            hero = Image.open(main_image).convert("RGB")
            hero = _apply_cover_scale(hero, THUMB_W, THUMB_H)
            bg.paste(hero, (0, 0))
        except Exception as e:
            logger.warning(f"主圖載入失敗：{e}")

    # 左側 → 右側漸層遮罩（讓左下文字區極暗）
    overlay = Image.new("RGBA", (THUMB_W, THUMB_H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    # 水平漸層：左側大面積壓黑，確保字可讀
    for x in range(THUMB_W):
        if x < THUMB_W * 0.45:
            a_h = 220
        elif x < THUMB_W * 0.7:
            # 從 220 漸變到 0
            frac = (x - THUMB_W * 0.45) / (THUMB_W * 0.25)
            a_h = int(220 * (1 - frac) ** 1.3)
        else:
            a_h = 0
        if a_h > 0:
            od.line([(x, 0), (x, THUMB_H)], fill=(0, 0, 0, a_h))
    # 疊加：整體底部再壓一層暗色
    bottom = Image.new("RGBA", (THUMB_W, THUMB_H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bottom)
    for y in range(THUMB_H):
        a_v = int(160 * (y / THUMB_H) ** 1.8)
        if a_v > 0:
            bd.line([(0, y), (THUMB_W, y)], fill=(0, 0, 0, a_v))
    bg = Image.alpha_composite(bg.convert("RGBA"), overlay)
    bg = Image.alpha_composite(bg, bottom).convert("RGB")

    draw = ImageDraw.Draw(bg)

    # ── 紅色圓圈強調（畫面右側中上） ──
    if show_ring:
        cx = int(THUMB_W * 0.78)
        cy = int(THUMB_H * 0.42)
        r  = 120
        for i in range(8):
            draw.ellipse(
                [cx - r - i, cy - r - i, cx + r + i, cy + r + i],
                outline=ring,
                width=1,
            )
        # 箭頭 → 從文字區指向圓圈
        ax, ay = int(THUMB_W * 0.55), int(THUMB_H * 0.52)
        bx, by = cx - r - 10, cy + r // 2
        draw.line([(ax, ay), (bx, by)], fill=ring, width=8)
        # 箭頭頭部三角
        draw.polygon([
            (bx, by),
            (bx - 28, by - 14),
            (bx - 28, by + 14),
        ], fill=ring)

    # ── 主衝擊字 ──
    punch = _punchline_from_script(script)
    font_sub  = _load_font(36)
    font_label = _load_font(28)

    # 依字數自動挑字級與換行
    n = len(punch)
    if n <= 4:
        size, chars_per_line = 200, max(n, 1)   # 一行
    elif n <= 6:
        size, chars_per_line = 160, n            # 一行
    elif n <= 8:
        size, chars_per_line = 130, 4            # 兩行，每行 4
    else:
        size, chars_per_line = 110, 5            # 兩行，每行 5
    font_main = _load_font(size)
    lines = _wrap_chinese(punch, chars_per_line)[:2]

    line_h = int(size * 1.05)
    total_h = line_h * len(lines)
    start_y = THUMB_H - total_h - 100

    for i, line in enumerate(lines):
        _draw_stroked_text(
            draw,
            (60, start_y + i * line_h),
            line,
            font_main,
            fill=accent,
            stroke_fill=(0, 0, 0),
            stroke_width=max(8, size // 18),
        )

    # ── 上方醒目小標（選填）──
    kicker = (script.get("thumbnail_kicker") or "").strip()[:16]
    if kicker:
        _draw_stroked_text(
            draw, (60, 60), kicker, font_sub,
            fill=(255, 255, 255), stroke_fill=(0, 0, 0), stroke_width=4,
        )

    # ── 右上角頻道標籤 ──
    label_pad = 14
    label_text = channel_name[:10]
    tw = draw.textlength(label_text, font=font_label)
    label_w = int(tw + label_pad * 2)
    label_h = 46
    lx1 = THUMB_W - label_w - 24
    ly1 = 24
    draw.rectangle([lx1, ly1, lx1 + label_w, ly1 + label_h],
                   fill=(124, 58, 237))
    draw.text((lx1 + label_pad, ly1 + label_h // 2), label_text,
              font=font_label, fill=(255, 255, 255), anchor="lm")

    bg.save(out, "PNG", quality=95)
    logger.info(f"縮圖已存：{out}（punch='{punch}'）")
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", type=Path, required=True)
    ap.add_argument("--image", type=Path, default=None)
    args = ap.parse_args()
    out = generate_thumbnail(args.script, args.image)
    print(f"[OK] 縮圖：{out}")
