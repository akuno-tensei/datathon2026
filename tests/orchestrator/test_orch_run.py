import pytest

from alphaforge.orchestrator import GovernanceOutcome, Orchestrator, RuleBasedParser, ToolCall
from alphaforge.orchestrator.orchestrator import MAX_FEEDBACK_ITERATIONS


@pytest.fixture
def orch(analyst):
    return Orchestrator(analyst)


def _steps(result):
    return [(s.step_id, s.agent, s.action) for s in result.plan.steps]


def _tools_run(result):
    return [e.payload["tool"] for e in result.ledger.entries("step")]


# ---- profil et demande ----------------------------------------------------


def test_unknown_profile_stops_before_anything(orch, assets):
    r = orch.run("Évalue AAPL", "personne", assets)
    assert r.status == "profile_invalid" and r.plan is None
    assert [e.kind for e in r.ledger.entries()] == ["note"]


def test_contradictory_profile_stops(orch, assets, profile_dict):
    p = profile_dict(
        hard_exclusions=[{"kind": "sector", "value": "Energy"}],
        soft_preferences=[{"kind": "sector", "value": "Energy"}],
    )
    r = orch.run("Évalue AAPL", p, assets)
    assert r.status == "profile_invalid" and "Energy" in r.message
    assert r.ledger.entries("evidence") == []


def test_profile_warnings_do_not_stop_and_are_in_ledger(orch, assets, profile_dict):
    p = profile_dict(risk_tolerance="eleve", horizon_years_min=0, horizon_years_max=1)
    r = orch.run("Évalue AAPL", p, assets)
    assert r.status == "partial"
    assert [i.code for i in r.profile_validation.issues] == ["RISK_HORIZON_CONFLICT"]
    logged = r.ledger.entries("profile")[0].payload["validation"]["issues"]
    assert logged[0]["code"] == "RISK_HORIZON_CONFLICT"


def test_request_without_asset_asks_for_clarification(orch, assets, profile_dict):
    r = orch.run("Bonjour", profile_dict(), assets)
    assert r.status == "needs_clarification" and r.plan is None
    assert r.ledger.entries("evidence") == []


# ---- plan -----------------------------------------------------------------


def test_plan_structure_single_asset(orch, assets, profile_dict):
    r = orch.run("Évalue AAPL", profile_dict(), assets)
    assert _steps(r) == [
        ("s1", "analyste", "asset_lookup"),
        ("s2", "analyste", "compute_metrics"),
        ("s3", "analyste", "compute_metrics"),
        ("s4", "analyste", "stress_window"),
        ("s5", "gouvernance", "review"),
        ("s6", "explicabilite", "explain"),
    ]
    windows = [s.arguments["window"]["alias"] for s in r.plan.steps if s.action == "compute_metrics"]
    assert windows == ["complet", "2022"]


def test_plan_dependencies(orch, assets, profile_dict):
    r = orch.run("Évalue AAPL", profile_dict(), assets)
    by_id = {s.step_id: s for s in r.plan.steps}
    assert by_id["s5"].depends_on == ["s1", "s2", "s3", "s4"]
    assert by_id["s6"].depends_on == ["s5"]


def test_plan_is_deterministic(analyst, assets, profile_dict):
    a = Orchestrator(analyst).run("Compare AAPL et NVDA", profile_dict(), assets)
    b = Orchestrator(analyst).run("Compare AAPL et NVDA", profile_dict(), assets)
    assert a.plan == b.plan
    assert [e.evidence_id for e in a.ledger.entries("evidence")] == [e.evidence_id for e in b.ledger.entries("evidence")]


def test_comparison_plans_a_correlation_matrix(orch, assets, profile_dict):
    r = orch.run("Compare AAPL et NVDA", profile_dict(base_currency="USD"), assets)
    (step,) = [s for s in r.plan.steps if s.action == "correlation_matrix"]
    assert step.arguments == {"tickers": ["AAPL", "NVDA"], "window": {"alias": "complet"}, "base_currency": "USD"}
    assert "correlation_matrix" in _tools_run(r) and r.plan.unavailable_tools == []


def test_scenario_plans_an_equal_weight_portfolio_replay(orch, assets, profile_dict):
    r = orch.run("Construis un scénario de portefeuille avec AAPL, NVDA et RY.TO", profile_dict(base_currency="USD"), assets)
    (step,) = [s for s in r.plan.steps if s.action == "portfolio_metrics"]
    assert [x["ticker"] for x in step.arguments["positions"]] == ["AAPL", "NVDA", "RY.TO"]
    assert sum(x["weight"] for x in step.arguments["positions"]) == pytest.approx(1.0)
    assert step.arguments["rebalancing"] == "rebalanced" and step.arguments["base_currency"] == "USD"
    assert "portfolio_metrics" in _tools_run(r) and r.plan.unavailable_tools == []
    c = orch.run("Compare AAPL et NVDA", profile_dict(), assets)
    assert "portfolio_metrics" not in _tools_run(c)  # une comparaison ne construit pas d'allocation


def test_missing_tool_is_recorded_not_faked(analyst, assets, profile_dict):
    analyst.tools = analyst.tools - {"portfolio_metrics"}  # l'analyste ne l'offre pas
    r = Orchestrator(analyst).run("Construis un scénario de portefeuille avec AAPL et NVDA", profile_dict(), assets)
    assert "portfolio_metrics" in r.plan.unavailable_tools
    notes = [e.payload["code"] for e in r.ledger.entries("note")]
    assert "tool_unavailable" in notes
    assert "portfolio_metrics" not in _tools_run(r)


def test_single_asset_does_not_ask_for_correlation(orch, assets, profile_dict):
    r = orch.run("Évalue AAPL", profile_dict(), assets)
    assert r.plan.unavailable_tools == []


def test_as_of_is_forwarded_to_tools(analyst, assets, profile_dict):
    from datetime import date

    r = Orchestrator(analyst, as_of=date(2023, 6, 30)).run("Évalue AAPL", profile_dict(), assets)
    assert all(s.arguments["as_of"] == "2023-06-30" for s in r.plan.steps if s.agent == "analyste")
    assert r.ledger.entries("evidence")[0].payload["as_of"] == "2023-06-30"


def test_profile_base_currency_is_forwarded_to_metric_tools(analyst, assets, profile_dict):
    r = Orchestrator(analyst).run("Évalue AAPL", profile_dict(base_currency="USD"), assets)
    priced = [s for s in r.plan.steps if s.action in ("compute_metrics", "stress_window")]
    assert priced and all(s.arguments["base_currency"] == "USD" for s in priced)
    assert {e.payload["params"]["base_currency"] for e in r.ledger.entries("evidence") if e.payload["tool"] == "compute_metrics"} == {"USD"}


def test_missing_window_alias_is_skipped(analyst, assets, profile_dict):
    analyst.window_aliases = {"complet"}
    r = Orchestrator(analyst).run("Évalue AAPL", profile_dict(), assets)
    assert [s.arguments["window"]["alias"] for s in r.plan.steps if s.action == "compute_metrics"] == ["complet"]


# ---- exécution de l'analyste ----------------------------------------------


def test_evidence_written_for_each_analyst_step(orch, assets, profile_dict):
    r = orch.run("Compare AAPL et NVDA", profile_dict(), assets)
    n_analyst = sum(1 for s in r.plan.steps if s.agent == "analyste")
    assert len(r.ledger.entries("evidence")) == n_analyst == len(r.ledger.entries("step"))
    assert all(e.evidence_id for e in r.ledger.entries("evidence"))


def test_unknown_symbol_becomes_refused_evidence(orch, assets, profile_dict):
    r = orch.run("Évalue ZZZZ.TO", profile_dict(), assets)
    assert r.status == "partial"
    ev = r.ledger.entries("evidence")
    assert len(ev) == 1 and ev[0].payload["status"] == "refused"
    assert ev[0].payload["refusal_reason"] == "asset_not_found"
    assert [s.action for s in r.plan.steps if s.agent == "analyste"] == ["asset_lookup"]  # pas de métriques


def test_known_and_unknown_symbols_together(orch, assets, profile_dict):
    r = orch.run("Compare AAPL et ZZZZ.TO", profile_dict(), assets)
    statuses = {e.payload["tool"] + ":" + str(e.payload["params"].get("query", e.payload["params"].get("ticker"))): e.payload["status"]
                for e in r.ledger.entries("evidence")}
    assert statuses["asset_lookup:ZZZZ.TO"] == "refused"
    assert statuses["asset_lookup:AAPL"] == "ok"


def test_invalid_follow_up_tool_is_logged_as_error(analyst, assets, profile_dict):
    calls = []

    def gov(profile, request, ledger):
        calls.append(1)
        return GovernanceOutcome(verdict={"status": "avertissement"}, follow_up=[ToolCall(tool="outil_inexistant")] if len(calls) == 1 else [])

    r = Orchestrator(analyst, governance=gov).run("Évalue AAPL", profile_dict(), assets)
    errors = [e.payload for e in r.ledger.entries("step") if e.payload["status"] == "error"]
    assert len(errors) == 1 and errors[0]["tool"] == "outil_inexistant"
    assert len(calls) == 2  # le run continue après l'erreur


def test_invalid_arguments_are_logged_as_error(analyst, assets, profile_dict):
    calls = []

    def gov(profile, request, ledger):
        calls.append(1)
        bad = ToolCall(tool="compute_metrics", arguments={"ticker": "AAPL"})  # fenêtre manquante
        return GovernanceOutcome(verdict={}, follow_up=[bad] if len(calls) == 1 else [])

    r = Orchestrator(analyst, governance=gov).run("Évalue AAPL", profile_dict(), assets)
    assert any(e.payload["status"] == "error" for e in r.ledger.entries("step"))


# ---- gouvernance, boucle de retour, explicabilité ----------------------------


def test_without_other_agents_result_is_partial(orch, assets, profile_dict):
    r = orch.run("Évalue AAPL", profile_dict(), assets)
    assert r.status == "partial" and r.pending_agents == ["gouvernance", "explicabilite"] and r.answer is None


def test_governance_only_is_partial(analyst, assets, profile_dict):
    gov = lambda p, q, l: GovernanceOutcome(verdict={"status": "conforme"})
    r = Orchestrator(analyst, governance=gov).run("Évalue AAPL", profile_dict(), assets)
    assert r.status == "partial" and r.pending_agents == ["explicabilite"]
    assert r.ledger.entries("verdict")[0].agent == "gouvernance"


def test_explainer_without_governance_is_not_called(analyst, assets, profile_dict):
    called = []
    explain = lambda p, l: called.append(1) or "texte"
    r = Orchestrator(analyst, explainer=explain).run("Évalue AAPL", profile_dict(), assets)
    assert called == [] and r.answer is None and "gouvernance" in r.pending_agents


def test_complete_run(analyst, assets, profile_dict):
    gov = lambda p, q, l: GovernanceOutcome(verdict={"status": "conforme"})
    explain = lambda p, l: f"{len(l.evidence())} preuves"
    r = Orchestrator(analyst, governance=gov, explainer=explain).run("Évalue AAPL", profile_dict(), assets)
    assert r.status == "complete" and r.answer == "4 preuves"  # lookup + 2 fenêtres + stress_window and r.pending_agents == []


def test_governance_receives_profile_request_and_ledger(analyst, assets, profile_dict):
    seen = {}

    def gov(profile, request, ledger):
        seen.update(pid=profile.profile_id, intent=request.intent, n=len(ledger.evidence()), flags=request.scope_flags)
        return GovernanceOutcome(verdict={})

    Orchestrator(analyst, governance=gov).run("Prédis AAPL", profile_dict(), assets)
    assert seen == {"pid": "test", "intent": "evaluate_asset", "n": 4, "flags": ["asks_prediction"]}


def test_follow_up_is_executed_then_governance_reevaluates(analyst, assets, profile_dict):
    seen_evidence = []

    def gov(profile, request, ledger):
        seen_evidence.append(len(ledger.evidence()))
        if len(seen_evidence) == 1:
            return GovernanceOutcome(
                verdict={"status": "avertissement"},
                follow_up=[ToolCall(tool="asset_lookup", arguments={"query": "RY.TO"})],
            )
        return GovernanceOutcome(verdict={"status": "conforme"})

    r = Orchestrator(analyst, governance=gov).run("Évalue AAPL", profile_dict(), assets)
    assert seen_evidence == [4, 5]  # la preuve de l'alternative est visible au second passage
    assert [e.payload["status"] for e in r.ledger.entries("verdict")] == ["avertissement", "conforme"]
    assert not any(e.payload.get("code") == "feedback_limit_reached" for e in r.ledger.entries("note"))


def test_feedback_loop_is_bounded(analyst, assets, profile_dict):
    calls = []
    tickers = ["RY.TO", "CNQ.TO", "CNR.TO", "NVDA"]

    def gov(profile, request, ledger):
        calls.append(1)
        return GovernanceOutcome(
            verdict={"status": "bloqué"},
            follow_up=[ToolCall(tool="asset_lookup", arguments={"query": tickers[len(calls) - 1]})],
        )

    r = Orchestrator(analyst, governance=gov).run("Évalue AAPL", profile_dict(), assets)
    assert len(calls) == 1 + MAX_FEEDBACK_ITERATIONS == 3
    notes = [e.payload["code"] for e in r.ledger.entries("note")]
    assert "feedback_limit_reached" in notes
    assert len([e for e in r.ledger.entries("step") if e.payload["step_id"].startswith("fb")]) == MAX_FEEDBACK_ITERATIONS


def test_repeated_follow_up_does_not_duplicate_evidence(analyst, assets, profile_dict):
    n = []

    def gov(profile, request, ledger):
        n.append(1)
        return GovernanceOutcome(verdict={}, follow_up=[ToolCall(tool="asset_lookup", arguments={"query": "AAPL"})] if len(n) < 3 else [])

    r = Orchestrator(analyst, governance=gov).run("Évalue AAPL", profile_dict(), assets)
    ids = [e.evidence_id for e in r.ledger.entries("evidence")]
    assert len(ids) == len(set(ids))


# ---- indépendance vis-à-vis des personas ---------------------------------


def test_orchestrator_never_computes_or_judges(analyst, assets, profile_dict):
    r = Orchestrator(analyst).run("Évalue AAPL", profile_dict(), assets)
    agents = {e.agent for e in r.ledger.entries()}
    assert agents <= {"orchestrateur", "analyste"}  # aucune entrée de verdict ni d'explication
    assert r.ledger.entries("verdict") == []


def test_same_request_same_plan_for_different_profiles(analyst, assets, profile_dict):
    a = Orchestrator(analyst).run("Évalue AAPL", profile_dict(profile_id="a", risk_tolerance="faible"), assets)
    b = Orchestrator(analyst).run("Évalue AAPL", profile_dict(profile_id="b", risk_tolerance="eleve"), assets)
    assert a.plan == b.plan  # le profil influence les limites (gouvernance), pas l'enchaînement des outils


def test_custom_parser_is_used(analyst, assets, profile_dict):
    class Fixed(RuleBasedParser):
        name = "fixe"

        def parse(self, text, assets):
            return super().parse("Évalue NVDA", assets).model_copy(update={"parser": "fixe"})

    r = Orchestrator(analyst, parser=Fixed()).run("n'importe quoi", profile_dict(), assets)
    assert r.request.parser == "fixe" and r.request.tickers == ["NVDA"]


def test_weights_from_the_request_reach_the_portfolio_tool_unchanged(orch, assets, profile_dict):
    r = orch.run("Construis un portefeuille avec 60 % AAPL et 40 % NVDA sans rééquilibrage", profile_dict(), assets)
    (step,) = [s for s in r.plan.steps if s.action == "portfolio_metrics"]
    assert step.arguments["positions"] == [{"ticker": "AAPL", "weight": 0.6}, {"ticker": "NVDA", "weight": 0.4}]
    assert step.arguments["weights_basis"] == "user" and step.arguments["rebalancing"] == "buy_and_hold"
    ev = next(e.payload for e in r.ledger.entries("evidence") if e.payload["tool"] == "portfolio_metrics")
    assert ev["status"] != "refused" and ev["params"]["weights_basis"] == "user" and ev["value"]["rebalancing"] == "buy_and_hold"


def test_without_weights_equal_weights_are_declared_as_an_assumption(orch, assets, profile_dict):
    r = orch.run("Construis un scénario de portefeuille avec AAPL et NVDA", profile_dict(), assets)
    (step,) = [s for s in r.plan.steps if s.action == "portfolio_metrics"]
    assert step.arguments["weights_basis"] == "assumed_equal" and step.arguments["rebalancing"] == "rebalanced"


def test_incomplete_or_wrong_weights_are_refused_by_the_analyst_not_corrected(orch, assets, profile_dict):
    for text in ("60 % AAPL et NVDA", "60 % AAPL et 30 % NVDA"):
        r = orch.run(text, profile_dict(), assets)
        ev = next(e.payload for e in r.ledger.entries("evidence") if e.payload["tool"] == "portfolio_metrics")
        assert ev["status"] == "refused" and ev["error_code"] == "INVALID_WEIGHTS", text
        assert "somme" in ev["notices"][-1]["message"]
