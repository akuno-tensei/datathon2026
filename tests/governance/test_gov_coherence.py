"""Cohérence gouvernance <-> analyste : devise des alternatives et requêtes ambiguës."""
import numpy as np
import pandas as pd

from alphaforge.governance import Governance
from alphaforge.orchestrator import Ledger, ParsedRequest, load_profile
from alphaforge.quant import DataStore, run_tool



def test_alternatives_are_priced_in_the_profile_currency(run):
    r = run("Évalue HIGH", risk_tolerance="faible_modere", base_currency="USD")
    follow = [e.payload["arguments"] for e in r.ledger.entries("step") if str(e.payload.get("step_id", "")).startswith("fb")]
    assert follow and all(a["base_currency"] == "USD" for a in follow)
    metrics = [e for e in r.ledger.evidence() if e["tool"] == "compute_metrics"]
    assert {e["params"]["base_currency"] for e in metrics} == {"USD"}  # actif demandé et alternatives : même devise


def test_ambiguous_query_is_never_conforme():
    idx = pd.bdate_range("2021-11-12", "2023-12-29")
    px = 100 * np.cumprod(1 + np.where(np.arange(len(idx)) % 2 == 0, 0.002, -0.002))
    assets = [{"asset_id": f"A{i}", "ticker": t, "name": "Shop", "asset_class": "equity", "market": "X", "currency": c, "sector": "T"}
              for i, (t, c) in enumerate((("SHOP", "USD"), ("SHOP.TO", "CAD")), 1)]
    prices = pd.concat([pd.DataFrame({"date": idx, "ticker": a["ticker"], "open": px, "high": px, "low": px, "close": px,
                                      "adj_close": px, "volume": 1, "currency": a["currency"]}) for a in assets])
    store = DataStore(pd.DataFrame(assets), prices, pd.DataFrame({"date": idx, "usdcad": 1.25, "cadusd": 0.8}))
    ledger = Ledger()
    ledger.write_evidence("analyste", run_tool(store, "asset_lookup", {"query": "Shop"}))
    req = ParsedRequest(raw_text="Évalue Shop", intent="evaluate_asset", tickers=[], unknown_tickers=["Shop"])
    v = Governance(assets)(load_profile("robert"), req, ledger).verdict
    a = v["assets"][0]
    assert v["status"] == "avertissement" and [r["code"] for r in a["reasons"]] == ["AMBIGUOUS_ASSET"]
    assert a["reasons"][0]["measures"]["candidates"] == ["SHOP", "SHOP.TO"]


def test_highly_correlated_pair_is_reported_as_information(run):
    # HIGH et HIGH2 alternent +r / -r les mêmes jours : rendements parfaitement corrélés.
    r = run("Compare HIGH et HIGH2", risk_tolerance="eleve", horizon_years_min=15, horizon_years_max=30)
    v = r.ledger.entries("verdict")[-1].payload
    (g,) = [x for x in v["global_reasons"] if x["code"] == "HIGH_CORRELATION"]
    assert g["check"] == "diversification" and g["severity"] == "info" and g["measures"]["corr"] > 0.99
    corr = next(e for e in r.ledger.evidence() if e["tool"] == "correlation_matrix")
    assert g["evidence_ids"] == [corr["evidence_id"]] and "," in g["message"].split("de ")[-1]


def test_uncorrelated_pair_is_not_reported(gov, analyst, assets, profile_dict):
    from alphaforge.governance import check_diversification, GovernanceConfig
    from alphaforge.orchestrator import Orchestrator

    r = Orchestrator(analyst, governance=gov).run("Compare LOW.TO et GOLD", profile_dict(), assets)
    corr = [e for e in r.ledger.evidence() if e["tool"] == "correlation_matrix"]
    assert corr
    high = check_diversification(corr, ["LOW.TO", "GOLD"], GovernanceConfig(high_correlation=1.01))
    assert high == []  # seuil hors d'atteinte : rien n'est signalé
