"""Univers synthétique et fabriques de profils pour les tests de l'orchestrateur."""
import numpy as np
import pandas as pd
import pytest

from alphaforge.orchestrator import QuantAnalyst
from alphaforge.quant import DataStore

ASSETS = [
    {"asset_id": "A01", "ticker": "RY.TO", "name": "Royal Bank of Canada", "asset_class": "equity", "market": "TSX", "currency": "CAD", "sector": "Financials"},
    {"asset_id": "A02", "ticker": "CNQ.TO", "name": "Canadian Natural Resources", "asset_class": "equity", "market": "TSX", "currency": "CAD", "sector": "Energy"},
    {"asset_id": "A03", "ticker": "CNR.TO", "name": "Canadian National Railway", "asset_class": "equity", "market": "TSX", "currency": "CAD", "sector": "Industrials"},
    {"asset_id": "A04", "ticker": "AAPL", "name": "Apple Inc.", "asset_class": "equity", "market": "NASDAQ", "currency": "USD", "sector": "Technology"},
    {"asset_id": "A05", "ticker": "NVDA", "name": "NVIDIA Corporation", "asset_class": "equity", "market": "NASDAQ", "currency": "USD", "sector": "Technology"},
    {"asset_id": "A06", "ticker": "BTC-USD", "name": "Bitcoin USD", "asset_class": "crypto", "market": "CCC", "currency": "USD", "sector": "Crypto"},
]


def _prices(ticker: str, currency: str, seed: int, dates) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    px = 100 * np.cumprod(1 + rng.normal(0.0004, 0.01, len(dates)))
    return pd.DataFrame(
        {"date": dates, "ticker": ticker, "open": px, "high": px, "low": px, "close": px, "adj_close": px, "volume": 1000, "currency": currency}
    )


@pytest.fixture(scope="session")
def assets():
    return [dict(a) for a in ASSETS]


@pytest.fixture(scope="session")
def store():
    dates = pd.bdate_range("2021-11-12", "2023-12-29")
    frames = [_prices(a["ticker"], a["currency"], i, dates) for i, a in enumerate(ASSETS)]
    fx = pd.DataFrame({"date": dates, "usdcad": 1.25, "cadusd": 0.8})
    return DataStore(pd.DataFrame(ASSETS), pd.concat(frames, ignore_index=True), fx)


@pytest.fixture
def analyst(store):
    return QuantAnalyst(store)


@pytest.fixture
def profile_dict():
    """Profil valide minimal, modifiable par les tests."""

    def _make(**over):
        base = {
            "profile_id": "test",
            "horizon_years_min": 5,
            "horizon_years_max": 10,
            "risk_tolerance": "modere",
            "objectives": ["croissance_moderee"],
        }
        base.update(over)
        return base

    return _make
