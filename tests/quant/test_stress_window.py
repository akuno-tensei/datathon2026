"""stress_window et fenêtres segmentées (« hors 2022 ») : verdicts connus sur séries artificielles."""
import numpy as np
import pandas as pd
import pytest

from alphaforge.quant import run_tool
from alphaforge.quant.config import QuantConfig

IDX = pd.bdate_range("2021-01-04", "2023-12-29")


def _alternating(r_by_year):
    """Rendements alternés +r / -r, avec un |r| par année : volatilité connue par fenêtre."""
    r = np.array([r_by_year.get(d.year, 0.005) for d in IDX]) * np.where(np.arange(len(IDX)) % 2 == 0, 1, -1)
    return pd.Series(100 * np.cumprod(1 + r), index=IDX)


def _stress(store, **kw):
    return run_tool(store, "stress_window", {"ticker": "X", "windows": [{"alias": "complet"}, {"alias": "2022"}, {"alias": "hors_2022"}], **kw})


def test_shock_in_2022_is_sensitive(make_store):
    store = make_store({"X": ("equity", "CAD", _alternating({2022: 0.03}))})
    e = _stress(store)
    v = e.value
    assert v["verdict"] == "sensible" and v["max_window"] == "2022" and v["min_window"] == "hors_2022"
    assert v["relative_spread"] > v["threshold"] == 0.25
    assert "sensible" in v["summary"] and "25%" in v["summary"]  # seuil affiché


def test_no_shock_is_stable(make_store):
    store = make_store({"X": ("equity", "CAD", _alternating({}))})
    v = _stress(store).value
    assert v["verdict"] == "stable" and v["relative_spread"] < 0.05


def test_threshold_and_metric_are_parameters_recorded_in_params(make_store):
    store = make_store({"X": ("equity", "CAD", _alternating({2022: 0.008}))})
    loose, strict = _stress(store, threshold=2.0), _stress(store, threshold=0.01)
    assert loose.value["verdict"] == "stable" and strict.value["verdict"] == "sensible"
    assert loose.params["threshold"] == 2.0 and loose.evidence_id != strict.evidence_id
    dd = _stress(store, metric="max_drawdown")
    assert dd.params["metric"] == "max_drawdown" and all(r["value"] >= 0 for r in dd.value["rows"])


def test_without_2022_filters_returns_not_prices_so_no_fake_return_spans_the_gap(make_store):
    px = pd.Series(100.0, index=IDX)
    px.loc["2022-06-01":] = 200.0  # +100 % un jour de 2022, puis plat
    store = make_store({"X": ("equity", "CAD", px)})
    e = run_tool(store, "compute_metrics", {"ticker": "X", "window": {"alias": "hors_2022"}})
    b = e.value["base"]
    # Filtrer les prix aurait créé un rendement de +100 % entre le 31 déc. 2021 et le 3 janv. 2023.
    assert b["total_return"] == pytest.approx(0.0) and b["annualized_volatility"] == pytest.approx(0.0)
    assert e.value["segmented"] is True and "SEGMENTED_WINDOW" in [n.code for n in e.notices]
    assert e.value["n_returns"] == (IDX.year != 2022).sum() - 1
    assert {y["year"] for y in b["calendar_year_returns"]} == {2021, 2023}
    assert e.window.excluded[0].start.isoformat() == "2022-01-01"


def test_segmented_annualization_uses_calendar_days_outside_the_excluded_period(make_store):
    daily = 0.0004
    px = pd.Series(100 * np.cumprod(np.full(len(IDX), 1 + daily)), index=IDX)
    store = make_store({"X": ("equity", "CAD", px)})
    b = run_tool(store, "compute_metrics", {"ticker": "X", "window": {"alias": "hors_2022"}}).value["base"]
    kept = int((IDX.year != 2022).sum()) - 1
    days = (IDX[-1] - IDX[0]).days - 365
    assert b["total_return"] == pytest.approx((1 + daily) ** kept - 1)
    assert b["annualized_return"] == pytest.approx((1 + b["total_return"]) ** (365.25 / days) - 1)


def test_every_window_is_recorded_including_refused_ones(make_store):
    store = make_store({"X": ("equity", "CAD", _alternating({}))})
    e = run_tool(store, "stress_window", {"ticker": "X", "windows": [{"alias": "2022"}, {"start": "2023-12-20", "end": "2023-12-29"}]})
    rows = e.value["rows"]
    assert [r["status"] for r in rows] == ["ok", "refused"] and rows[1]["error_code"] == "INSUFFICIENT_DATA"
    assert e.value["verdict"] == "indetermine" and e.value["n_windows"] == 2 and e.value["n_computed"] == 1
    assert {"WINDOW_NOT_COMPUTED", "STRESS_UNDETERMINED"} <= {n.code for n in e.notices}


def test_rows_point_to_reproducible_compute_metrics_evidence(make_store):
    store = make_store({"X": ("equity", "USD", _alternating({2022: 0.02}))})
    e = run_tool(store, "stress_window", {"ticker": "X", "base_currency": "USD"})
    assert [r["window"] for r in e.value["rows"]] == QuantConfig().stress_default_windows  # fenêtres par défaut de la config
    assert e.depends_on == [r["evidence_id"] for r in e.value["rows"]]
    again = run_tool(store, "compute_metrics", {"ticker": "X", "window": {"alias": "2022"}, "base_currency": "USD"})
    row = next(r for r in e.value["rows"] if r["window"] == "2022")
    assert again.evidence_id == row["evidence_id"] and again.value["base"]["annualized_volatility"] == row["value"]


def test_unknown_asset_and_bad_threshold_are_refused(make_store):
    store = make_store({"X": ("equity", "CAD", _alternating({}))})
    assert run_tool(store, "stress_window", {"ticker": "NOPE"}).error_code == "NOT_IN_UNIVERSE"
    assert run_tool(store, "stress_window", {"ticker": "X", "threshold": 0}).error_code == "INVALID_THRESHOLD"


def test_exclusion_is_clipped_to_the_window_and_as_of(make_store):
    store = make_store({"X": ("equity", "CAD", _alternating({2022: 0.03}))})
    e = run_tool(store, "compute_metrics", {"ticker": "X", "window": {"alias": "hors_2022"}, "as_of": "2022-06-30"})
    assert e.status in ("ok", "warning") and e.window.end.isoformat() == "2022-06-30"
    assert e.window.excluded[0].end.isoformat() == "2022-06-30"  # exclusion ramenée à as_of
    assert {y["year"] for y in e.value["base"]["calendar_year_returns"]} == {2021}  # aucune donnée de 2022 lue


def test_fully_excluded_window_is_refused(make_store):
    from alphaforge.quant.config import ExcludedPeriod, WindowAlias

    store = make_store({"X": ("equity", "CAD", _alternating({}))})
    p22 = ExcludedPeriod(start="2022-01-01", end="2022-12-31")
    store.config.window_aliases["vide"] = WindowAlias(description="test", start="2022-01-01", end="2022-12-31", exclude=[p22])
    e = run_tool(store, "compute_metrics", {"ticker": "X", "window": {"alias": "vide"}})
    assert e.status == "refused" and e.error_code == "WINDOW_FULLY_EXCLUDED"


def test_absolute_floor_keeps_a_near_zero_measure_stable(make_store):
    # Volatilités de ~0,8 % (2022) et ~0,4 % : écart relatif de 100 %, mais écart réel de 0,4 point.
    store = make_store({"X": ("equity", "CAD", _alternating({2021: 0.0005, 2022: 0.0005, 2023: 0.00025}))})
    e = run_tool(store, "stress_window", {"ticker": "X", "windows": [{"alias": "2022"}, {"start": "2023-01-02", "end": "2023-12-29"}]})
    v = e.value
    assert v["relative_spread"] > 0.25 and v["absolute_spread"] < 0.02
    assert v["verdict"] == "stable" and v["floor_applied"] is True and "plancher" in v["summary"]
    assert e.params["min_abs_spread"] == 0.02
    off = run_tool(store, "stress_window", {"ticker": "X", "min_abs_spread": 0, "windows": [{"alias": "2022"}, {"start": "2023-01-02", "end": "2023-12-29"}]})
    assert off.value["verdict"] == "sensible" and off.value["floor_applied"] is False and off.evidence_id != e.evidence_id
    assert run_tool(store, "stress_window", {"ticker": "X", "min_abs_spread": -1}).error_code == "INVALID_THRESHOLD"


def test_floor_does_not_hide_a_real_shock(make_store):
    store = make_store({"X": ("equity", "CAD", _alternating({2022: 0.03}))})
    v = _stress(store).value
    assert v["verdict"] == "sensible" and v["floor_applied"] is False and v["absolute_spread"] > 0.2
