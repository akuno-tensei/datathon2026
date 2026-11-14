"""Lecture des champs texte du formulaire de profil. Pur et testable."""
from typing import Any, Dict, List

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
