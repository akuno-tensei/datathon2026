"""Agent 1 : orchestrateur. Charge le profil, valide, interprète la demande, planifie, route, consigne.

Ne calcule rien et ne rend aucun verdict. Les agents Gouvernance et Explicabilité sont injectés : tant qu'ils sont
absents, l'exécution s'arrête proprement (`status="partial"`) en laissant analyste et registre exploitables.
"""
from datetime import date
from typing import Any, Callable, Dict, List, Literal, Optional, Protocol, Set, Union

from pydantic import BaseModel, ConfigDict, Field

from ..quant import DataStore, Evidence, run_tool
from ..quant.registry import TOOLS
from .ledger import Ledger
from .profile import InvestorProfile, ProfileValidation
from .request import ParsedRequest, RuleBasedParser
from .tools import ProfileError, ledger_write, load_profile, validate_profile

AGENT = "orchestrateur"
MAX_FEEDBACK_ITERATIONS = 2  # boucle Gouvernance -> Orchestrateur -> Analyste (Agents.md, 1.3)
DEFAULT_WINDOWS = ("complet", "2022")  # fenêtre complète et fenêtre de stress, pour tester la sensibilité


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolCall(_Strict):
    tool: str
    arguments: Dict[str, Any] = {}


class PlanStep(_Strict):
    step_id: str
    agent: Literal["analyste", "gouvernance", "explicabilite"]
    action: str
    arguments: Dict[str, Any] = {}
    depends_on: List[str] = []


class Plan(_Strict):
    steps: List[PlanStep]
    unavailable_tools: List[str] = []  # outils souhaités mais non implémentés : consignés, pas contournés


# ---- ports vers les autres agents ---------------------------------------


class AnalystPort(Protocol):
    tools: Set[str]
    window_aliases: Set[str]

    def run(self, call: ToolCall) -> Evidence: ...


class QuantAnalyst:
    """Adaptateur vers `alphaforge.quant` : exécute un outil par son nom, avec validation du schéma."""

    def __init__(self, store: DataStore):
        self._store = store
        self.store = store  # lecture seule : l'agent d'enrichissement compare les besoins à ce que l'univers contient
        self.tools: Set[str] = set(TOOLS)
        self.window_aliases: Set[str] = set(store.config.window_aliases)

    def run(self, call: ToolCall) -> Evidence:
        return run_tool(self._store, call.tool, call.arguments)


class GovernanceOutcome(_Strict):
    """Retour de la gouvernance. `follow_up` demande à l'analyste de chiffrer un écart ou d'évaluer une alternative."""

    verdict: Dict[str, Any]
    follow_up: List[ToolCall] = []


GovernanceFn = Callable[[InvestorProfile, ParsedRequest, Ledger], GovernanceOutcome]
ExplainFn = Callable[[InvestorProfile, Ledger], str]


# ---- résultat ------------------------------------------------------------

RunStatus = Literal["complete", "partial", "needs_clarification", "profile_invalid"]


class RunResult(_Strict):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    status: RunStatus
    message: Optional[str] = None
    profile_validation: Optional[ProfileValidation] = None
    request: Optional[ParsedRequest] = None
    plan: Optional[Plan] = None
    pending_agents: List[str] = []
    answer: Optional[str] = None
    ledger: Ledger = Field(exclude=True)


# ---- orchestrateur -------------------------------------------------------


class Orchestrator:
    def __init__(
        self,
        analyst: AnalystPort,
        governance: Optional[GovernanceFn] = None,
        explainer: Optional[ExplainFn] = None,
        parser: Optional[Any] = None,
        as_of: Optional[date] = None,
        on_entry: Optional[Callable[[Any], None]] = None,
        enrichment: Optional[Any] = None,
    ):
        # Agent d'enrichissement (facultatif) : `detect_needs(profil, demande, store, as_of, registre)` puis `run(besoins, registre, as_of)`.
        # Sans lui, le noyau à 4 agents fonctionne seul (mode dégradé, dit explicitement dans le registre).
        self._enrichment = enrichment
        self._on_entry = on_entry  # observateur facultatif des écritures du registre (affichage en direct)
        self._analyst = analyst
        self._governance = governance
        self._explainer = explainer
        self._parser = parser or RuleBasedParser()
        self._as_of = as_of

    # -- planification -----------------------------------------------------
    def build_plan(self, request: ParsedRequest, profile: Optional[InvestorProfile] = None) -> Plan:
        """Plan déterministe : la même demande et les mêmes outils disponibles donnent le même plan.

        La devise de base du profil est transmise aux outils chiffrés : les métriques sont exprimées dans la devise
        de l'investisseur, pas dans le défaut de l'analyste.
        """
        steps: List[PlanStep] = []
        unavailable: List[str] = []
        extra = {"as_of": self._as_of.isoformat()} if self._as_of else {}
        money = {"base_currency": profile.base_currency} if profile is not None else {}

        def add(agent: str, action: str, args: Dict[str, Any], deps: Optional[List[str]] = None) -> str:
            sid = f"s{len(steps) + 1}"
            steps.append(PlanStep(step_id=sid, agent=agent, action=action, arguments=args, depends_on=deps or []))  # type: ignore[arg-type]
            return sid

        def want(tool: str) -> bool:
            if tool in self._analyst.tools:
                return True
            if tool not in unavailable:
                unavailable.append(tool)
            return False

        windows = [w for w in DEFAULT_WINDOWS if w in self._analyst.window_aliases]
        analyst_ids: List[str] = []

        # Les symboles hors univers passent aussi par asset_lookup : le refus devient une preuve visible.
        for sym in request.tickers + request.unknown_tickers:
            if want("asset_lookup"):
                analyst_ids.append(add("analyste", "asset_lookup", {"query": sym, **extra}))

        for t in request.tickers:
            if want("compute_metrics"):
                for w in windows:
                    analyst_ids.append(
                        add("analyste", "compute_metrics", {"ticker": t, "window": {"alias": w}, **money, **extra})
                    )
            # Sensibilité à la période (fenêtres par défaut de l'analyste) : le mandat demande de mettre les résultats à l'épreuve.
            if want("stress_window"):
                analyst_ids.append(add("analyste", "stress_window", {"ticker": t, **money, **extra}))

        if len(request.tickers) >= 2 and request.intent in ("compare_assets", "build_scenario"):
            # Diversification : corrélations des rendements sur la fenêtre complète, alignement choisi par l'analyste.
            if want("correlation_matrix"):
                analyst_ids.append(add("analyste", "correlation_matrix",
                                       {"tickers": list(request.tickers), "window": {"alias": "complet"}, **money, **extra}))
        if len(request.tickers) >= 2 and request.intent == "build_scenario":
            # Rejeu historique d'une allocation fictive. Si la demande donne des poids, on les transmet tels quels : jamais
            # complétés ni normalisés (incomplets ou de somme différente de 1, l'analyste refuse avec une erreur typée).
            # Sinon, pondération égale posée comme hypothèse de scénario. Dans les deux cas, l'origine est consignée.
            if want("portfolio_metrics"):
                if request.weights:
                    positions = [{"ticker": t, "weight": request.weights[t]} for t in request.tickers if t in request.weights]
                    basis = "user"
                else:
                    positions = [{"ticker": t, "weight": 1.0 / len(request.tickers)} for t in request.tickers]
                    basis = "assumed_equal"
                analyst_ids.append(add("analyste", "portfolio_metrics", {
                    "positions": positions, "window": {"alias": "complet"}, "rebalancing": request.rebalancing or "rebalanced",
                    "weights_basis": basis, **money, **extra}))
        gov = add("gouvernance", "review", {"scope_flags": list(request.scope_flags)}, analyst_ids)
        add("explicabilite", "explain", {}, [gov])
        return Plan(steps=steps, unavailable_tools=unavailable)

    # -- exécution ---------------------------------------------------------
    def _run_analyst(self, ledger: Ledger, call: ToolCall, step_id: str) -> None:
        try:
            ev = self._analyst.run(call)
        except (KeyError, ValueError) as e:  # outil inconnu ou arguments invalides : pas de preuve, mais une trace
            ledger_write(ledger, "step", AGENT, {"step_id": step_id, "tool": call.tool, "arguments": call.arguments, "status": "error", "error": str(e)})
            return
        ledger.write_evidence("analyste", ev)
        ledger_write(
            ledger, "step", AGENT,
            {"step_id": step_id, "tool": call.tool, "arguments": call.arguments, "status": ev.status, "evidence_id": ev.evidence_id},
            evidence_id=ev.evidence_id,
        )

    def _enrich(self, ledger: Ledger, profile: InvestorProfile, request: ParsedRequest, relaunch: bool = False) -> bool:
        """Détecte les besoins de données qui dépassent le jeu de départ et, s'il y en a de comblables, lance l'enrichissement.

        Ne tourne pas à chaque requête : sans besoin détecté, rien n'est écrit. Une panne de l'agent est consignée et
        n'arrête pas l'analyse. Retourne vrai si le registre a reçu des données ou des lacunes nouvelles.
        """
        store = getattr(self._analyst, "store", None)
        if store is None:
            ledger_write(ledger, "note", AGENT, {"code": "enrichment_failed", "message": "Enrichissement indisponible : l'analyste n'expose pas son magasin de données."})
            return False
        try:
            # Relance : les alternatives ne reçoivent pas les données de contexte automatiques (seulement ce que la demande évoque).
            extra_kw = {"auto_context": False} if relaunch else {}
            needs = self._enrichment.detect_needs(profile, request, store, self._as_of, ledger, **extra_kw)
            if not needs.gaps:
                return False
            fillable = [g for g in needs.gaps if g.fillable]
            label = ", ".join(f"{g.kind}:{g.ticker}" for g in fillable) or "aucun"
            ledger_write(ledger, "note", AGENT, {
                "code": "needs_relaunched" if relaunch else "needs_detected",
                "message": f"Besoins de données détectés : {len(fillable)} comblable(s) ({label}), {len(needs.gaps) - len(fillable)} hors périmètre.",
                "gaps": [g.model_dump(mode="json") for g in needs.gaps],
            })
            if not fillable:
                return False
            report = self._enrichment.run(needs, ledger, needs.as_of)
        except Exception as e:  # noqa: BLE001  (l'enrichissement est facultatif : sa panne ne doit jamais arrêter l'analyse)
            ledger_write(ledger, "note", AGENT, {"code": "enrichment_failed", "message": f"Enrichissement en échec ({type(e).__name__}) : le noyau poursuit sans lui."})
            return False
        ledger_write(ledger, "note", AGENT, {
            "code": "enrichment_done",
            "message": (f"Enrichissement : {len(report.written)} donnée(s) écrite(s), {len(report.gaps_written)} lacune(s) non comblée(s), "
                        f"{report.excluded_after_as_of} élément(s) postérieur(s) à as_of écarté(s)."
                        + (f" Données de contexte indisponibles, sans effet sur le verdict : {', '.join(getattr(report, 'context_unavailable', []))}."
                           if getattr(report, "context_unavailable", None) else "")),
            "written": len(report.written), "gaps": len(report.gaps_written), "excluded_after_as_of": report.excluded_after_as_of,
            "rejected_facts": report.rejected_facts, "degraded": bool(report.degraded),
        })
        return bool(report.written or report.gaps_written)

    def run(self, request_text: str, profile_source: Union[str, Dict[str, Any], InvestorProfile], assets: List[Dict[str, str]]) -> RunResult:
        """`assets` : lignes de l'univers (au moins `ticker` et `name`), pour reconnaître les actifs cités."""
        ledger = Ledger(on_write=self._on_entry)

        # 1. Profil
        try:
            profile = load_profile(profile_source)
        except ProfileError as e:
            ledger_write(ledger, "note", AGENT, {"code": e.reason, "message": e.message})
            return RunResult(status="profile_invalid", message=e.message, ledger=ledger)
        validation = validate_profile(profile)
        ledger_write(ledger, "profile", AGENT, {"profile": profile.model_dump(mode="json"), "validation": validation.model_dump(mode="json")})
        if not validation.valid:
            errors = "; ".join(i.message for i in validation.issues if i.severity == "error")
            return RunResult(status="profile_invalid", message=errors, profile_validation=validation, ledger=ledger)

        # 2. Demande
        request = self._parser.parse(request_text, assets)
        ledger_write(ledger, "request", AGENT, request.model_dump(mode="json"))
        if not request.tickers and not request.unknown_tickers:
            msg = "Aucun actif identifié dans la demande : précisez le ou les actifs à examiner."
            ledger_write(ledger, "note", AGENT, {"code": "needs_clarification", "message": msg})
            return RunResult(status="needs_clarification", message=msg, profile_validation=validation, request=request, ledger=ledger)

        # 3. Plan
        plan = self.build_plan(request, profile)
        ledger_write(ledger, "plan", AGENT, plan.model_dump(mode="json"))
        for tool in plan.unavailable_tools:
            ledger_write(ledger, "note", AGENT, {"code": "tool_unavailable", "message": f"Outil non disponible : {tool}. Lacune consignée, aucun substitut."})

        # 3b. Enrichissement, à la demande : seulement si un besoin de donnée dépasse le jeu de départ
        if self._enrichment is not None:
            self._enrich(ledger, profile, request)
        # 4. Analyste
        for step in plan.steps:
            if step.agent == "analyste":
                self._run_analyst(ledger, ToolCall(tool=step.action, arguments=step.arguments), step.step_id)

        result = RunResult(status="partial", profile_validation=validation, request=request, plan=plan, ledger=ledger)

        # 5. Gouvernance, avec boucle de retour bornée
        if self._governance is None:
            result.pending_agents.append("gouvernance")
        else:
            outcome = self._governance(profile, request, ledger)
            ledger_write(ledger, "verdict", "gouvernance", outcome.verdict)
            iteration = 0
            while outcome.follow_up and iteration < MAX_FEEDBACK_ITERATIONS:
                iteration += 1
                for i, call in enumerate(outcome.follow_up, 1):
                    self._run_analyst(ledger, call, f"fb{iteration}.{i}")
                outcome = self._governance(profile, request, ledger)
                ledger_write(ledger, "verdict", "gouvernance", outcome.verdict)
            if self._enrichment is not None:  # relance unique (Agents.md 2.4) : les alternatives chiffrées n'ont pas encore leurs données
                seen = set(request.tickers)
                extra = [t for t in dict.fromkeys(e["params"].get("ticker") for e in ledger.evidence() if e["tool"] == "compute_metrics")
                         if t and t not in seen]
                if extra and self._enrich(ledger, profile, request.model_copy(update={"tickers": extra}), relaunch=True):
                    outcome = self._governance(profile, request, ledger)
                    ledger_write(ledger, "verdict", "gouvernance", outcome.verdict)
            if outcome.follow_up:
                ledger_write(ledger, "note", AGENT, {"code": "feedback_limit_reached", "message": f"Limite de {MAX_FEEDBACK_ITERATIONS} itérations atteinte ; la tension reste non résolue."})

        # 6. Explicabilité
        if self._explainer is None:
            result.pending_agents.append("explicabilite")
        elif self._governance is not None:
            result.answer = self._explainer(profile, ledger)
            result.status = "complete"
        else:
            result.pending_agents.append("explicabilite")  # sans verdict, on n'explique pas
        return result
