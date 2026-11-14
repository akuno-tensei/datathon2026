"""Univers synthétique à volatilités connues, et fabriques de faits pour les contrôles purs.

Les rendements alternent +r / -r : la volatilité annualisée vaut environ 15,9 × r (base 252), et le drawdown reste de l'ordre de r.
"""
import numpy as np
import pandas as pd
import pytest

from alphaforge.governance import Governance
from alphaforge.orchestrator import QuantAnalyst
from alphaforge.quant import DataStore

DATES = pd.bdate_range("2021-11-12", "2023-12-29")

# ticker, classe, devise, secteur, rendement quotidien |r| (volatilité approximative entre parenthèses)
SPEC = [
    ("LOW.TO", "equity", "CAD", "Finance", 0.002),  # ~3 %
    ("MID", "equity", "USD", "Finance", 0.010),  # ~16 %
    ("HIGH", "equity", "USD", "Tech", 0.030),  # ~48 %
    ("HIGH2", "equity", "USD", "Tech", 0.040),  # ~63 %
    ("CALM", "equity", "USD", "Tech", 0.002),  # ~3 %
    ("CRASH", "equity", "USD", "Krach", 0.001),  # krach de 50 % jamais récupéré
    ("COIN-USD", "crypto", "USD", "Crypto asset", 0.030),
    ("BND.TO", "etf_bond", "CAD", "Bonds", 0.001),
    ("GOLD", "etf_commodity", "USD", "Gold", 0.004),
    ("ESGV", "etf_equity", "USD", "Broad market", 0.004),
]


def _px(r: float, crash: bool) -> np.ndarray:
    n = len(DATES)
    px = 100 * np.cumprod(1 + np.where(np.arange(n) % 2 == 0, r, -r))
    if crash:
        px[100:] *= 0.5
    return px


def _frame(ticker, currency, px):
    return pd.DataFrame(
        {"date": DATES, "ticker": ticker, "open": px, "high": px, "low": px, "close": px, "adj_close": px, "volume": 1000, "currency": currency}
    )


@pytest.fixture(scope="session")
def assets():
    return [
        {"asset_id": f"A{i:02d}", "ticker": t, "name": t, "asset_class": c, "market": "X", "currency": ccy, "sector": s}
        for i, (t, c, ccy, s, _) in enumerate(SPEC, 1)
    ]


@pytest.fixture(scope="session")
def store(assets):
    frames = [_frame(t, ccy, _px(r, crash=(t == "CRASH"))) for t, _, ccy, _, r in SPEC]
    fx = pd.DataFrame({"date": DATES, "usdcad": 1.25, "cadusd": 0.8})
    return DataStore(pd.DataFrame(assets), pd.concat(frames, ignore_index=True), fx)


@pytest.fixture
def analyst(store):
    return QuantAnalyst(store)


@pytest.fixture
def gov(assets):
    return Governance(assets)


@pytest.fixture
def profile_dict():
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


@pytest.fixture
def run(analyst, gov, assets, profile_dict):
    """run(texte, **profil) -> RunResult avec gouvernance branchée (pas d'explicabilité)."""
    from alphaforge.orchestrator import Orchestrator

    def _run(text, as_of=None, governance=None, **profile):
        return Orchestrator(analyst, governance=governance or gov, as_of=as_of).run(text, profile_dict(**profile), assets)

    return _run
