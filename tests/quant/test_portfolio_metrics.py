"""portfolio_metrics : valeurs connues sur séries synthétiques, calendriers, rééquilibrage, validation des poids."""
import numpy as np
import pandas as pd
import pytest

from alphaforge.quant import run_tool

FULL = {"alias": "complet"}
BD = pd.bdate_range("2022-01-03", "2023-06-30")


def _walk(seed, idx, sd=0.01):
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.cumprod(1 + rng.normal(0, sd, len(idx))), index=idx)


def _pf(store, positions, rebalancing="rebalanced", window=FULL, **kw):
    pos = [{"ticker": t, "weight": w} for t, w in positions]
    return run_tool(store, "portfolio_metrics", {"positions": pos, "window": window, "rebalancing": rebalancing, **kw})


def test_two_identical_assets_50_50_have_the_volatility_of_the_asset(make_store):
    a = _walk(1, BD)
    store = make_store({"A": ("equity", "CAD", a), "B": ("equity", "CAD", a)})
    e = _pf(store, [("A", .5), ("B", .5)])
    alone = run_tool(store, "compute_metrics", {"ticker": "A", "window": FULL}).value["base"]
    m = e.value["metrics"]
    assert m["annualized_volatility"] == pytest.approx(alone["annualized_volatility"])
    assert m["total_return"] == pytest.approx(alone["total_return"])
    r = e.value["risk"]
    assert r["diversification_ratio"] == pytest.approx(1.0)  # aucune diversification
    assert [c["risk_share"] for c in r["contributions"]] == pytest.approx([0.5, 0.5])
    assert r["portfolio_volatility_initial_weights"] == pytest.approx(m["annualized_volatility"])


def test_perfectly_anticorrelated_assets_cancel_out(make_store):
    r = np.random.default_rng(2).normal(0, 0.01, len(BD))
    a = pd.Series(100 * np.cumprod(1 + r), index=BD)
    b = pd.Series(100 * np.cumprod(1 - r), index=BD)  # rendements exactement opposés
    store = make_store({"A": ("equity", "CAD", a), "B": ("equity", "CAD", b)})
    e = _pf(store, [("A", .5), ("B", .5)])
    assert e.value["metrics"]["annualized_volatility"] < 1e-6 * run_tool(store, "compute_metrics", {"ticker": "A", "window": FULL}).value["base"]["annualized_volatility"]
    assert e.value["risk"]["diversification_ratio"] is None and e.value["risk"]["contributions"] is None
    assert "ZERO_PORTFOLIO_VOLATILITY" in [n.code for n in e.notices]


def test_rebalanced_and_buy_and_hold_give_different_known_results(make_store):
    n = len(BD)
    grow = pd.Series(100 * 1.01 ** np.arange(n), index=BD)  # +1 % par jour
    flat = pd.Series(100.0, index=BD)
    store = make_store({"G": ("equity", "CAD", grow), "F": ("equity", "CAD", flat)})
    bh = _pf(store, [("G", .5), ("F", .5)], "buy_and_hold").value
    rb = _pf(store, [("G", .5), ("F", .5)], "rebalanced").value
    assert bh["metrics"]["total_return"] == pytest.approx(0.5 * 1.01 ** (n - 1) + 0.5 - 1)
    assert rb["metrics"]["total_return"] == pytest.approx(1.005 ** (n - 1) - 1)
    fw = bh["concentration"]["final_weights"]
    assert sum(fw.values()) == pytest.approx(1.0) and fw["G"] > 0.5  # le gagnant a dérivé au-dessus de 50 %
    assert rb["concentration"]["final_weights"] is None and bh["rebalancing"] == "buy_and_hold"
    assert "dériver" in bh["rebalancing_description"] and "rééquilibrés" in rb["rebalancing_description"]


def test_concentration_and_breakdowns(make_store):
    px = lambda s: _walk(s, BD)
    store = make_store({"A": ("equity", "CAD", px(3)), "B": ("equity", "USD", px(4)), "C": ("etf_bond", "CAD", px(5))})
    store.assets["sector"] = ["Tech", "Tech", "Bonds"]
    v = _pf(store, [("A", .5), ("B", .3), ("C", .2)]).value
    c = v["concentration"]
    assert c["max_weight"] == .5 and c["max_weight_ticker"] == "A" and c["hhi"] == pytest.approx(.38)
    assert c["effective_positions"] == pytest.approx(1 / .38) and c["n_positions"] == 3
    by = {k: {x["name"]: x["weight"] for x in v["breakdown"][k]} for k in ("asset_class", "sector", "currency")}
    assert by["asset_class"] == pytest.approx({"equity": .8, "etf_bond": .2})
    assert by["sector"] == pytest.approx({"Tech": .8, "Bonds": .2}) and by["currency"] == pytest.approx({"CAD": .7, "USD": .3})
    assert sum(x["risk_contribution"] for x in v["risk"]["contributions"]) == pytest.approx(v["risk"]["portfolio_volatility_initial_weights"])
    assert sum(x["risk_share"] for x in v["risk"]["contributions"]) == pytest.approx(1.0)


def test_weights_are_validated(make_store):
    store = make_store({"A": ("equity", "CAD", _walk(6, BD)), "B": ("equity", "CAD", _walk(7, BD))})
    assert _pf(store, [("A", .5), ("B", .4)]).error_code == "INVALID_WEIGHTS"
    assert _pf(store, [("A", 1.2), ("B", -.2)]).error_code == "INVALID_WEIGHTS"  # pas de vente à découvert
    assert _pf(store, [("A", 1.0), ("B", 0.0)]).error_code == "INVALID_WEIGHTS"
    assert _pf(store, [("A", .5), ("a", .5)]).error_code == "DUPLICATE_POSITION"
    assert _pf(store, [("A", .5), ("NOPE", .5)]).error_code == "NOT_IN_UNIVERSE"
    assert _pf(store, [("A", .5), ("B", .5 + 1e-9)]).status in ("ok", "warning")  # tolérance numérique


def test_rebalancing_hypothesis_is_mandatory_and_recorded(make_store):
    store = make_store({"A": ("equity", "CAD", _walk(8, BD)), "B": ("equity", "CAD", _walk(9, BD))})
    with pytest.raises(ValueError):
        run_tool(store, "portfolio_metrics", {"positions": [{"ticker": "A", "weight": .5}, {"ticker": "B", "weight": .5}], "window": FULL})
    e = _pf(store, [("A", .5), ("B", .5)], "buy_and_hold")
    assert e.params["rebalancing"] == "buy_and_hold" and e.params["mode"] == "rejeu_historique"
    assert any("Achat-et-conservation" in a for a in e.assumptions)
    assert _pf(store, [("A", .5), ("B", .5)], "rebalanced").evidence_id != e.evidence_id


def test_calendar_basis_by_portfolio_content(make_store):
    days = pd.date_range("2022-01-03", "2023-06-30", freq="D")
    store = make_store({"EQ": ("equity", "CAD", _walk(10, BD)), "EQ2": ("equity", "CAD", _walk(11, BD)),
                        "C1": ("crypto", "CAD", _walk(12, days)), "C2": ("crypto", "CAD", _walk(13, days))})
    eq = _pf(store, [("EQ", .5), ("EQ2", .5)]).value
    cr = _pf(store, [("C1", .5), ("C2", .5)]).value
    mx = _pf(store, [("EQ", .5), ("C1", .5)]).value
    assert (eq["calendar_basis"], eq["periods_per_year"]) == ("daily", 252)
    assert (cr["calendar_basis"], cr["periods_per_year"]) == ("daily", 365)
    assert (mx["calendar_basis"], mx["periods_per_year"], mx["period_label"]) == ("weekly", 52, "semaine")
    assert mx["n_returns"] < len(BD) // 4 and "semaines" in mx["annualization_basis"]


def test_union_calendar_forward_fills_are_bounded_and_counted(make_store):
    ca = _walk(14, BD.delete([10, 50]))  # deux jours fériés canadiens
    us = _walk(15, BD.delete([20]))
    store = make_store({"CA.TO": ("equity", "CAD", ca), "US": ("equity", "CAD", us)})
    e = _pf(store, [("CA.TO", .5), ("US", .5)])
    f = next(n for n in e.notices if n.code == "PANEL_FORWARD_FILLED")
    assert e.status == "warning" and "CA.TO 2" in f.message and "US 1" in f.message  # chaque férié manquant est comblé sur la date de l'autre
    # Trou plus long que la borne de 5 jours civils : jamais comblé, les dates sont retirées.
    gap = us.drop(us.index[100:110])
    store2 = make_store({"CA.TO": ("equity", "CAD", ca), "US": ("equity", "CAD", gap)})
    e2 = _pf(store2, [("CA.TO", .5), ("US", .5)])
    assert "PANEL_ROWS_DROPPED" in [n.code for n in e2.notices]


def test_late_starting_asset_is_reported_not_renormalized(make_store):
    late = _walk(16, BD[30:])  # début tardif de ~6 semaines, couverture encore suffisante
    store = make_store({"OLD": ("equity", "CAD", _walk(17, BD)), "NEW": ("equity", "CAD", late)})
    e = _pf(store, [("OLD", .5), ("NEW", .5)])
    assert e.value["effective_start"] == BD[30].date().isoformat() and "COMMON_START_LATE" in [n.code for n in e.notices]
    assert [p["weight"] for p in e.value["positions"]] == [.5, .5]  # les poids demandés ne sont jamais modifiés


def test_insufficient_asset_refuses_instead_of_dropping_it(make_store):
    store = make_store({"A": ("equity", "CAD", _walk(18, BD)), "S": ("equity", "CAD", _walk(19, BD[-30:]))})
    e = _pf(store, [("A", .5), ("S", .5)])
    assert e.error_code == "INSUFFICIENT_DATA" and "S" in e.warnings[0] and len(e.depends_on) == 2


def test_fx_conversion_happens_before_returns(make_store):
    fx = pd.Series(np.linspace(1.25, 1.375, len(BD)), index=BD)  # USD/CAD +10 %
    store = make_store({"US": ("equity", "USD", pd.Series(100.0, index=BD)), "CA": ("equity", "CAD", pd.Series(100.0, index=BD))}, fx=fx)
    e = _pf(store, [("US", .5), ("CA", .5)], "buy_and_hold")
    assert e.value["metrics"]["total_return"] == pytest.approx(0.05)  # moitié du portefeuille gagne 10 % de change
    assert {i.file for i in e.inputs} == {"prices_daily.csv", "fx_usdcad_daily.csv"}


def test_excluded_window_and_as_of_are_respected(make_store):
    idx = pd.bdate_range("2021-01-04", "2023-12-29")
    a, b = _walk(20, idx), _walk(21, idx)
    store = make_store({"A": ("equity", "CAD", a), "B": ("equity", "CAD", b)})
    e = _pf(store, [("A", .5), ("B", .5)], window={"alias": "hors_2022"})
    assert e.value["segmented"] and e.window.excluded and e.value["n_returns"] == int((idx.year != 2022).sum()) - 1
    more = pd.bdate_range("2024-01-01", "2024-06-28")
    store2 = make_store({"A": ("equity", "CAD", pd.concat([a, _walk(22, more)])), "B": ("equity", "CAD", pd.concat([b, _walk(23, more)]))})
    x = _pf(store, [("A", .5), ("B", .5)], as_of="2023-12-29").value["metrics"]
    y = _pf(store2, [("A", .5), ("B", .5)], as_of="2023-12-29").value["metrics"]
    assert x == y


def test_evidence_is_labelled_scenario_and_series_is_stored(make_store):
    store = make_store({"A": ("equity", "CAD", _walk(24, BD)), "B": ("equity", "CAD", _walk(25, BD))})
    e = _pf(store, [("A", .5), ("B", .5)])
    assert e.label == "scenario" and "Scénario rétrospectif fictif" in e.value["label"] and "aucune prévision" in e.value["label"]
    s = store.series.get(e.value["series_id_portfolio"]).series
    assert s.prices.iloc[0] == 100.0 and len(s.prices) == e.n_obs + 1 and s.currency == "CAD"
    assert {i.symbol for i in e.inputs} == {"A", "B"} and len(e.depends_on) == 2
    assert any("FNB" in l for l in e.limits)  # un FNB est traité comme une position unique
    assert any("Aucun frais" in a for a in e.assumptions)
    assert e.evidence_id == _pf(store, [("A", .5), ("B", .5)]).evidence_id


def test_weights_origin_is_recorded_and_changes_the_evidence(make_store):
    store = make_store({"A": ("equity", "CAD", _walk(30, BD)), "B": ("equity", "CAD", _walk(31, BD))})
    user = _pf(store, [("A", .5), ("B", .5)])
    assumed = _pf(store, [("A", .5), ("B", .5)], weights_basis="assumed_equal")
    assert user.params["weights_basis"] == "user" and user.value["weights_basis"] == "user"
    assert assumed.params["weights_basis"] == "assumed_equal" and assumed.evidence_id != user.evidence_id
    assert any("fournis par l'utilisateur" in a for a in user.assumptions)
    assert any("pondération égale posée par hypothèse" in a for a in assumed.assumptions)
    assert user.value["metrics"] == assumed.value["metrics"]  # seule l'origine diffère, pas le calcul
