"""Calculs déterministes : rendements, volatilité, drawdown. Aucun appel au modèle de langage."""
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from .models import StrictModel


class Drawdown(StrictModel):
    depth: float  # <= 0, ex. -0.5 pour une perte de 50 % depuis le sommet
    peak_date: Optional[date]
    trough_date: Optional[date]
    recovery_date: Optional[date]
    recovered: bool


class MetricBlock(StrictModel):
    total_return: float
    annualized_return: float
    annualized_volatility: float
    max_drawdown: Drawdown


def simple_returns(prices: pd.Series) -> pd.Series:
    return prices.pct_change().dropna()


def max_drawdown(prices: pd.Series) -> Drawdown:
    peak = prices.cummax()
    dd = prices / peak - 1.0
    trough = dd.idxmin()
    depth = float(dd.loc[trough])
    if depth >= 0:
        return Drawdown(depth=0.0, peak_date=None, trough_date=None, recovery_date=None, recovered=True)
    peak_value = peak.loc[trough]
    before = prices.loc[:trough]
    peak_date = before[before >= peak_value - 1e-12].index[0]
    after = prices.loc[trough:]
    rec = after[after >= peak_value - 1e-12]
    recovery = rec.index[0] if len(rec) else None
    return Drawdown(
        depth=depth,
        peak_date=peak_date.date(),
        trough_date=trough.date(),
        recovery_date=recovery.date() if recovery is not None else None,
        recovered=recovery is not None,
    )


def metric_block(prices: pd.Series, periods_per_year: int) -> MetricBlock:
    """Métriques sur une série de prix, de la première à la dernière observation de la fenêtre."""
    rets = simple_returns(prices)
    n = len(rets)
    total = float(prices.iloc[-1] / prices.iloc[0] - 1.0)
    ann = -1.0 if total <= -1.0 else float((1.0 + total) ** (periods_per_year / n) - 1.0)
    vol = float(rets.std(ddof=1) * np.sqrt(periods_per_year)) if n > 1 else float("nan")
    return MetricBlock(
        total_return=total,
        annualized_return=ann,
        annualized_volatility=vol,
        max_drawdown=max_drawdown(prices),
    )
