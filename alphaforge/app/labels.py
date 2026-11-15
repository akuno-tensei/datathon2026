"""Libellés lisibles pour l'affichage. Affichage seulement : les valeurs enregistrées et transmises aux agents ne changent pas."""
import re
from typing import Dict

_LABELS: Dict[str, Dict[str, str]] = {
    "fr": {
        # tolérance au risque
        "faible": "Faible", "faible_modere": "Faible à modéré", "modere": "Modéré", "modere_eleve": "Modéré à élevé", "eleve": "Élevé",
        # expérience
        "debutant": "Débutant", "intermediaire": "Intermédiaire", "avance": "Avancé",
        # objectifs
        "preservation_capital": "Préservation du capital", "revenu": "Revenu",
        "croissance_moderee": "Croissance modérée", "croissance_elevee": "Croissance élevée",
        # classes d'actifs
        "equity": "Actions", "crypto": "Cryptoactifs", "etf_bond": "FNB obligataire", "etf_commodity": "FNB de matières premières",
        "etf_equity": "FNB d'actions",
        # thèmes
        "esg": "ESG", "isr": "ISR", "investissement_responsable": "Investissement responsable", "durable": "Durable",
        "eviter_controverses": "Éviter les controverses", "controverses": "Controverses", "sans_controverse": "Sans controverse",
        # enrichissement : types de données et causes de lacune
        "controversies": "Controverses", "filings": "Dépôts réglementaires", "fundamentals": "Fondamentaux", "news": "Nouvelles",
        "etf_holdings": "Composition du FNB", "no_provider": "aucun fournisseur branché", "no_data": "aucune donnée trouvée",
        "no_data_before_as_of": "aucune donnée publiée avant la date d'analyse", "no_matching_data": "aucune donnée correspondante",
        "rate_limited": "trop d'appels à la source", "auth_required": "identification requise", "unsupported": "actif non couvert par la source",
        "invalid_ticker": "symbole invalide", "lacune_non_comblee": "lacune non comblée",
    },
    "en": {
        "faible": "Low", "faible_modere": "Low to moderate", "modere": "Moderate", "modere_eleve": "Moderate to high", "eleve": "High",
        "debutant": "Beginner", "intermediaire": "Intermediate", "avance": "Advanced",
        "preservation_capital": "Capital preservation", "revenu": "Income",
        "croissance_moderee": "Moderate growth", "croissance_elevee": "High growth",
        "equity": "Equities", "crypto": "Crypto-assets", "etf_bond": "Bond ETF", "etf_commodity": "Commodity ETF", "etf_equity": "Equity ETF",
        "esg": "ESG", "isr": "SRI", "investissement_responsable": "Responsible investing", "durable": "Sustainable",
        "eviter_controverses": "Avoid controversies", "controverses": "Controversies", "sans_controverse": "No controversy",
        "controversies": "Controversies", "filings": "Regulatory filings", "fundamentals": "Fundamentals", "news": "News",
        "etf_holdings": "ETF holdings", "no_provider": "no provider connected", "no_data": "no data found",
        "no_data_before_as_of": "no data published before the analysis date", "no_matching_data": "no matching data",
        "rate_limited": "too many calls to the source", "auth_required": "identification required", "unsupported": "asset not covered by the source",
        "invalid_ticker": "invalid symbol", "lacune_non_comblee": "unfilled gap",
    },
}


def human(value: str, lang: str = "fr") -> str:
    """Code -> libellé. Inconnu : les « _ » deviennent des espaces et la première lettre passe en majuscule.
    Les valeurs déjà mises en forme (tickers comme NVDA ou RY.TO, secteurs comme « Information Technology ») restent telles quelles."""
    if not isinstance(value, str) or not value:
        return value
    known = _LABELS.get(lang, _LABELS["fr"]).get(value)
    if known:
        return known
    if value != value.lower():  # majuscules déjà présentes : valeur nommée, on n'y touche pas
        return value
    s = value.replace("_", " ")
    return s[:1].upper() + s[1:]


_CODE = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b|(?<=« )[a-z_]+(?= »)")


def humanize_text(text: str, lang: str = "fr") -> str:
    """Remplace dans une phrase les seuls codes connus (« faible_modere », eviter_controverses...) par leur libellé.
    Un mot inconnu n'est jamais modifié : le texte des agents reste exact."""
    if not isinstance(text, str):
        return text
    table = _LABELS.get(lang, _LABELS["fr"])

    def sub(m: "re.Match") -> str:
        label = table.get(m.group(0))
        if label is None:
            return m.group(0)
        quoted = text[max(m.start() - 2, 0):m.start()] == "« "
        if quoted or label[1:2].isupper():  # entre guillemets ou sigle (ESG) : tel quel
            return label
        return label[:1].lower() + label[1:]  # au fil de la phrase : pas de majuscule au milieu

    return _CODE.sub(sub, text)
