"""B-roll 素材自動下載器 — 用 Pexels API 依關鍵字抓免費商用素材。

流程：
  1. 讀取 script.json 的每個 section 的 broll_keywords
  2. 用 Pexels Videos API 搜尋 → 下載 HD MP4
  3. 快取到 data/broll_cache/，以 keyword hash 命名
  4. 回傳 section_id → broll clip path 的 mapping

無 PEXELS_API_KEY 時，函式回傳空 dict，讓 compositor 退回 AI 生圖模式。

執行：
  python -m modules.video.broll_fetcher --script data/scripts/xxx/script.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import time
import urllib.parse
import urllib.request
from pathlib import Path

from loguru import logger

from modules.common.config import PROJECT_ROOT
from modules.common.logging_setup import setup_logger

setup_logger()

BROLL_CACHE = PROJECT_ROOT / "data" / "broll_cache"
BROLL_CACHE.mkdir(parents=True, exist_ok=True)

PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"


def _api_key() -> str | None:
    key = os.getenv("PEXELS_API_KEY", "").strip()
    return key or None


def is_available() -> bool:
    return _api_key() is not None


def _keyword_hash(keyword: str) -> str:
    return hashlib.md5(keyword.lower().strip().encode("utf-8")).hexdigest()[:10]


def _pick_video_file(video_files: list[dict], target_height: int = 1080) -> dict | None:
    """從 Pexels video_files 挑最接近目標高度的 HD MP4。"""
    mp4s = [f for f in video_files if f.get("file_type") == "video/mp4"]
    if not mp4s:
        return None
    # 挑離 target_height 最近且 >= 720 的
    mp4s.sort(key=lambda f: (abs((f.get("height") or 0) - target_height), -(f.get("height") or 0)))
    for f in mp4s:
        if (f.get("height") or 0) >= 720:
            return f
    return mp4s[0]


def _download(url: str, out_path: Path, timeout: int = 120) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        if len(data) < 10_000:
            logger.warning(f"下載資料過小（{len(data)} bytes），視為失敗")
            return False
        out_path.write_bytes(data)
        return True
    except Exception as e:
        logger.warning(f"下載失敗：{e}")
        return False


def search_pexels(keyword: str, per_page: int = 10) -> list[dict]:
    """回傳 Pexels videos 搜尋結果清單。失敗回空 list。"""
    key = _api_key()
    if not key:
        return []

    params = {
        "query": keyword,
        "per_page": per_page,
        "orientation": "landscape",
        "size": "large",
    }
    url = f"{PEXELS_SEARCH_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={
        "Authorization": key,
        "User-Agent": "Mozilla/5.0 (compatible; AI-Channel-Bot/1.0)",
        "Accept": "application/json",
    })

    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode("utf-8"))
        return body.get("videos", []) or []
    except Exception as e:
        logger.warning(f"Pexels 搜尋失敗（{keyword}）：{e}")
        return []


def fetch_broll(
    keyword: str,
    min_duration: float = 3.0,
    max_duration: float = 20.0,
    pick_index: int = 0,
) -> Path | None:
    """依關鍵字抓一支 B-roll，回傳本地 MP4 路徑。

    - pick_index：搜尋結果第幾個（0=最相關，>0 可避免跟前段用同一支）
    - 快取命中會直接回傳，不重複下載
    """
    if not _api_key():
        return None

    kw_hash = _keyword_hash(keyword)
    # 快取檔名含 pick_index 避免不同段覆寫
    cached = BROLL_CACHE / f"{kw_hash}_{pick_index}.mp4"
    if cached.exists() and cached.stat().st_size > 10_000:
        logger.debug(f"B-roll 快取命中：{keyword} → {cached.name}")
        return cached

    logger.info(f"Pexels 搜尋：{keyword}（pick={pick_index}）")
    videos = search_pexels(keyword, per_page=15)

    # 過濾時長
    usable = [
        v for v in videos
        if min_duration <= (v.get("duration") or 0) <= max_duration
    ]
    if not usable:
        usable = videos  # 放寬時長限制

    if not usable:
        logger.warning(f"Pexels 無結果：{keyword}")
        return None

    # 依 pick_index 取（避免越界）
    idx = pick_index % len(usable)
    vid = usable[idx]
    vfile = _pick_video_file(vid.get("video_files", []))
    if not vfile:
        logger.warning(f"Pexels 無可用 MP4：{keyword}")
        return None

    logger.info(f"下載 B-roll：{vfile.get('width')}x{vfile.get('height')} {vid.get('duration')}s")
    if _download(vfile["link"], cached):
        return cached
    return None


def fetch_broll_pool_for_script(
    script_path: Path,
    scene_secs: float = 10.0,
) -> dict[int, list[Path]]:
    """為每個 section 抓一組（而非一支）B-roll，每 `scene_secs` 秒換一支不同畫面。

    需要幾支：ceil(section_audio_duration / scene_secs)
    - 讀取 section_NNN.wav 實際時長（沒有就用 script 估計）
    - 依 broll_keywords 輪流搜，同關鍵字用 pick_index 避開重複
    - 如果關鍵字不夠，循環使用但遞增 pick_index 拿不同支

    回傳：{section_id: [clip1, clip2, ...]}
    """
    import math
    import wave

    script = json.loads(script_path.read_text(encoding="utf-8"))
    sections = script.get("script_sections", [])

    if not _api_key():
        logger.info("未設定 PEXELS_API_KEY，跳過 B-roll")
        return {}

    slug = script_path.parent.name
    audio_dir = PROJECT_ROOT / "data" / "audio" / slug

    def _real_dur(sid: int, fallback: float) -> float:
        af = audio_dir / f"section_{sid:03d}.wav"
        if af.exists():
            try:
                with wave.open(str(af), "rb") as f:
                    d = f.getnframes() / f.getframerate()
                return d if d > 0.3 else fallback
            except Exception:
                pass
        return fallback

    keyword_pick: dict[str, int] = {}
    result: dict[int, list[Path]] = {}

    try:
        from modules.database import db_manager as _db
    except Exception:
        _db = None

    total_sec = len(sections)
    for idx, sec in enumerate(sections, 1):
        sid = sec["section_id"]
        keywords = sec.get("broll_keywords") or (
            [sec["broll_keyword"]] if sec.get("broll_keyword") else []
        )
        if not keywords:
            continue

        dur = _real_dur(sid, float(sec.get("duration_seconds", 5)))
        n_scenes = max(1, math.ceil(dur / scene_secs))

        if _db:
            _db.update_progress(f"抓 B-roll 段 {idx}/{total_sec}：{n_scenes} 個場景")

        clips: list[Path] = []
        for i in range(n_scenes):
            # 輪流用 keywords；同 kw 用遞增 pick_index 拿不同支
            kw = keywords[i % len(keywords)]
            pick = keyword_pick.get(kw, 0)

            clip = None
            # 失敗退回其他 keyword
            tried = set()
            attempt_list = [keywords[(i + j) % len(keywords)] for j in range(len(keywords))]
            for try_kw in attempt_list:
                if try_kw in tried:
                    continue
                tried.add(try_kw)
                p = keyword_pick.get(try_kw, 0)
                clip = fetch_broll(try_kw, pick_index=p)
                if clip:
                    keyword_pick[try_kw] = p + 1
                    break
                time.sleep(0.3)

            if clip:
                clips.append(clip)
            else:
                logger.info(f"段 {sid} 場景 {i+1}：無 B-roll")

            time.sleep(random.uniform(0.2, 0.5))

        if clips:
            result[sid] = clips
            logger.info(f"段 {sid}：{len(clips)} 支 clip（{n_scenes} 個場景）")

    total_clips = sum(len(v) for v in result.values())
    logger.info(f"B-roll pool 完成：{total_clips} 支 clips / {len(result)} 段")
    return result


def fetch_broll_for_script(script_path: Path) -> dict[int, Path]:
    """為整份 script 抓 B-roll，回傳 {section_id: clip_path}。

    每個 section 可有：
      - broll_keywords: ["keyword1", "keyword2"]（依序嘗試）
      - broll_keyword : "單一關鍵字"（相容舊格式）

    同一關鍵字在不同段會自動錯開 pick_index，降低視覺重複。
    """
    script = json.loads(script_path.read_text(encoding="utf-8"))
    sections = script.get("script_sections", [])

    if not _api_key():
        logger.info("未設定 PEXELS_API_KEY，跳過 B-roll 抓取")
        return {}

    keyword_counter: dict[str, int] = {}
    result: dict[int, Path] = {}

    try:
        from modules.database import db_manager as _db
    except Exception:
        _db = None

    total = len(sections)
    for i, sec in enumerate(sections, 1):
        sid = sec["section_id"]
        keywords = sec.get("broll_keywords") or (
            [sec["broll_keyword"]] if sec.get("broll_keyword") else []
        )

        if not keywords:
            continue

        if _db:
            _db.update_progress(f"抓 B-roll {i}/{total}：{keywords[0][:30]}")

        clip: Path | None = None
        for kw in keywords:
            idx = keyword_counter.get(kw, 0)
            clip = fetch_broll(kw, pick_index=idx)
            if clip:
                keyword_counter[kw] = idx + 1
                break
            time.sleep(0.5)

        if clip:
            result[sid] = clip
            logger.info(f"段落 {sid} B-roll：{clip.name}")
        else:
            logger.info(f"段落 {sid} 無 B-roll，將 fallback 到 AI 生圖")

        time.sleep(random.uniform(0.3, 0.8))  # 禮貌間隔

    logger.info(f"B-roll 抓取完成：{len(result)}/{total} 段取得素材")
    return result


def prefetch_all_sections(
    script_path: Path,
    *,
    slug: str | None = None,
    scene_secs: float = 10.0,
    thumbs_per_section: int = 3,
) -> dict:
    """預抓 + 寫 manifest — 供 prefetch 階段與腳本 Tab 預覽使用。

    步驟：
      1. 呼叫 fetch_broll_pool_for_script 下載所有段落的 clips
      2. 對每個段落的 keywords 取得 Pexels metadata（含 thumb url）
      3. 輸出 manifest.json 到 data/broll_cache/{slug}/manifest.json

    manifest 結構：
      {"slug", "generated_at", "sections": {sid: [{pexels_id, url, thumb,
       duration_sec, local_path, keyword}]}}

    回傳 manifest dict。
    """
    from datetime import datetime, timezone

    if not _api_key():
        logger.info("未設定 PEXELS_API_KEY，prefetch 只寫 empty manifest")
        manifest = {
            "slug": slug,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sections": {},
            "pexels_available": False,
        }
        _write_manifest(slug, manifest)
        return manifest

    # 1. 下載 clips（依 scene_secs 切段、輪播）
    pool = fetch_broll_pool_for_script(script_path, scene_secs=scene_secs)

    # 2. 組 manifest — 再查一次 metadata 取得縮圖 URL
    script = json.loads(script_path.read_text(encoding="utf-8"))
    sections_map: dict[str, list[dict]] = {}
    for sec in script.get("script_sections", []):
        sid = sec["section_id"]
        keywords = sec.get("broll_keywords") or (
            [sec["broll_keyword"]] if sec.get("broll_keyword") else []
        )
        if not keywords:
            sections_map[str(sid)] = []
            continue

        items: list[dict] = []
        for kw in keywords[:thumbs_per_section]:
            vids = search_pexels(kw, per_page=3)
            if not vids:
                continue
            v = vids[0]
            vfile = _pick_video_file(v.get("video_files", []) or [])
            thumb = v.get("image") or ""
            kw_hash = _keyword_hash(kw)
            # 預設用 pick_index=0 快取檔
            local = BROLL_CACHE / f"{kw_hash}_0.mp4"
            items.append({
                "pexels_id": v.get("id"),
                "url": (vfile or {}).get("link") or "",
                "thumb": thumb,
                "duration_sec": v.get("duration") or 0,
                "local_path": str(local) if local.exists() else "",
                "keyword": kw,
                "user": (v.get("user") or {}).get("name") or "",
            })
            time.sleep(random.uniform(0.2, 0.4))
        sections_map[str(sid)] = items

    # 3. 命中統計
    total_clips = sum(len(v) for v in pool.values())
    manifest = {
        "slug": slug,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pexels_available": True,
        "sections": sections_map,
        "stats": {
            "sections_total": len(script.get("script_sections", [])),
            "sections_with_clips": len(pool),
            "clips_downloaded": total_clips,
        },
    }
    _write_manifest(slug, manifest)
    logger.info(f"B-roll manifest 已寫入：{total_clips} clips / {len(pool)} 段")
    return manifest


def _write_manifest(slug: str | None, manifest: dict) -> None:
    if not slug:
        return
    out_dir = BROLL_CACHE / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", type=Path, required=True)
    args = ap.parse_args()
    mapping = fetch_broll_for_script(args.script)
    print(f"[OK] B-roll：{len(mapping)} 段")
    for sid, p in sorted(mapping.items()):
        print(f"  section {sid}: {p}")


if __name__ == "__main__":
    main()
