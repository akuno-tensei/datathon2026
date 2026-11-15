"""extract_facts : texte -> faits structurés {énoncé, extrait court, URL, date de publication, date d'observation}.

Le texte est la zone la plus risquée de l'agent (hallucination). Un extracteur, par règles ou par modèle de langage,
*propose* des candidats ; le code *dispose* : un fait n'existe que si
- l'URL et la date de publication sont fournies (et la publication n'est pas postérieure à `as_of`),
- l'extrait est court et se retrouve mot pour mot dans le texte,
- chaque nombre de l'énoncé figure dans l'extrait (l'énoncé n'invente aucun chiffre).
Les candidats refusés sont renvoyés avec leur raison, pas silencieusement perdus.
"""
import re
from datetime import date
from typing import List, Optional, Protocol, Tuple

from pydantic import BaseModel, ConfigDict

from .config import (
    MAX_EXCERPT_CHARS, MAX_FACTS_PER_TEXT, MAX_STATEMENT_CHARS, NEWS_FALLBACK_SENTENCES, TOPIC_KEYWORDS,
)
from .models import ExtractionResult, Fact, RejectedFact


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str
    excerpt: str


class FactExtractor(Protocol):
    def propose(self, text: str, topic: Optional[str]) -> List[Candidate]: ...


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý0-9\"«])")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def split_sentences(text: str) -> List[str]:
    return [s for s in (_normalize(p) for p in _SENTENCE_END.split(text)) if len(s) >= 15]


class RuleBasedExtractor:
    """Repli déterministe : retient les phrases qui contiennent un mot-clé du sujet, mot pour mot.

    L'énoncé est la phrase elle-même (tronquée si besoin) : aucune reformulation, donc rien à inventer.
    Sans sujet reconnu (nouvelles générales), garde les premières phrases.
    """

    def propose(self, text: str, topic: Optional[str]) -> List[Candidate]:
        sentences = split_sentences(text)
        pattern = TOPIC_KEYWORDS.get(topic or "")
        picked = [s for s in sentences if pattern.search(s)] if pattern else sentences[:NEWS_FALLBACK_SENTENCES]
        return [Candidate(statement=s[:MAX_STATEMENT_CHARS], excerpt=s[:MAX_EXCERPT_CHARS]) for s in picked]


def _digits(s: str) -> List[str]:
    """Nombres d'un texte, sans séparateurs décimaux ou de milliers, pour comparer « 1,200.5 » et « 1.200,5 »."""
    return [re.sub(r"[.,]", "", m) for m in _NUMBER.findall(s)]


def _check(c: Candidate, text_norm: str) -> Optional[str]:
    statement, excerpt = _normalize(c.statement), _normalize(c.excerpt)
    if not statement:
        return "énoncé vide"
    if not excerpt:
        return "extrait vide"
    if len(statement) > MAX_STATEMENT_CHARS:
        return f"énoncé trop long (> {MAX_STATEMENT_CHARS} caractères)"
    if len(excerpt) > MAX_EXCERPT_CHARS:
        return f"extrait trop long (> {MAX_EXCERPT_CHARS} caractères) : le registre ne stocke pas d'articles"
    if excerpt not in text_norm:
        return "extrait introuvable mot pour mot dans le texte source"
    excerpt_digits = set(_digits(excerpt))
    invented = [d for d in _digits(statement) if d not in excerpt_digits]
    if invented:
        return f"nombre(s) de l'énoncé absent(s) de l'extrait : {invented}"
    return None


def extract_facts(
    text: str,
    *,
    url: str,
    date_publication: Optional[date],
    date_observation: Optional[date] = None,
    topic: Optional[str] = None,
    as_of: Optional[date] = None,
    extractor: Optional[FactExtractor] = None,
) -> ExtractionResult:
    """`date_observation` : date que décrit le texte ; à défaut, la date de publication (limite à consigner).

    Sans URL ou sans date de publication, ou publié après `as_of`, aucun fait n'est produit.
    """
    def refuse_all(reason: str) -> ExtractionResult:
        return ExtractionResult(rejected=[RejectedFact(statement="(texte entier)", reason=reason)])

    if not url or not re.match(r"^https?://", url.strip(), re.I):
        return refuse_all("URL absente ou invalide : un fait sans trace est rejeté")
    if date_publication is None:
        return refuse_all("date de publication absente : un fait sans date est rejeté")
    if as_of is not None and date_publication > as_of:
        return refuse_all(f"publié le {date_publication}, après as_of ({as_of})")
    observed = date_observation or date_publication
    if observed > date_publication:
        return refuse_all("date d'observation postérieure à la date de publication")
    if not text or not text.strip():
        return refuse_all("texte vide")

    text_norm = _normalize(text)
    facts: List[Fact] = []
    rejected: List[RejectedFact] = []
    seen: set = set()
    try:
        candidates = (extractor or RuleBasedExtractor()).propose(text, topic)
    except Exception as e:  # noqa: BLE001  (un extracteur en panne ne produit aucun fait, il n'en invente pas)
        return refuse_all(f"extracteur en échec : {type(e).__name__}")

    for c in candidates:
        reason = _check(c, text_norm)
        key: Tuple[str, str] = (_normalize(c.statement), _normalize(c.excerpt))
        if reason is None and key in seen:
            continue
        if reason is None and len(facts) >= MAX_FACTS_PER_TEXT:
            reason = f"limite de {MAX_FACTS_PER_TEXT} faits par texte atteinte"
        if reason:
            rejected.append(RejectedFact(statement=_normalize(c.statement)[:80], reason=reason))
            continue
        seen.add(key)
        facts.append(Fact(statement=key[0], excerpt=key[1], url=url.strip(), date_publication=date_publication, date_observation=observed))
    return ExtractionResult(facts=facts, rejected=rejected)
