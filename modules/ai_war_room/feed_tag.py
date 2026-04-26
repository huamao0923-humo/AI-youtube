"""AI 新聞分類（feed tag）— 把 NewsItem 分到 product / funding / partnership /
research / policy / other 六大類，戰情室「焦點新聞」與 category_summarizer 共用。
"""
from __future__ import annotations


_RESEARCH_SRC = (
    "arxiv", "papers", "huggingface", "github trending",
    "r/machinelearning", "r/locallm", "r/localllama",
)
_POLICY_KW = (
    "regulat", "lawmaker", "senate", "congress", "fcc", "ftc", "eu ai act",
    "監管", "法案", "法規", "立法", "政策", "禁令", "制裁",
)
_PRODUCT_KW = (
    "launch", "release", "announce", "unveil", "introduc", "debut",
    "ship", "roll out", "rolling out", "now available", "now live",
    "goes live", "open beta", "open-source", "open source",
    "發布", "推出", "上線", "上架", "開放", "開源", "公布", "正式", "亮相",
)
_FUNDING_KW = (
    "raise", "funding", "series ", "valuation", "valued at", "ipo",
    "acquire", "acquisition", "buyout", "invest", "round",
    "融資", "併購", "收購", "估值", "投資", "入股",
)
_PARTNER_KW = (
    "partner", "partnership", "teams up", "team up", "joint ", "joins forces",
    "sign", "deal with", "collaborat", "alliance",
    "合作", "聯手", "攜手", "結盟", "簽約", "共同",
)


def ai_feed_tag(row) -> str:
    """row 可以是 NewsItem ORM instance 或 dict（需有 title/title_zh/category/source_name 欄位）。"""
    if isinstance(row, dict):
        title = (row.get("title") or "").lower()
        title_zh = row.get("title_zh") or ""
        cat = (row.get("category") or "").lower()
        src = (row.get("source_name") or "").lower()
    else:
        title = (getattr(row, "title", "") or "").lower()
        title_zh = getattr(row, "title_zh", "") or ""
        cat = (getattr(row, "category", "") or "").lower()
        src = (getattr(row, "source_name", "") or "").lower()
    text = title + " " + title_zh

    if any(k in src for k in _RESEARCH_SRC):
        return "research"
    if cat == "policy" or any(k in text for k in _POLICY_KW):
        return "policy"
    if cat == "product" or any(k in text for k in _PRODUCT_KW):
        return "product"
    if any(k in text for k in _FUNDING_KW):
        return "funding"
    if any(k in text for k in _PARTNER_KW):
        return "partnership"
    return "other"


FEED_LABELS = {
    "product":     ("📦", "產品發布"),
    "funding":     ("💰", "融資 / 商業"),
    "partnership": ("🤝", "合作"),
    "research":    ("🔬", "研究前沿"),
    "policy":      ("📜", "政策 / 法規"),
    "other":       ("📰", "其他動態"),
}
