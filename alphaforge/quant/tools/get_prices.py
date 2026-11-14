"""get_prices : extrait une série de prix propre sur une fenêtre explicite et la dépose dans le magasin de séries."""
from datetime import date
from typing import Literal, Optional

import pandas as pd

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


@tool(
    "get_prices",
    "1.1.0",
    "Extrait les prix quotidiens d'un actif sur une fenêtre explicite, dans sa devise de cotation, sans remplissage ni "
    "conversion. Renvoie un series_id et un résumé, jamais la série elle-même. Tronque à la couverture disponible et le signale. "
    "adj_close pour les rendements ; close seulement pour afficher un niveau de prix.",
    GetPricesParams,
)
def get_prices(store: DataStore, p: GetPricesParams) -> Evidence:
    try:
        asset, as_of, window, notices = prelude(store, p.ticker, p.window, p.as_of)
    except ToolInputError as e:
        params, as_of = fallback_params(p.ticker, p.window, p.as_of, store, field=p.field)
        return refused(store, "get_prices", params, as_of, e)

    params = resolved_params(asset, window, as_of, field=p.field)
    inputs = [store.input_ref("prices_daily.csv", asset["ticker"], as_of)]
    ps = load_prices(store, asset, window, as_of, p.field)
    if len(ps.prices) == 0:
        return refused(
            store, "get_prices", params, as_of,
            ToolInputError("no_data_in_window", f"Aucun prix pour {asset['ticker']} entre {window.start} et {window.end}."),
            window=window, notices=notices, inputs=inputs, n_obs=0,
        )
    notices = list(notices)
    s = ps.prices
    cov = store.price_frame(asset["ticker"], as_of).index
    cov_first, cov_last = cov.min(), cov.max()
    truncated = pd.Timestamp(window.start) < cov_first or pd.Timestamp(window.end) > cov_last
    if truncated:
        notices.append(
            Notice(
                code="WINDOW_TRUNCATED_TO_COVERAGE",
                message=(
                    f"Fenêtre demandée {window.start} à {window.end} ; couverture disponible pour {asset['ticker']} : "
                    f"{cov_first.date()} à {cov_last.date()}. Série tronquée à {s.index[0].date()} – {s.index[-1].date()}."
                ),
            )
        )
    if p.field == "close":
        notices.append(
            Notice(code="CLOSE_NOT_DIVIDEND_ADJUSTED", severity="info",
                   message="`close` est ajusté des fractionnements seulement ; utiliser `adj_close` pour les rendements.")
        )
    recipe = {"kind": "native", "ticker": asset["ticker"], "field": p.field, "start": s.index[0].date().isoformat(),
              "end": s.index[-1].date().isoformat(), "as_of": as_of.isoformat()}
    sid = store.series.put(ps, recipe, inputs[0].sha256)
    value = {
        "series_id": sid,
        "ticker": asset["ticker"],
        "currency": ps.currency,
        "field": p.field,
        "n_obs": int(len(s)),
        "first_date": s.index[0].date().isoformat(),
        "last_date": s.index[-1].date().isoformat(),
        "first_price": float(s.iloc[0]),
        "last_price": float(s.iloc[-1]),
        "truncated": bool(truncated),
    }
    return build_evidence(
        store, "get_prices", params, as_of, window=window, value=value, n_obs=int(len(s)), inputs=inputs,
        sources=[store.source_label("prices_daily.csv")], notices=notices,
        assumptions=["Prix dans la devise de cotation de l'actif ; aucun remplissage ni alignement de calendrier."],
    )
