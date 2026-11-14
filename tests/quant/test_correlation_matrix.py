"""correlation_matrix : valeurs connues, alignement des calendriers, devise, cohérence temporelle."""
import numpy as np
import pandas as pd
import pytest

from alphaforge.quant import run_tool

FULL = {"alias": "complet"}
BD = pd.bdate_range("2022-01-03", "2023-06-30")


def _walk(seed, idx, sd=0.01):
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.cumprod(1 + rng.normal(0, sd, len(idx))), index=idx)


def _corr(store, tickers, **kw):
    return run_tool(store, "correlation_matrix", {"tickers": tickers, "window": FULL, **kw})


def test_identical_assets_have_correlation_one_and_opposite_minus_one(make_store):
    a = _walk(1, BD)
    r = a.pct_change().fillna(0).values
    opposite = pd.Series(100 * np.cumprod(1 - r), index=BD)  # rendements exactement opposés
    store = make_store({"A": ("equity", "CAD", a), "B": ("equity", "CAD", a * 2), "C": ("equity", "CAD", opposite)})
    m = _corr(store, ["A", "B", "C"]).value["matrices"]["daily_common"]["matrix"]
    assert m["A"]["B"] == pytest.approx(1.0) and m["A"]["C"] == pytest.approx(-1.0) and m["A"]["A"] == pytest.approx(1.0)


def test_correlation_uses_returns_not_prices(make_store):
    # Deux tendances haussières indépendantes : prix très corrélés, rendements indépendants.
    up = pd.Series(np.linspace(100, 200, len(BD)), index=BD)
    a = up * (1 + _walk(2, BD, 0.02).pct_change().fillna(0))
    b = up * (1 + _walk(3, BD, 0.02).pct_change().fillna(0))
    store = make_store({"A": ("equity", "CAD", a), "B": ("equity", "CAD", b)})
    assert np.corrcoef(a, b)[0, 1] > 0.9
    assert abs(_corr(store, ["A", "B"]).value["max_pair"]["corr"]) < 0.2


def test_crypto_weekend_moves_are_compared_on_common_dates_without_forward_fill(make_store):
    days = pd.date_range("2022-01-03", "2023-06-30", freq="D")
    crypto = _walk(4, days, 0.03)
    equity = crypto[crypto.index.dayofweek < 5]  # même prix que la crypto les jours de bourse
    store = make_store({"EQ": ("equity", "CAD", equity), "CR": ("crypto", "CAD", crypto)})
    v = _corr(store, ["EQ", "CR"]).value
    assert v["mixed_calendars"] and v["reference"] == "weekly"
    daily = v["matrices"]["daily_common"]
    # Inner join : le rendement de la crypto du vendredi au lundi est comparé à celui de l'action, donc corrélation de 1.
    assert daily["matrix"]["EQ"]["CR"] == pytest.approx(1.0) and daily["n_obs"] == len(equity) - 1
    assert v["matrices"]["weekly"]["matrix"]["EQ"]["CR"] == pytest.approx(1.0)
    # Un report vers l'avant fabriquerait des rendements nuls le week-end et sous-estimerait la corrélation.
    ffilled = pd.DataFrame({"EQ": equity.reindex(days).ffill(), "CR": crypto}).pct_change().dropna()
    assert ffilled.corr().loc["EQ", "CR"] < 0.9


def test_mixed_reference_is_weekly_and_frequency_can_be_forced(make_store):
    days = pd.date_range("2022-01-03", "2023-06-30", freq="D")
    store = make_store({"EQ": ("equity", "CAD", _walk(5, BD)), "CR": ("crypto", "CAD", _walk(6, days))})
    e = _corr(store, ["EQ", "CR"])
    assert e.value["reference"] == "weekly" and e.value["matrices"]["weekly"]["periods_per_year"] == 52
    assert "MIXED_CALENDARS" in [n.code for n in e.notices]
    forced = _corr(store, ["EQ", "CR"], frequency="daily")
    assert forced.value["reference"] == "daily_common" and forced.params["frequency"] == "daily"
    only_equity = make_store({"A": ("equity", "CAD", _walk(7, BD)), "B": ("equity", "CAD", _walk(8, BD))})
    assert set(_corr(only_equity, ["A", "B"]).value["matrices"]) == {"daily_common"}  # même calendrier : pas d'hebdomadaire


def test_holiday_mismatch_keeps_only_common_dates(make_store):
    ca = _walk(9, BD.delete([10, 50]))
    us = _walk(10, BD.delete([20]))
    store = make_store({"CA.TO": ("equity", "CAD", ca), "US": ("equity", "USD", us)})
    v = _corr(store, ["CA.TO", "US"]).value
    assert v["n_obs"] == len(BD) - 3 - 1 and v["base_currency"] == "CAD"


def test_prices_are_converted_to_base_currency_before_returns(make_store):
    a = _walk(11, BD)
    moving = pd.Series(1.25 * np.cumprod(1 + np.random.default_rng(12).normal(0, 0.01, len(BD))), index=BD)
    flat = pd.Series(1.25, index=BD)
    # Même trajectoire de prix, l'un coté en USD, l'autre en CAD : en CAD, le USD/CAD s'ajoute aux rendements du premier.
    with_fx = make_store({"US": ("equity", "USD", a), "CA": ("equity", "CAD", a)}, fx=moving)
    no_fx = make_store({"US": ("equity", "USD", a), "CA": ("equity", "CAD", a)}, fx=flat)
    assert _corr(no_fx, ["US", "CA"]).value["max_pair"]["corr"] == pytest.approx(1.0)
    assert _corr(with_fx, ["US", "CA"], base_currency="CAD").value["max_pair"]["corr"] < 0.95


def test_excluded_period_and_as_of_are_respected(make_store):
    idx = pd.bdate_range("2021-01-04", "2023-12-29")
    a = _walk(13, idx)
    b = a.copy()
    in22 = b.index.year == 2022
    b[in22] = _walk(14, idx)[in22].values  # décorrélés seulement en 2022
    store = make_store({"A": ("equity", "CAD", a), "B": ("equity", "CAD", b)})
    hors = run_tool(store, "correlation_matrix", {"tickers": ["A", "B"], "window": {"alias": "hors_2022"}})
    assert hors.window.excluded and hors.value["matrices"]["daily_common"]["n_obs"] == int((idx.year != 2022).sum()) - 1
    longer = make_store({"A": ("equity", "CAD", pd.concat([a, _walk(15, pd.bdate_range("2024-01-01", "2024-06-28"))])),
                         "B": ("equity", "CAD", pd.concat([b, _walk(16, pd.bdate_range("2024-01-01", "2024-06-28"))]))})
    x = _corr(store, ["A", "B"], as_of="2023-12-29").value
    y = _corr(longer, ["A", "B"], as_of="2023-12-29").value
    assert x["matrices"] == y["matrices"]  # aucune donnée postérieure à as_of n'est lue


def test_few_common_observations_warn(make_store):
    days = pd.date_range("2022-01-03", "2023-06-30", freq="D")
    store = make_store({"EQ": ("equity", "CAD", _walk(17, BD)), "CR": ("crypto", "CAD", _walk(18, days))})
    e = _corr(store, ["EQ", "CR"], window={"start": "2023-03-22", "end": "2023-06-30"})  # ~14 semaines communes
    weekly = e.value["matrices"]["weekly"]
    assert e.status == "warning" and "FEW_COMMON_OBSERVATIONS" in [n.code for n in e.notices]
    assert weekly["computed"] and 10 <= weekly["n_obs"] < 26 and e.n_obs == weekly["n_obs"]


def test_insufficient_asset_is_excluded_and_unknown_asset_refused(make_store):
    short = _walk(19, BD[-30:])
    store = make_store({"A": ("equity", "CAD", _walk(20, BD)), "B": ("equity", "CAD", _walk(21, BD)), "S": ("equity", "CAD", short)})
    e = _corr(store, ["A", "B", "S"])
    assert e.value["tickers"] == ["A", "B"] and e.value["excluded_assets"][0]["ticker"] == "S"
    assert "ASSET_EXCLUDED" in [n.code for n in e.notices] and len(e.depends_on) == 3
    assert _corr(store, ["A", "NOPE"]).error_code == "NOT_IN_UNIVERSE"
    assert _corr(store, ["A", "a"]).error_code == "NOT_ENOUGH_ASSETS"
    assert _corr(store, ["A", "S"]).error_code == "INSUFFICIENT_DATA"


def test_evidence_contract(make_store):
    store = make_store({"A": ("equity", "USD", _walk(22, BD)), "B": ("equity", "CAD", _walk(23, BD))})
    e = _corr(store, ["A", "B"])
    assert {i.file for i in e.inputs} == {"prices_daily.csv", "fx_usdcad_daily.csv"}
    assert e.params["window"]["alias"] == "complet" and e.params["base_currency"] == "CAD"
    assert e.evidence_id == _corr(store, ["A", "B"]).evidence_id
    assert any("jamais sur les prix" in a for a in e.assumptions)
