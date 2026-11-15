"""Agent 2.1 : enrichissement. Va chercher des données publiques absentes du jeu de départ, les normalise, les date,
et les écrit au registre avec leur provenance complète. Ne conclut rien : faits sourcés et signaux qualitatifs.

Chaîne pour chaque lacune comblable et chaque outil de collecte :
  fetch_* -> point_in_time_filter (obligatoire) -> extract_facts (textes) -> source_card -> ledger_write
Une source en échec, ou sans donnée utilisable avant `as_of`, produit un enregistrement `lacune_non_comblee`, jamais
une valeur devinée. Le noyau à 4 agents fonctionne sans cet agent (mode dégradé).
"""
from datetime import date
from typing import Dict, List, Mapping, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict

from ..orchestrator.ledger import Ledger
from ..orchestrator.profile import InvestorProfile
from ..orchestrator.request import ParsedRequest
from ..quant.datastore import DataStore
from .config import TOOL_KIND
from .facts import FactExtractor, extract_facts
from .fetchers import FETCH_TOOLS
from .ledger_write import ledger_write
from .models import EnrichmentRecord, FetchResult, Gap, NeedsReport, RawItem, SourceCard
from .needs import AUTO_FUNDAMENTALS, detect_needs
from .pit import point_in_time_filter
from .providers import Provider
from .records import gap_record, structured_record, text_record
from .source_card import SourceRegistry, source_card


class EnrichmentReport(BaseModel):
    """Bilan d'une exécution. Les chiffres sont des comptes, pas des jugements."""

    model_config = ConfigDict(extra="forbid")

    as_of: date
    written: List[str] = []  # record_id des données `ok` écrites
    gaps_written: List[str] = []  # record_id des lacunes non comblées écrites
    excluded_after_as_of: int = 0  # éléments postérieurs à as_of, écartés par le filtre temporel
    # Données de contexte automatiques (fondamentaux sans mot-clé dans la demande) restées sans réponse : comptées, sans lacune
    # écrite. Personne ne les a demandées : leur absence ne doit ni avertir ni changer un verdict.
    context_unavailable: List[str] = []
    rejected_facts: int = 0
    unfillable: List[str] = []  # besoins hors périmètre (« esg:BTC-USD : non_applicable_crypto »)

    @property
    def degraded(self) -> bool:
        """Vrai si au moins un besoin comblable n'a rien produit : l'orchestrateur doit le dire."""
        return bool(self.gaps_written)


class EnrichmentAgent:
    def __init__(
        self,
        providers: Mapping[str, Provider],
        sources: Optional[SourceRegistry] = None,
        extractor: Optional[FactExtractor] = None,
    ):
        """`providers` : outil de collecte -> fournisseur (« fetch_esg », « fetch_news », ...). Un outil sans fournisseur
        produit une lacune `no_provider`."""
        self._providers = dict(providers)
        self._quiet = False  # vrai pendant un besoin de contexte automatique : ses échecs ne deviennent pas des lacunes
        self._sources = sources
        self._extractor = extractor

    # -- outils exposés à l'orchestrateur -----------------------------------
    def detect_needs(self, profile: InvestorProfile, request: ParsedRequest, store: DataStore,
                     as_of: Optional[date] = None, ledger: Optional[Ledger] = None, auto_context: bool = True) -> NeedsReport:
        report = detect_needs(profile, request, store, as_of, ledger, auto_context=auto_context)
        # Un besoin de contexte automatique sans source branchée n'est pas une lacune utile : il est retiré, sans bruit.
        keep = [g for g in report.gaps if g.triggered_by != [AUTO_FUNDAMENTALS] or any(t in self._providers for t in g.tools)]
        return report.model_copy(update={"gaps": keep})

    def run(self, needs: Union[NeedsReport, List[Gap]], ledger: Ledger, as_of: Optional[date] = None) -> EnrichmentReport:
        gaps = needs.gaps if isinstance(needs, NeedsReport) else needs
        as_of_r = as_of or (needs.as_of if isinstance(needs, NeedsReport) else None)
        if as_of_r is None:
            raise ValueError("as_of est obligatoire : le filtre temporel ne peut pas s'appliquer sans date d'analyse.")
        report = EnrichmentReport(as_of=as_of_r)
        fetched: Dict[tuple, FetchResult] = {}  # un même (outil, actif) n'est collecté qu'une fois par exécution

        for gap in gaps:
            if not gap.fillable:
                report.unfillable.append(f"{gap.kind}:{gap.ticker} : {gap.reason}")
                continue
            self._quiet = gap.triggered_by == [AUTO_FUNDAMENTALS]
            produced = 0
            silent: List[str] = []  # outils qui ont répondu sans rien produire d'utilisable (ni erreur, ni lacune déjà écrite)
            for tool in gap.tools:
                n, explained = self._run_tool(tool, gap, as_of_r, ledger, report, fetched)
                produced += n
                if n == 0 and not explained:
                    silent.append(tool)
            if produced == 0:
                for tool in silent:
                    self._gap(report, ledger, gap_record(
                        TOOL_KIND[tool], gap.kind, gap.ticker, as_of_r, tool, "no_matching_data",  # type: ignore[arg-type]
                        "Aucune donnée utilisable trouvée avant as_of. Ce n'est pas la preuve d'une absence de problème : "
                        "la lacune reste ouverte.",
                    ))
            self._quiet = False
        return report

    # -- interne --------------------------------------------------------------
    def _gap(self, report: EnrichmentReport, ledger: Ledger, record: EnrichmentRecord) -> None:
        if self._quiet:
            report.context_unavailable.append(f"{record.kind}:{record.ticker} ({record.reason_code})")
            return
        if ledger_write(ledger, record) is not None:
            report.gaps_written.append(record.record_id)

    def _run_tool(self, tool: str, gap: Gap, as_of: date, ledger: Ledger, report: EnrichmentReport,
                  fetched: Dict[tuple, FetchResult]) -> Tuple[int, bool]:
        """Retourne (nombre d'enregistrements ok, une lacune explicative a-t-elle déjà été écrite)."""
        kind = TOOL_KIND[tool]
        provider = self._providers.get(tool)
        if provider is None:
            self._gap(report, ledger, gap_record(
                kind, gap.kind, gap.ticker, as_of, tool, "no_provider", f"Aucun fournisseur branché pour {tool}."))  # type: ignore[arg-type]
            return 0, True

        key = (tool, gap.ticker)
        first_call = key not in fetched
        if first_call:
            fetched[key] = FETCH_TOOLS[tool](provider, gap.ticker, as_of)
        result = fetched[key]
        if result.error_code:  # source inaccessible ou limitée : lacune, pas de supposition
            self._gap(report, ledger, gap_record(
                kind, gap.kind, gap.ticker, as_of, tool, result.error_code, result.error_message or "Source en échec.",  # type: ignore[arg-type]
                getattr(provider, "source_id", None)))
            return 0, True

        filtered = point_in_time_filter(result.items, as_of)  # obligatoire, avant toute écriture
        if first_call:
            report.excluded_after_as_of += len(filtered.excluded)
        if not filtered.kept:
            reason = "no_data_before_as_of" if filtered.excluded else "no_data"
            msg = (f"{len(filtered.excluded)} élément(s) trouvé(s), tous publiés après as_of ({as_of}) : écartés."
                   if filtered.excluded else "La source n'a renvoyé aucun élément.")
            self._gap(report, ledger, gap_record(
                kind, gap.kind, gap.ticker, as_of, tool, reason, msg, getattr(provider, "source_id", None)))  # type: ignore[arg-type]
            return 0, True

        produced = 0
        for item in filtered.kept:
            produced += self._write_item(item, tool, gap, as_of, ledger, report)
        return produced, False

    def _card(self, item: RawItem, from_text: bool, n_facts: int = 0) -> SourceCard:
        processing = [f"Collecte via {item.source_id}.", "Filtre temporel appliqué (publication <= as_of)."]
        if from_text:
            processing.append(f"Extraction de {n_facts} fait(s) par validation d'extrait mot pour mot ; texte source non conservé.")
        if item.from_cache:
            processing.append("Servi depuis le cache local (appel en direct non utilisé).")
        return source_card(item.source_id, item.retrieved_on, processing=processing, methodology=item.methodology, registry=self._sources)

    def _write_item(self, item: RawItem, tool: str, gap: Gap, as_of: date, ledger: Ledger, report: EnrichmentReport) -> int:
        written = 0
        # Pour les controverses, une donnée ESG ne compte que si elle documente des controverses.
        answers_need = not (gap.kind == "controversies" and item.kind == "esg" and "controversies" not in (item.structured or {}))
        if item.structured is not None and answers_need:
            card = self._card(item, from_text=False)
            rec = structured_record(item, card, gap.kind, as_of, tool)
            if ledger_write(ledger, rec, card) is not None:
                report.written.append(rec.record_id)
            written += 1
        if item.text and item.text.strip():
            topic = gap.kind if gap.kind in ("esg", "controversies", "fundamentals") else None
            extraction = extract_facts(
                item.text, url=item.url, date_publication=item.date_publication, date_observation=item.date_observation,
                topic=topic, as_of=as_of, extractor=self._extractor,
            )
            report.rejected_facts += len(extraction.rejected)
            if extraction.facts:
                card = self._card(item, from_text=True, n_facts=len(extraction.facts))
                rec = text_record(item, extraction.facts, card, gap.kind, as_of, tool)
                if ledger_write(ledger, rec, card) is not None:
                    report.written.append(rec.record_id)
                written += 1
        return written
