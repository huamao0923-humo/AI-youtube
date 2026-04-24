"""AI 戰情室模組 — 新聞 AI 過濾、公司對映、模型偵測、已用標記。

Sub-modules:
  filter           — is_ai_related()
  company_matcher  — match_company() 讀 config/ai_companies.yaml
  model_registry   — detect_model_release() regex 偵測
  used_marks       — AiUsedMark CRUD
  backfill         — 掃既有 news_items 回填欄位
"""
