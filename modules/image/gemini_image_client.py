"""AI 圖片生成客戶端。

優先順序：
  1. Gemini Imagen 4（需 GOOGLE_API_KEY + 付費方案，最高品質）
  2. Pollinations.ai（完全免費，Flux 模型，無需 API Key）
  3. 文字卡片 fallback（Pillow）

執行：
  python -m modules.image.gemini_image_client --script data/scripts/xxx/script.json
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

from loguru import logger

from modules.common.config import PROJECT_ROOT
from modules.common.logging_setup import setup_logger

setup_logger()

IMAGES_DIR = PROJECT_ROOT / "data" / "images"
_IMAGEN_MODEL = "imagen-4.0-fast-generate-001"


def _api_key() -> str | None:
    return os.getenv("GOOGLE_API_KEY", "").strip() or None


def is_available() -> bool:
    """Pollinations.ai 免費可用，永遠回傳 True（不需要 API Key）。"""
    return True


def _generate_via_imagen(prompt: str, out_path: Path) -> bool:
    """Gemini Imagen 4（付費方案）。"""
    key = _api_key()
    if not key:
        return False
    try:
        from google import genai
        from google.genai import types as gtypes

        client = genai.Client(api_key=key)
        response = client.models.generate_images(
            model=_IMAGEN_MODEL,
            prompt=prompt,
            config=gtypes.GenerateImagesConfig(number_of_images=1, aspect_ratio="16:9"),
        )
        if response.generated_images:
            out_path.write_bytes(response.generated_images[0].image.image_bytes)
            return True
        return False
    except Exception as e:
        logger.debug(f"Imagen 不可用（可能需付費方案）：{e}")
        return False


def _generate_via_pollinations(prompt: str, out_path: Path) -> bool:
    """Pollinations.ai 免費 Flux 生圖（無需 API Key）。"""
    try:
        full_prompt = (
            f"{prompt}, traditional Chinese colored ink painting (彩墨), "
            "vibrant saturated colors, luminous golden sunset glow, "
            "rich turquoise and magenta washes, high contrast, glowing highlights, "
            "flowing watercolor brush strokes, masterpiece quality, 16:9 aspect ratio"
        )
        encoded = urllib.parse.quote(full_prompt)
        url = (
            f"https://image.pollinations.ai/prompt/{encoded}"
            "?width=2048&height=1152&model=flux&nologo=true&enhance=true&nofeed=true"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        if len(data) > 5000:
            out_path.write_bytes(data)
            return True
        logger.warning("Pollinations 回傳資料過小，可能為錯誤")
        return False
    except Exception as e:
        logger.error(f"Pollinations 失敗：{e}")
        return False


def generate_image(prompt: str, out_path: Path) -> bool:
    """生成單張圖片：先嘗試 Imagen，失敗改用 Pollinations。"""
    if _generate_via_imagen(prompt, out_path):
        logger.debug("Imagen 生成成功")
        return True
    logger.info("改用 Pollinations.ai（免費 Flux）生成")
    return _generate_via_pollinations(prompt, out_path)


def generate_images(script_path: Path) -> list[Path]:
    """讀取 script.json，為每個段落生成圖片，回傳路徑清單。"""
    script = json.loads(script_path.read_text(encoding="utf-8"))
    sections = script.get("script_sections", [])
    slug = script_path.parent.name
    out_dir = IMAGES_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    total = len(sections)
    try:
        from modules.database import db_manager as _db
    except Exception:
        _db = None

    paths: list[Path] = []
    for i, sec in enumerate(sections, 1):
        sid = sec["section_id"]
        prompt = sec.get("visual_prompt", f"AI technology news section {sid}")
        out = out_dir / f"section_{sid:03d}.png"

        if _db:
            _db.update_progress(f"生成第 {i}/{total} 張圖片…（{sec.get('type','')}: {prompt[:30]}）")

        if out.exists():
            logger.info(f"段落 {sid} 圖片已存在，跳過")
            paths.append(out)
            continue

        logger.info(f"段落 {sid} 生成中… prompt: {prompt[:50]}")
        success = generate_image(prompt, out)
        if success:
            logger.info(f"段落 {sid} 圖片已存：{out.name}")
        else:
            logger.warning(f"段落 {sid} 生成失敗，改用文字卡片")
            _make_text_card(sid, sec, out)

        paths.append(out)
        time.sleep(1)  # 避免 rate limit

    if _db:
        _db.update_progress(f"全部 {total} 張圖片完成")
    logger.info(f"圖片生成完成：{len(paths)} 張，存於 {out_dir}")
    return paths


def _make_text_card(section_id: int, sec: dict, out_path: Path) -> None:
    """最終 fallback：Pillow 文字卡片。"""
    try:
        from PIL import Image, ImageDraw

        W, H = 1920, 1080
        img = Image.new("RGB", (W, H), color=(10, 12, 20))
        draw = ImageDraw.Draw(img)

        label_colors = {
            "hook": (255, 80, 60),
            "main": (60, 160, 255),
            "cta": (60, 220, 120),
        }
        sec_type = sec.get("type", "").lower()
        label_color = label_colors.get(sec_type, (150, 150, 180))
        label_text = sec_type.upper() or f"#{section_id:02d}"

        draw.rectangle([(80, 80), (80 + len(label_text) * 18 + 40, 130)], fill=label_color)
        draw.text((100, 88), label_text, fill=(255, 255, 255))
        draw.text((W - 120, 80), f"#{section_id:02d}", fill=(80, 80, 100))

        narration = (sec.get("narration_text") or sec.get("content", ""))[:168]
        lines = [narration[i:i+42] for i in range(0, len(narration), 42)]
        y = H // 2 - len(lines) * 28
        for line in lines:
            draw.text((W // 2, y), line, fill=(220, 225, 235), anchor="mm")
            y += 56

        img.save(out_path, "PNG")
    except ImportError:
        out_path.write_bytes(b"")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", type=Path, required=True)
    args = ap.parse_args()
    imgs = generate_images(args.script)
    print(f"[OK] 生成 {len(imgs)} 張圖片")
