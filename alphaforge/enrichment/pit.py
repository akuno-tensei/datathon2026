"""point_in_time_filter : ne garde que ce qui était publié à la date d'analyse.

Même convention que l'analyste (`date <= as_of`). Une donnée postérieure n'existe pas pour la simulation : elle est
écartée et comptée, jamais écrite au registre.
"""
from datetime import date
from typing import List

from .models import FilterResult, RawItem


def point_in_time_filter(items: List[RawItem], as_of: date) -> FilterResult:
    kept = [i for i in items if i.date_publication <= as_of and i.date_observation <= as_of]
    excluded = [i for i in items if not (i.date_publication <= as_of and i.date_observation <= as_of)]
    return FilterResult(as_of=as_of, kept=kept, excluded=excluded)
