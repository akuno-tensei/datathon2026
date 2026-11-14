"""Paramètres de politique de la gouvernance.

Ces valeurs sont des choix de politique, pas des faits : elles sont ici, en un seul endroit, pour être revues,
justifiées et modifiées sans toucher à la logique. Elles ne dépendent d'aucun persona, seulement du niveau de risque.
"""
from typing import Dict, List

from pydantic import BaseModel, Field

from ..orchestrator.profile import RISK_RANK

RISK_LEVELS: List[str] = sorted(RISK_RANK, key=RISK_RANK.get)  # du plus prudent au plus offensif


class RiskLimits(BaseModel):
    max_volatility: float
    max_drawdown: float
    max_single_asset_weight: float


def _default_limits() -> Dict[str, RiskLimits]:
    return {
        "faible": RiskLimits(max_volatility=0.08, max_drawdown=0.10, max_single_asset_weight=0.10),
        "faible_modere": RiskLimits(max_volatility=0.15, max_drawdown=0.20, max_single_asset_weight=0.15),
        "modere": RiskLimits(max_volatility=0.22, max_drawdown=0.30, max_single_asset_weight=0.20),
        "modere_eleve": RiskLimits(max_volatility=0.32, max_drawdown=0.45, max_single_asset_weight=0.25),
        "eleve": RiskLimits(max_volatility=0.65, max_drawdown=0.80, max_single_asset_weight=0.35),
    }


def _default_class_caps() -> Dict[str, Dict[str, float]]:
    # Seules les classes plafonnées sont listées ; une classe absente n'a pas de plafond (1.0). 0.0 = non permise.
    return {
        "faible": {"crypto": 0.0, "etf_commodity": 0.05, "equity": 0.30},
        "faible_modere": {"crypto": 0.0, "etf_commodity": 0.10, "equity": 0.50},
        "modere": {"crypto": 0.10, "etf_commodity": 0.15, "equity": 0.70},
        "modere_eleve": {"crypto": 0.20, "etf_commodity": 0.20, "equity": 0.85},
        "eleve": {"crypto": 0.35, "etf_commodity": 0.30},
    }


class GovernanceConfig(BaseModel):
    risk_limits: Dict[str, RiskLimits] = Field(default_factory=_default_limits)
    class_caps: Dict[str, Dict[str, float]] = Field(default_factory=_default_class_caps)
    # Sous cet horizon maximal (années), les limites sont resserrées d'un niveau de risque.
    short_horizon_years: int = 3
    # Thèmes pour lesquels le jeu de départ n'a pas de donnée, avec les actifs qui servent de substitut déclaré.
    theme_proxies: Dict[str, List[str]] = Field(default_factory=lambda: {"esg": ["ESGV"]})
    max_alternatives: int = 4
    # Diversification : au-delà de cette corrélation des rendements (matrice de référence), la paire est signalée (info).
    high_correlation: float = 0.8
