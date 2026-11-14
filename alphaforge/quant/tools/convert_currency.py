"""convert_currency : exprime une série dans la devise de l'investisseur, à la date de chaque observation."""
from datetime import date
from typing import Optional

from pydantic import model_validator

from ..datastore import DataStore
from ..models import Evidence, Notice, ResolvedWindow, StrictModel, ToolInputError, WindowSpec
from ..registry import build_evidence, refused, tool
from ..series import convert_series, load_prices
from ..windows import resolve_as_of
from ._common import fallback_params, prelude, resolved_params

FX_LIMIT = (
    "Taux USD/CAD de la Banque du Canada : un taux par jour, publié vers 16 h 30 ET d'après sources_metadata.csv. "
    "L'écart avec l'heure de clôture des marchés, et avec les jours civils UTC de la crypto, n'est pas corrigé."
)


class ConvertCurrencyParams(StrictModel):
    """Soit `series_id` (série produite par get_prices), soit `ticker` + `window` (la série native est alors créée)."""

    series_id: Optional[str] = None
    ticker: Optional[str] = None
    window: Optional[WindowSpec] = None
    source_currency: Optional[str] = None  # facultatif : vérifié contre la devise réelle de la série
    target_currency: str
    as_of: Optional[date] = None

    @model_validator(mode="after")
    def _check(self) -> "ConvertCurrencyParams":
        if self.series_id and (self.ticker or self.window):
            raise ValueError("Fournir soit `series_id`, soit `ticker` et `window`, pas les deux.")
        if not self.series_id and not (self.ticker and self.window):
            raise ValueError("Fournir `series_id`, ou `ticker` et `window`.")
        return self


@tool(
    "convert_currency",
    "1.1.0",
    "Convertit une série de prix (series_id) vers CAD ou USD avec le taux du même jour (report de la dernière valeur "
    "connue, borné, jamais de valeur future) et renvoie un nouveau series_id. USD→CAD multiplie, CAD→USD divise. "
    "Refuse une série déjà dans la devise cible ou déjà convertie.",
    ConvertCurrencyParams,
)
def convert_currency(store: DataStore, p: ConvertCurrencyParams) -> Evidence:
    target = p.target_currency.upper()
    parent_sid: Optional[str] = None
    notices = []
    if p.series_id:
        as_of = resolve_as_of(store, p.as_of)
        base_params = {"series_id": p.series_id, "target_currency": target, "as_of": as_of.isoformat()}
        try:
            stored = store.series.get(p.series_id)
        except ToolInputError as e:
            return refused(store, "convert_currency", base_params, as_of, e)
        native, parent_sid = stored.series, stored.series_id
        asset = store.asset(native.ticker)
        window = ResolvedWindow(start=native.prices.index[0].date(), end=native.prices.index[-1].date())
        if p.as_of is None and stored.recipe.get("as_of"):
            as_of = date.fromisoformat(stored.recipe["as_of"])
            base_params["as_of"] = as_of.isoformat()
        params = {**base_params, "source_currency": native.currency}
    else:
        try:
            asset, as_of, window, notices = prelude(store, p.ticker, p.window, p.as_of)
        except ToolInputError as e:
            params, as_of = fallback_params(p.ticker, p.window, p.as_of, store, target_currency=target)
            return refused(store, "convert_currency", params, as_of, e)
        native = load_prices(store, asset, window, as_of, "adj_close")
        params = resolved_params(asset, window, as_of, source_currency=native.currency, target_currency=target)
        if len(native.prices):
            recipe = {"kind": "native", "ticker": asset["ticker"], "field": "adj_close",
                      "start": native.prices.index[0].date().isoformat(), "end": native.prices.index[-1].date().isoformat(),
                      "as_of": as_of.isoformat()}
            parent_sid = store.series.put(native, recipe, store.input_ref("prices_daily.csv", asset["ticker"], as_of).sha256)

    notices = list(notices)
    inputs = [store.input_ref("prices_daily.csv", native.ticker, as_of), store.input_ref("fx_usdcad_daily.csv", "USDCAD", as_of)]

    def fail(reason: str, msg: str) -> Evidence:
        return refused(store, "convert_currency", params, as_of, ToolInputError(reason, msg),
                       window=window, notices=notices, inputs=inputs, depends_on=[])

    if len(native.prices) == 0:
        return fail("no_data_in_window", f"Aucun prix pour {native.ticker} entre {window.start} et {window.end}.")
    if p.source_currency and p.source_currency.upper() != native.currency:
        return fail("currency_mismatch", f"Devise source déclarée {p.source_currency.upper()} mais la série est en {native.currency}.")
    if native.converted_from is not None:
        return fail("double_conversion", f"Série déjà convertie de {native.converted_from} vers {native.currency} : refus de la reconvertir.")
    if native.currency == target:
        return fail("already_in_target_currency",
                    f"{native.ticker} est déjà en {target} : refus de convertir (un FNB en CAD qui détient des actions américaines "
                    "porte déjà son exposition au change).")
    try:
        conv = convert_series(store, native, target, as_of)
    except ToolInputError as e:
        return fail(e.reason, e.message)

    cfg = store.config
    if conv.fx_filled_days:
        notices.append(Notice(code="FX_FORWARD_FILLED",
                              message=f"{conv.fx_filled_days} jour(s) sans taux de change du même jour : dernier taux connu reporté (max {cfg.ffill_limit_days} jours civils)."))
    if conv.fx_dropped_days:
        notices.append(Notice(code="FX_DROPPED_DAYS",
                              message=f"{conv.fx_dropped_days} jour(s) exclus : aucun taux de change connu dans les {cfg.ffill_limit_days} jours précédents."))

    c, n, rates = conv.prices, native.prices, conv.fx_rates
    recipe = {"kind": "converted", "parent": parent_sid, "target": target, "as_of": as_of.isoformat()}
    sid = store.series.put(conv, recipe, inputs[1].sha256, parent=parent_sid)
    value = {
        "series_id": sid,
        "parent_series_id": parent_sid,
        "ticker": native.ticker,
        "from_currency": native.currency,
        "to_currency": target,
        "n_obs_in": int(len(n)),
        "n_obs_out": int(len(c)),
        "fx_forward_filled_days": conv.fx_filled_days,
        "fx_dropped_days": conv.fx_dropped_days,
        "fx_rates_used": {
            "pair": "USD/CAD (CAD par USD)",
            "first": float(rates.iloc[0]), "last": float(rates.iloc[-1]),
            "min": float(rates.min()), "max": float(rates.max()),
        },
        "first_date": c.index[0].date().isoformat(),
        "last_date": c.index[-1].date().isoformat(),
        "first_price_native": float(n.iloc[0]),
        "last_price_native": float(n.iloc[-1]),
        "first_price_converted": float(c.iloc[0]),
        "last_price_converted": float(c.iloc[-1]),
    }
    return build_evidence(
        store, "convert_currency", params, as_of, window=window, value=value, n_obs=int(len(c)), inputs=inputs,
        sources=[store.source_label("prices_daily.csv"), store.source_label("fx_usdcad_daily.csv")],
        notices=notices,
        assumptions=[
            "Prix convertis un à un avec le taux du même jour ; rendements calculés ensuite sur les prix convertis (jamais l'inverse).",
            f"Si le taux du jour manque (jour férié canadien, fin de semaine pour la crypto) : dernier taux connu, au plus {cfg.ffill_limit_days} jours civils, jamais le taux suivant.",
            "USD→CAD : prix × taux ; CAD→USD : prix ÷ taux.",
        ],
        limits=[FX_LIMIT],
        depends_on=[],
    )
