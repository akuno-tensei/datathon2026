"""Résolution des fenêtres nommées ou libres en dates concrètes."""
from datetime import date, timedelta
from typing import List, Tuple

from .datastore import DataStore
from .models import Notice, ResolvedWindow, ToolInputError, WindowSpec


def resolve_as_of(store: DataStore, as_of) -> date:
    return as_of or store.last_date


def resolve_window(store: DataStore, spec: WindowSpec, as_of: date) -> Tuple[ResolvedWindow, List[Notice]]:
    notices: List[Notice] = []
    alias = None
    if spec.alias:
        alias = spec.alias
        a = store.config.window_aliases.get(alias)
        if a is None:
            raise ToolInputError(
                "unknown_window_alias", f"Alias de fenêtre inconnu: {alias!r}. Disponibles: {sorted(store.config.window_aliases)}"
            )
        if a.full:
            start, end = store.first_date, as_of
        elif a.lookback_days:
            start, end = as_of - timedelta(days=a.lookback_days), as_of
        else:
            start, end = a.start, a.end or as_of
    else:
        start, end = spec.start, spec.end

    if start > as_of:
        raise ToolInputError("window_after_as_of", f"La fenêtre commence ({start}) après as_of ({as_of}).")
    if end > as_of:
        notices.append(
            Notice(
                code="WINDOW_CLIPPED_TO_AS_OF",
                message=f"Fin de fenêtre {end} postérieure à as_of {as_of} : tronquée à as_of (aucune donnée future utilisée).",
            )
        )
        end = as_of
    return ResolvedWindow(alias=alias, start=start, end=end), notices
