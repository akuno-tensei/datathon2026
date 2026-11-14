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
    ):
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
            want("correlation_matrix")  # pas encore implémenté : consigné comme manque, pas simulé

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

    def run(self, request_text: str, profile_source: Union[str, Dict[str, Any], InvestorProfile], assets: List[Dict[str, str]]) -> RunResult:
        """`assets` : lignes de l'univers (au moins `ticker` et `name`), pour reconnaître les actifs cités."""
        ledger = Ledger()

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
