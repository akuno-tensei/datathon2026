"""Univers synthétique, fournisseurs simulés et fabriques pour les tests de l'agent d'enrichissement."""
import numpy as np
import pandas as pd
import pytest

from alphaforge.enrichment.source_card import DEFAULT_SOURCES, SourceDefinition, SourceRegistry
from alphaforge.orchestrator import RuleBasedParser, load_profile
from alphaforge.quant import DataStore


ASSETS = [
    {"asset_id": "A01", "ticker": "RY.TO", "name": "Royal Bank of Canada", "asset_class": "equity", "market": "TSX", "currency": "CAD", "sector": "Financials"},
    {"asset_id": "A02", "ticker": "NVDA", "name": "NVIDIA Corporation", "asset_class": "equity", "market": "NASDAQ", "currency": "USD", "sector": "Technology"},
    {"asset_id": "A03", "ticker": "ESGV", "name": "Vanguard ESG U.S. Stock ETF", "asset_class": "etf_equity", "market": "Cboe", "currency": "USD", "sector": "Broad"},
    {"asset_id": "A04", "ticker": "AGG", "name": "iShares Core U.S. Aggregate Bond ETF", "asset_class": "etf_bond", "market": "NYSE Arca", "currency": "USD", "sector": "Bonds"},
    {"asset_id": "A05", "ticker": "BTC-USD", "name": "Bitcoin USD", "asset_class": "crypto", "market": "CCC", "currency": "USD", "sector": "Crypto"},
]


@pytest.fixture(scope="session")
def store():
    dates = pd.bdate_range("2021-11-12", "2026-11-12")
    frames = []
    for i, a in enumerate(ASSETS):
        px = 100 * np.cumprod(1 + np.random.default_rng(i).normal(0.0004, 0.01, len(dates)))
        frames.append(pd.DataFrame({"date": dates, "ticker": a["ticker"], "open": px, "high": px, "low": px, "close": px,
                                    "adj_close": px, "volume": 1000, "currency": a["currency"]}))
    fx = pd.DataFrame({"date": dates, "usdcad": 1.25, "cadusd": 0.8})
    return DataStore(pd.DataFrame(ASSETS), pd.concat(frames, ignore_index=True), fx)


@pytest.fixture(scope="session")
def assets():
    return [dict(a) for a in ASSETS]


@pytest.fixture
def mateo():
    return load_profile("mateo")  # préférences ESG et « éviter les controverses »


@pytest.fixture
def neutral_profile():
    return load_profile({"profile_id": "neutre", "horizon_years_min": 5, "horizon_years_max": 10, "risk_tolerance": "modere",
                         "objectives": ["croissance_moderee"]})


@pytest.fixture
def parse(assets):
    return lambda text: RuleBasedParser().parse(text, assets)


@pytest.fixture
def registry():
    vendor = SourceDefinition(source_id="esg_vendor", name="Fournisseur ESG fictif", origin_url="https://vendor.example",
                              source_class="third_party", reuse_terms="Licence fictive", limits=["Couverture partielle."])
    return SourceRegistry(DEFAULT_SOURCES + [vendor])
