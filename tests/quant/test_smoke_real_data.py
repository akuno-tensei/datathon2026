"""Tests de fumée sur le jeu de départ : aucune erreur, valeurs plausibles (pas de valeurs exactes)."""
import pytest

from alphaforge.quant import DataStore, run_tool
from alphaforge.quant.config import DEFAULT_DATA_DIR

pytestmark = pytest.mark.smoke

if not (DEFAULT_DATA_DIR / "prices_daily.csv").exists():
    pytest.skip("jeu de départ absent", allow_module_level=True)


@pytest.fixture(scope="module")
def store():
    return DataStore.from_directory()


@pytest.mark.parametrize(
    "ticker,vol_range,converted",
    [
        ("RY.TO", (0.05, 0.35), False),   # titre canadien
        ("AAPL", (0.15, 0.60), True),     # titre américain converti en CAD
        ("BTC-USD", (0.30, 1.20), True),  # crypto, base 365
        ("XBB.TO", (0.01, 0.20), False),  # FNB obligataire
    ],
)
def test_real_assets_produce_plausible_metrics(store, ticker, vol_range, converted):
    e = run_tool(store, "compute_metrics", {"ticker": ticker, "window": {"alias": "complet"}})
    assert e.status in ("ok", "warning"), e.notices
    v = e.value
    assert v["currency_converted"] is converted
    assert vol_range[0] < v["base"]["annualized_volatility"] < vol_range[1]
    assert -1 < v["base"]["max_drawdown"]["depth"] < 0
    assert v["periods_per_year"] == (365 if ticker == "BTC-USD" else 252)
    assert e.depends_on and e.data_fingerprint == store.fingerprint


def test_known_data_issues_are_flagged(store):
    cnq = run_tool(store, "data_quality_check", {"ticker": "CNQ.TO", "window": {"alias": "2022"}})
    assert "SPLIT_RATIO_UNUSUAL" in [n.code for n in cnq.notices]
    zst = run_tool(store, "data_quality_check", {"ticker": "ZST.TO", "window": {"alias": "complet"}})
    assert "ZERO_VOLUME" in [n.code for n in zst.notices]
