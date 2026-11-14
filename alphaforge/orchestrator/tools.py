"""Outils de l'orchestrateur : load_profile, validate_profile, ledger_write. Aucun calcul financier, aucun verdict."""
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import ValidationError

from .ledger import EntryKind, Ledger, LedgerEntry
from .profile import RISK_RANK, InvestorProfile, ProfileIssue, ProfileValidation

PROFILES_DIR = Path(__file__).resolve().parent / "profiles"

# Seuils de cohérence interne du profil. Génériques : aucune valeur ne dépend d'un persona.
HIGH_RISK_MIN_RANK = RISK_RANK["modere_eleve"]
LOW_RISK_MAX_RANK = RISK_RANK["faible_modere"]
SHORT_HORIZON_MAX_YEARS = 2

_PROFILE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class ProfileError(Exception):
    """Profil introuvable ou invalide. `reason` est un code stable pour la trace."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


def load_profile(source: Union[str, Dict[str, Any], InvestorProfile], profiles_dir: Optional[Path] = None) -> InvestorProfile:
    """Charge un profil par identifiant (fichier JSON), depuis un dict, ou le renvoie tel quel.

    Un nouveau persona se déclare en ajoutant un fichier JSON ou en passant un dict : aucun code à modifier.
    """
    if isinstance(source, InvestorProfile):
        return source
    if isinstance(source, str):
        if not _PROFILE_ID.match(source):  # empêche toute sortie du dossier (../)
            raise ProfileError("invalid_profile_id", f"Identifiant de profil invalide: {source!r}.")
        path = (profiles_dir or PROFILES_DIR) / f"{source}.json"
        if not path.is_file():
            raise ProfileError("profile_not_found", f"Profil introuvable: {source!r}.")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ProfileError("profile_unreadable", f"JSON invalide dans {path.name}: {e}") from e
    else:
        data = source
    try:
        return InvestorProfile.model_validate(data)
    except ValidationError as e:
        raise ProfileError("profile_schema_invalid", f"Profil non conforme au schéma: {e}") from e


def validate_profile(profile: InvestorProfile) -> ProfileValidation:
    """Détecte les profils incomplets ou internes incohérents.

    Les erreurs bloquent l'exécution ; les avertissements sont transmis à la gouvernance et à l'explication.
    """
    issues: List[ProfileIssue] = []
    rank = RISK_RANK[profile.risk_tolerance]

    if not profile.objectives:
        issues.append(ProfileIssue(code="NO_OBJECTIVES", severity="warning", message="Aucun objectif déclaré : le profil est incomplet."))

    if rank >= HIGH_RISK_MIN_RANK and profile.horizon_years_max <= SHORT_HORIZON_MAX_YEARS:
        issues.append(
            ProfileIssue(
                code="RISK_HORIZON_CONFLICT",
                severity="warning",
                message=(
                    f"Tolérance au risque « {profile.risk_tolerance} » avec un horizon d'au plus {profile.horizon_years_max} an(s) : "
                    "une perte importante pourrait ne pas laisser le temps de se rétablir."
                ),
            )
        )

    if rank <= LOW_RISK_MAX_RANK and "croissance_elevee" in profile.objectives:
        issues.append(
            ProfileIssue(
                code="RISK_OBJECTIVE_CONFLICT",
                severity="warning",
                message="Tolérance au risque faible avec un objectif de croissance élevée : ces deux déclarations sont en tension.",
            )
        )
    if rank >= HIGH_RISK_MIN_RANK and profile.objectives == ["preservation_capital"]:
        issues.append(
            ProfileIssue(
                code="RISK_OBJECTIVE_CONFLICT",
                severity="warning",
                message="Tolérance au risque élevée avec pour seul objectif la préservation du capital : ces deux déclarations sont en tension.",
            )
        )

    excluded = {(c.kind, c.value.strip().lower()) for c in profile.hard_exclusions}
    for pref in profile.soft_preferences:
        if (pref.kind, pref.value.strip().lower()) in excluded:
            issues.append(
                ProfileIssue(
                    code="EXCLUSION_PREFERENCE_CONTRADICTION",
                    severity="error",
                    message=f"« {pref.value} » ({pref.kind}) est à la fois exclu et préféré.",
                )
            )

    return ProfileValidation(profile_id=profile.profile_id, valid=not any(i.severity == "error" for i in issues), issues=issues)


def ledger_write(
    ledger: Ledger, kind: EntryKind, agent: str, payload: Dict[str, Any], evidence_id: Optional[str] = None
) -> LedgerEntry:
    """Consigne une étape (profil, demande, plan, étape, verdict, note) au registre."""
    return ledger.write(kind, agent, payload, evidence_id)
