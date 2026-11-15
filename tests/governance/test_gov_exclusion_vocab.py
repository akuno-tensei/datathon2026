"""Exclusions dures dont la valeur ne désigne aucun actif de l'univers : elles doivent être signalées, jamais ignorées en silence.

Univers synthétique (conftest) : secteurs Finance, Tech, Krach, Crypto asset, Bonds, Gold, Broad market ;
classes equity, crypto, etf_bond, etf_commodity, etf_equity.
"""
import pytest

from alphaforge.governance import GovernanceVerdict, check_exclusion_vocabulary
from alphaforge.orchestrator import load_profile
from helpers_gov import last_verdict

CODE = "EXCLUSION_VALUE_UNKNOWN"


def unknown(v):
    return [r for r in v["global_reasons"] if r["code"] == CODE]


def excl(kind, value):
    return {"hard_exclusions": [{"kind": kind, "value": value}]}


# ---- valeurs reconnues : aucun avertissement, l'exclusion bloque -----------


@pytest.mark.parametrize("kind,value,ticker", [
    ("sector", "Tech", "HIGH"),
    ("sector", "  tech ", "HIGH"),  # casse et espaces ignorés, comme pour le blocage
    ("asset_class", "crypto", "COIN-USD"),
    ("ticker", "LOW", "LOW.TO"),  # racine du ticker
    ("ticker", "low.to", "LOW.TO"),
])
def test_known_value_blocks_and_is_not_flagged(run, kind, value, ticker):
    v = last_verdict(run(f"Évalue {ticker}", **excl(kind, value)))
    assert unknown(v) == []
    assert v["status"] == "bloqué"


def test_theme_exclusion_keeps_its_own_warning(run):
    v = last_verdict(run("Évalue LOW.TO", **excl("theme", "tabac")))
    assert unknown(v) == []
    assert "THEME_EXCLUSION_UNVERIFIED" in [r["code"] for r in v["assets"][0]["reasons"]]


# ---- valeurs inconnues : avertissement visible, avec suggestions --------------


def test_misspelled_sector_is_flagged_with_suggestion(run):
    v = last_verdict(run("Évalue HIGH", **excl("sector", "Technologie")))
    (r,) = unknown(v)
    assert r["severity"] == "warn" and r["check"] == "hard_constraints"
    assert r["measures"]["suggestions"] == ["Tech"]
    assert "ne s'applique donc à aucun actif" in r["message"] and "Tech" in r["message"]
    assert v["status"] != "conforme"  # l'investisseur ne peut pas croire l'exclusion appliquée
    assert "EXCLUDED_SECTOR" not in [x["code"] for x in v["assets"][0]["reasons"]]  # on ne devine pas à sa place


def test_flag_is_raised_even_for_an_unrelated_asset(run):
    """Le défaut est dans le profil, pas dans l'actif : il est signalé quelle que soit la demande."""
    v = last_verdict(run("Évalue LOW.TO", **excl("sector", "Technologie")))
    assert len(unknown(v)) == 1 and v["status"] == "avertissement"


def test_asset_class_in_french_suggests_data_value(run):
    (r,) = unknown(last_verdict(run("Évalue COIN-USD", **excl("asset_class", "cryptomonnaie"))))
    assert r["measures"]["suggestions"] == ["crypto"]


def test_no_close_value_lists_possible_values(run, assets):
    (r,) = unknown(last_verdict(run("Évalue HIGH", **excl("sector", "Semiconductors"))))
    assert r["measures"]["suggestions"] == []
    assert r["measures"]["known_values"] == sorted({a["sector"] for a in assets})
    assert "Valeurs possibles" in r["message"]


def test_unknown_ticker_suggests_close_tickers(run):
    (r,) = unknown(last_verdict(run("Évalue LOW.TO", **excl("ticker", "HIGHX"))))
    assert "HIGH" in r["measures"]["suggestions"]


def test_each_unknown_exclusion_is_reported_once(run):
    p = {"hard_exclusions": [{"kind": "sector", "value": "Technologie"}, {"kind": "asset_class", "value": "actions"},
                             {"kind": "sector", "value": "Finance"}]}
    v = last_verdict(run("Évalue LOW.TO", **p))
    assert [r["measures"]["value"] for r in unknown(v)] == ["Technologie", "actions"]
    assert v["status"] == "bloqué"  # « Finance » reste appliquée et bloque LOW.TO


def test_verdict_with_flag_matches_schema(run):
    GovernanceVerdict.model_validate(last_verdict(run("Évalue HIGH", **excl("sector", "Technologie"))))


# ---- fonction pure ---------------------------------------------------------


def test_pure_function_on_universe(assets, profile_dict):
    p = load_profile(profile_dict(hard_exclusions=[{"kind": "sector", "value": "Golds"}, {"kind": "sector", "value": "Gold"}]))
    (r,) = check_exclusion_vocabulary(p, assets)
    assert r.measures["value"] == "Golds" and r.measures["suggestions"] == ["Gold"]
