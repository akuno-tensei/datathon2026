"""compute_metrics : rendement annualisé, volatilité, drawdown maximal, avec effet de change rapporté."""
from datetime import date
from typing import Optional

from ..datastore import DataStore
from ..metrics import excluded_mask, metric_block, metric_block_segmented, sharpe_ratio, simple_returns
from ..models import Evidence, Notice, StrictModel, ToolInputError, WindowSpec
from ..registry import build_evidence, refused, tool
from ..series import annualization_basis, calendar_kind, convert_series, load_prices, periods_per_year
from ._common import LIMIT_PAST, LIMIT_SURVIVOR, fallback_params, prelude, resolved_params
from .convert_currency import FX_LIMIT
from .data_quality import DataQualityParams, data_quality_check


class ComputeMetricsParams(StrictModel):
    ticker: str
    window: WindowSpec
    base_currency: Optional[str] = None
    as_of: Optional[date] = None
    include_sharpe: bool = False  # optionnel : ratio de Sharpe avec taux directeur de macro_rates.csv


@tool(
    "compute_metrics",
    "1.1.0",
    "Rendement total et annualisé, volatilité annualisée et drawdown maximal d'un actif sur une fenêtre, en devise native "
    "et en devise de base (CAD par défaut), avec l'effet de change. Exécute d'abord data_quality_check ; "
    "refuse de calculer si les données sont insuffisantes.",
    ComputeMetricsParams,
)
def compute_metrics(store: DataStore, p: ComputeMetricsParams) -> Evidence:
    cfg = store.config
    base = (p.base_currency or cfg.default_base_currency).upper()
    try:
        asset, as_of, window, notices = prelude(store, p.ticker, p.window, p.as_of)
    except ToolInputError as e:
        params, as_of = fallback_params(p.ticker, p.window, p.as_of, store, base_currency=base)
        return refused(store, "compute_metrics", params, as_of, e)

    ppy = periods_per_year(asset, cfg)
    # Tout ce qui conditionne le résultat est consigné explicitement dans params, y compris les valeurs issues de la config.
    params = resolved_params(
        asset, window, as_of, base_currency=base, price_field="adj_close", annualization=ppy,
        annualization_min_days=cfg.annualization_min_days, include_sharpe=p.include_sharpe,
    )
    notices = list(notices)
    inputs = [store.input_ref("prices_daily.csv", asset["ticker"], as_of)]

    dq = data_quality_check(store, DataQualityParams(ticker=asset["ticker"], window=p.window, as_of=as_of))
    notices += [n for n in dq.notices if n.code != "WINDOW_CLIPPED_TO_AS_OF"]
    depends = [dq.evidence_id]
    if dq.status == "refused" or dq.value["quality"] == "insuffisant":
        return refused(
            store, "compute_metrics", params, as_of,
            ToolInputError("insufficient_data", "Calcul refusé : le contrôle de qualité des données conclut à des données insuffisantes."),
            window=window, notices=notices, depends_on=depends, inputs=inputs, n_obs=dq.n_obs,
        )

    native = load_prices(store, asset, window, as_of, "adj_close")
    try:
        conv = convert_series(store, native, base, as_of)
    except ToolInputError as e:
        return refused(store, "compute_metrics", params, as_of, e, window=window, notices=notices, depends_on=depends, inputs=inputs)

    converted = conv.converted_from is not None
    if conv.fx_filled_days:
        notices.append(
            Notice(code="FX_FORWARD_FILLED",
                   message=f"{conv.fx_filled_days} jour(s) sans taux du même jour : dernier taux connu reporté (max {cfg.ffill_limit_days} jours civils).")
        )
    if conv.fx_dropped_days:
        notices.append(Notice(code="FX_DROPPED_DAYS", message=f"{conv.fx_dropped_days} jour(s) exclus faute de taux de change."))
    if len(conv.prices) < cfg.min_obs_insufficient:
        return refused(
            store, "compute_metrics", params, as_of,
            ToolInputError("insufficient_data", f"Seulement {len(conv.prices)} prix après conversion en {base}."),
            window=window, notices=notices, depends_on=depends, inputs=inputs, n_obs=int(len(conv.prices)),
        )

    if converted:
        inputs.append(store.input_ref("fx_usdcad_daily.csv", "USDCAD", as_of))
    segmented = bool(window.excluded)
    if segmented:
        n_ret = int((~excluded_mask(simple_returns(native.prices).index, window.excluded)).sum())
        if n_ret < cfg.min_obs_insufficient:
            return refused(
                store, "compute_metrics", params, as_of,
                ToolInputError("insufficient_data", f"Seulement {n_ret} rendements hors des périodes exclues (minimum {cfg.min_obs_insufficient})."),
                window=window, notices=notices, depends_on=depends, inputs=inputs, n_obs=n_ret,
            )
        nat_block = metric_block_segmented(native.prices, window.excluded, ppy, cfg.annualization_min_days)
        base_block = metric_block_segmented(conv.prices, window.excluded, ppy, cfg.annualization_min_days)
        notices.append(Notice(
            code="SEGMENTED_WINDOW", severity="info",
            message="Fenêtre segmentée : rendements datés dans " + ", ".join(f"{x.start} – {x.end}" for x in window.excluded)
                    + " retirés (filtrage des rendements, pas des prix : aucun rendement n'enjambe la période exclue)."))
    else:
        nat_block = metric_block(native.prices, ppy, cfg.annualization_min_days)
        base_block = metric_block(conv.prices, ppy, cfg.annualization_min_days)
        n_ret = len(native.prices) - 1
    if base_block.annualized_return is None:
        notices.append(
            Notice(code="ANNUALIZATION_SKIPPED", severity="info",
                   message=f"Période observée de moins de {cfg.annualization_min_days} jours : pas de rendement annualisé (annualiser du court terme trompe), seulement le cumulatif.")
        )
    sharpe = None
    sharpe_limits = []
    if p.include_sharpe:
        rf = store.risk_free_rate(base, as_of)
        if rf is None:
            notices.append(Notice(code="SHARPE_UNAVAILABLE", message=f"Sharpe demandé mais aucun taux directeur en {base} dans les données (macro_rates.csv)."))
        elif base_block.annualized_return is None:
            notices.append(Notice(code="SHARPE_SKIPPED", message=f"Sharpe non calculé : période observée de moins de {cfg.annualization_min_days} jours."))
        else:
            r_all = simple_returns(conv.prices)
            r_kept = r_all[~excluded_mask(r_all.index, window.excluded)] if segmented else r_all
            sharpe = {"optional": True, **sharpe_ratio(r_kept, rf, ppy)}
            inputs.append(store.input_ref("macro_rates.csv", rf.name, as_of))
            sharpe_limits = [
                "Sharpe (optionnel) : excédent de rendement sur le taux directeur au jour le jour (V39079 pour CAD, DFF pour USD), "
                "divisé par la volatilité, annualisé sur la base N. Un taux à 3 mois ou un bon du Trésor serait un autre choix défendable.",
                "Le Sharpe suppose que la volatilité résume le risque et dépend fortement de la fenêtre ; à lire avec le drawdown, pas seul.",
            ]
    nat_id = store.series.put(native, {"kind": "native", "ticker": asset["ticker"], "field": "adj_close",
                                        "start": native.prices.index[0].date().isoformat(),
                                        "end": native.prices.index[-1].date().isoformat(), "as_of": as_of.isoformat()}, inputs[0].sha256)
    base_id = nat_id if not converted else store.series.put(conv, {"kind": "converted", "parent": nat_id, "target": base, "as_of": as_of.isoformat()}, inputs[1].sha256, parent=nat_id)
    if not base_block.max_drawdown.recovered:
        notices.append(Notice(code="DRAWDOWN_NOT_RECOVERED", severity="info",
                              message="Le drawdown maximal n'était pas récupéré à la fin de la fenêtre."))

    value = {
        "ticker": asset["ticker"],
        "asset_class": asset["asset_class"],
        "native_currency": native.currency,
        "base_currency": base,
        "currency_converted": converted,
        "calendar": calendar_kind(asset),
        "periods_per_year": ppy,
        "annualization_basis": annualization_basis(ppy, cfg),
        "n_prices": int(len(native.prices)),
        "n_returns": int(n_ret),
        "segmented": segmented,
        "first_date": native.prices.index[0].date().isoformat(),
        "last_date": native.prices.index[-1].date().isoformat(),
        "sharpe": sharpe,
        "series_id_native": nat_id,
        "series_id_base": base_id,
        "native": nat_block.model_dump(mode="json"),
        "base": base_block.model_dump(mode="json"),
        "fx_effect": {
            "total_return": base_block.total_return - nat_block.total_return,
            "annualized_return": (
                None if base_block.annualized_return is None or nat_block.annualized_return is None
                else base_block.annualized_return - nat_block.annualized_return
            ),
        },
        "data_quality": {"quality": dq.value["quality"], "evidence_id": dq.evidence_id},
    }
    assumptions = [
        "Rendements simples quotidiens calculés sur adj_close (dividendes réinvestis), de la première à la dernière observation de la fenêtre.",
        f"Volatilité annualisée sur {annualization_basis(ppy, cfg)} : écart-type des rendements quotidiens × √{ppy}.",
        f"Rendement annualisé = (1 + cumulatif)^(1/années) − 1, années = jours civils observés ÷ 365,25, seulement si la période couvre au moins {cfg.annualization_min_days} jours.",
        "Pire mois et rendements par année civile : la première période part du premier prix de la fenêtre (partial = vrai).",
        "Calendrier propre à l'actif, sans remplissage. Un actif crypto (365) et un actif coté (252) n'ont pas la même base : ne pas comparer leurs volatilités sans le dire.",
        "Effet de change = rendement en devise de base moins rendement en devise native (points de rendement).",
        "Drawdown mesuré à l'intérieur de la fenêtre seulement (négatif ; -0,5 = perte de 50 % depuis le sommet).",
    ]
    limits = [LIMIT_PAST, LIMIT_SURVIVOR] + sharpe_limits
    if converted:
        limits.append(FX_LIMIT)
    return build_evidence(
        store, "compute_metrics", params, as_of, window=window, value=value, n_obs=int(len(native.prices)), inputs=inputs,
        sources=[store.source_label("prices_daily.csv")] + ([store.source_label("fx_usdcad_daily.csv")] if converted else []),
        notices=notices, assumptions=assumptions, limits=limits, depends_on=depends,
    )
