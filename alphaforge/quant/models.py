"""Modèles typés partagés : fenêtres, avis, entrée du registre de preuves."""
from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, computed_field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WindowSpec(StrictModel):
    """Fenêtre demandée : un alias de configuration OU des dates explicites (start et end)."""

    alias: Optional[str] = None
    start: Optional[date] = None
    end: Optional[date] = None

    @model_validator(mode="after")
    def _check(self) -> "WindowSpec":
        explicit = self.start is not None or self.end is not None
        if self.alias and explicit:
            raise ValueError("Fournir soit `alias`, soit `start` et `end`, pas les deux.")
        if not self.alias:
            if self.start is None or self.end is None:
                raise ValueError("Une fenêtre explicite exige `start` et `end`.")
            if self.start > self.end:
                raise ValueError("`start` doit précéder `end`.")
        return self


class ResolvedWindow(StrictModel):
    """Fenêtre telle qu'appliquée. L'alias est conservé pour la traçabilité."""

    alias: Optional[str] = None
    start: date
    end: date


class Notice(StrictModel):
    code: str
    message: str
    severity: Literal["info", "warning"] = "warning"


class InputRef(StrictModel):
    """Donnée lue par l'outil : fichier, symbole, empreinte SHA-256 des lignes lues (jusqu'à as_of)."""

    file: str
    symbol: str
    sha256: str


class Evidence(StrictModel):
    """Entrée du registre de preuves : {valeur, source, fenêtre, paramètres} + traçabilité."""

    evidence_id: str
    tool: str
    tool_version: str
    data_fingerprint: str
    as_of: date
    params: Dict[str, Any]
    window: Optional[ResolvedWindow] = None
    status: Literal["ok", "warning", "refused"]
    refusal_reason: Optional[str] = None  # historique, en minuscules : lu par la gouvernance
    error_code: Optional[str] = None  # erreur typée du contrat : NOT_IN_UNIVERSE, INSUFFICIENT_DATA, ...
    n_obs: Optional[int] = None
    inputs: List[InputRef] = []
    value: Optional[Dict[str, Any]] = None
    sources: List[str] = []
    notices: List[Notice] = []
    assumptions: List[str] = []
    limits: List[str] = []
    depends_on: List[str] = []
    label: Literal["fait"] = "fait"

    @computed_field  # type: ignore[misc]
    @property
    def warnings(self) -> List[str]:
        """Messages des avis de gravité `warning` (forme du contrat). Le détail codé reste dans `notices`."""
        return [n.message for n in self.notices if n.severity == "warning"]


class ToolInputError(Exception):
    """Entrée invalide ou impossible à satisfaire. Convertie en preuve `refused` par l'enveloppe des outils."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message
