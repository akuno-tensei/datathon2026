"""Modèles typés de la gouvernance : contraintes dérivées, raisons chiffrées, verdict."""
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Status = Literal["conforme", "avertissement", "bloqué"]
Severity = Literal["block", "warn", "info"]
CheckName = Literal[
    "hard_constraints", "soft_preferences", "horizon_fit", "risk_limits", "data_gaps", "scope_limits", "profile", "diversification", "portfolio"
]


class Reason(StrictModel):
    """Une raison de verdict. Chaque nombre cité est dans `measures` et renvoie à une preuve du registre."""

    check: CheckName
    code: str
    severity: Severity
    message: str
    measures: Dict[str, Any] = {}
    evidence_ids: List[str] = []


class Constraints(StrictModel):
    """Limites numériques dérivées du profil. `max_single_asset_weight` et `class_caps` valent pour les scénarios de portefeuille."""

    profile_id: str
    risk_level_applied: str
    max_volatility: float  # volatilité annualisée maximale (0,2 = 20 %)
    max_drawdown: float  # perte maximale depuis un sommet, en valeur positive (0,3 = -30 %)
    max_single_asset_weight: float
    class_caps: Dict[str, float]  # 0.0 = classe non permise
    adjustments: List[str] = []


class ScopeRefusal(StrictModel):
    kind: Literal["prediction", "transaction", "personal_advice"]
    status: Status
    message: str
    offered_instead: str


class AssetAssessment(StrictModel):
    ticker: str
    status: Status
    reasons: List[Reason] = []
    data_notes: List[str] = []  # codes d'avis de l'analyste, transmis sans les interpréter
    alternative_for: List[str] = []


class GovernanceVerdict(StrictModel):
    status: Status
    constraints: Constraints
    assets: List[AssetAssessment]
    alternatives: List[AssetAssessment] = []
    global_reasons: List[Reason] = []
    scope_refusals: List[ScopeRefusal] = []
