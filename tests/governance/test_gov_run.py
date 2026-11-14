"""Gouvernance branchée sur l'orchestrateur, avec un univers synthétique à volatilités connues."""
from datetime import date

import pytest

from alphaforge.governance import Governance, GovernanceConfig, GovernanceVerdict
from alphaforge.orchestrator import GovernanceOutcome, Orchestrator, load_profile

from helpers_gov import last_verdict

NO_PREFS = {}


def asset(v, ticker, key="assets"):
    return next(a for a in v[key] if a["ticker"] == ticker)


def codes(a):
    return [r["code"] for r in a["reasons"]]


# ---- verdicts de base -----------------------------------------------------


def test_asset_within_limits_is_conforme(run):
    v = last_verdict(run("Évalue LOW.TO"))
    assert v["status"] == "conforme" and asset(v, "LOW.TO")["status"] == "conforme"
    assert v["scope_refusals"] == [] and v["global_reasons"] == []


def test_volatility_above_limit_warns_with_measures(run):
    v = last_verdict(run("Évalue HIGH", risk_tolerance="faible_modere"))
    a = asset(v, "HIGH")
    assert v["status"] == "avertissement" and a["status"] == "avertissement"
    r = next(r for r in a["reasons"] if r["code"] == "VOLATILITY_ABOVE_LIMIT")
    assert r["measures"]["limit"] == 0.15 and r["measures"]["value"] > 0.4 and r["measures"]["ratio"] > 2.5


def test_same_asset_is_acceptable_for_higher_tolerance(run):
    v = last_verdict(run("Évalue HIGH", risk_tolerance="eleve", horizon_years_min=5, horizon_years_max=15))
    assert "VOLATILITY_ABOVE_LIMIT" not in codes(asset(v, "HIGH"))


def test_verdict_payload_matches_schema(run):
    GovernanceVerdict.model_validate(last_verdict(run("Évalue HIGH")))


def test_constraints_are_part_of_the_verdict(run):
    c = last_verdict(run("Évalue MID", risk_tolerance="faible"))["constraints"]
    assert c["risk_level_applied"] == "faible" and c["max_volatility"] == 0.08


# ---- blocages -------------------------------------------------------------


def test_sector_exclusion_blocks(run):
    v = last_verdict(run("Évalue HIGH", hard_exclusions=[{"kind": "sector", "value": "tech"}]))
    assert v["status"] == "bloqué" and "EXCLUDED_SECTOR" in codes(asset(v, "HIGH"))


def test_class_not_permitted_blocks_low_risk_profile(run):
    v = last_verdict(run("Évalue COIN", risk_tolerance="faible", horizon_years_max=10))
    assert v["status"] == "bloqué" and "CLASS_NOT_PERMITTED" in codes(asset(v, "COIN-USD"))


def test_same_asset_not_blocked_when_class_is_capped_above_zero(run):
    v = last_verdict(run("Évalue COIN", risk_tolerance="modere"))
    assert "CLASS_NOT_PERMITTED" not in codes(asset(v, "COIN-USD"))
    assert v["status"] == "avertissement"  # volatilité au-dessus de la limite, mais pas de blocage


def test_one_blocked_asset_blocks_the_verdict_but_not_the_other_asset(run):
    v = last_verdict(run("Compare LOW.TO et HIGH", hard_exclusions=[{"kind": "ticker", "value": "HIGH"}]))
    assert v["status"] == "bloqué"
    assert asset(v, "LOW.TO")["status"] != "bloqué" and asset(v, "HIGH")["status"] == "bloqué"


# ---- périmètre ------------------------------------------------------------


def test_prediction_request_warns_and_offers_description(run):
    v = last_verdict(run("Prédis LOW.TO l'an prochain"))
    assert v["status"] == "avertissement" and asset(v, "LOW.TO")["status"] == "conforme"
    (s,) = v["scope_refusals"]
    assert s["kind"] == "prediction" and s["offered_instead"]


def test_transaction_request_blocks_even_if_asset_is_conforme(run):
    v = last_verdict(run("Achète LOW.TO"))
    assert v["status"] == "bloqué" and asset(v, "LOW.TO")["status"] == "conforme"
    assert [s["kind"] for s in v["scope_refusals"]] == ["transaction"]


def test_personal_advice_request_warns(run):
    v = last_verdict(run("Dois-je acheter LOW.TO ?"))
    assert v["status"] == "avertissement" and v["scope_refusals"][0]["kind"] == "personal_advice"


# ---- données manquantes ---------------------------------------------------


def test_unknown_symbol_is_never_conforme(run):
    v = last_verdict(run("Évalue ZZZZ.TO"))
    assert v["status"] == "avertissement" and "OUT_OF_UNIVERSE" in codes(asset(v, "ZZZZ.TO"))


def test_missing_tool_is_a_global_gap(run, analyst):
    analyst.tools = analyst.tools - {"portfolio_metrics"}
    v = last_verdict(run("Construis un scénario de portefeuille avec LOW.TO et BND.TO"))
    g = [r for r in v["global_reasons"] if r["code"] == "TOOL_UNAVAILABLE"]
    assert g and "portfolio_metrics" in g[0]["message"]
    assert v["status"] == "avertissement"  # les deux actifs sont conformes, mais le scénario de portefeuille n'est pas chiffré


def test_internal_profile_conflict_surfaces_in_verdict(run):
    v = last_verdict(run("Évalue LOW.TO", risk_tolerance="eleve", horizon_years_min=0, horizon_years_max=1))
    assert "RISK_HORIZON_CONFLICT" in [r["code"] for r in v["global_reasons"]]
    assert v["status"] == "avertissement" and v["constraints"]["adjustments"]


def test_theme_preference_without_data_warns_per_asset(run):
    v = last_verdict(run("Évalue LOW.TO et ESGV", soft_preferences=[{"kind": "theme", "value": "esg"}]))
    assert "THEME_DATA_MISSING" in codes(asset(v, "LOW.TO"))
    esgv = codes(asset(v, "ESGV"))
    assert "THEME_PROXY" in esgv and "THEME_DATA_MISSING" not in esgv


def test_horizon_fit_flags_unrecovered_crash(run):
    short = last_verdict(run("Évalue CRASH", horizon_years_min=1, horizon_years_max=5))
    long_ = last_verdict(run("Évalue CRASH", horizon_years_min=10, horizon_years_max=15))
    assert "RECOVERY_LONGER_THAN_HORIZON" in codes(asset(short, "CRASH"))
    assert "RECOVERY_LONGER_THAN_HORIZON" not in codes(asset(long_, "CRASH"))


# ---- alternatives et boucle de retour ---------------------------------------


def test_alternatives_are_requested_evaluated_and_compared(run):
    r = run("Évalue HIGH", risk_tolerance="faible_modere")
    verdicts = r.ledger.entries("verdict")
    assert len(verdicts) == 2  # une relance, pas plus
    assert verdicts[0].payload["alternatives"] == []
    alts = {a["ticker"]: a for a in verdicts[1].payload["alternatives"]}
    assert set(alts) == {"CALM", "HIGH2"}  # pairs du secteur Tech, hors l'actif lui-même
    assert alts["CALM"]["status"] == "conforme" and alts["HIGH2"]["status"] == "avertissement"
    assert alts["CALM"]["alternative_for"] == ["HIGH"]


def test_alternatives_do_not_change_the_verdict_status(run):
    r = run("Évalue HIGH", risk_tolerance="faible_modere")
    first, second = (e.payload for e in r.ledger.entries("verdict"))
    assert first["status"] == second["status"] == "avertissement"
    assert first["assets"] == second["assets"]


def test_alternative_uses_same_windows_and_as_of_as_primary(run):
    r = run("Évalue HIGH", as_of=date(2023, 6, 30), risk_tolerance="faible_modere")
    by_ticker = {}
    for e in r.ledger.evidence():
        if e["tool"] == "compute_metrics":
            by_ticker.setdefault(e["params"]["ticker"], []).append((e["params"]["window"]["alias"], e["params"]["as_of"]))
    assert sorted(by_ticker["CALM"]) == sorted(by_ticker["HIGH"])
    assert {asof for _, asof in by_ticker["CALM"]} == {"2023-06-30"}


def test_no_alternative_search_when_asset_is_within_limits(run):
    r = run("Évalue LOW.TO")
    assert len(r.ledger.entries("verdict")) == 1
    assert not [e for e in r.ledger.entries("step") if e.payload["step_id"].startswith("fb")]


def test_excluded_peers_are_not_proposed(run):
    r = run("Évalue HIGH", risk_tolerance="faible_modere", hard_exclusions=[{"kind": "ticker", "value": "HIGH2"}])
    assert {a["ticker"] for a in last_verdict(r)["alternatives"]} == {"CALM"}


def test_no_admissible_alternative_is_reported(run):
    v = last_verdict(run("Évalue HIGH", hard_exclusions=[{"kind": "sector", "value": "Tech"}]))
    assert v["alternatives"] == []
    assert "NO_ALTERNATIVE" in [r["code"] for r in v["global_reasons"]]


def test_max_alternatives_is_respected(run, assets):
    gov = Governance(assets, GovernanceConfig(max_alternatives=1))
    r = run("Évalue HIGH", governance=gov, risk_tolerance="faible_modere")
    assert [a["ticker"] for a in last_verdict(r)["alternatives"]] == ["CALM"]  # ordre alphabétique : CALM avant HIGH2


def test_peers_fall_back_to_same_asset_class_when_sector_is_unique(assets):
    g = Governance(assets)
    assert g._peers("CRASH") == sorted(a["ticker"] for a in assets if a["asset_class"] == "equity" and a["ticker"] != "CRASH")
    assert g._peers("COIN-USD") == []  # seul actif crypto : pas de pair


def test_unknown_asset_has_no_peers(assets):
    assert Governance(assets)._peers("ZZZ") == []


# ---- propriétés générales ---------------------------------------------------


def test_every_cited_evidence_id_exists_in_the_ledger(run):
    r = run("Compare HIGH et LOW.TO", risk_tolerance="faible_modere", soft_preferences=[{"kind": "theme", "value": "esg"}])
    known = {e["evidence_id"] for e in r.ledger.evidence()}
    v = last_verdict(r)
    cited = [i for a in v["assets"] + v["alternatives"] for rs in a["reasons"] for i in rs["evidence_ids"]]
    assert cited and set(cited) <= known


def test_governance_does_not_write_or_mutate_the_ledger(analyst, gov, assets, profile_dict):
    r = Orchestrator(analyst).run("Évalue HIGH", profile_dict(), assets)
    profile = load_profile(profile_dict())
    before = len(r.ledger)
    out = gov(profile, r.request, r.ledger)
    assert isinstance(out, GovernanceOutcome) and len(r.ledger) == before


def test_same_input_same_verdict(run):
    a = [e.payload for e in run("Évalue HIGH", risk_tolerance="faible_modere").ledger.entries("verdict")]
    b = [e.payload for e in run("Évalue HIGH", risk_tolerance="faible_modere").ledger.entries("verdict")]
    assert a == b


def test_profile_identity_does_not_change_judgement(run):
    a = last_verdict(run("Évalue HIGH", profile_id="robert", risk_tolerance="faible_modere"))
    b = last_verdict(run("Évalue HIGH", profile_id="amira", risk_tolerance="faible_modere"))
    for v in (a, b):
        v["constraints"].pop("profile_id")
    assert a == b


def test_new_profile_is_judged_with_no_code_change(run):
    # Un profil jamais vu (retraité prudent voulant de la crypto) est bloqué par les règles dérivées.
    v = last_verdict(run("Évalue COIN", profile_id="retraite70", risk_tolerance="faible", horizon_years_min=1, horizon_years_max=5, objectives=["revenu"]))
    assert v["status"] == "bloqué" and "CLASS_NOT_PERMITTED" in codes(asset(v, "COIN-USD"))


def test_complete_run_with_explainer(analyst, gov, assets, profile_dict):
    seen = []
    explain = lambda p, ledger: seen.append(ledger.entries("verdict")[-1].payload["status"]) or "ok"
    r = Orchestrator(analyst, governance=gov, explainer=explain).run("Évalue LOW.TO", profile_dict(), assets)
    assert r.status == "complete" and seen == ["conforme"]
