"""Paramètres de l'agent d'enrichissement : vocabulaire des besoins, bornes de texte, mots-clés d'extraction.

Choix de politique et de vocabulaire, regroupés ici pour être revus sans toucher à la logique. Rien ne dépend d'un persona :
les déclencheurs viennent des champs du profil (thèmes) et du texte de la demande.
"""
import re
from typing import Dict, List, Set

# ---- bornes de stockage (Agents.md 2.2, règle 5 : métadonnées et courts extraits, jamais d'articles complets) ----
MAX_EXCERPT_CHARS = 300
MAX_STATEMENT_CHARS = 300
MAX_FACTS_PER_TEXT = 10

# Seuls les dépôts de ces formulaires sont retenus (rapports périodiques et événements).
FILING_FORMS: Set[str] = {"10-K", "10-Q", "8-K", "20-F", "40-F", "6-K"}
MAX_FILINGS = 20

# ---- besoins déclenchés par le profil (kind == "theme") ----
ESG_THEMES: Set[str] = {"esg", "isr", "investissement_responsable", "durable"}
CONTROVERSY_THEMES: Set[str] = {"eviter_controverses", "controverses", "sans_controverse"}

# ---- besoins déclenchés par le texte de la demande ----
REQUEST_TRIGGERS: Dict[str, "re.Pattern[str]"] = {
    "esg": re.compile(r"\besg\b|\bisr\b|durable|sustainab|responsable|[ée]missions?|carbone|carbon", re.I),
    "controversies": re.compile(r"controvers|scandale|scandal|poursuite|lawsuit|litige|enqu[êe]te|investigation", re.I),
    "fundamentals": re.compile(
        r"fondamentau|fundamental|b[ée]n[ée]fice|earnings|revenus?\b|revenue|\bdette\b|\bdebt\b|flux de tr[ée]sorerie|cash ?flow|"
        r"valorisation|valuation|\bP/?E\b|dividende|dividend|r[ée]sultats", re.I),
    "news": re.compile(r"nouvelle|actualit[ée]|\bnews\b|r[ée]cent|recent|derni[èe]res? semaines?|latest", re.I),
}

# Classes d'actifs pour lesquelles chaque besoin a un sens (champ `asset_class` de l'univers).
APPLICABLE_CLASSES: Dict[str, Set[str]] = {
    "esg": {"equity", "etf_equity"},
    "controversies": {"equity"},  # pour un FNB, les controverses passent par ses titres : besoin `etf_holdings`
    "fundamentals": {"equity"},
    "news": {"equity", "etf_equity", "etf_bond", "etf_commodity", "crypto"},
    "etf_holdings": {"etf_equity", "etf_bond", "etf_commodity"},
}

# Outils appelés pour combler chaque besoin.
NEED_TOOLS: Dict[str, List[str]] = {
    "esg": ["fetch_esg"],
    "controversies": ["fetch_esg", "fetch_news"],
    "fundamentals": ["fetch_fundamentals", "fetch_filings"],
    "news": ["fetch_news"],
    "etf_holdings": ["fetch_etf_holdings"],
}

# Type de donnée produit par chaque outil.
TOOL_KIND: Dict[str, str] = {
    "fetch_esg": "esg",
    "fetch_filings": "filings",
    "fetch_fundamentals": "fundamentals",
    "fetch_news": "news",
    "fetch_etf_holdings": "etf_holdings",
}

# ---- extraction de faits par règles (repli déterministe, sans modèle de langage) ----
TOPIC_KEYWORDS: Dict[str, "re.Pattern[str]"] = {
    "esg": re.compile(
        r"\besg\b|[ée]missions?|emissions?|carbon|carbone|climat|climate|sustainab|durab|gouvernance|governance|diversit", re.I),
    "controversies": re.compile(
        r"controvers|lawsuit|poursuite|class action|recours collectif|enqu[êe]te|investigation|amende|\bfine[ds]?\b|penalt|"
        r"settlement|r[èe]glement amiable|recall|rappel|scandal|fraud|fraude|violation|sanction|d[ée]versement|spill", re.I),
    "fundamentals": re.compile(
        r"revenue|revenus?\b|earnings|b[ée]n[ée]fice|net income|\bdebt\b|\bdette\b|cash flow|flux de tr[ée]sorerie|\beps\b|guidance|"
        r"r[ée]sultats", re.I),
}
NEWS_FALLBACK_SENTENCES = 3  # sans sujet précis, on garde les premières phrases (le contexte, pas un calcul)
