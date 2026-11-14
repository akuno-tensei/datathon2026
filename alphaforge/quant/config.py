"""Configuration de l'analyste quantitatif : alias de fenêtres, bases d'annualisation, seuils."""
from datetime import date
from pathlib import Path
from typing import Dict, Optional

from pydantic import BaseModel, Field

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "alphaforge_starter"


class WindowAlias(BaseModel):
    """Définition d'un alias de fenêtre. Une seule forme à la fois.

    - `start` et `end` fixes (`end` absent = as_of) ;
    - `lookback_days` : fenêtre glissante qui se termine à as_of ;
    - `full` : toute la période disponible jusqu'à as_of.
    """

    description: str
    start: Optional[date] = None
    end: Optional[date] = None
    lookback_days: Optional[int] = None
    full: bool = False


def _default_aliases() -> Dict[str, WindowAlias]:
    return {
        "complet": WindowAlias(description="Toute la période disponible jusqu'à as_of", full=True),
        "2022": WindowAlias(
            description="Année 2022 : actions et obligations ont reculé en même temps",
            start=date(2022, 1, 1),
            end=date(2022, 12, 31),
        ),
        "depuis_2023": WindowAlias(description="Du 1er janvier 2023 jusqu'à as_of", start=date(2023, 1, 1)),
        "1_an": WindowAlias(description="365 derniers jours avant as_of", lookback_days=365),
        "3_ans": WindowAlias(description="1095 derniers jours avant as_of", lookback_days=1095),
    }


class QuantConfig(BaseModel):
    default_base_currency: str = "CAD"
    supported_currencies: tuple = ("CAD", "USD")

    # Annualisation : base par type de calendrier. Tout ce qui mélange crypto et actions passe en hebdomadaire.
    periods_per_year_exchange: int = 252
    periods_per_year_crypto: int = 365
    periods_per_year_mixed: int = 52

    # Report de dernière valeur (ffill) : borne en jours civils depuis la dernière observation réelle.
    # 5 jours couvrent un vendredi férié suivi d'une fin de semaine. Jamais de bfill.
    ffill_limit_days: int = 5

    # Seuils de qualité des données.
    min_obs_insufficient: int = 60  # compute_metrics refuse en dessous (INSUFFICIENT_DATA)
    min_obs_warning: int = 120
    coverage_insufficient: float = 0.90  # couverture sous ce seuil : données insuffisantes
    coverage_warning: float = 0.98
    edge_gap_days: int = 5
    internal_gap_days_exchange: int = 5
    internal_gap_days_crypto: int = 1
    large_move_exchange: float = 0.25
    large_move_crypto: float = 0.40
    large_move_sigma: float = 3.0  # saut suspect aussi au-delà de k écarts-types des rendements de la fenêtre
    adj_close_over_close_tolerance: float = 0.01  # adj_close > close de plus de 1 % : incohérent

    # Rendement annualisé : seulement si la période observée couvre au moins ce nombre de jours civils.
    annualization_min_days: int = 350

    window_aliases: Dict[str, WindowAlias] = Field(default_factory=_default_aliases)
