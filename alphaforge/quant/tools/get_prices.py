"""get_prices : prix d'un actif sur une fenêtre explicite, dans sa devise de cotation."""
from datetime import date
from typing import Literal, Optional

from ..datastore import DataStore
from ..models import Evidence, Notice, StrictModel, ToolInputError, WindowSpec
from ..registry import build_evidence, refused, tool
from ..series import load_prices
from ._common import fallback_params, prelude, resolved_params


class GetPricesParams(StrictModel):
    ticker: str
    window: WindowSpec
    field: Literal["adj_close", "close"] = "adj_close"
    as_of: Optional[date] = None
    include_series: bool = True


@tool(
    "get_prices",
    "1.0.0",
    "Prix quotidiens d'un actif (adj_close par défaut) sur une fenêtre explicite, dans sa devise de cotation, sans remplissage.",
    GetPricesParams,
)
def get_prices(store: DataStore, p: GetPricesParams) -> Evidence:
    try:
        asset, as_of, window, notices = prelude(store, p.ticker, p.window, p.as_of)
    except ToolInputError as e:
        params, as_of = fallback_params(p.ticker, p.window, p.as_of, store, field=p.field, include_series=p.include_series)
        return refused(store, "get_prices", params, as_of, e)

    params = resolved_params(asset, window, as_of, field=p.field, include_series=p.include_series)
    ps = load_prices(store, asset, window, as_of, p.field)
    if len(ps.prices) == 0:
        return refused(
            store, "get_prices", params, as_of,
            ToolInputError("no_data_in_window", f"Aucun prix pour {asset['ticker']} entre {window.start} et {window.end}."),
            window=window, notices=notices,
        )
    notices = list(notices)
    if p.field == "close":
        notices.append(
            Notice(code="CLOSE_NOT_DIVIDEND_ADJUSTED", severity="info",
                   message="`close` est ajusté des fractionnements seulement ; utiliser `adj_close` pour les rendements.")
        )
    s = ps.prices
    value = {
        "ticker": asset["ticker"],
        "currency": ps.currency,
        "field": p.field,
        "n_obs": int(len(s)),
        "first_date": s.index[0].date().isoformat(),
        "last_date": s.index[-1].date().isoformat(),
        "first_price": float(s.iloc[0]),
        "last_price": float(s.iloc[-1]),
    }
    if p.include_series:
        value["series"] = [[d.date().isoformat(), float(v)] for d, v in s.items()]
    return build_evidence(
        store, "get_prices", params, as_of, window=window, value=value,
        sources=[store.source_label("prices_daily.csv")], notices=notices,
        assumptions=["Prix dans la devise de cotation de l'actif ; aucun remplissage ni alignement de calendrier."],
    )
