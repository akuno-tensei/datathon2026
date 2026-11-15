"""Formatage des nombres, partagé par la rédaction et le vérificateur : le même code produit et contrôle le texte affiché."""
import re

_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _money_in_billions(v: float) -> bool:
    """Échelle choisie APRÈS arrondi : 999 960 000 $ s'affiche « 1,0 milliard », pas « 1000,0 millions »."""
    return v >= 1e9 or round(v / 1e6, 1) >= 1000


def shown_value(value: float, unit: str) -> float:
    """Nombre attendu dans le texte, après arrondi. Le signe n'est jamais affiché (la phrase dit « perte »)."""
    v = abs(float(value))
    if unit == "pct":
        return round(v * 100, 1)
    if unit == "corr":
        return round(v, 2)
    if unit == "money":  # montant en dollars US, affiché en milliards (ou en millions sous le milliard), une décimale
        return round(v / 1e9, 1) if _money_in_billions(v) else round(v / 1e6, 1)
    return v if v.is_integer() else round(v, 1)


def format_number(value: float, unit: str, lang: str) -> str:
    x = shown_value(value, unit)
    if unit == "corr":  # le signe d'une corrélation porte le sens : il est affiché
        s = ("-" if value < 0 and x > 0 else "") + f"{x:.2f}"
        return s.replace(".", ",") if lang == "fr" else s
    if unit == "pct":
        s = f"{x:.1f}"
        return s.replace(".", ",") + " %" if lang == "fr" else s + "%"
    if unit == "money":
        big = _money_in_billions(abs(float(value)))
        s = f"{x:.1f}"
        if lang == "fr":
            plural = "s" if x >= 2 else ""  # « 1,2 milliard », « 2,0 milliards »
            return s.replace(".", ",") + (f" milliard{plural} $ US" if big else f" million{plural} $ US")
        return "USD " + s + (" billion" if big else " million")
    s = str(int(x)) if float(x).is_integer() else f"{x:.1f}"
    return s.replace(".", ",") if lang == "fr" else s


def parse_shown(shown: str) -> float:
    m = _NUM.search(shown)
    if not m:
        raise ValueError(shown)
    return float(m.group(0).replace(",", "."))
