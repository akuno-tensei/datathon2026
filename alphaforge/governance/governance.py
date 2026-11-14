"""Agent 3 : gouvernance. Lit le registre, vérifie la cohérence avec le profil, rend un verdict visible.

Ne calcule aucune métrique : elle compare des preuves de l'analyste à des limites dérivées du profil. Quand une
idée dépasse une limite, elle demande à l'orchestrateur de faire chiffrer des alternatives (`follow_up`).
"""
from typing import Any, Dict, List, Optional

from ..orchestrator.orchestrator import GovernanceOutcome, ToolCall
from ..orchestrator.ledger import Ledger
from ..orchestrator.profile import InvestorProfile
from ..orchestrator.request import ParsedRequest
from ..orchestrator.tools import validate_profile
from .checks import (
    ALTERNATIVE_TRIGGER_CODES, check_data_gaps, check_hard_constraints, check_horizon_fit, check_risk_limits,
    check_scope_limits, check_soft_preferences, derive_constraints, status_of, worst_status,
)
from .config import GovernanceConfig
from .facts import AssetFacts, build_facts, ledger_notes
from .models import AssetAssessment, Constraints, GovernanceVerdict, Reason

AGENT = "gouvernance"


class Governance:
    """Appelable : `Governance(assets)(profile, request, ledger) -> GovernanceOutcome`. Compatible avec `Orchestrator`."""

    def __init__(self, assets: List[Dict[str, Any]], config: Optional[GovernanceConfig] = None):
        self._assets = assets
        self._cfg = config or GovernanceConfig()

    # -- évaluation d'un actif --------------------------------------------
    def assess(self, profile: InvestorProfile, constraints: Constraints, f: AssetFacts, alternative_for: Optional[List[str]] = None) -> AssetAssessment:
        reasons: List[Reason] = []
        reasons += check_data_gaps(f)
        reasons += check_hard_constraints(profile, constraints, f)
        reasons += check_soft_preferences(profile, self._cfg, f)
        reasons += check_risk_limits(constraints, f)
        reasons += check_horizon_fit(profile, f)
        return AssetAssessment(
            ticker=f.ticker, status=status_of(reasons), reasons=reasons, data_notes=list(f.note_codes), alternative_for=alternative_for or []
        )

    # -- alternatives -----------------------------------------------------
    def _peers(self, ticker: str) -> List[str]:
        """Actifs comparables : même secteur ; à défaut, même classe d'actifs. Ordre alphabétique (déterministe)."""
        me = next((a for a in self._assets if a["ticker"] == ticker), None)
        if me is None:
            return []
        same_sector = sorted(a["ticker"] for a in self._assets if a["sector"] == me["sector"] and a["ticker"] != ticker)
        if same_sector:
            return same_sector
        return sorted(a["ticker"] for a in self._assets if a["asset_class"] == me["asset_class"] and a["ticker"] != ticker)

    def _admissible(self, profile: InvestorProfile, constraints: Constraints, ticker: str) -> bool:
        """Une alternative ne doit pas être bloquée d'emblée (exclusion ou classe non permise)."""
        a = next(x for x in self._assets if x["ticker"] == ticker)
        f = AssetFacts(ticker=ticker, asset=a)
        return not any(r.severity == "block" for r in check_hard_constraints(profile, constraints, f))

    def _follow_up(self, profile, constraints, primary: Dict[str, AssetAssessment], facts: Dict[str, AssetFacts]) -> List[ToolCall]:
        already = [t for t in facts if t not in primary]
        if already:  # des alternatives ont déjà été chiffrées : une seule relance par demande
            return []
        flagged = [t for t, a in primary.items() if any(r.code in ALTERNATIVE_TRIGGER_CODES for r in a.reasons)]
        if not flagged:
            return []
        candidates: List[str] = []
        for t in flagged:
            for p in self._peers(t):
                if p not in primary and p not in candidates and self._admissible(profile, constraints, p):
                    candidates.append(p)
        candidates = candidates[: self._cfg.max_alternatives]

        # Mêmes fenêtres, même as_of et même devise que l'analyse initiale : les chiffres restent comparables.
        windows = [w for t in flagged for w in facts[t].windows]
        aliases = list(dict.fromkeys(w.alias for w in windows if w.alias)) or ["complet"]
        as_of = next((w.as_of for w in windows if w.as_of), None)
        currency = next((w.base_currency for w in windows if w.base_currency), None)
        calls: List[ToolCall] = []
        for t in candidates:
            for alias in aliases:
                args: Dict[str, Any] = {"ticker": t, "window": {"alias": alias}}
                if as_of:
                    args["as_of"] = as_of
                if currency:
                    args["base_currency"] = currency
                calls.append(ToolCall(tool="compute_metrics", arguments=args))
        return calls

    # -- point d'entrée ---------------------------------------------------
    def __call__(self, profile: InvestorProfile, request: ParsedRequest, ledger: Ledger) -> GovernanceOutcome:
        facts = build_facts(ledger.evidence())
        constraints = derive_constraints(profile, self._cfg)
        primary_keys = list(dict.fromkeys(request.tickers + request.unknown_tickers))

        primary = {t: self.assess(profile, constraints, facts.get(t, AssetFacts(ticker=t))) for t in primary_keys}

        alternatives: List[AssetAssessment] = []
        for t, f in facts.items():
            if t in primary or not f.windows:
                continue
            owners = [p for p in primary if p in facts and t in self._peers(p) and primary[p].status != "conforme"]
            alternatives.append(self.assess(profile, constraints, f, alternative_for=owners))

        global_reasons: List[Reason] = []
        for issue in validate_profile(profile).issues:
            global_reasons.append(
                Reason(check="profile", code=issue.code, severity="warn" if issue.severity == "warning" else "block", message=issue.message)
            )
        for note in ledger_notes(ledger.entries(), "tool_unavailable"):
            global_reasons.append(Reason(check="data_gaps", code="TOOL_UNAVAILABLE", severity="warn", message=note["message"]))
        refusals = check_scope_limits(list(request.scope_flags))

        follow_up = self._follow_up(profile, constraints, primary, facts)
        flagged = [t for t, a in primary.items() if any(r.code in ALTERNATIVE_TRIGGER_CODES for r in a.reasons)]
        if flagged and not follow_up and not alternatives:
            global_reasons.append(
                Reason(
                    check="data_gaps", code="NO_ALTERNATIVE", severity="info",
                    message="Aucune alternative comparable de l'univers ne respecte les exclusions et les classes permises du profil.",
                    measures={"flagged": flagged},
                )
            )

        status = worst_status(
            [a.status for a in primary.values()] + [status_of(global_reasons)] + [r.status for r in refusals]
        )
        verdict = GovernanceVerdict(
            status=status,  # type: ignore[arg-type]
            constraints=constraints,
            assets=list(primary.values()),
            alternatives=alternatives,
            global_reasons=global_reasons,
            scope_refusals=refusals,
        )
        return GovernanceOutcome(verdict=verdict.model_dump(mode="json"), follow_up=follow_up)
