"""detect_needs : compare les besoins (profil + demande) aux données disponibles et liste les lacunes comblables.

Les déclencheurs viennent des champs du profil (thèmes préférés ou exclus) et du texte de la demande : aucune règle
par persona. Le jeu de départ ne contient ni ESG, ni controverses, ni fondamentaux, ni nouvelles ; une lacune disparaît
seulement si le registre contient déjà une donnée `ok` pour le même actif et le même besoin.
"""
import hashlib
from datetime import date
from typing import Dict, Iterable, List, Optional, Set, Tuple

from ..orchestrator.ledger import Ledger
from ..orchestrator.profile import InvestorProfile
from ..orchestrator.request import ParsedRequest
from ..quant.datastore import DataStore
from ..quant.windows import resolve_as_of
from .config import APPLICABLE_CLASSES, CONTROVERSY_THEMES, ESG_THEMES, NEED_TOOLS, REQUEST_TRIGGERS
from .models import Gap, NeedKind, NeedsReport


def _profile_triggers(profile: InvestorProfile) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    criteria = [("exclusion", c) for c in profile.hard_exclusions] + [("preference", c) for c in profile.soft_preferences]
    for origin, c in criteria:
        if c.kind != "theme":
            continue
        value = c.value.strip().lower()
        label = f"profile:{origin}:theme:{value}"
        if value in ESG_THEMES:
            out.setdefault("esg", []).append(label)
        if value in CONTROVERSY_THEMES:
            out.setdefault("controversies", []).append(label)
    return out


def _request_triggers(request: ParsedRequest) -> Dict[str, List[str]]:
    return {need: [f"request:{need}"] for need, rx in REQUEST_TRIGGERS.items() if rx.search(request.raw_text)}


def _covered(ledger: Optional[Ledger]) -> Set[Tuple[str, str]]:
    """(besoin, actif) déjà comblés par une donnée `ok` du registre."""
    if ledger is None:
        return set()
    return {(e.payload["need"], e.payload["ticker"]) for e in ledger.entries("enrichment") if e.payload.get("status") == "ok"}


def _need_id(kind: str, ticker: str, as_of: date) -> str:
    return "need_" + hashlib.sha256(f"{kind}|{ticker}|{as_of.isoformat()}".encode()).hexdigest()[:12]


def detect_needs(
    profile: InvestorProfile,
    request: ParsedRequest,
    store: DataStore,
    as_of: Optional[date] = None,
    ledger: Optional[Ledger] = None,
) -> NeedsReport:
    as_of_r = resolve_as_of(store, as_of)
    triggers: Dict[str, List[str]] = {}
    for source in (_profile_triggers(profile), _request_triggers(request)):
        for need, labels in source.items():
            triggers.setdefault(need, []).extend(labels)

    covered = _covered(ledger)
    gaps: List[Gap] = []

    def add(kind: NeedKind, ticker: str, why: List[str], asset: Optional[dict]) -> None:
        if (kind, ticker) in covered:
            return
        reason: Optional[str] = None
        if asset is None:
            reason = "hors_univers"
        elif asset["asset_class"] not in APPLICABLE_CLASSES[kind]:
            reason = f"non_applicable_{asset['asset_class']}"
        gaps.append(Gap(
            need_id=_need_id(kind, ticker, as_of_r), kind=kind, ticker=ticker, triggered_by=sorted(set(why)),
            fillable=reason is None, reason=reason, tools=list(NEED_TOOLS[kind]) if reason is None else [],
        ))

    for ticker in list(dict.fromkeys(request.tickers)):
        asset = store.asset(ticker)
        for kind in ("esg", "controversies", "fundamentals", "news"):
            if kind in triggers:
                add(kind, ticker, triggers[kind], asset)  # type: ignore[arg-type]
        # ESG « par transparence » : pour un FNB, la composition renseigne l'ESG et les controverses de ses titres.
        if asset is not None and asset["asset_class"].startswith("etf") and ("esg" in triggers or "controversies" in triggers):
            add("etf_holdings", ticker, triggers.get("esg", []) + triggers.get("controversies", []), asset)

    return NeedsReport(as_of=as_of_r, profile_id=profile.profile_id, gaps=gaps)
