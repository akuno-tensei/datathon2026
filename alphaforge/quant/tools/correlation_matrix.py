"""correlation_matrix : corrélations des rendements entre actifs, avec un alignement de calendrier explicite.

Règles :
- corrélations sur les rendements, jamais sur les prix ; prix convertis dans la devise de base AVANT les rendements ;
- quotidien : intersection des dates de cotation (inner join), rendements calculés entre dates communes, aucun report.
  Le rendement de BTC du vendredi au lundi est comparé à celui d'une action du vendredi au lundi ;
- mélange crypto + marchés cotés : l'hebdomadaire (semaine close le vendredi) est la référence, le quotidien
  en dates communes est fourni en complément ;
- fenêtre segmentée (hors 2022) : rendements datés dans la période exclue retirés.
"""
from datetime import date
from typing import Dict, List, Literal, Optional

import numpy as np
import pandas as pd
from pydantic import Field

from ..datastore import DataStore
from ..metrics import excluded_mask
from ..models import Evidence, Notice, StrictModel, ToolInputError, WindowSpec
from ..registry import build_evidence, refused, tool
from ..series import calendar_kind, convert_series, load_prices
from ..windows import resolve_as_of, resolve_window
from ._common import LIMIT_PAST, LIMIT_SURVIVOR
from .convert_currency import FX_LIMIT
from .data_quality import DataQualityParams, data_quality_check

Frequency = Literal["auto", "daily", "weekly"]


class CorrelationMatrixParams(StrictModel):
    tickers: List[str] = Field(min_length=2, max_length=15)
    window: WindowSpec
    frequency: Frequency = "auto"  # auto : hebdomadaire de référence si crypto et marchés cotés sont mélangés, sinon quotidien
    base_currency: Optional[str] = None
    as_of: Optional[date] = None


def _matrix(rets: pd.DataFrame, method: str, ppy: int, cfg) -> Dict:
    n = int(len(rets))
    tick = list(rets.columns)
    out = {"method": method, "periods_per_year": ppy, "n_obs": n,
           "first_date": rets.index[0].date().isoformat() if n else None,
           "last_date": rets.index[-1].date().isoformat() if n else None}
    if n < cfg.corr_min_obs_refuse:
        out.update(computed=False, matrix=None, pairs=[])
        return out
    c = rets.corr(method="pearson")
    m = {a: {b: (None if pd.isna(c.loc[a, b]) else float(c.loc[a, b])) for b in tick} for a in tick}
    pairs = [{"a": a, "b": b, "corr": m[a][b]} for i, a in enumerate(tick) for b in tick[i + 1:] if m[a][b] is not None]
    pairs.sort(key=lambda x: -x["corr"])
    out.update(
        computed=True, matrix=m, pairs=pairs,
        mean_pairwise=float(np.mean([x["corr"] for x in pairs])) if pairs else None,
        max_pair=pairs[0] if pairs else None, min_pair=pairs[-1] if pairs else None,
    )
    return out


@tool(
    "correlation_matrix",
    "1.0.0",
    "Corrélations des rendements entre 2 à 15 actifs sur une fenêtre, en devise de base. Quotidien sur les dates de "
    "cotation communes (inner join, aucun report) ; si crypto et marchés cotés sont mélangés, l'hebdomadaire (semaine "
    "close le vendredi) est la référence et le quotidien en dates communes est donné en complément. Méthode d'alignement "
    "et nombre d'observations communes consignés.",
    CorrelationMatrixParams,
)
def correlation_matrix(store: DataStore, p: CorrelationMatrixParams) -> Evidence:
    cfg = store.config
    as_of = resolve_as_of(store, p.as_of)
    base = (p.base_currency or cfg.default_base_currency).upper()
    raw = list(dict.fromkeys(p.tickers))
    params = {"tickers": raw, "window": p.window.model_dump(mode="json", exclude_none=True), "frequency": p.frequency,
              "base_currency": base, "as_of": as_of.isoformat()}
    try:
        assets, unknown = [], []
        for q in raw:
            a = store.asset(q)
            (assets.append(a) if a is not None else unknown.append(q))
        if unknown:
            raise ToolInputError("asset_not_found", f"Actif(s) hors de l'univers de données : {', '.join(unknown)}. Aucune donnée disponible.")
        tickers = list(dict.fromkeys(a["ticker"] for a in assets))
        if len(tickers) < 2:
            raise ToolInputError("not_enough_assets", "Il faut au moins deux actifs distincts pour une corrélation.")
        window, notices = resolve_window(store, p.window, as_of)
    except ToolInputError as e:
        return refused(store, "correlation_matrix", params, as_of, e)
    params.update(tickers=tickers, window=window.model_dump(mode="json"))
    notices = list(notices)
    by_t = {a["ticker"]: a for a in assets}

    # Contrôle de qualité de chaque série : une série insuffisante est retirée, et c'est dit.
    depends, kept, excluded = [], [], []
    for t in tickers:
        dq = data_quality_check(store, DataQualityParams(ticker=t, window=p.window, as_of=as_of))
        depends.append(dq.evidence_id)
        if dq.status == "refused" or dq.value["quality"] == "insuffisant":
            excluded.append({"ticker": t, "reason": "données insuffisantes", "evidence_id": dq.evidence_id})
            notices.append(Notice(code="ASSET_EXCLUDED", message=f"{t} retiré de la matrice : le contrôle de qualité conclut à des données insuffisantes."))
        else:
            kept.append(t)
    inputs = [store.input_ref("prices_daily.csv", t, as_of) for t in tickers]
    if len(kept) < 2:
        return refused(store, "correlation_matrix", params, as_of,
                       ToolInputError("insufficient_data", "Moins de deux actifs avec des données suffisantes."),
                       window=window, notices=notices, depends_on=depends, inputs=inputs)

    # Prix en devise de base, chacun sur son propre calendrier.
    prices, converted, fx_filled = {}, False, 0
    for t in kept:
        ps = convert_series(store, load_prices(store, by_t[t], window, as_of, "adj_close"), base, as_of)
        converted |= ps.converted_from is not None
        fx_filled += ps.fx_filled_days
        prices[t] = ps.prices
    if converted:
        inputs.append(store.input_ref("fx_usdcad_daily.csv", "USDCAD", as_of))
        if fx_filled:
            notices.append(Notice(code="FX_FORWARD_FILLED", severity="info",
                                  message=f"{fx_filled} jour(s)-actif sans taux de change du même jour : dernier taux connu reporté (max {cfg.ffill_limit_days} jours)."))

    kinds = {calendar_kind(by_t[t]) for t in kept}
    mixed = len(kinds) > 1
    freq = ("weekly" if mixed else "daily") if p.frequency == "auto" else p.frequency

    def returns(df: pd.DataFrame) -> pd.DataFrame:
        r = df.dropna().pct_change().iloc[1:]  # dates communes seulement, puis rendements entre ces dates
        if window.excluded:
            r = r[~excluded_mask(r.index, window.excluded)]
        return r

    frame = pd.DataFrame(prices)
    daily = _matrix(returns(frame), "quotidien, dates de cotation communes (inner join, aucun report)",
                    cfg.periods_per_year_exchange if "exchange" in kinds else cfg.periods_per_year_crypto, cfg)
    weekly_px = pd.DataFrame({t: s.resample(cfg.corr_weekly_anchor).last() for t, s in prices.items()})
    weekly = _matrix(returns(weekly_px), f"hebdomadaire, dernier prix de chaque semaine close le vendredi ({cfg.corr_weekly_anchor}), semaines communes",
                     cfg.periods_per_year_mixed, cfg)

    matrices = {"daily_common": daily, "weekly": weekly}
    reference = "weekly" if freq == "weekly" else "daily_common"
    if not mixed and p.frequency == "auto":
        matrices = {"daily_common": daily}  # même calendrier : le quotidien suffit, pas de complément
    ref = matrices[reference]
    if not ref["computed"]:
        return refused(store, "correlation_matrix", params, as_of,
                       ToolInputError("insufficient_data", f"{ref['n_obs']} observations communes seulement (minimum {cfg.corr_min_obs_refuse})."),
                       window=window, notices=notices, depends_on=depends, inputs=inputs)
    for key, m in matrices.items():
        need = cfg.corr_min_obs_weekly if key == "weekly" else cfg.corr_min_obs_daily
        if m["computed"] and m["n_obs"] < need:
            notices.append(Notice(code="FEW_COMMON_OBSERVATIONS",
                                  message=f"Matrice {key} : {m['n_obs']} observations communes (moins de {need}) ; corrélations peu fiables (actif récent ou fenêtre courte)."))
        if m["computed"] and any(v is None for row in m["matrix"].values() for v in row.values()):
            notices.append(Notice(code="UNDEFINED_CORRELATION", message=f"Matrice {key} : corrélation non définie pour une série constante sur la période."))
    if mixed:
        notices.append(Notice(code="MIXED_CALENDARS", severity="info",
                              message="Crypto (7 jours sur 7) et marchés cotés mélangés : la matrice hebdomadaire sert de référence ; "
                                      "le quotidien n'utilise que les jours de bourse communs (rendement de la crypto du vendredi au lundi)."))

    value = {
        "tickers": kept, "excluded_assets": excluded, "base_currency": base, "mixed_calendars": mixed,
        "reference": reference, "matrices": matrices,
        "n_obs": ref["n_obs"], "max_pair": ref["max_pair"], "min_pair": ref["min_pair"], "mean_pairwise": ref["mean_pairwise"],
    }
    return build_evidence(
        store, "correlation_matrix", params, as_of, window=window, value=value, n_obs=ref["n_obs"], inputs=inputs,
        depends_on=depends,
        sources=[store.source_label("prices_daily.csv")] + ([store.source_label("fx_usdcad_daily.csv")] if converted else []),
        notices=notices,
        assumptions=[
            "Corrélation de Pearson sur les rendements simples, jamais sur les prix.",
            f"Prix convertis en {base} jour par jour avant le calcul des rendements.",
            "Quotidien : intersection des dates de cotation, sans aucun report de valeur ; un report fabriquerait des rendements nuls "
            "les jours fermés et sous-estimerait la corrélation.",
            "Hebdomadaire : dernier prix connu de chaque semaine close le vendredi, semaines communes à tous les actifs.",
        ],
        limits=[
            LIMIT_PAST, LIMIT_SURVIVOR,
            "Les corrélations changent selon les régimes de marché (en 2022, actions et obligations ont baissé ensemble) : "
            "une corrélation passée faible ne garantit pas une diversification future.",
        ] + ([FX_LIMIT] if converted else []),
    )
