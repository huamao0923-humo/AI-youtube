# AI 商業新聞 YouTube 頻道 — 自動化流水線

每日自動產出 AI 商業與應用新聞的繁體中文 YouTube 影片。

## 建置進度

- [x] **Module 1** — 新聞爬蟲 + Claude 評分
- [ ] Module 2 — Daily Brief + Telegram 推送
- [ ] Module 3 — 腳本生成
- [ ] Module 4 — XTTS-v2 配音
- [ ] Module 5 — ComfyUI 圖片生成
- [ ] Module 6 — FFmpeg 影片合成
- [ ] Module 7 — YouTube 上傳 + 社群發文
- [ ] Module 8 — 資料庫與數據分析
- [ ] Module 9 — 排程系統
- [ ] Module 10 — 環境設定完整化

## 快速開始（Module 1 測試）

### 1. 安裝環境

建議用 Python 3.11+，建立虛擬環境：

```bash
python -m venv .venv
# Windows bash:
source .venv/Scripts/activate
# Linux/Mac:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. 設定環境變數

```bash
cp .env.example .env
# 編輯 .env，至少填入 ANTHROPIC_API_KEY
```

### 3. 初始化資料庫

```bash
python -m modules.database.db_manager
```
應輸出：`[OK] 資料庫初始化完成：.../data/channel.db`

### 4. 測試爬蟲（不寫 DB）

```bash
python -m modules.scraper.rss_fetcher --test
```

預期：30-60 秒內從 20+ 個來源抓到當日新聞，印出統計。

### 5. 完整抓取並跑 Claude 評分

```bash
python -m modules.scraper.fetch_all --score
```

這會：
1. 並行執行 RSS / Web / HN-Reddit 三類爬蟲
2. 本地粗分 + 去重後寫入 SQLite
3. 將未評分的新聞送 Claude 批次評分
4. 分數 ≥ 6 的標記為 `candidate`，供 Module 2 生成 Daily Brief 使用

### 6. 檢視候選新聞

```bash
sqlite3 data/channel.db
sqlite> .mode column
sqlite> .headers on
sqlite> SELECT id, source_name, ai_score, suggested_title FROM news_items
        WHERE status='candidate' ORDER BY ai_score DESC LIMIT 10;
```

## 目錄結構

```
ai_channel/
├── config/         — sources / keywords / settings YAML
├── modules/
│   ├── common/     — 設定、logger、本地評分
│   ├── database/   — SQLite schema + CRUD
│   ├── scraper/    — RSS / Web / HN-Reddit / 官方部落格
│   ├── filter/     — 去重 + Claude 評分
│   └── (brief/script/tts/image/video/publish — 後續模組)
├── data/           — SQLite、腳本、音訊、圖片、影片
├── voice_samples/  — 你的聲音樣本（處理後供 XTTS 使用）
├── templates/      — 縮圖/片頭/社群貼文模板
└── logs/           — loguru 日誌
```

## 單模組測試指令

| 模組 | 指令 |
|------|------|
| RSS 爬蟲 | `python -m modules.scraper.rss_fetcher --test` |
| Web 爬蟲 | `python -m modules.scraper.web_scraper --test` |
| HN/Reddit | `python -m modules.scraper.hn_reddit_fetcher --test` |
| 全部爬蟲 | `python -m modules.scraper.fetch_all --test` |
| Claude 評分 | `python -m modules.filter.scorer` |
| DB 初始化 | `python -m modules.database.db_manager` |
