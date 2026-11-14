"""portfolio_metrics : rejeu historique d'une allocation fictive (poids donnés), indicateurs de portefeuille.

Mode « rejeu historique » : on applique les poids à des prix passés, on ne projette rien. Résultat étiqueté
« scénario » dans la preuve. Aucun frais, aucune taxe, aucune vente à découvert.

Calendriers (décisions de l'analyste) :
- actions et FNB (CA + US) : union des jours de bourse, report vers l'avant borné à 5 jours civils, chaque report compté ;
- crypto seule : ses 7 jours sur 7, base 365 ;
- crypto et marchés cotés mélangés : base hebdomadaire (semaine close le vendredi), 52 périodes par an.
Les prix sont convertis en devise de base avant les rendements. L'hypothèse de rééquilibrage est obligatoire.
"""
from datetime import date
from typing import Dict, List, Literal, Optional

import numpy as np
import pandas as pd
from pydantic import Field

from ..datastore import DataStore
from ..metrics import excluded_mask, metric_block, metric_block_segmented
from ..models import Evidence, Notice, StrictModel, ToolInputError, WindowSpec
from ..registry import build_evidence, refused, tool
from ..series import PriceSeries, annualization_basis, calendar_kind, convert_series, load_prices
from ..windows import resolve_as_of, resolve_window
from ._common import LIMIT_PAST, LIMIT_SURVIVOR
from .convert_currency import FX_LIMIT
from .data_quality import DataQualityParams, data_quality_check

Rebalancing = Literal["rebalanced", "buy_and_hold"]
SCENARIO_LABEL = "Scénario rétrospectif fictif (rejeu historique), aucune prévision"


class Position(StrictModel):
    ticker: str
    weight: float


class PortfolioMetricsParams(StrictModel):
    positions: List[Position] = Field(min_length=1, max_length=15)
    window: WindowSpec
    rebalancing: Rebalancing  # obligatoire : poids constants rééquilibrés à chaque période, ou achat-et-conservation
    mode: Literal["rejeu_historique"] = "rejeu_historique"
    base_currency: Optional[str] = None
    as_of: Optional[date] = None


def _bounded_ffill(s: pd.Series, index: pd.DatetimeIndex, limit_days: int):
    """Reporte la dernière valeur connue vers l'avant, au plus `limit_days` jours civils après la dernière observation réelle."""
    union = s.index.union(index)
    obs = pd.Series(s.index, index=s.index).reindex(union).ffill()
    vals = s.reindex(union).ffill()
    age = (pd.Series(union, index=union) - obs).dt.days
    out = vals.reindex(index).where(age.reindex(index) <= limit_days)
    filled = int(((age.reindex(index) > 0) & out.notna()).sum())
    return out, filled


@tool(
    "portfolio_metrics",
    "1.0.0",
    "Rejeu historique d'une allocation fictive (2 à 15 positions, poids positifs de somme 1, aucune vente à découvert) sur une "
    "fenêtre : mêmes indicateurs que compute_metrics au niveau du portefeuille, concentration (poids maximal, HHI, nombre effectif), "
    "répartition par classe, secteur et devise, contribution au risque de chaque position et ratio de diversification. "
    "L'hypothèse de rééquilibrage est obligatoire. Résultat étiqueté scénario, jamais une prévision.",
    PortfolioMetricsParams,
)
def portfolio_metrics(store: DataStore, p: PortfolioMetricsParams) -> Evidence:
    cfg = store.config
    as_of = resolve_as_of(store, p.as_of)
    base = (p.base_currency or cfg.default_base_currency).upper()
    params = {"positions": [x.model_dump() for x in p.positions], "window": p.window.model_dump(mode="json", exclude_none=True),
              "rebalancing": p.rebalancing, "mode": p.mode, "base_currency": base, "as_of": as_of.isoformat()}
    try:
        meta, weights, unknown = [], [], []
        for pos in p.positions:
            a = store.asset(pos.ticker)
            if a is None:
                unknown.append(pos.ticker)
            else:
                meta.append(a)
                weights.append(pos.weight)
        if unknown:
            raise ToolInputError("asset_not_found", f"Actif(s) hors de l'univers de données : {', '.join(unknown)}. Aucune donnée disponible.")
        tickers = [a["ticker"] for a in meta]
        if len(set(tickers)) != len(tickers):
            raise ToolInputError("duplicate_position", "Un actif apparaît plusieurs fois dans les positions : regrouper ses poids.")
        if any(w <= 0 for w in weights):
            raise ToolInputError("invalid_weights", "Poids invalides : chaque poids doit être strictement positif (aucune vente à découvert en simulation).")
        total = float(sum(weights))
        if abs(total - 1.0) > cfg.portfolio_weight_tolerance:
            raise ToolInputError("invalid_weights", f"Les poids doivent sommer à 1 : somme = {total:.6f}.")
        window, notices = resolve_window(store, p.window, as_of)
    except ToolInputError as e:
        return refused(store, "portfolio_metrics", params, as_of, e)
    w = np.array(weights, dtype=float)
    params.update(positions=[{"ticker": t, "weight": x} for t, x in zip(tickers, weights)], window=window.model_dump(mode="json"))
    notices = list(notices)
    inputs = [store.input_ref("prices_daily.csv", t, as_of) for t in tickers]

    depends, bad = [], []
    for t in tickers:
        dq = data_quality_check(store, DataQualityParams(ticker=t, window=p.window, as_of=as_of))
        depends.append(dq.evidence_id)
        if dq.status == "refused" or dq.value["quality"] == "insuffisant":
            bad.append(t)
    if bad:  # on ne retire jamais un actif en silence : les poids ne seraient plus ceux demandés
        return refused(store, "portfolio_metrics", params, as_of,
                       ToolInputError("insufficient_data", f"Données insuffisantes pour : {', '.join(bad)}. Les poids demandés ne peuvent pas être respectés."),
                       window=window, notices=notices, depends_on=depends, inputs=inputs)

    prices, converted, fx_filled = {}, False, 0
    for a in meta:
        ps = convert_series(store, load_prices(store, a, window, as_of, "adj_close"), base, as_of)
        converted |= ps.converted_from is not None
        fx_filled += ps.fx_filled_days
        prices[a["ticker"]] = ps.prices
    if converted:
        inputs.append(store.input_ref("fx_usdcad_daily.csv", "USDCAD", as_of))
        if fx_filled:
            notices.append(Notice(code="FX_FORWARD_FILLED", severity="info",
                                  message=f"{fx_filled} jour(s)-actif sans taux de change du même jour : dernier taux connu reporté (max {cfg.ffill_limit_days} jours)."))

    kinds = {calendar_kind(a) for a in meta}
    if kinds == {"crypto", "exchange"}:
        step, ppy, step_label = "weekly", cfg.periods_per_year_mixed, "semaine"
        panel = pd.DataFrame({t: s.resample(cfg.corr_weekly_anchor).last() for t, s in prices.items()})
        ffilled: Dict[str, int] = {}
    else:
        step, step_label = "daily", "jour"
        ppy = cfg.periods_per_year_crypto if kinds == {"crypto"} else cfg.periods_per_year_exchange
        union = pd.DatetimeIndex(sorted(set().union(*[set(s.index) for s in prices.values()])))
        cols, ffilled = {}, {}
        for t, s in prices.items():
            cols[t], ffilled[t] = _bounded_ffill(s, union, cfg.ffill_limit_days)
        panel = pd.DataFrame(cols)
    before = len(panel)
    panel = panel.dropna()
    dropped = before - len(panel)
    if any(ffilled.values()):
        notices.append(Notice(code="PANEL_FORWARD_FILLED",
                              message="Jours reportés vers l'avant (dernier prix connu, max "
                                      f"{cfg.ffill_limit_days} jours civils) : " + ", ".join(f"{t} {n}" for t, n in ffilled.items() if n) + "."))
    if dropped:
        notices.append(Notice(code="PANEL_ROWS_DROPPED",
                              message=f"{dropped} date(s) retirée(s) : au moins un actif sans prix connu (début tardif ou trou au-delà de {cfg.ffill_limit_days} jours)."))
    need = cfg.corr_min_obs_weekly if step == "weekly" else cfg.min_obs_insufficient
    if len(panel) - 1 < need:
        return refused(store, "portfolio_metrics", params, as_of,
                       ToolInputError("insufficient_data", f"{max(len(panel) - 1, 0)} rendements communs seulement (minimum {need})."),
                       window=window, notices=notices, depends_on=depends, inputs=inputs, n_obs=max(len(panel) - 1, 0))
    eff_start = panel.index[0].date()
    if (eff_start - window.start).days > cfg.edge_gap_days:
        notices.append(Notice(code="COMMON_START_LATE",
                              message=f"Portefeuille mesuré à partir du {eff_start} (fenêtre demandée dès le {window.start}) : "
                                      "avant cette date, au moins un actif n'a pas de données."))

    rets = panel.pct_change().iloc[1:]
    if p.rebalancing == "rebalanced":
        value = pd.Series(100.0 * np.concatenate([[1.0], np.cumprod(1.0 + rets.values @ w)]), index=panel.index)
    else:
        value = pd.Series(100.0 * ((panel / panel.iloc[0]).values @ w), index=panel.index)
    value.name = "PORTFOLIO"
    keep = ~excluded_mask(rets.index, window.excluded) if window.excluded else np.ones(len(rets), dtype=bool)
    if window.excluded:
        if int(keep.sum()) < need:
            return refused(store, "portfolio_metrics", params, as_of,
                           ToolInputError("insufficient_data", f"{int(keep.sum())} rendements hors des périodes exclues (minimum {need})."),
                           window=window, notices=notices, depends_on=depends, inputs=inputs)
        block = metric_block_segmented(value, window.excluded, ppy, cfg.annualization_min_days)
        notices.append(Notice(code="SEGMENTED_WINDOW", severity="info",
                              message="Fenêtre segmentée : rendements datés dans " + ", ".join(f"{x.start} – {x.end}" for x in window.excluded) + " retirés."))
    else:
        block = metric_block(value, ppy, cfg.annualization_min_days)
    if block.annualized_return is None:
        notices.append(Notice(code="ANNUALIZATION_SKIPPED", severity="info",
                              message=f"Période observée de moins de {cfg.annualization_min_days} jours : pas de rendement annualisé, seulement le cumulatif."))

    # Risque : covariance annualisée des rendements de chaque actif ; contributions avec les poids initiaux.
    r_kept = rets[keep]
    cov = r_kept.cov().values * ppy
    sig_i = np.sqrt(np.diag(cov))
    var_p = float(w @ cov @ w)
    sigma_p = float(np.sqrt(max(var_p, 0.0)))
    contrib, ratio = None, None
    if sigma_p > 1e-6 * max(float(sig_i.max()), 1e-12):  # seuil relatif : un reste d'arrondi n'est pas une volatilité
        mrc = cov @ w
        contrib = [{"ticker": t, "weight": float(wi), "standalone_volatility": float(si),
                    "risk_contribution": float(wi * m / sigma_p), "risk_share": float(wi * m / var_p)}
                   for t, wi, si, m in zip(tickers, w, sig_i, mrc)]
        ratio = float(np.dot(w, sig_i) / sigma_p)
    else:
        notices.append(Notice(code="ZERO_PORTFOLIO_VOLATILITY",
                              message="Volatilité du portefeuille nulle : contributions au risque et ratio de diversification non définis."))
    hhi = float(np.sum(w ** 2))
    imax = int(np.argmax(w))
    final_w = None
    if p.rebalancing == "buy_and_hold":
        g = (panel.iloc[-1] / panel.iloc[0]).values * w
        final_w = dict(zip(tickers, (g / g.sum()).tolist()))

    def breakdown(key: str) -> List[dict]:
        acc: Dict[str, float] = {}
        for a, wi in zip(meta, w):
            acc[a[key]] = acc.get(a[key], 0.0) + float(wi)
        return [{"name": k, "weight": v} for k, v in sorted(acc.items(), key=lambda kv: -kv[1])]

    recipe = {"kind": "portfolio", "positions": [[t, float(x)] for t, x in zip(tickers, w)], "rebalancing": p.rebalancing,
              "base": base, "start": panel.index[0].date().isoformat(), "end": panel.index[-1].date().isoformat(), "as_of": as_of.isoformat()}
    sid = store.series.put(PriceSeries("PORTFOLIO", base, value, "portfolio_value"), recipe,
                           "|".join(i.sha256 for i in inputs))
    n_ret = int(keep.sum())
    value_out = {
        "label": SCENARIO_LABEL, "mode": p.mode, "rebalancing": p.rebalancing,
        "rebalancing_description": ("poids constants, rééquilibrés à chaque " + step_label) if p.rebalancing == "rebalanced"
                                   else "achat-et-conservation : poids initiaux, laissés dériver",
        "base_currency": base, "calendar_basis": step, "period_label": step_label, "periods_per_year": ppy,
        "annualization_basis": annualization_basis(ppy, cfg), "n_returns": n_ret, "segmented": bool(window.excluded),
        "first_date": panel.index[0].date().isoformat(), "last_date": panel.index[-1].date().isoformat(),
        "effective_start": eff_start.isoformat(),
        "positions": [{"ticker": a["ticker"], "name": a["name"], "weight": float(wi), "asset_class": a["asset_class"],
                       "sector": a["sector"], "currency": a["currency"]} for a, wi in zip(meta, w)],
        "metrics": block.model_dump(mode="json"),
        "concentration": {"max_weight": float(w[imax]), "max_weight_ticker": tickers[imax], "hhi": hhi,
                          "effective_positions": float(1.0 / hhi), "n_positions": len(tickers), "final_weights": final_w},
        "breakdown": {"asset_class": breakdown("asset_class"), "sector": breakdown("sector"), "currency": breakdown("currency")},
        "risk": {"portfolio_volatility_initial_weights": sigma_p, "weighted_average_volatility": float(np.dot(w, sig_i)),
                 "diversification_ratio": ratio, "contributions": contrib},
        "series_id_portfolio": sid,
        "data_quality": [{"ticker": t, "evidence_id": d} for t, d in zip(tickers, depends)],
    }
    return build_evidence(
        store, "portfolio_metrics", params, as_of, window=window, value=value_out, n_obs=n_ret, inputs=inputs, depends_on=depends,
        sources=[store.source_label("prices_daily.csv")] + ([store.source_label("fx_usdcad_daily.csv")] if converted else []),
        notices=notices, label="scenario",
        assumptions=[
            "Scénario fictif : les poids sont une hypothèse de simulation, pas une recommandation. Aucune transaction réelle.",
            ("Poids constants, rééquilibrés à chaque " + step_label + " : rendement du portefeuille = somme pondérée des rendements des actifs."
             if p.rebalancing == "rebalanced" else
             "Achat-et-conservation : les quantités restent fixes, les poids dérivent avec les prix (poids finaux fournis)."),
            "Aucun frais, aucune taxe, aucun glissement de prix ; aucune vente à découvert ; dividendes réinvestis (adj_close).",
            f"Prix convertis en {base} jour par jour avant le calcul des rendements ; report borné à {cfg.ffill_limit_days} jours civils, chaque report compté.",
            f"Base d'annualisation : {annualization_basis(ppy, cfg)}.",
            "Contribution au risque = poids × covariance de l'actif avec le portefeuille ÷ volatilité du portefeuille, avec les poids initiaux ; "
            "ratio de diversification = volatilité moyenne pondérée des actifs ÷ volatilité du portefeuille.",
        ],
        limits=[
            LIMIT_PAST, LIMIT_SURVIVOR,
            "Rejeu rétrospectif : il décrit ce qu'aurait donné cette allocation sur la période, il ne projette rien.",
            "Sans les titres détenus, un FNB est traité comme une position unique : la transparence réelle (par exemple la part de "
            "technologie d'un FNB et d'actions combinés) exigerait des données de composition, hors de ce jeu de départ.",
        ] + ([FX_LIMIT] if converted else []),
    )
