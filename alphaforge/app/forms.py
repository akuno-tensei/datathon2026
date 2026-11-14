"""Formulaire de profil et de scénario : fonctions pures, testables sans interface."""
import re
import unicodedata
from typing import Any, Dict, List, Optional

KINDS = ("asset_class", "sector", "ticker", "theme")


def parse_criteria(text: str, with_weight: bool) -> List[Dict[str, Any]]:
    """Une ligne par critère : `type:valeur[:poids]`. Lignes vides ignorées. Lève ValueError sur une ligne invalide."""
    out: List[Dict[str, Any]] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(":")]
        if len(parts) < 2 or parts[0] not in KINDS or not parts[1]:
            raise ValueError(f"Ligne {n} invalide : « {line} ». Format attendu type:valeur[:poids], type parmi {', '.join(KINDS)}.")
        item: Dict[str, Any] = {"kind": parts[0], "value": parts[1]}
        if with_weight and len(parts) >= 3 and parts[2]:
            try:
                item["weight"] = float(parts[2].replace(",", "."))
            except ValueError as e:
                raise ValueError(f"Ligne {n} : poids invalide « {parts[2]} » (nombre entre 0 et 1).") from e
        out.append(item)
    return out


# ---- saisie des poids d'un scénario ---------------------------------------
WEIGHT_TOLERANCE_PCT = 1e-4  # même exigence que l'analyste (1e-6 en fraction) : le bouton ne s'active que si l'outil acceptera


def equal_weights(tickers: List[str]) -> Dict[str, float]:
    """Poids égaux en pourcentage, arrondis à 0,1 ; le reste d'arrondi va au premier actif pour sommer exactement 100."""
    n = len(tickers)
    if n == 0:
        return {}
    w = {t: round(100.0 / n, 1) for t in tickers}
    w[tickers[0]] = round(w[tickers[0]] + (100.0 - sum(w.values())), 1)
    return w


def weights_sum(weights_pct: Dict[str, float]) -> float:
    return round(sum(weights_pct.values()), 6)


def weights_ok(weights_pct: Dict[str, float]) -> bool:
    """Au moins un actif, tous les poids strictement positifs, somme égale à 100 % (tolérance de saisie)."""
    return bool(weights_pct) and all(v > 0 for v in weights_pct.values()) and abs(weights_sum(weights_pct) - 100.0) <= WEIGHT_TOLERANCE_PCT


def normalize(weights_pct: Dict[str, float]) -> Dict[str, float]:
    """Ramène la somme à 100 en gardant les proportions. Action explicite de l'utilisateur (bouton), jamais automatique."""
    tot = sum(v for v in weights_pct.values() if v > 0)
    if tot <= 0:
        return dict(weights_pct)
    out = {t: round(max(v, 0.0) * 100.0 / tot, 1) for t, v in weights_pct.items()}
    first = next(iter(out))
    out[first] = round(out[first] + (100.0 - sum(out.values())), 1)
    return out


def to_fractions(weights_pct: Dict[str, float]) -> Dict[str, float]:
    """Pourcentages saisis -> fractions transmises à l'analyste, sans correction : il valide lui-même la somme exacte."""
    return {t: v / 100.0 for t, v in weights_pct.items()}


# ---- création d'un profil quelconque ------------------------------------------
RISK_LEVELS = ("faible", "faible_modere", "modere", "modere_eleve", "eleve")
OBJECTIVES = ("preservation_capital", "revenu", "croissance_moderee", "croissance_elevee")
BLANK_PROFILE: Dict[str, Any] = {
    "display_name": "", "profile_id": "", "language": "fr", "experience": "debutant", "risk_tolerance": "modere",
    "base_currency": "CAD", "horizon_years_min": 5, "horizon_years_max": 10, "objectives": [],
    "soft_preferences": [], "hard_exclusions": [],
}


def slugify(name: str, max_len: int = 40) -> str:
    """Identifiant de fichier sûr à partir d'un nom : minuscules, sans accents, `_` entre les mots. Vide si rien d'exploitable."""
    ascii_name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", ascii_name.lower()).strip("_")[:max_len].strip("_")


def add_criterion(rows: List[Dict[str, Any]], kind: str, value: Optional[str], weight: Optional[float] = None) -> List[Dict[str, Any]]:
    """Ajoute un critère à une liste (nouvelle liste). Refuse un type inconnu, une valeur vide, un doublon, un poids hors de [0, 1]."""
    value = (value or "").strip()
    if kind not in KINDS:
        raise ValueError(f"Type de critère inconnu : {kind!r}. Types : {', '.join(KINDS)}.")
    if not value:
        raise ValueError("La valeur du critère est vide.")
    if any(r["kind"] == kind and r["value"].strip().casefold() == value.casefold() for r in rows):
        raise ValueError(f"« {kind} : {value} » figure déjà dans la liste.")
    row: Dict[str, Any] = {"kind": kind, "value": value}
    if weight is not None:
        if not 0.0 <= float(weight) <= 1.0:
            raise ValueError("Le poids d'une préférence doit être entre 0 et 1.")
        row["weight"] = round(float(weight), 2)
    return rows + [row]


def remove_criterion(rows: List[Dict[str, Any]], index: int) -> List[Dict[str, Any]]:
    return [r for i, r in enumerate(rows) if i != index]


def build_profile_dict(
    *, profile_id: str, display_name: str, language: str, experience: str, risk_tolerance: str, base_currency: str,
    horizon_years_min: int, horizon_years_max: int, objectives: List[str],
    preferences: List[Dict[str, Any]], exclusions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Dictionnaire de profil prêt pour `load_profile`. L'identifiant vide est déduit du nom. Aucune valeur n'est corrigée."""
    pid = (profile_id or "").strip() or slugify(display_name)
    return {
        "profile_id": pid, "display_name": (display_name or "").strip() or None, "language": language, "experience": experience,
        "risk_tolerance": risk_tolerance, "base_currency": base_currency, "horizon_years_min": horizon_years_min,
        "horizon_years_max": horizon_years_max, "objectives": list(objectives),
        "soft_preferences": [dict(r, weight=r.get("weight", 0.5)) for r in preferences],
        "hard_exclusions": [{"kind": r["kind"], "value": r["value"]} for r in exclusions],
    }


def form_state_from_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Valeurs initiales du formulaire à partir d'un profil existant (copie), identifiant et nom vidés pour en créer un nouveau."""
    st = {k: profile[k] for k in ("language", "experience", "risk_tolerance", "base_currency", "horizon_years_min",
                                  "horizon_years_max", "objectives")}
    st.update(display_name="", profile_id="",
              soft_preferences=[dict(x) for x in profile.get("soft_preferences", [])],
              hard_exclusions=[{"kind": x["kind"], "value": x["value"]} for x in profile.get("hard_exclusions", [])])
    return st


def criterion_options(assets: List[Dict[str, Any]], themes: List[str]) -> Dict[str, List[str]]:
    """Valeurs que les données savent vérifier, par type de critère. Un thème libre reste possible : il sera signalé non vérifiable."""
    return {
        "asset_class": sorted({a["asset_class"] for a in assets}),
        "sector": sorted({a["sector"] for a in assets}),
        "ticker": sorted(a["ticker"] for a in assets),
        "theme": sorted(themes),
    }
