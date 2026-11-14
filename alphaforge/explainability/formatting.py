"""Formatage des nombres, partagé par la rédaction et le vérificateur : le même code produit et contrôle le texte affiché."""
import re

_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def shown_value(value: float, unit: str) -> float:
    """Nombre attendu dans le texte, après arrondi. Le signe n'est jamais affiché (la phrase dit « perte »)."""
    v = abs(float(value))
    if unit == "pct":
        return round(v * 100, 1)
    if unit == "corr":
        return round(v, 2)
    return v if v.is_integer() else round(v, 1)


def format_number(value: float, unit: str, lang: str) -> str:
    x = shown_value(value, unit)
    if unit == "corr":  # le signe d'une corrélation porte le sens : il est affiché
        s = ("-" if value < 0 and x > 0 else "") + f"{x:.2f}"
        return s.replace(".", ",") if lang == "fr" else s
    if unit == "pct":
        s = f"{x:.1f}"
        return s.replace(".", ",") + " %" if lang == "fr" else s + "%"
    s = str(int(x)) if float(x).is_integer() else f"{x:.1f}"
    return s.replace(".", ",") if lang == "fr" else s


def parse_shown(shown: str) -> float:
    m = _NUM.search(shown)
    if not m:
        raise ValueError(shown)
    return float(m.group(0).replace(",", "."))
