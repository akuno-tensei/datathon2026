"""Modèles typés de l'agent d'enrichissement : besoins, éléments bruts, faits, fiches source, enregistrements du registre."""
import hashlib
from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import model_validator

from ..quant.models import Notice, StrictModel
from ..quant.registry import canonical_json

NeedKind = Literal["esg", "controversies", "fundamentals", "news", "etf_holdings"]
DataKind = Literal["esg", "filings", "fundamentals", "news", "etf_holdings"]
Confidence = Literal["A", "B", "C"]
SourceClass = Literal["official", "third_party", "unknown"]

LACUNE = "lacune_non_comblee"


# ---- besoins -------------------------------------------------------------


class Gap(StrictModel):
    """Lacune de données : un besoin (profil ou demande) que le jeu de départ ne couvre pas."""

    need_id: str
    kind: NeedKind
    ticker: str
    triggered_by: List[str]  # « profile:theme:esg », « request:esg », ...
    fillable: bool
    reason: Optional[str] = None  # pourquoi elle n'est pas comblable (hors univers, classe d'actif non concernée)
    tools: List[str] = []


class NeedsReport(StrictModel):
    as_of: date
    profile_id: str
    gaps: List[Gap] = []

    @property
    def fillable(self) -> List[Gap]:
        return [g for g in self.gaps if g.fillable]

    @property
    def unfillable(self) -> List[Gap]:
        return [g for g in self.gaps if not g.fillable]


# ---- éléments bruts renvoyés par un fournisseur -----------------------------


class RawItem(StrictModel):
    """Ce qu'un fournisseur renvoie. Non fiable : le filtre temporel et les validations s'appliquent avant toute écriture."""

    ticker: str
    kind: DataKind
    source_id: str
    url: str
    title: Optional[str] = None
    date_publication: date
    date_observation: date
    retrieved_on: date  # date réelle de collecte, fournie par le fournisseur (l'agent n'a pas d'horloge)
    structured: Optional[Dict[str, Any]] = None  # donnée structurée (cote, ratios, métadonnées d'un dépôt, composition)
    text: Optional[str] = None  # texte non structuré (nouvelle, communiqué) : jamais stocké en entier
    methodology: Optional[str] = None  # méthodologie du fournisseur (cotes ESG)
    from_cache: bool = False

    @model_validator(mode="after")
    def _has_content(self) -> "RawItem":
        if self.structured is None and not (self.text and self.text.strip()):
            raise ValueError("Un élément brut contient au moins `structured` ou `text`.")
        return self


class FetchResult(StrictModel):
    tool: str
    kind: DataKind
    ticker: str
    as_of: date
    items: List[RawItem] = []
    error_code: Optional[str] = None  # unavailable, rate_limited, unsupported, auth_required, provider_failure, ...
    error_message: Optional[str] = None


class FilterResult(StrictModel):
    as_of: date
    kept: List[RawItem] = []
    excluded: List[RawItem] = []  # publiés après as_of : pour la simulation, ils n'existent pas


# ---- faits extraits d'un texte --------------------------------------------


class Fact(StrictModel):
    """Fait structuré tiré d'un texte. Sans URL, date et extrait, il n'existe pas (Agents.md 2.2, règle 2)."""

    statement: str
    excerpt: str
    url: str
    date_publication: date
    date_observation: date

    @model_validator(mode="after")
    def _check(self) -> "Fact":
        if not (self.statement.strip() and self.excerpt.strip() and self.url.strip()):
            raise ValueError("Un fait exige un énoncé, un extrait et une URL non vides.")
        if self.date_observation > self.date_publication:
            raise ValueError("date_observation ne peut pas suivre date_publication.")
        return self


class RejectedFact(StrictModel):
    statement: str
    reason: str


class ExtractionResult(StrictModel):
    facts: List[Fact] = []
    rejected: List[RejectedFact] = []


# ---- fiche source -----------------------------------------------------------


class SourceCard(StrictModel):
    """Origine, date, traitement, limites, conditions d'utilisation et de réutilisation d'une source."""

    card_id: str
    source_id: str
    name: str
    origin_url: str
    source_class: SourceClass
    retrieved_on: date
    processing: List[str]
    limits: List[str]
    terms_url: Optional[str] = None
    reuse_terms: str
    storage_policy: str
    methodology: Optional[str] = None


# ---- enregistrement du registre ------------------------------------------------


class EnrichmentRecord(StrictModel):
    """Donnée enrichie, ou lacune non comblée, telle qu'écrite au registre.

    Le modèle refuse les enregistrements incohérents (publication après `as_of`, confiance C utilisée pour un calcul,
    fait sans trace) : un enregistrement invalide ne peut donc pas atteindre le registre.
    """

    record_id: str
    status: Literal["ok", "lacune_non_comblee"]
    kind: DataKind  # type de donnée (source)
    need: NeedKind  # besoin que l'enregistrement sert
    ticker: str
    as_of: date
    tool: str
    tool_version: str
    # status == "ok"
    date_publication: Optional[date] = None
    date_observation: Optional[date] = None
    confidence: Optional[Confidence] = None
    usable_for_calculation: bool = False  # vrai seulement pour A et B ; les nouvelles n'alimentent aucun calcul
    source_card_id: Optional[str] = None
    url: Optional[str] = None
    value: Optional[Dict[str, Any]] = None  # donnée structurée
    facts: List[Fact] = []  # faits extraits de texte
    # status == "lacune_non_comblee"
    reason_code: Optional[str] = None
    message: Optional[str] = None
    notices: List[Notice] = []
    limits: List[str] = []
    label: Literal["fait", "signal_qualitatif"] = "fait"

    @model_validator(mode="after")
    def _check(self) -> "EnrichmentRecord":
        if self.status == "lacune_non_comblee":
            if not self.reason_code:
                raise ValueError("Une lacune non comblée exige un `reason_code`.")
            if self.value or self.facts or self.confidence or self.usable_for_calculation:
                raise ValueError("Une lacune non comblée ne porte ni donnée, ni confiance.")
            return self

        if not (self.date_publication and self.date_observation and self.confidence and self.source_card_id):
            raise ValueError("Un enregistrement ok exige date_publication, date_observation, confiance et fiche source.")
        if self.date_publication > self.as_of:
            raise ValueError(f"Publié le {self.date_publication}, après as_of ({self.as_of}) : refusé.")
        if self.date_observation > self.date_publication:
            raise ValueError("date_observation ne peut pas suivre date_publication.")
        if bool(self.value) == bool(self.facts):
            raise ValueError("Fournir soit `value` (structuré), soit `facts` (texte), pas les deux ni aucun.")
        for f in self.facts:
            if f.date_publication > self.as_of:
                raise ValueError(f"Fait publié le {f.date_publication}, après as_of ({self.as_of}) : refusé.")
        if self.usable_for_calculation and self.confidence == "C":
            raise ValueError("Le niveau C n'alimente jamais un calcul.")
        if self.kind == "news" and (self.confidence != "C" or self.usable_for_calculation):
            raise ValueError("Les nouvelles sont de niveau C et n'alimentent aucun calcul.")
        if self.facts and self.confidence != "C":
            raise ValueError("Un fait tiré d'un texte est de niveau C.")
        return self


def make_record_id(**parts: Any) -> str:
    return "en_" + hashlib.sha256(canonical_json(parts).encode()).hexdigest()[:16]
