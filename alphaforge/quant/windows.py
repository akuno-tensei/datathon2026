"""Résolution des fenêtres nommées ou libres en dates concrètes."""
from datetime import date, timedelta
from typing import List, Tuple

from .datastore import DataStore
from .models import Notice, Period, ResolvedWindow, ToolInputError, WindowSpec


def resolve_as_of(store: DataStore, as_of) -> date:
    return as_of or store.last_date


def resolve_window(store: DataStore, spec: WindowSpec, as_of: date) -> Tuple[ResolvedWindow, List[Notice]]:
    notices: List[Notice] = []
    alias = None
    exclude = []
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
        exclude = list(a.exclude)
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
    # Périodes exclues, ramenées à l'intérieur de la fenêtre ; celles qui ne la recoupent pas disparaissent.
    excluded = [Period(start=max(x.start, start), end=min(x.end, end)) for x in exclude if x.start <= end and x.end >= start]
    if excluded and excluded[0].start <= start and excluded[-1].end >= end and len(excluded) == 1:
        raise ToolInputError("window_fully_excluded", f"La fenêtre {start} à {end} est entièrement exclue : aucune période à mesurer.")
    return ResolvedWindow(alias=alias, start=start, end=end, excluded=excluded), notices
