"""Exigences du contrat de l'analyste : recherche, séries par référence, erreurs typées, annualisation, extrêmes."""
import numpy as np
import pandas as pd
import pytest

from alphaforge.quant import DataStore, run_tool

from conftest import series

FULL = {"alias": "complet"}


def _universe(make_store):
    names = {"AAPL": "Apple", "SHOP.TO": "Shopify", "TSLA": "Tesla", "BTC-USD": "Bitcoin"}
    s = series(np.linspace(100, 130, 120))
    store = make_store({t: ("equity", "USD" if "." not in t else "CAD", s) for t in names})
    store.assets["name"] = store.assets["ticker"].map(names)
    return store


def test_asset_lookup_free_text_and_typed_errors(make_store):
    store = _universe(make_store)
    ok = run_tool(store, "asset_lookup", {"query": "Apple"})
    assert ok.status in ("ok", "warning") and ok.value["ticker"] == "AAPL" and ok.n_obs == ok.value["n_rows"] == 120
    assert run_tool(store, "asset_lookup", {"query": "shopify"}).value["ticker"] == "SHOP.TO"
    assert run_tool(store, "asset_lookup", {"query": "SHOP"}).value["ticker"] == "SHOP.TO"

    near = run_tool(store, "asset_lookup", {"query": "Tesla Motors"})
    assert near.status == "refused" and near.error_code == "NOT_IN_UNIVERSE" and "TSLA" in near.value["suggestions"]

    absent = run_tool(store, "asset_lookup", {"query": "Netflix"})
    assert absent.error_code == "NOT_IN_UNIVERSE" and absent.refusal_reason == "asset_not_found"  # ancien champ conservé


def test_asset_lookup_returns_all_candidates_when_ambiguous(make_store):
    s = series(np.linspace(100, 130, 120))
    store = make_store({"SHOP.TO": ("equity", "CAD", s), "SHOP": ("equity", "USD", s)})
    exact = run_tool(store, "asset_lookup", {"query": "SHOP.TO"})  # symbole complet : une seule cote
    assert exact.value["ticker"] == "SHOP.TO"
    e = run_tool(store, "asset_lookup", {"query": "SHOP"})  # symbole nu : les deux cotes sont candidates
    assert e.error_code == "AMBIGUOUS_QUERY" and e.status == "refused"
    assert {c["ticker"] for c in e.value["candidates"]} == {"SHOP.TO", "SHOP"}


def test_get_prices_returns_series_id_not_series_and_signals_truncation(make_store):
    store = _universe(make_store)
    e = run_tool(store, "get_prices", {"ticker": "AAPL", "window": {"start": "2010-01-01", "end": "2030-01-01"}})
    assert "series" not in e.value and e.value["series_id"].startswith("sr_")
    assert e.value["truncated"] is True and "WINDOW_TRUNCATED_TO_COVERAGE" in [n.code for n in e.notices]
    stored = store.series.get(e.value["series_id"])
    assert len(stored.series.prices) == e.n_obs == 120
    again = run_tool(store, "get_prices", {"ticker": "AAPL", "window": {"start": "2010-01-01", "end": "2030-01-01"}})
    assert again.value["series_id"] == e.value["series_id"]  # déterministe


def test_convert_currency_chains_series_ids_and_refuses_target_currency_and_reconversion(make_store):
    idx = series([100.0] * 80).index
    store = make_store({"X": ("equity", "USD", pd.Series(100.0, index=idx)), "Y": ("equity", "CAD", pd.Series(100.0, index=idx))},
                       fx=pd.Series(1.25, index=idx))
    px = run_tool(store, "get_prices", {"ticker": "X", "window": FULL})
    conv = run_tool(store, "convert_currency", {"series_id": px.value["series_id"], "source_currency": "USD", "target_currency": "CAD"})
    assert conv.status in ("ok", "warning") and conv.value["parent_series_id"] == px.value["series_id"]
    assert conv.value["fx_rates_used"]["first"] == 1.25
    assert store.series.get(conv.value["series_id"]).series.prices.iloc[0] == pytest.approx(125.0)

    again = run_tool(store, "convert_currency", {"series_id": conv.value["series_id"], "target_currency": "USD"})
    assert again.error_code == "DOUBLE_CONVERSION"
    cad = run_tool(store, "get_prices", {"ticker": "Y", "window": FULL})
    same = run_tool(store, "convert_currency", {"series_id": cad.value["series_id"], "target_currency": "CAD"})
    assert same.error_code == "ALREADY_IN_TARGET_CURRENCY"
    wrong = run_tool(store, "convert_currency", {"series_id": px.value["series_id"], "source_currency": "CAD", "target_currency": "CAD"})
    assert wrong.error_code == "CURRENCY_MISMATCH"
    unknown = run_tool(store, "convert_currency", {"series_id": "sr_inconnu", "target_currency": "CAD"})
    assert unknown.error_code == "UNKNOWN_SERIES"


def test_evidence_has_fixed_contract_fields(make_store):
    store = make_store({"X": ("equity", "USD", series(np.linspace(100, 130, 120)))})
    e = run_tool(store, "compute_metrics", {"ticker": "X", "window": FULL})
    d = e.model_dump(mode="json")
    for key in ("evidence_id", "tool", "value", "window", "params", "inputs", "n_obs", "warnings", "notices", "error_code"):
        assert key in d
    assert d["n_obs"] == 120 and all(set(i) == {"file", "symbol", "sha256"} and len(i["sha256"]) == 64 for i in d["inputs"])
    assert {i["file"] for i in d["inputs"]} == {"prices_daily.csv", "fx_usdcad_daily.csv"}
    assert all(isinstance(w, str) for w in d["warnings"])
    # Rien d'implicite : devise, champ de prix, base d'annualisation et fenêtre sont consignés dans params.
    for key in ("base_currency", "price_field", "annualization", "window", "as_of"):
        assert key in d["params"]
    assert d["value"]["series_id_native"].startswith("sr_")


def test_input_hash_ignores_data_after_as_of(make_store):
    short = series(np.linspace(100, 130, 120))
    store_a = make_store({"X": ("equity", "CAD", short)})
    store_b = make_store({"X": ("equity", "CAD", pd.concat([short, series(np.linspace(130, 300, 30), start="2023-01-01")]))})
    cut = short.index[-1].date().isoformat()
    a = run_tool(store_a, "asset_lookup", {"query": "X", "as_of": cut})
    b = run_tool(store_b, "asset_lookup", {"query": "X", "as_of": cut})
    assert a.inputs == b.inputs


def test_no_annualization_under_one_year_but_cumulative_is_given(make_store):
    store = make_store({"X": ("equity", "CAD", series(np.linspace(100, 120, 100)))})  # ~140 jours civils
    e = run_tool(store, "compute_metrics", {"ticker": "X", "window": FULL})
    b = e.value["base"]
    assert b["annualized_return"] is None and b["total_return"] == pytest.approx(0.2)
    assert "ANNUALIZATION_SKIPPED" in [n.code for n in e.notices]
    assert e.value["fx_effect"]["annualized_return"] is None


def test_minimum_60_observations(make_store):
    store = make_store({"X": ("equity", "CAD", series(np.linspace(100, 120, 59)))})
    e = run_tool(store, "compute_metrics", {"ticker": "X", "window": FULL})
    assert e.error_code == "INSUFFICIENT_DATA" and e.value is None


def test_worst_day_worst_month_and_calendar_years(make_store):
    idx = pd.bdate_range("2021-11-01", "2023-03-31")
    px = pd.Series(100.0, index=idx)
    px.loc["2022-06-15":] *= 0.90  # un jour à -10 % le 2022-06-15
    px.loc["2023-01-02":] *= 1.20
    store = make_store({"X": ("equity", "CAD", px)})
    v = run_tool(store, "compute_metrics", {"ticker": "X", "window": FULL}).value["base"]
    assert v["worst_day"] == {"date": "2022-06-15", "return_": pytest.approx(-0.10)}
    assert v["worst_month"]["month"] == "2022-06" and v["worst_month"]["return_"] == pytest.approx(-0.10)
    years = {y["year"]: y for y in v["calendar_year_returns"]}
    assert set(years) == {2021, 2022, 2023}
    assert years[2022]["return_"] == pytest.approx(0.90 / 1.0 - 1) and not years[2022]["partial"]
    assert years[2021]["partial"] is True and years[2023]["return_"] == pytest.approx(0.20)


def test_data_quality_reports_late_start_longest_gap_and_field_incoherence(make_store):
    full = series(np.linspace(100, 130, 150))
    late = full.iloc[40:]
    store = make_store({"EARLY": ("equity", "CAD", full), "LATE": ("equity", "CAD", late)})
    q = run_tool(store, "data_quality_check", {"ticker": "LATE", "window": {"start": str(full.index[0].date()), "end": str(full.index[-1].date())}})
    assert "LATE_START" in [n.code for n in q.notices] and q.value["longest_missing_run"] == 40

    store._px["EARLY"].loc[store._px["EARLY"].index[10], "adj_close"] = 500.0  # adj_close >> close
    store._px["EARLY"].loc[store._px["EARLY"].index[11], "currency"] = "USD"
    q = run_tool(store, "data_quality_check", {"ticker": "EARLY", "window": FULL})
    assert {"ADJ_CLOSE_ABOVE_CLOSE", "CURRENCY_MISMATCH"} <= {n.code for n in q.notices}
    assert q.status == "warning" and q.value["quality"] == "avertissement"  # l'outil signale, il ne bloque pas


def test_large_moves_flag_by_sigma_and_never_removed(make_store):
    rng = np.random.default_rng(3)
    r = rng.normal(0, 0.002, 200)
    r[100] = 0.06  # +6 % : sous le seuil fixe (25 %) mais ~30 écarts-types
    store = make_store({"X": ("equity", "CAD", series(100 * np.cumprod(1 + r)))})
    q = run_tool(store, "data_quality_check", {"ticker": "X", "window": FULL})
    top = q.value["large_moves"][0]
    assert top["trigger"] == "sigma" and top["return"] == pytest.approx(0.06) and q.value["n_large_moves"] >= 1
    assert q.value["quality"] == "ok"  # informatif : un saut étiqueté ne bloque ni n'avertit
    m = run_tool(store, "compute_metrics", {"ticker": "X", "window": FULL})
    assert m.value["base"]["worst_day"]["return_"] != 0 and m.value["n_returns"] == 199  # le saut reste dans les calculs


def test_quality_labels_are_french(make_store):
    store = make_store({"X": ("equity", "CAD", series(np.linspace(100, 110, 30)))})
    assert run_tool(store, "data_quality_check", {"ticker": "X", "window": FULL}).value["quality"] == "insuffisant"


def test_parquet_series_store_roundtrip_and_survives_restart(make_store, tmp_path):
    idx = series([100.0] * 80).index
    px = pd.Series(100.0, index=idx)
    mk = lambda: make_store({"X": ("equity", "USD", px)}, fx=pd.Series(1.25, index=idx))
    a = mk()
    a.series.path = tmp_path
    p = run_tool(a, "get_prices", {"ticker": "X", "window": FULL})
    c = run_tool(a, "convert_currency", {"series_id": p.value["series_id"], "target_currency": "CAD"})
    assert (tmp_path / f"{c.value['series_id']}.parquet").exists()
    from alphaforge.quant.series_store import SeriesStore

    fresh = SeriesStore(path=tmp_path)  # « redémarrage » : mémoire vide, relecture depuis le disque
    got = fresh.get(c.value["series_id"])
    assert got.series.currency == "CAD" and got.series.converted_from == "USD" and got.parent == p.value["series_id"]
    assert got.series.prices.iloc[0] == pytest.approx(125.0) and got.series.fx_rates.iloc[0] == 1.25
    assert c.value["series_id"] in fresh and "sr_inconnu" not in fresh


def test_sharpe_is_optional_off_by_default_and_uses_known_rates_only(make_store):
    idx = pd.date_range("2022-01-01", periods=400, freq="D")
    rng = np.random.default_rng(5)
    px = pd.Series(100 * np.cumprod(1 + rng.normal(0.0005, 0.01, 400)), index=idx)
    macro = pd.DataFrame({"date": ["2022-03-01"], "series_id": ["V39079"], "value": [2.0]})  # taux connu à partir du 1er mars
    store = make_store({"X": ("crypto", "CAD", px)})
    store._macro = macro.assign(date=pd.to_datetime(macro["date"]))
    off = run_tool(store, "compute_metrics", {"ticker": "X", "window": FULL})
    assert off.value["sharpe"] is None
    on = run_tool(store, "compute_metrics", {"ticker": "X", "window": FULL, "include_sharpe": True})
    sh = on.value["sharpe"]
    assert sh["optional"] is True and sh["ratio"] is not None and sh["mean_risk_free_annual"] == pytest.approx(0.02)
    assert sh["n_dropped_no_rate"] == 58  # rendements du 2 janvier au 28 février exclus (30 + 28) : jamais de taux futur reporté en arrière
    assert any("Sharpe" in l for l in on.limits) and "macro_rates.csv" in {i.file for i in on.inputs}
    assert off.evidence_id != on.evidence_id


def test_sharpe_skipped_under_one_year_and_without_macro(make_store):
    store = make_store({"X": ("equity", "CAD", series(np.linspace(100, 120, 100)))})
    e = run_tool(store, "compute_metrics", {"ticker": "X", "window": FULL, "include_sharpe": True})
    assert e.value["sharpe"] is None and "SHARPE_UNAVAILABLE" in [n.code for n in e.notices]
