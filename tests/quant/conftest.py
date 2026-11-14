"""Fabrique de DataStore synthétiques à résultat connu."""
import numpy as np
import pandas as pd
import pytest

from alphaforge.quant import DataStore


def _frame(ticker, currency, prices: pd.Series) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": prices.index,
            "ticker": ticker,
            "open": prices.values,
            "high": prices.values,
            "low": prices.values,
            "close": prices.values,
            "adj_close": prices.values,
            "volume": 1000,
            "currency": currency,
        }
    )


@pytest.fixture
def make_store():
    """make_store({ticker: (asset_class, currency, Series)}, fx=Series USD/CAD ou None) -> DataStore."""

    def _make(series, fx=None):
        assets, frames = [], []
        for i, (t, (cls, ccy, s)) in enumerate(series.items(), 1):
            assets.append(
                {"asset_id": f"A{i:02d}", "ticker": t, "name": t, "asset_class": cls, "market": "X", "currency": ccy, "sector": "S"}
            )
            frames.append(_frame(t, ccy, s))
        if fx is None:
            all_dates = sorted(set().union(*[set(s.index) for _, _, s in series.values()]))
            fx = pd.Series(1.25, index=pd.DatetimeIndex(all_dates))
        fxdf = pd.DataFrame({"date": fx.index, "usdcad": fx.values, "cadusd": 1 / fx.values})
        return DataStore(pd.DataFrame(assets), pd.concat(frames, ignore_index=True), fxdf)

    return _make


def bdays(n, start="2022-01-03"):
    return pd.bdate_range(start, periods=n)


def series(values, start="2022-01-03"):
    idx = bdays(len(values), start)
    return pd.Series(np.asarray(values, dtype=float), index=idx)
