"""Éléments communs aux outils."""
from datetime import date
from typing import List, Optional, Tuple

from ..datastore import DataStore
from ..models import Notice, ResolvedWindow, ToolInputError, WindowSpec
from ..windows import resolve_as_of, resolve_window

LIMIT_PAST = "Les données historiques décrivent le passé ; elles ne prédisent pas les rendements futurs."
LIMIT_SURVIVOR = (
    "L'univers a été choisi en 2026 parmi des titres aujourd'hui importants (biais du survivant) : "
    "les chiffres historiques sont plus favorables que ce qu'une sélection faite en 2021 aurait produit."
)


def prelude(
    store: DataStore, query: str, window_spec: WindowSpec, as_of: Optional[date]
) -> Tuple[dict, date, ResolvedWindow, List[Notice]]:
    """Résout as_of, l'actif et la fenêtre. Lève ToolInputError si impossible."""
    as_of_r = resolve_as_of(store, as_of)
    asset = store.asset(query)
    if asset is None:
        sugg = store.suggest(query)
        hint = f" Suggestions: {sugg}." if sugg else ""
        raise ToolInputError(
            "asset_not_found",
            f"Actif {query!r} hors de l'univers de données (31 actifs). Aucune donnée disponible.{hint}",
        )
    window, notices = resolve_window(store, window_spec, as_of_r)
    return asset, as_of_r, window, notices


def fallback_params(query, window_spec: WindowSpec, as_of: Optional[date], store: DataStore, **extra) -> Tuple[dict, date]:
    """Paramètres bruts pour une preuve refusée avant résolution complète."""
    as_of_r = resolve_as_of(store, as_of)
    params = {"ticker": str(query), "window": window_spec.model_dump(mode="json"), "as_of": as_of_r.isoformat()}
    params.update(extra)
    return params, as_of_r


def resolved_params(asset: dict, window: ResolvedWindow, as_of: date, **extra) -> dict:
    p = {"ticker": asset["ticker"], "window": window.model_dump(mode="json"), "as_of": as_of.isoformat()}
    p.update(extra)
    return p
