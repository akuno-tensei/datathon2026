"""Séries synthétiques à résultat connu : calculs, conversion, as_of, preuves."""
import numpy as np
import pandas as pd
import pytest

from alphaforge.quant import DataStore, run_tool, tool_schemas
from alphaforge.quant.registry import TOOLS
from alphaforge.quant.series import PriceSeries, convert_series
from alphaforge.quant.models import ToolInputError

from conftest import bdays, series

FULL = {"alias": "complet"}


def metrics(store, ticker="X", window=FULL, **kw):
    return run_tool(store, "compute_metrics", {"ticker": ticker, "window": window, **kw})


def test_constant_series_has_zero_volatility_and_return(make_store):
    store = make_store({"X": ("equity", "CAD", series([100.0] * 100))})
    e = metrics(store)
    assert e.status in ("ok", "warning") and e.value
    b = e.value["base"]
    assert b["total_return"] == 0 and b["annualized_volatility"] == 0
    assert b["max_drawdown"]["depth"] == 0


def test_series_doubling_in_one_year_gives_100_percent(make_store):
    # Crypto cotée 7 jours sur 7 : 366 prix = exactement 365 jours civils, donc un an pour l'annualisation.
    idx = pd.date_range("2022-01-01", periods=366, freq="D")
    prices = pd.Series(100 * 2 ** (np.arange(366) / 365), index=idx)
    store = make_store({"X": ("crypto", "CAD", prices)})
    b = metrics(store).value["base"]
    assert b["total_return"] == pytest.approx(1.0)
    assert b["annualized_return"] == pytest.approx(2 ** (365.25 / 365) - 1)


def test_drawdown_with_recovery(make_store):
    prices = [100.0] * 30 + list(np.linspace(100, 50, 30)) + list(np.linspace(50, 100, 30)) + [100.0] * 10
    store = make_store({"X": ("equity", "CAD", series(prices))})
    dd = metrics(store).value["base"]["max_drawdown"]
    assert dd["depth"] == pytest.approx(-0.5)
    assert dd["recovered"] is True and dd["recovery_date"] is not None


def test_drawdown_not_recovered_is_reported(make_store):
    prices = [100.0] * 40 + list(np.linspace(100, 60, 40)) + [60.0] * 20
    store = make_store({"X": ("equity", "CAD", series(prices))})
    e = metrics(store)
    assert e.value["base"]["max_drawdown"]["recovered"] is False
    assert "DRAWDOWN_NOT_RECOVERED" in [n.code for n in e.notices]


def test_crypto_uses_365_and_exchange_uses_252(make_store):
    rets = np.tile([0.01, -0.01], 60)
    prices = 100 * np.cumprod(1 + rets)
    store = make_store({"C": ("crypto", "CAD", series(prices)), "E": ("equity", "CAD", series(prices))})
    c, e = metrics(store, "C").value, metrics(store, "E").value
    assert c["periods_per_year"] == 365 and e["periods_per_year"] == 252
    ratio = c["base"]["annualized_volatility"] / e["base"]["annualized_volatility"]
    assert ratio == pytest.approx(np.sqrt(365 / 252))


def test_usd_asset_flat_in_usd_shows_pure_fx_effect(make_store):
    idx = bdays(100)
    fx = pd.Series(np.linspace(1.25, 1.375, 100), index=idx)  # USD/CAD +10 %
    store = make_store({"X": ("equity", "USD", pd.Series(100.0, index=idx))}, fx=fx)
    v = metrics(store).value
    assert v["native"]["total_return"] == 0
    assert v["base"]["total_return"] == pytest.approx(0.10)
    assert v["fx_effect"]["total_return"] == pytest.approx(0.10)


def test_usd_to_cad_to_usd_is_identity(make_store):
    idx = bdays(50)
    rng = np.random.default_rng(1)
    fx = pd.Series(1.25 + 0.05 * rng.random(50), index=idx)
    usd = pd.Series(100 + rng.random(50) * 10, index=idx)
    store = make_store({"X": ("equity", "USD", usd)}, fx=fx)
    as_of = idx[-1].date()
    cad = convert_series(store, PriceSeries("X", "USD", usd, "adj_close"), "CAD", as_of)
    fresh = PriceSeries("X", "CAD", cad.prices, "adj_close")  # série CAD « neuve », sans marque de conversion
    back = convert_series(store, fresh, "USD", as_of)
    np.testing.assert_allclose(back.prices.values, usd.values)


def test_double_conversion_is_refused(make_store):
    idx = bdays(30)
    store = make_store({"X": ("equity", "USD", pd.Series(100.0, index=idx))})
    as_of = idx[-1].date()
    cad = convert_series(store, PriceSeries("X", "USD", pd.Series(100.0, index=idx), "adj_close"), "CAD", as_of)
    with pytest.raises(ToolInputError) as err:
        convert_series(store, cad, "USD", as_of)
    assert err.value.reason == "double_conversion"


def test_fx_forward_fill_is_bounded_never_backward_and_reported(make_store):
    idx = bdays(40)
    # Taux absents sur les 3 premiers jours (pas de bfill) et sur 8 jours au milieu (au-delà de la borne de 5 jours).
    fx_idx = idx.delete(list(range(0, 3)) + list(range(20, 28)))
    fx = pd.Series(1.30, index=fx_idx)
    store = make_store({"X": ("equity", "USD", pd.Series(100.0, index=idx))}, fx=fx)
    e = run_tool(store, "convert_currency", {"ticker": "X", "window": FULL, "target_currency": "CAD"})
    v = e.value
    codes = [n.code for n in e.notices]
    assert v["fx_dropped_days"] > 0 and "FX_DROPPED_DAYS" in codes
    assert v["fx_forward_filled_days"] > 0 and "FX_FORWARD_FILLED" in codes
    assert v["first_date"] == idx[3].date().isoformat()  # les 3 premiers jours ne sont pas remplis par un taux futur
    assert v["n_obs_out"] == v["n_obs_in"] - v["fx_dropped_days"]


def test_results_ignore_data_after_as_of(make_store):
    rng = np.random.default_rng(7)
    base = series(100 * np.cumprod(1 + rng.normal(0, 0.01, 200)))
    cut = base.index[149]
    extended = pd.concat([base.loc[:cut], base.loc[cut:].iloc[1:] * 3])  # futur très différent
    fx = pd.Series(1.25, index=extended.index)
    s_short = make_store({"X": ("equity", "USD", base.loc[:cut])}, fx=fx.loc[:cut])
    s_long = make_store({"X": ("equity", "USD", extended)}, fx=fx)
    args = dict(window={"start": "2022-01-03", "end": "2023-12-29"}, as_of=cut.date().isoformat())
    a, b = metrics(s_short, **args), metrics(s_long, **args)
    # L'evidence_id inclut l'empreinte des données complètes, donc il diffère ; les chiffres, eux, doivent être identiques.
    strip = lambda v: {k: x for k, x in v.items() if k != "data_quality"}
    assert strip(a.value) == strip(b.value)
    assert a.value["data_quality"]["quality"] == b.value["data_quality"]["quality"]
    assert "WINDOW_CLIPPED_TO_AS_OF" in [n.code for n in b.notices]
    assert b.window.end == cut.date()


def test_evidence_id_depends_on_params_data_and_tool_version(make_store):
    s1 = series(np.linspace(100, 120, 100))
    store = make_store({"X": ("equity", "CAD", s1)})
    same = make_store({"X": ("equity", "CAD", s1)})
    other_data = make_store({"X": ("equity", "CAD", s1 * 1.0001)})
    e1, e2 = metrics(store), metrics(same)
    assert e1.evidence_id == e2.evidence_id
    assert metrics(other_data).evidence_id != e1.evidence_id
    assert metrics(store, base_currency="USD").evidence_id != e1.evidence_id
    old = TOOLS["compute_metrics"].version
    try:
        TOOLS["compute_metrics"].version = "9.9.9"
        assert metrics(store).evidence_id != e1.evidence_id
    finally:
        TOOLS["compute_metrics"].version = old


def test_insufficient_data_blocks_metrics_and_links_quality_evidence(make_store):
    store = make_store({"X": ("equity", "CAD", series(np.linspace(100, 110, 100)))})
    e = metrics(store, window={"start": "2022-01-03", "end": "2022-01-10"})
    assert e.status == "refused" and e.refusal_reason == "insufficient_data"
    assert e.value is None and len(e.depends_on) == 1


def test_unknown_asset_and_unknown_alias_are_refused(make_store):
    store = make_store({"X": ("equity", "CAD", series([100.0] * 80))})
    e = metrics(store, ticker="NOPE")
    assert e.status == "refused" and e.refusal_reason == "asset_not_found" and e.value is None
    e = metrics(store, window={"alias": "inexistant"})
    assert e.status == "refused" and e.refusal_reason == "unknown_window_alias"


def test_json_schemas_are_generated_and_arguments_validated(make_store):
    names = {t["name"] for t in tool_schemas()}
    assert names == {"asset_lookup", "get_prices", "data_quality_check", "convert_currency", "compute_metrics"}
    assert all("properties" in t["input_schema"] for t in tool_schemas())
    store = make_store({"X": ("equity", "CAD", series([100.0] * 80))})
    with pytest.raises(ValueError):
        run_tool(store, "compute_metrics", {"ticker": "X", "window": FULL, "champ_inconnu": 1})
    with pytest.raises(ValueError):
        run_tool(store, "compute_metrics", {"ticker": "X", "window": {"alias": "2022", "start": "2022-01-01"}})
