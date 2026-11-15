"""Outil `label_content` : étiquette chaque énoncé « fait », « hypothèse » ou « scénario fictif » selon son origine."""
from typing import List

from .models import Label, Statement

_BY_BASIS = {"evidence": "fait", "assumption": "hypothese", "scenario": "scenario_fictif", "synthesis": "synthese", "conclusion": "conclusion"}


def label_content(statement: Statement) -> Statement:
    label: Label = _BY_BASIS[statement.basis]  # type: ignore[assignment]
    return statement.model_copy(update={"label": label})


def label_all(statements: List[Statement]) -> List[Statement]:
    return [label_content(s) for s in statements]
