"""ComfyUI API 客戶端 — 批次生成腳本所需圖片。

需要本地 ComfyUI 在 http://localhost:8188 執行中。
若 ComfyUI 未啟動，自動 fallback 到佔位圖片模式。

執行：
  python -m modules.image.comfyui_client --script data/scripts/20260416_xxx/script.json
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from loguru import logger

from modules.common.config import PROJECT_ROOT, settings
from modules.common.logging_setup import setup_logger

setup_logger()

IMAGES_DIR = PROJECT_ROOT / "data" / "images"


def _comfyui_host() -> str:
    return settings()["comfyui"]["host"].rstrip("/")


def _is_comfyui_running() -> bool:
    try:
        urllib.request.urlopen(f"{_comfyui_host()}/system_stats", timeout=3)
        return True
    except Exception:
        return False


def _build_flux_workflow(prompt: str, seed: int | None = None) -> dict:
    """最小化 Flux.1-schnell 工作流（4步快速出圖）。"""
    cfg = settings()["comfyui"]
    seed = seed or int(time.time() * 1000) % (2 ** 31)
    return {
        "3": {"class_type": "KSampler", "inputs": {
            "cfg": 1.0, "denoise": 1.0, "latent_image": ["5", 0],
            "model": ["4", 0], "negative": ["7", 0], "positive": ["6", 0],
            "sampler_name": "euler", "scheduler": "simple",
            "seed": seed, "steps": cfg.get("steps", 4)
        }},
        "4": {"class_type": "UNETLoader", "inputs": {
            "model_name": cfg.get("default_model", "flux1-schnell.safetensors"),
            "weight_dtype": "fp8_e4m3fn"
        }},
        "5": {"class_type": "EmptyLatentImage", "inputs": {
            "batch_size": 1, "height": 576, "width": 1024
        }},
        "6": {"class_type": "CLIPTextEncode", "inputs": {
            "clip": ["11", 0],
            "text": f"{prompt}, cinematic lighting, sharp focus, 16:9 ratio, high quality"
        }},
        "7": {"class_type": "CLIPTextEncode", "inputs": {
            "clip": ["11", 0], "text": "ugly, blurry, low quality, watermark, text"
        }},
        "8": {"class_type": "VAEDecode", "inputs": {
            "samples": ["3", 0], "vae": ["12", 0]
        }},
        "9": {"class_type": "SaveImage", "inputs": {
            "filename_prefix": "ai_channel", "images": ["8", 0]
        }},
        "11": {"class_type": "DualCLIPLoader", "inputs": {
            "clip_name1": "t5xxl_fp8_e4m3fn.safetensors",
            "clip_name2": "clip_l.safetensors", "type": "flux"
        }},
        "12": {"class_type": "VAELoader", "inputs": {
            "vae_name": "ae.safetensors"
        }},
    }


def _submit_prompt(workflow: dict) -> str:
    """送出工作流，回傳 prompt_id。"""
    host = _comfyui_host()
    data = json.dumps({"prompt": workflow, "client_id": str(uuid.uuid4())}).encode()
    req = urllib.request.Request(f"{host}/prompt", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["prompt_id"]


def _wait_for_image(prompt_id: str, timeout: int = 300) -> bytes | None:
    """輪詢等待 ComfyUI 完成，回傳 PNG bytes。"""
    host = _comfyui_host()
    deadline = time.time() + timeout
    while time.time() < deadline:
        with urllib.request.urlopen(f"{host}/history/{prompt_id}", timeout=10) as r:
            hist = json.loads(r.read())
        if prompt_id in hist:
            outputs = hist[prompt_id].get("outputs", {})
            for node_output in outputs.values():
                for img in node_output.get("images", []):
                    params = urllib.parse.urlencode({
                        "filename": img["filename"],
                        "subfolder": img.get("subfolder", ""),
                        "type": img.get("type", "output"),
                    })
                    with urllib.request.urlopen(f"{host}/view?{params}", timeout=30) as r:
                        return r.read()
        time.sleep(2)
    return None


def _make_placeholder(section_id: int, label: str, out_path: Path) -> None:
    """ComfyUI 未啟動時，生成純色佔位圖（需要 Pillow）。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (1024, 576), color=(15, 17, 23))
        draw = ImageDraw.Draw(img)
        draw.text((512, 288), f"[段落 {section_id}]\n{label[:40]}",
                  fill=(100, 100, 120), anchor="mm")
        img.save(out_path, "PNG")
    except ImportError:
        # Pillow 未安裝：建空檔
        out_path.write_bytes(b"")


def generate_images(script_path: Path) -> list[Path]:
    """讀取 script.json，為每個段落生成圖片，回傳圖片路徑清單。"""
    script = json.loads(script_path.read_text(encoding="utf-8"))
    sections = script.get("script_sections", [])
    slug = script_path.parent.name
    out_dir = IMAGES_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    use_comfyui = _is_comfyui_running()
    if use_comfyui:
        logger.info("ComfyUI 已啟動，使用 GPU 生成圖片")
    else:
        logger.warning("ComfyUI 未啟動，使用佔位圖模式（設定 ComfyUI 後重新執行）")

    paths: list[Path] = []
    for sec in sections:
        sid = sec["section_id"]
        prompt = sec.get("visual_prompt", f"section {sid}")
        out = out_dir / f"section_{sid:03d}.png"

        if out.exists():
            logger.info(f"段落 {sid} 圖片已存在，跳過")
            paths.append(out)
            continue

        if use_comfyui:
            try:
                workflow = _build_flux_workflow(prompt)
                pid = _submit_prompt(workflow)
                logger.info(f"段落 {sid} 送出生成，prompt_id={pid}")
                img_bytes = _wait_for_image(pid)
                if img_bytes:
                    out.write_bytes(img_bytes)
                    logger.info(f"段落 {sid} 圖片已存：{out.name}")
                else:
                    logger.warning(f"段落 {sid} 生成超時，改用佔位圖")
                    _make_placeholder(sid, sec.get("type", ""), out)
            except Exception as e:
                logger.error(f"段落 {sid} ComfyUI 失敗：{e}，改用佔位圖")
                _make_placeholder(sid, sec.get("type", ""), out)
        else:
            _make_placeholder(sid, sec.get("type", ""), out)

        paths.append(out)

    logger.info(f"圖片生成完成：{len(paths)} 張，存於 {out_dir}")
    return paths


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", type=Path, required=True)
    args = ap.parse_args()
    imgs = generate_images(args.script)
    print(f"[OK] 生成 {len(imgs)} 張圖片")
