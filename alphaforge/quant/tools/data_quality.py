"""data_quality_check : état des données d'un actif sur une fenêtre. Consommé par compute_metrics."""
from datetime import date
from typing import List, Optional

import pandas as pd

from ..datastore import DataStore
from ..models import Evidence, Notice, StrictModel, ToolInputError, WindowSpec
from ..registry import build_evidence, refused, tool
from ..series import calendar_kind
from ._common import fallback_params, prelude, resolved_params

_RANK = {"ok": 0, "avertissement": 1, "insuffisant": 2}  # libellés de value.quality (le statut de la preuve reste ok/warning/refused)


class DataQualityParams(StrictModel):
    ticker: str
    window: WindowSpec
    as_of: Optional[date] = None


@tool(
    "data_quality_check",
    "1.1.0",
    "Contrôle la qualité des données d'un actif sur une fenêtre : couverture, trous, prix invalides, volume nul, "
    "fractionnements suspects. Renvoie value.quality = ok, avertissement ou insuffisant (insuffisant bloque compute_metrics).",
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

    def flag(name: str, passed: bool, detail: str, code: str = "", lvl: str = "avertissement", ok: str = "ok") -> None:
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
    longest_missing_run = 0
    if len(ref):
        run = 0
        present = set(w.index)
        for d in ref:
            run = 0 if d in present else run + 1
            longest_missing_run = max(longest_missing_run, run)

    if n_obs == 0:
        flag("observations", False, "Aucune observation dans la fenêtre.", "NO_DATA", "insuffisant")
    elif n_obs < cfg.min_obs_insufficient:
        flag("observations", False, f"{n_obs} observations seulement (minimum {cfg.min_obs_insufficient}).", "INSUFFICIENT_OBSERVATIONS", "insuffisant")
    elif n_obs < cfg.min_obs_warning:
        flag("observations", False, f"{n_obs} observations : volatilité et rendement annualisé peu fiables.", "FEW_OBSERVATIONS")
    else:
        flag("observations", True, "", ok=f"{n_obs} observations.")

    if n_obs:
        if coverage < cfg.coverage_insufficient:
            flag("couverture", False, f"Couverture {coverage:.0%} des jours de cotation de référence ({n_obs}/{expected}).", "LOW_COVERAGE", "insuffisant")
        elif coverage < cfg.coverage_warning:
            ex = ", ".join(d.date().isoformat() for d in missing[:5])
            flag("couverture", False, f"Couverture {coverage:.1%} ({n_obs}/{expected}) ; jours manquants, ex.: {ex}.", "MISSING_DAYS")
        else:
            flag("couverture", True, "", ok=f"Couverture {coverage:.1%} ({n_obs}/{expected}).")

        first, last = w.index[0], w.index[-1]
        sg, eg = (first - t0).days, (t1 - last).days
        cov_first = store.price_frame(ticker, as_of).index.min()
        late = sg > cfg.edge_gap_days and first == cov_first  # aucune donnée avant : l'actif n'existait pas (ou pas dans le jeu)
        flag(
            "debut_fenetre", sg <= cfg.edge_gap_days,
            (f"Début tardif : première observation {first.date()}, {sg} jours après le début de fenêtre ; aucune donnée antérieure "
             "(actif lancé plus tard ou non couvert)." if late else
             f"Première observation {first.date()} : {sg} jours après le début de fenêtre, alors que des données antérieures existent."),
            "LATE_START" if late else "WINDOW_START_GAP",
        )
        flag("fin_fenetre", eg <= cfg.edge_gap_days, f"Dernière observation {last.date()} : {eg} jours avant la fin de fenêtre.", "WINDOW_END_GAP")

        flag(
            "sequence_manquante", longest_missing_run <= 3,
            f"Plus longue séquence de jours de cotation manquants : {longest_missing_run}.", "MISSING_RUN",
            ok=f"Plus longue séquence manquante : {longest_missing_run} jour(s).",
        )
        thr = cfg.internal_gap_days_crypto if kind == "crypto" else cfg.internal_gap_days_exchange
        gaps = pd.Series(w.index[1:] - w.index[:-1], index=w.index[1:]).dt.days if n_obs > 1 else pd.Series(dtype=int)
        mg = int(gaps.max()) if len(gaps) else 0
        where = gaps.idxmax().date() if len(gaps) and mg > thr else None
        flag("trous_internes", mg <= thr, f"Écart maximal de {mg} jours entre deux observations (jusqu'au {where}).", "INTERNAL_GAP")

        raw_nan = int(w[["close", "adj_close"]].isna().any(axis=1).sum())
        nonpos = int(((w[["close", "adj_close"]] <= 0).any(axis=1)).sum())
        flag("prix_non_positifs", nonpos == 0, f"{nonpos} jour(s) avec un prix nul ou négatif.", "NON_POSITIVE_PRICE", "insuffisant")
        flag("prix_manquants", raw_nan == 0, f"{raw_nan} jour(s) avec un prix manquant (exclus des calculs).", "NAN_PRICES")

        bad_ccy = int((w["currency"] != asset["currency"]).sum())
        flag("devise_des_lignes", bad_ccy == 0,
             f"{bad_ccy} ligne(s) dont la devise diffère de celle de l'univers ({asset['currency']}).", "CURRENCY_MISMATCH")
        over = int((w["adj_close"] > w["close"] * (1 + cfg.adj_close_over_close_tolerance)).sum())
        flag("adj_close_vs_close", over == 0,
             f"{over} jour(s) où adj_close dépasse close de plus de {cfg.adj_close_over_close_tolerance:.0%} (ajustement incohérent).",
             "ADJ_CLOSE_ABOVE_CLOSE")

        zero = w.index[w["volume"] == 0]
        flag(
            "volume_nul",
            len(zero) == 0,
            f"{len(zero)} jour(s) à volume nul: {', '.join(d.date().isoformat() for d in zero[:5])} (cote sans transaction possible).",
            "ZERO_VOLUME",
        )

    # Sauts suspects : étiquetés, jamais supprimés (un krach réel n'est pas une erreur). Seuil fixe OU k écarts-types.
    rets = full["adj_close"].pct_change()
    in_win = rets[(rets.index >= t0) & (rets.index <= t1)].dropna()
    thr_move = cfg.large_move_crypto if kind == "crypto" else cfg.large_move_exchange
    sigma = float(in_win.std(ddof=1)) if len(in_win) > 2 else float("nan")
    sigma_thr = cfg.large_move_sigma * sigma if sigma == sigma else float("inf")
    flagged = []
    for d, r in in_win.items():
        by_fixed, by_sigma = abs(r) > thr_move, abs(r) > sigma_thr
        if by_fixed or by_sigma:
            flagged.append({"date": d.date().isoformat(), "return": float(r), "z": float(r / sigma) if sigma == sigma and sigma > 0 else None,
                            "trigger": "fixe+sigma" if by_fixed and by_sigma else ("fixe" if by_fixed else "sigma")})
    flagged.sort(key=lambda m: -abs(m["return"]))
    large_moves = flagged[:20]  # les 20 plus grands ; le nombre total est dans n_large_moves
    if flagged:
        notices.append(
            Notice(code="LARGE_MOVES", severity="info",
                   message=f"{len(flagged)} variation(s) quotidienne(s) au-delà de {thr_move:.0%} ou de {cfg.large_move_sigma:g} écarts-types "
                           f"(σ = {sigma:.2%}) dans la fenêtre ; étiquetées dans value.large_moves, non corrigées.")
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
                "SPLIT_NOT_ADJUSTED",
            )

    if level == "insuffisant":
        notices.append(Notice(code="DATA_INSUFFICIENT", message="Données insuffisantes : les métriques ne seront pas calculées."))

    value = {
        "ticker": ticker,
        "quality": level,  # ok | avertissement | insuffisant
        "n_obs": n_obs,
        "expected_obs": expected,
        "longest_missing_run": longest_missing_run,
        "coverage_ratio": round(coverage, 6),
        "first_date": w.index[0].date().isoformat() if n_obs else None,
        "last_date": w.index[-1].date().isoformat() if n_obs else None,
        "checks": checks,
        "n_large_moves": len(flagged),
        "large_moves": large_moves,
        "corporate_actions": {"dividends": int((acts["action"] == "dividend").sum()), "splits": split_list},
    }
    params = resolved_params(asset, window, as_of)
    return build_evidence(
        store, "data_quality_check", params, as_of, window=window, value=value, n_obs=n_obs,
        inputs=[store.input_ref("prices_daily.csv", ticker, as_of), store.input_ref("corporate_actions.csv", ticker, as_of)],
        sources=[store.source_label("prices_daily.csv"), store.source_label("corporate_actions.csv")],
        notices=notices,
        assumptions=[
            "Les jours de cotation attendus sont l'union des dates des actifs du même marché (TSX, États-Unis ou crypto).",
        ],
        limits=["Le contrôle détecte des anomalies visibles dans les séries ; il ne certifie pas l'exactitude des cours (Yahoo Finance n'est pas certifié par les bourses)."],
    )
