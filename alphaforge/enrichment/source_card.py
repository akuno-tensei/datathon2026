"""source_card : fiche source (origine, date, traitement, limites, conditions d'utilisation et de réutilisation).

Le mandat exige de documenter chaque source. Les sources connues sont décrites ici ; une source inconnue obtient une fiche
qui dit explicitement que ses conditions ne sont pas documentées, et ses données restent plafonnées au niveau C.
Ajouter un fournisseur (cotes ESG, fondamentaux) = enregistrer sa `SourceDefinition`, sans toucher au reste.
"""
import hashlib
from datetime import date
from typing import Dict, List, Optional

from ..quant.models import StrictModel
from ..quant.registry import canonical_json
from .models import SourceCard, SourceClass

UNDOCUMENTED = "non documentées : à vérifier avant toute réutilisation"
STORAGE_POLICY = "Métadonnées, valeurs structurées et extraits courts seulement ; aucun article ou document complet n'est conservé."


class SourceDefinition(StrictModel):
    source_id: str
    name: str
    origin_url: str
    source_class: SourceClass  # official : dépôt réglementaire, banque centrale ; third_party : fournisseur commercial ou média
    terms_url: Optional[str] = None
    reuse_terms: str
    limits: List[str] = []


DEFAULT_SOURCES = [
    SourceDefinition(
        source_id="sec_edgar",
        name="SEC EDGAR (U.S. Securities and Exchange Commission)",
        origin_url="https://www.sec.gov/edgar",
        source_class="official",
        terms_url="https://www.sec.gov/os/webmaster-faq#developers",
        reuse_terms="Données publiques de l'agence. L'accès automatisé exige un User-Agent identifiant l'appelant et respecte la limite de débit de la SEC.",
        limits=[
            "Seules les métadonnées des dépôts (formulaire, dates, lien) sont lues, pas le contenu des états financiers.",
            "La date de dépôt fait foi pour la publication ; la date d'observation est la fin de période déclarée.",
        ],
    ),
    SourceDefinition(
        source_id="sedar_plus",
        name="SEDAR+ (Autorités canadiennes en valeurs mobilières)",
        origin_url="https://www.sedarplus.ca",
        source_class="official",
        terms_url="https://www.sedarplus.ca",
        reuse_terms="Conditions d'utilisation du site à vérifier : aucune API publique documentée pour l'accès automatisé.",
        limits=["Pas de collecte automatisée prévue : les dépôts canadiens restent une lacune tant qu'une source autorisée n'est pas branchée."],
    ),
]


class SourceRegistry:
    def __init__(self, definitions: Optional[List[SourceDefinition]] = None):
        self._defs: Dict[str, SourceDefinition] = {}
        for d in DEFAULT_SOURCES if definitions is None else definitions:
            self.register(d)

    def register(self, definition: SourceDefinition) -> None:
        self._defs[definition.source_id] = definition

    def get(self, source_id: str) -> Optional[SourceDefinition]:
        return self._defs.get(source_id)


DEFAULT_REGISTRY = SourceRegistry()


def source_card(
    source_id: str,
    retrieved_on: date,
    *,
    processing: Optional[List[str]] = None,
    extra_limits: Optional[List[str]] = None,
    methodology: Optional[str] = None,
    registry: Optional[SourceRegistry] = None,
) -> SourceCard:
    """Fiche d'une source pour une collecte. `processing` décrit ce que l'agent a fait (filtre, extraction, cache)."""
    d = (registry or DEFAULT_REGISTRY).get(source_id)
    processing = list(processing or [])
    limits = list(d.limits) if d else [f"Source « {source_id} » non répertoriée : origine et conditions de réutilisation non vérifiées."]
    limits += list(extra_limits or [])
    fields = dict(
        source_id=source_id,
        name=d.name if d else source_id,
        origin_url=d.origin_url if d else "inconnue",
        source_class=d.source_class if d else "unknown",
        retrieved_on=retrieved_on,
        processing=processing,
        limits=limits,
        terms_url=d.terms_url if d else None,
        reuse_terms=d.reuse_terms if d else UNDOCUMENTED,
        storage_policy=STORAGE_POLICY,
        methodology=methodology,
    )
    card_id = "sc_" + hashlib.sha256(canonical_json(fields).encode()).hexdigest()[:16]
    return SourceCard(card_id=card_id, **fields)
