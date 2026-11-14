"""Univers synthétique à volatilités connues pour tester le pipeline et les vues sans dépendre des données réelles."""
import numpy as np
import pandas as pd
import pytest

from alphaforge.app.pipeline import AlphaForge
from alphaforge.quant import DataStore

DATES = pd.bdate_range("2021-11-12", "2023-12-29")
SPEC = [  # ticker, classe, devise, secteur, |r| quotidien (alterné +r / -r)
    ("LOW.TO", "equity", "CAD", "Finance", 0.002),
    ("HIGH", "equity", "USD", "Tech", 0.030),
    ("CALM", "equity", "USD", "Tech", 0.002),
    ("COIN-USD", "crypto", "USD", "Crypto asset", 0.030),
]


@pytest.fixture
def forge(tmp_path):
    assets = [{"asset_id": f"A{i:02d}", "ticker": t, "name": t, "asset_class": c, "market": "X", "currency": ccy, "sector": s}
              for i, (t, c, ccy, s, _) in enumerate(SPEC, 1)]
    frames = []
    for t, _, ccy, _, r in SPEC:
        px = 100 * np.cumprod(1 + np.where(np.arange(len(DATES)) % 2 == 0, r, -r))
        frames.append(pd.DataFrame({"date": DATES, "ticker": t, "open": px, "high": px, "low": px, "close": px, "adj_close": px,
                                    "volume": 1000, "currency": ccy}))
    fx = pd.DataFrame({"date": DATES, "usdcad": 1.25, "cadusd": 0.8})
    store = DataStore(pd.DataFrame(assets), pd.concat(frames, ignore_index=True), fx)
    return AlphaForge(store, user_profiles_dir=tmp_path)


@pytest.fixture
def prof():
    def _make(**over):
        base = {"profile_id": "test", "horizon_years_min": 5, "horizon_years_max": 10, "risk_tolerance": "modere",
                "objectives": ["croissance_moderee"]}
        base.update(over)
        return base
    return _make
