"""asset_lookup : porte d'entrée. Confirme qu'un actif existe dans l'univers et décrit ce qu'on en sait."""
from datetime import date
from typing import Optional

from ..datastore import DataStore
from ..models import Evidence, ToolInputError
from ..models import StrictModel
from ..registry import build_evidence, refused, tool
from ..windows import resolve_as_of


class AssetLookupParams(StrictModel):
    query: str  # symbole ou nom libre : « SHOP.TO », « Shopify », « bitcoin »
    as_of: Optional[date] = None


def _meta(asset: dict) -> dict:
    return {k: asset[k] for k in ("asset_id", "ticker", "name", "asset_class", "market", "currency", "sector")}


@tool(
    "asset_lookup",
    "1.1.0",
    "Confirme qu'un actif (symbole ou nom libre) existe dans l'univers de données et renvoie ses métadonnées et sa "
    "couverture (première date, dernière date, nombre de lignes). Hors univers : NOT_IN_UNIVERSE avec les plus proches. "
    "Plusieurs candidats : AMBIGUOUS_QUERY avec la liste, à trancher par l'appelant.",
    AssetLookupParams,
)
def asset_lookup(store: DataStore, p: AssetLookupParams) -> Evidence:
    as_of = resolve_as_of(store, p.as_of)
    kind, hits = store.resolve(p.query)
    raw_params = {"query": p.query, "as_of": as_of.isoformat()}

    if len(hits) > 1:
        err = ToolInputError(
            "ambiguous_query",
            f"{p.query!r} correspond à plusieurs actifs ({', '.join(h['ticker'] for h in hits)}) : préciser le symbole.",
        )
        return refused(
            store, "asset_lookup", raw_params, as_of, err,
            value={"in_universe": True, "ambiguous": True, "candidates": [_meta(h) for h in hits]},
        )
    if not hits:
        sugg = store.suggest(p.query)
        err = ToolInputError(
            "asset_not_found",
            f"Actif {p.query!r} hors de l'univers de données ({len(store.assets)} actifs). Aucune donnée disponible."
            + (f" Les plus proches : {sugg}." if sugg else ""),
        )
        return refused(store, "asset_lookup", raw_params, as_of, err, value={"in_universe": False, "suggestions": sugg})

    asset = hits[0]
    df = store.price_frame(asset["ticker"], as_of)
    n = int(len(df))
    value = {
        "in_universe": True,
        **_meta(asset),
        "match_type": kind,
        "n_rows": n,
        "n_obs_available": n,  # nom historique, lu par d'autres agents
        "first_date": df.index.min().date().isoformat() if n else None,
        "last_date": df.index.max().date().isoformat() if n else None,
    }
    return build_evidence(
        store, "asset_lookup", {"query": asset["ticker"], "as_of": as_of.isoformat()}, as_of,
        value=value, n_obs=n,
        inputs=[store.input_ref("assets_universe.csv", asset["ticker"], as_of), store.input_ref("prices_daily.csv", asset["ticker"], as_of)],
        sources=[store.source_label("assets_universe.csv"), store.source_label("prices_daily.csv")],
        limits=["Les secteurs sont attribués par les organisateurs et simplifiés ; pour un FNB, ils décrivent l'exposition."],
    )
