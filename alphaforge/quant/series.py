"""Chargement des séries de prix, conversion de devise, base d'annualisation.

Règles (voir la spécification de l'analyste quantitatif) :
- un actif seul est évalué sur son propre calendrier, sans ffill ;
- la conversion se fait sur les prix, jour par jour, avec le taux du même jour ;
- le taux manquant est reporté vers l'avant seulement (jamais de bfill), borné en jours civils ;
- une série déjà convertie n'est jamais reconvertie.
"""
from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd

from .config import QuantConfig
from .datastore import DataStore
from .models import ResolvedWindow, ToolInputError


@dataclass
class PriceSeries:
    ticker: str
    currency: str
    prices: pd.Series
    field: str
    converted_from: Optional[str] = None
    fx_filled_days: int = 0
    fx_dropped_days: int = 0


def calendar_kind(asset: dict) -> str:
    return "crypto" if asset["asset_class"] == "crypto" else "exchange"


def periods_per_year(asset: dict, cfg: QuantConfig) -> int:
    return cfg.periods_per_year_crypto if calendar_kind(asset) == "crypto" else cfg.periods_per_year_exchange


def annualization_basis(ppy: int, cfg: QuantConfig) -> str:
    if ppy == cfg.periods_per_year_crypto:
        return f"{ppy} jours par an (crypto, cotée 7 jours sur 7)"
    if ppy == cfg.periods_per_year_exchange:
        return f"{ppy} jours de bourse par an"
    if ppy == cfg.periods_per_year_mixed:
        return f"{ppy} semaines par an (base commune pour un mélange crypto et actions)"
    return f"{ppy} périodes par an"


def load_prices(store: DataStore, asset: dict, window: ResolvedWindow, as_of: date, field: str = "adj_close") -> PriceSeries:
    df = store.price_frame(asset["ticker"], as_of)
    s = df[field].astype(float)
    s = s.loc[(s.index >= pd.Timestamp(window.start)) & (s.index <= pd.Timestamp(window.end))].dropna()
    s.name = asset["ticker"]
    return PriceSeries(ticker=asset["ticker"], currency=asset["currency"], prices=s, field=field)


def convert_series(store: DataStore, ps: PriceSeries, target: str, as_of: date) -> PriceSeries:
    cfg = store.config
    target = target.upper()
    if target not in cfg.supported_currencies:
        raise ToolInputError("unsupported_currency", f"Devise non prise en charge: {target}. Disponibles: {list(cfg.supported_currencies)}")
    if ps.currency == target:
        return ps
    if ps.converted_from is not None:
        raise ToolInputError(
            "double_conversion",
            f"Série déjà convertie de {ps.converted_from} vers {ps.currency} : refus de la reconvertir en {target}.",
        )

    fx = store.fx_series(as_of)
    idx = ps.prices.index
    if len(fx) == 0 or len(idx) == 0:
        return PriceSeries(ps.ticker, target, ps.prices.iloc[0:0], ps.field, ps.currency, 0, len(idx))
    union = fx.index.union(idx)
    obs_date = pd.Series(fx.index, index=fx.index).reindex(union).ffill()
    rate = fx.reindex(union).ffill()
    age = (pd.Series(union, index=union) - obs_date).dt.days
    rate_on, age_on = rate.reindex(idx), age.reindex(idx)
    valid = rate_on.notna() & (age_on <= cfg.ffill_limit_days)

    filled = int((valid & (age_on > 0)).sum())
    dropped = int((~valid).sum())
    p, r = ps.prices[valid], rate_on[valid]
    if ps.currency == "USD" and target == "CAD":
        conv = p * r
    elif ps.currency == "CAD" and target == "USD":
        conv = p / r
    else:
        raise ToolInputError("unsupported_pair", f"Paire non prise en charge: {ps.currency}->{target}")
    conv.name = ps.ticker
    return PriceSeries(ps.ticker, target, conv, ps.field, ps.currency, filled, dropped)
