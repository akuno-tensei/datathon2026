"""convert_currency : convertit la série de prix d'un actif CAD/USD, jour par jour."""
from datetime import date
from typing import Optional

from ..datastore import DataStore
from ..models import Evidence, Notice, StrictModel, ToolInputError, WindowSpec
from ..registry import build_evidence, refused, tool
from ..series import convert_series, load_prices
from ._common import fallback_params, prelude, resolved_params

FX_LIMIT = (
    "Taux USD/CAD de la Banque du Canada : un taux par jour, publié vers 16 h 30 ET d'après sources_metadata.csv. "
    "L'écart avec l'heure de clôture des marchés, et avec les jours civils UTC de la crypto, n'est pas corrigé."
)


class ConvertCurrencyParams(StrictModel):
    ticker: str
    window: WindowSpec
    target_currency: str = "CAD"
    as_of: Optional[date] = None
    include_series: bool = False


@tool(
    "convert_currency",
    "1.0.0",
    "Convertit les prix d'un actif de sa devise de cotation vers CAD ou USD, avec le taux du même jour "
    "(report de la dernière valeur connue, borné, jamais de valeur future). Refuse une double conversion.",
    ConvertCurrencyParams,
)
def convert_currency(store: DataStore, p: ConvertCurrencyParams) -> Evidence:
    target = p.target_currency.upper()
    try:
        asset, as_of, window, notices = prelude(store, p.ticker, p.window, p.as_of)
    except ToolInputError as e:
        params, as_of = fallback_params(p.ticker, p.window, p.as_of, store, target_currency=target, include_series=p.include_series)
        return refused(store, "convert_currency", params, as_of, e)

    params = resolved_params(asset, window, as_of, target_currency=target, include_series=p.include_series)
    notices = list(notices)
    native = load_prices(store, asset, window, as_of, "adj_close")
    if len(native.prices) == 0:
        return refused(
            store, "convert_currency", params, as_of,
            ToolInputError("no_data_in_window", f"Aucun prix pour {asset['ticker']} entre {window.start} et {window.end}."),
            window=window, notices=notices,
        )
    try:
        conv = convert_series(store, native, target, as_of)
    except ToolInputError as e:
        return refused(store, "convert_currency", params, as_of, e, window=window, notices=notices)

    converted = conv.converted_from is not None
    if not converted:
        notices.append(
            Notice(code="ALREADY_IN_TARGET_CURRENCY", severity="info",
                   message=f"{asset['ticker']} est déjà coté en {target} : aucune conversion appliquée.")
        )
    if conv.fx_filled_days:
        notices.append(
            Notice(code="FX_FORWARD_FILLED",
                   message=f"{conv.fx_filled_days} jour(s) sans taux de change du même jour : dernier taux connu reporté (max {store.config.ffill_limit_days} jours civils).")
        )
    if conv.fx_dropped_days:
        notices.append(
            Notice(code="FX_DROPPED_DAYS",
                   message=f"{conv.fx_dropped_days} jour(s) exclus : aucun taux de change connu dans les {store.config.ffill_limit_days} jours précédents.")
        )

    n, c = native.prices, conv.prices
    value = {
        "ticker": asset["ticker"],
        "from_currency": native.currency,
        "to_currency": target,
        "converted": converted,
        "n_obs_in": int(len(n)),
        "n_obs_out": int(len(c)),
        "fx_forward_filled_days": conv.fx_filled_days,
        "fx_dropped_days": conv.fx_dropped_days,
        "first_date": c.index[0].date().isoformat() if len(c) else None,
        "last_date": c.index[-1].date().isoformat() if len(c) else None,
        "first_price_native": float(n.iloc[0]),
        "last_price_native": float(n.iloc[-1]),
        "first_price_converted": float(c.iloc[0]) if len(c) else None,
        "last_price_converted": float(c.iloc[-1]) if len(c) else None,
    }
    if p.include_series:
        value["series"] = [
            [d.date().isoformat(), float(n.loc[d]), float(v)] for d, v in c.items()
        ]
    return build_evidence(
        store, "convert_currency", params, as_of, window=window, value=value,
        sources=[store.source_label("prices_daily.csv"), store.source_label("fx_usdcad_daily.csv")],
        notices=notices,
        assumptions=[
            "Prix convertis un à un avec le taux du même jour ; rendements calculés ensuite sur les prix convertis.",
            f"Si le taux du jour manque (jour férié canadien, fin de semaine pour la crypto) : dernier taux connu, au plus {store.config.ffill_limit_days} jours civils, jamais le taux suivant.",
        ],
        limits=[FX_LIMIT],
    )
