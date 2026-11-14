"""asset_lookup : vérifie qu'un actif est dans l'univers et renvoie ses métadonnées."""
from datetime import date
from typing import Optional

from ..datastore import DataStore
from ..models import Evidence, StrictModel, ToolInputError
from ..registry import build_evidence, refused, tool
from ..windows import resolve_as_of


class AssetLookupParams(StrictModel):
    query: str
    as_of: Optional[date] = None


@tool(
    "asset_lookup",
    "1.0.0",
    "Vérifie qu'un actif (ticker ou asset_id) est dans l'univers de données et renvoie ses métadonnées et sa couverture de dates.",
    AssetLookupParams,
)
def asset_lookup(store: DataStore, p: AssetLookupParams) -> Evidence:
    as_of = resolve_as_of(store, p.as_of)
    asset = store.asset(p.query)
    if asset is None:
        sugg = store.suggest(p.query)
        err = ToolInputError(
            "asset_not_found",
            f"Actif {p.query!r} hors de l'univers de données ({len(store.assets)} actifs). Aucune donnée disponible."
            + (f" Suggestions: {sugg}." if sugg else ""),
        )
        return refused(
            store, "asset_lookup", {"query": p.query, "as_of": as_of.isoformat()}, as_of, err,
            value={"in_universe": False, "suggestions": sugg},
        )
    df = store.price_frame(asset["ticker"], as_of)
    value = {
        "in_universe": True,
        **{k: asset[k] for k in ("asset_id", "ticker", "name", "asset_class", "market", "currency", "sector")},
        "n_obs_available": int(len(df)),
        "first_date": df.index.min().date().isoformat() if len(df) else None,
        "last_date": df.index.max().date().isoformat() if len(df) else None,
    }
    return build_evidence(
        store, "asset_lookup", {"query": asset["ticker"], "as_of": as_of.isoformat()}, as_of,
        value=value,
        sources=[store.source_label("assets_universe.csv"), store.source_label("prices_daily.csv")],
        limits=["Les secteurs sont attribués par les organisateurs et simplifiés ; pour un FNB, ils décrivent l'exposition."],
    )
