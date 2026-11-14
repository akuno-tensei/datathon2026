"""data_quality_check : état des données d'un actif sur une fenêtre. Consommé par compute_metrics."""
from datetime import date
from typing import List, Optional

import pandas as pd

from ..datastore import DataStore
from ..models import Evidence, Notice, StrictModel, ToolInputError, WindowSpec
from ..registry import build_evidence, refused, tool
from ..series import calendar_kind
from ._common import fallback_params, prelude, resolved_params

_RANK = {"ok": 0, "warning": 1, "insufficient": 2}


class DataQualityParams(StrictModel):
    ticker: str
    window: WindowSpec
    as_of: Optional[date] = None


@tool(
    "data_quality_check",
    "1.0.0",
    "Contrôle la qualité des données d'un actif sur une fenêtre : couverture, trous, prix invalides, volume nul, "
    "fractionnements suspects. Renvoie quality = ok, warning ou insufficient (insufficient bloque compute_metrics).",
    DataQualityParams,
)
def data_quality_check(store: DataStore, p: DataQualityParams) -> Evidence:
    try:
        asset, as_of, window, win_notices = prelude(store, p.ticker, p.window, p.as_of)
    except ToolInputError as e:
        params, as_of = fallback_params(p.ticker, p.window, p.as_of, store)
        return refused(store, "data_quality_check", params, as_of, e)

    cfg = store.config
    ticker = asset["ticker"]
    kind = calendar_kind(asset)
    t0, t1 = pd.Timestamp(window.start), pd.Timestamp(window.end)
    full = store.price_frame(ticker, as_of)
    w = full.loc[(full.index >= t0) & (full.index <= t1)]
    n_obs = len(w)

    notices: List[Notice] = list(win_notices)
    checks: List[dict] = []
    level = "ok"

    def flag(name: str, passed: bool, detail: str, code: str = "", lvl: str = "warning", ok: str = "ok") -> None:
        nonlocal level
        checks.append({"name": name, "passed": passed, "detail": ok if passed else detail})
        if not passed:
            notices.append(Notice(code=code, message=detail))
            if _RANK[lvl] > _RANK[level]:
                level = lvl

    # Observations et couverture par rapport aux jours de cotation de référence (actifs du même marché).
    ref = store.peer_dates(ticker, as_of)
    ref = ref[(ref >= t0) & (ref <= t1)]
    expected = len(ref)
    coverage = (n_obs / expected) if expected else 0.0
    missing = ref.difference(w.index)

    if n_obs == 0:
        flag("observations", False, "Aucune observation dans la fenêtre.", "NO_DATA", "insufficient")
    elif n_obs < cfg.min_obs_insufficient:
        flag("observations", False, f"{n_obs} observations seulement (minimum {cfg.min_obs_insufficient}).", "INSUFFICIENT_OBSERVATIONS", "insufficient")
    elif n_obs < cfg.min_obs_warning:
        flag("observations", False, f"{n_obs} observations : volatilité et rendement annualisé peu fiables.", "FEW_OBSERVATIONS")
    else:
        flag("observations", True, "", ok=f"{n_obs} observations.")

    if n_obs:
        if coverage < cfg.coverage_insufficient:
            flag("couverture", False, f"Couverture {coverage:.0%} des jours de cotation de référence ({n_obs}/{expected}).", "LOW_COVERAGE", "insufficient")
        elif coverage < cfg.coverage_warning:
            ex = ", ".join(d.date().isoformat() for d in missing[:5])
            flag("couverture", False, f"Couverture {coverage:.1%} ({n_obs}/{expected}) ; jours manquants, ex.: {ex}.", "MISSING_DAYS")
        else:
            flag("couverture", True, "", ok=f"Couverture {coverage:.1%} ({n_obs}/{expected}).")

        first, last = w.index[0], w.index[-1]
        sg, eg = (first - t0).days, (t1 - last).days
        flag("debut_fenetre", sg <= cfg.edge_gap_days, f"Première observation {first.date()} : {sg} jours après le début de fenêtre.", "WINDOW_START_GAP")
        flag("fin_fenetre", eg <= cfg.edge_gap_days, f"Dernière observation {last.date()} : {eg} jours avant la fin de fenêtre.", "WINDOW_END_GAP")

        thr = cfg.internal_gap_days_crypto if kind == "crypto" else cfg.internal_gap_days_exchange
        gaps = pd.Series(w.index[1:] - w.index[:-1], index=w.index[1:]).dt.days if n_obs > 1 else pd.Series(dtype=int)
        mg = int(gaps.max()) if len(gaps) else 0
        where = gaps.idxmax().date() if len(gaps) and mg > thr else None
        flag("trous_internes", mg <= thr, f"Écart maximal de {mg} jours entre deux observations (jusqu'au {where}).", "INTERNAL_GAP")

        raw_nan = int(w[["close", "adj_close"]].isna().any(axis=1).sum())
        nonpos = int(((w[["close", "adj_close"]] <= 0).any(axis=1)).sum())
        flag("prix_non_positifs", nonpos == 0, f"{nonpos} jour(s) avec un prix nul ou négatif.", "NON_POSITIVE_PRICE", "insufficient")
        flag("prix_manquants", raw_nan == 0, f"{raw_nan} jour(s) avec un prix manquant (exclus des calculs).", "NAN_PRICES")

        zero = w.index[w["volume"] == 0]
        flag(
            "volume_nul",
            len(zero) == 0,
            f"{len(zero)} jour(s) à volume nul: {', '.join(d.date().isoformat() for d in zero[:5])} (cote sans transaction possible).",
            "ZERO_VOLUME",
        )

    # Mouvements extrêmes : informatifs, sauf quand ils coïncident avec une opération sur titres.
    rets = full["adj_close"].pct_change()
    thr_move = cfg.large_move_crypto if kind == "crypto" else cfg.large_move_exchange
    big = rets[(rets.abs() > thr_move) & (rets.index >= t0) & (rets.index <= t1)]
    large_moves = [{"date": d.date().isoformat(), "return": float(r)} for d, r in big.items()]
    if large_moves:
        notices.append(
            Notice(code="LARGE_MOVES", severity="info",
                   message=f"{len(large_moves)} variation(s) quotidienne(s) au-delà de {thr_move:.0%} dans la fenêtre (voir value.large_moves) ; à vérifier, non corrigées.")
        )

    # Opérations sur titres.
    acts = store.corporate_actions(ticker, as_of)
    acts = acts[(acts["ex_date"] >= t0) & (acts["ex_date"] <= t1)]
    splits = acts[acts["action"] == "split"]
    split_list = []
    close_ret = full["close"].pct_change()
    for _, r in splits.iterrows():
        ratio, d = float(r["value"]), r["ex_date"]
        split_list.append({"date": d.date().isoformat(), "ratio": ratio})
        if 1.0 < ratio < 1.5:
            flag(
                f"fractionnement_{d.date()}", False,
                f"Fractionnement de ratio {ratio} le {d.date()} : ne ressemble pas à un fractionnement réel "
                "(probable distribution spéciale). À vérifier auprès de l'émetteur avant de s'y fier.",
                "SPLIT_RATIO_UNUSUAL",
            )
        elif ratio >= 1.5 and d in close_ret.index and close_ret.loc[d] <= -(1 - 1 / ratio) * 0.8:
            flag(
                f"fractionnement_{d.date()}", False,
                f"Fractionnement {ratio}:1 le {d.date()} : le cours `close` chute de {close_ret.loc[d]:.0%}, "
                "ce qui suggère des prix non ajustés.",
                "SPLIT_NOT_ADJUSTED", "insufficient",
            )

    if level == "insufficient":
        notices.append(Notice(code="DATA_INSUFFICIENT", message="Données insuffisantes : les métriques ne seront pas calculées."))

    value = {
        "ticker": ticker,
        "quality": level,
        "n_obs": n_obs,
        "expected_obs": expected,
        "coverage_ratio": round(coverage, 6),
        "first_date": w.index[0].date().isoformat() if n_obs else None,
        "last_date": w.index[-1].date().isoformat() if n_obs else None,
        "checks": checks,
        "large_moves": large_moves,
        "corporate_actions": {"dividends": int((acts["action"] == "dividend").sum()), "splits": split_list},
    }
    params = resolved_params(asset, window, as_of)
    return build_evidence(
        store, "data_quality_check", params, as_of, window=window, value=value,
        sources=[store.source_label("prices_daily.csv"), store.source_label("corporate_actions.csv")],
        notices=notices,
        assumptions=[
            "Les jours de cotation attendus sont l'union des dates des actifs du même marché (TSX, États-Unis ou crypto).",
        ],
        limits=["Le contrôle détecte des anomalies visibles dans les séries ; il ne certifie pas l'exactitude des cours (Yahoo Finance n'est pas certifié par les bourses)."],
    )
