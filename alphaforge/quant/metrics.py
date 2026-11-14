"""Calculs déterministes : rendements, volatilité, drawdown, extrêmes. Aucun appel au modèle de langage."""
from datetime import date
from typing import List, Optional

import numpy as np
import pandas as pd

from .models import StrictModel


class Drawdown(StrictModel):
    depth: float  # <= 0, ex. -0.5 pour une perte de 50 % depuis le sommet
    peak_date: Optional[date]
    trough_date: Optional[date]
    recovery_date: Optional[date]
    recovered: bool


class WorstDay(StrictModel):
    date: date
    return_: float


class WorstMonth(StrictModel):
    month: str  # AAAA-MM
    return_: float
    partial: bool  # vrai si le mois est mesuré depuis le premier prix de la fenêtre et non depuis la fin du mois précédent


class CalendarYear(StrictModel):
    year: int
    return_: float
    partial: bool  # même convention que ci-dessus
    first_date: date
    last_date: date


class MetricBlock(StrictModel):
    total_return: float
    annualized_return: Optional[float]  # None si la période observée est inférieure à un an
    annualized_volatility: float
    max_drawdown: Drawdown
    worst_day: WorstDay
    worst_month: WorstMonth
    calendar_year_returns: List[CalendarYear]


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


def _period_returns(prices: pd.Series, freq: str) -> pd.DataFrame:
    """Rendement par période civile ('M' ou 'Y') : dernier prix de la période / dernier prix de la précédente.
    La première période part du premier prix de la fenêtre (partial = True)."""
    per = prices.index.to_period(freq)
    last = prices.groupby(per).last()
    first_d = prices.index.to_series().groupby(per).first()
    last_d = prices.index.to_series().groupby(per).last()
    prev = last.shift(1)
    prev.iloc[0] = prices.iloc[0]
    out = pd.DataFrame({"ret": last / prev - 1.0, "first": first_d, "last": last_d})
    out["partial"] = False
    out.iloc[0, out.columns.get_loc("partial")] = True
    return out


def metric_block(prices: pd.Series, periods_per_year: int, annualization_min_days: int = 350) -> MetricBlock:
    """Métriques sur une série de prix, de la première à la dernière observation de la fenêtre.

    Rendement annualisé = (1 + cumulatif) ^ (1 / années) - 1, années = jours civils observés / 365,25,
    seulement si la période observée couvre au moins `annualization_min_days` jours civils.
    """
    rets = simple_returns(prices)
    n = len(rets)
    total = float(prices.iloc[-1] / prices.iloc[0] - 1.0)
    span_days = (prices.index[-1] - prices.index[0]).days
    if span_days < annualization_min_days:
        ann: Optional[float] = None
    elif total <= -1.0:
        ann = -1.0
    else:
        ann = float((1.0 + total) ** (365.25 / span_days) - 1.0)
    vol = float(rets.std(ddof=1) * np.sqrt(periods_per_year)) if n > 1 else float("nan")

    wd = rets.idxmin()
    months = _period_returns(prices, "M")
    wm = months["ret"].idxmin()
    years = _period_returns(prices, "Y")
    return MetricBlock(
        total_return=total,
        annualized_return=ann,
        annualized_volatility=vol,
        max_drawdown=max_drawdown(prices),
        worst_day=WorstDay(date=wd.date(), return_=float(rets.loc[wd])),
        worst_month=WorstMonth(month=str(wm), return_=float(months.loc[wm, "ret"]), partial=bool(months.loc[wm, "partial"])),
        calendar_year_returns=[
            CalendarYear(year=int(y.year), return_=float(r["ret"]), partial=bool(r["partial"]),
                         first_date=r["first"].date(), last_date=r["last"].date())
            for y, r in years.iterrows()
        ],
    )


def sharpe_ratio(rets: pd.Series, risk_free_annual: pd.Series, periods_per_year: int) -> dict:
    """Ratio de Sharpe (optionnel). Taux sans risque reporté vers l'avant seulement (jamais de valeur future).

    Excédent par période = rendement − taux annuel ÷ N. Sharpe = moyenne(excédent) ÷ écart-type(excédent) × √N.
    Les jours antérieurs à la première valeur connue du taux sont exclus (pas de bfill).
    """
    idx = rets.index
    rf = risk_free_annual.reindex(risk_free_annual.index.union(idx)).ffill().reindex(idx)
    ok = rf.notna()
    excess = (rets[ok] - rf[ok] / periods_per_year)
    n = int(len(excess))
    sd = float(excess.std(ddof=1)) if n > 1 else float("nan")
    ratio = float(excess.mean() / sd * np.sqrt(periods_per_year)) if n > 1 and sd > 0 else None
    return {
        "ratio": ratio,
        "n_returns": n,
        "n_dropped_no_rate": int((~ok).sum()),
        "mean_risk_free_annual": float(rf[ok].mean()) if n else None,
        "risk_free_series": str(risk_free_annual.name),
    }
