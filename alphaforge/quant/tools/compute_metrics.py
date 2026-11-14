"""compute_metrics : rendement annualisé, volatilité, drawdown maximal, avec effet de change rapporté."""
from datetime import date
from typing import Optional

from ..datastore import DataStore
from ..metrics import metric_block
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


@tool(
    "compute_metrics",
    "1.0.0",
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

    params = resolved_params(asset, window, as_of, base_currency=base)
    notices = list(notices)

    dq = data_quality_check(store, DataQualityParams(ticker=asset["ticker"], window=p.window, as_of=as_of))
    notices += [n for n in dq.notices if n.code != "WINDOW_CLIPPED_TO_AS_OF"]
    depends = [dq.evidence_id]
    if dq.status == "refused" or dq.value["quality"] == "insufficient":
        return refused(
            store, "compute_metrics", params, as_of,
            ToolInputError("insufficient_data", "Calcul refusé : le contrôle de qualité des données conclut à des données insuffisantes."),
            window=window, notices=notices, depends_on=depends,
        )

    native = load_prices(store, asset, window, as_of, "adj_close")
    try:
        conv = convert_series(store, native, base, as_of)
    except ToolInputError as e:
        return refused(store, "compute_metrics", params, as_of, e, window=window, notices=notices, depends_on=depends)

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
            window=window, notices=notices, depends_on=depends,
        )

    ppy = periods_per_year(asset, cfg)
    nat_block = metric_block(native.prices, ppy)
    base_block = metric_block(conv.prices, ppy)
    n_ret = len(native.prices) - 1
    if n_ret < ppy * 0.5:
        notices.append(
            Notice(code="SHORT_WINDOW",
                   message=f"Fenêtre de {n_ret} rendements (< demi-année de la base {ppy}) : le rendement annualisé est une extrapolation.")
        )
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
        "first_date": native.prices.index[0].date().isoformat(),
        "last_date": native.prices.index[-1].date().isoformat(),
        "native": nat_block.model_dump(mode="json"),
        "base": base_block.model_dump(mode="json"),
        "fx_effect": {
            "total_return": base_block.total_return - nat_block.total_return,
            "annualized_return": base_block.annualized_return - nat_block.annualized_return,
        },
        "data_quality": {"quality": dq.value["quality"], "evidence_id": dq.evidence_id},
    }
    assumptions = [
        "Rendements simples quotidiens calculés sur adj_close (dividendes réinvestis), de la première à la dernière observation de la fenêtre.",
        f"Annualisation sur {annualization_basis(ppy, cfg)} ; volatilité = écart-type des rendements × √{ppy}.",
        "Calendrier propre à l'actif, sans remplissage. Un actif crypto (365) et un actif coté (252) n'ont pas la même base : ne pas comparer leurs volatilités sans le dire.",
        "Effet de change = rendement en devise de base moins rendement en devise native (points de rendement).",
        "Drawdown mesuré à l'intérieur de la fenêtre seulement (négatif ; -0,5 = perte de 50 % depuis le sommet).",
    ]
    limits = [LIMIT_PAST, LIMIT_SURVIVOR]
    if converted:
        limits.append(FX_LIMIT)
    return build_evidence(
        store, "compute_metrics", params, as_of, window=window, value=value,
        sources=[store.source_label("prices_daily.csv")] + ([store.source_label("fx_usdcad_daily.csv")] if converted else []),
        notices=notices, assumptions=assumptions, limits=limits, depends_on=depends,
    )
