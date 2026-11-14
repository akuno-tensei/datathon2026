"""Profil d'investisseur : une donnée validée, jamais de logique propre à un persona."""
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


RiskTolerance = Literal["faible", "faible_modere", "modere", "modere_eleve", "eleve"]
Objective = Literal["preservation_capital", "revenu", "croissance_moderee", "croissance_elevee"]
CriterionKind = Literal["asset_class", "sector", "ticker", "theme"]

RISK_RANK = {"faible": 0, "faible_modere": 1, "modere": 2, "modere_eleve": 3, "eleve": 4}


class Criterion(StrictModel):
    """Critère de sélection. `kind` et `value` sont comparés par la gouvernance, pas ici."""

    kind: CriterionKind
    value: str
    reason: Optional[str] = None


class Preference(Criterion):
    """Préférence souple : oriente sans bloquer. `weight` de 0 (faible) à 1 (forte)."""

    weight: float = Field(default=0.5, ge=0.0, le=1.0)


class InvestorProfile(StrictModel):
    profile_id: str
    display_name: Optional[str] = None
    language: Literal["fr", "en"] = "fr"
    experience: Literal["debutant", "intermediaire", "avance"] = "debutant"
    horizon_years_min: int = Field(ge=0)
    horizon_years_max: int = Field(ge=0)
    risk_tolerance: RiskTolerance
    objectives: List[Objective] = []
    base_currency: Literal["CAD", "USD"] = "CAD"
    hard_exclusions: List[Criterion] = []
    soft_preferences: List[Preference] = []

    @model_validator(mode="after")
    def _horizon_order(self) -> "InvestorProfile":
        if self.horizon_years_min > self.horizon_years_max:
            raise ValueError("horizon_years_min doit être inférieur ou égal à horizon_years_max.")
        return self


class ProfileIssue(StrictModel):
    code: str
    severity: Literal["error", "warning"]
    message: str


class ProfileValidation(StrictModel):
    profile_id: str
    valid: bool  # faux dès qu'une erreur est présente
    issues: List[ProfileIssue] = []
