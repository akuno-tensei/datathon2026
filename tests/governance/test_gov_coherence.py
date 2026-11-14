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
