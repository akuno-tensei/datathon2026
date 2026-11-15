"""Construction des enregistrements : niveau de confiance et assemblage, sans écriture.

Niveaux (Agents.md 2.2) :
  A  donnée structurée d'une source officielle ;
  B  donnée structurée d'un fournisseur tiers, avec sa méthodologie ;
  C  information tirée d'un texte ou d'une nouvelle, qualitative, jamais utilisée pour un calcul.
Le niveau se déduit de la source et de la forme de la donnée, jamais d'un jugement sur son contenu.
"""
from datetime import date
from typing import List, Optional, Tuple

from ..quant.models import Notice
from .models import (
    Confidence, DataKind, EnrichmentRecord, Fact, LACUNE, NeedKind, RawItem, SourceCard, SourceClass, make_record_id,
)

TOOL_VERSION = "1.0.0"


def assign_confidence(kind: DataKind, source_class: SourceClass, structured: bool, has_methodology: bool) -> Tuple[Confidence, List[Notice]]:
    notices: List[Notice] = []
    if kind == "news" or not structured:
        return "C", notices
    if source_class == "official":
        return "A", notices
    if source_class == "third_party":
        if has_methodology:
            return "B", notices
        notices.append(Notice(code="METHODOLOGY_MISSING", message="Fournisseur tiers sans méthodologie fournie : niveau B refusé, ramené à C."))
        return "C", notices
    notices.append(Notice(code="SOURCE_UNDOCUMENTED", message="Source non répertoriée : conditions inconnues, niveau plafonné à C."))
    return "C", notices


def structured_record(item: RawItem, card: SourceCard, need: NeedKind, as_of: date, tool: str) -> EnrichmentRecord:
    """Enregistrement d'une donnée structurée. `item.structured` doit être non nul."""
    assert item.structured is not None
    confidence, notices = assign_confidence(item.kind, card.source_class, True, bool(item.methodology))
    limits: List[str] = []
    if item.methodology:
        limits.append(f"Méthodologie du fournisseur : {item.methodology}")
    if item.from_cache:
        limits.append("Servi depuis le cache : dates de publication et de collecte d'origine conservées.")
    return EnrichmentRecord(
        record_id=make_record_id(kind=item.kind, need=need, ticker=item.ticker, source=item.source_id, url=item.url,
                                 published=item.date_publication.isoformat(), value=item.structured, as_of=as_of.isoformat()),
        status="ok", kind=item.kind, need=need, ticker=item.ticker, as_of=as_of, tool=tool, tool_version=TOOL_VERSION,
        date_publication=item.date_publication, date_observation=item.date_observation, confidence=confidence,
        usable_for_calculation=confidence in ("A", "B"), source_card_id=card.card_id, url=item.url, value=item.structured,
        notices=notices, limits=limits,
    )


def text_record(item: RawItem, facts: List[Fact], card: SourceCard, need: NeedKind, as_of: date, tool: str) -> EnrichmentRecord:
    """Enregistrement de faits tirés d'un texte : toujours niveau C, signal qualitatif."""
    limits = ["Information tirée d'un texte : qualitative, à vérifier, jamais utilisée pour un calcul ni pour bloquer à elle seule."]
    if item.from_cache:
        limits.append("Servi depuis le cache : dates de publication et de collecte d'origine conservées.")
    return EnrichmentRecord(
        record_id=make_record_id(kind=item.kind, need=need, ticker=item.ticker, source=item.source_id, url=item.url,
                                 published=item.date_publication.isoformat(), facts=[f.statement for f in facts], as_of=as_of.isoformat()),
        status="ok", kind=item.kind, need=need, ticker=item.ticker, as_of=as_of, tool=tool, tool_version=TOOL_VERSION,
        date_publication=item.date_publication, date_observation=item.date_observation, confidence="C",
        usable_for_calculation=False, source_card_id=card.card_id, url=item.url, facts=facts, limits=limits,
        label="signal_qualitatif",
    )


def gap_record(kind: DataKind, need: NeedKind, ticker: str, as_of: date, tool: str, reason_code: str, message: str,
               source_id: Optional[str] = None) -> EnrichmentRecord:
    """Lacune non comblée : la donnée manque et l'agent ne la remplace par aucune supposition."""
    return EnrichmentRecord(
        record_id=make_record_id(status=LACUNE, kind=kind, need=need, ticker=ticker, reason=reason_code, as_of=as_of.isoformat()),
        status=LACUNE, kind=kind, need=need, ticker=ticker, as_of=as_of, tool=tool, tool_version=TOOL_VERSION,
        reason_code=reason_code, message=message,
        limits=[f"Source : {source_id}."] if source_id else [],
    )

