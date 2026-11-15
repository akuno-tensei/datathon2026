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
        # outils de l'analyste (trace des agents)
        "asset_lookup": "Fiche de l'actif", "get_prices": "Lecture des prix", "data_quality_check": "Contrôle de qualité des données",
        "convert_currency": "Conversion de devise", "compute_metrics": "Mesures de risque et de rendement",
        "stress_window": "Test de sensibilité à la période", "correlation_matrix": "Corrélations entre actifs",
        "portfolio_metrics": "Mesures du portefeuille",
        # intentions de la demande
        "evaluate_asset": "Évaluer un actif", "compare_assets": "Comparer des actifs", "build_scenario": "Simuler un portefeuille",
        "explain_risk": "Expliquer un risque",
        # termes techniques des hypothèses
        "adj_close": "prix ajustés", "evidence_id": "identifiant de preuve",
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
        "asset_lookup": "Asset sheet", "get_prices": "Price reading", "data_quality_check": "Data quality check",
        "convert_currency": "Currency conversion", "compute_metrics": "Risk and return measures",
        "stress_window": "Period sensitivity test", "correlation_matrix": "Correlations between assets",
        "portfolio_metrics": "Portfolio measures",
        "evaluate_asset": "Evaluate an asset", "compare_assets": "Compare assets", "build_scenario": "Simulate a portfolio",
        "explain_risk": "Explain a risk",
        "adj_close": "adjusted prices", "evidence_id": "evidence identifier",
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


# Périodes de mesure (« fenêtres » de l'analyste) : nom (« année 2022 ») et tournure dans une phrase (« en 2022 »).
WINDOWS: Dict[str, Dict[str, tuple]] = {
    "fr": {
        "complet": ("toute la période", "sur toute la période"), "2022": ("année 2022", "en 2022"),
        "hors_2022": ("toute la période sauf 2022", "sur toute la période sauf 2022"), "depuis_2023": ("depuis 2023", "depuis 2023"),
        "1_an": ("dernière année", "sur la dernière année"), "3_ans": ("3 dernières années", "sur les 3 dernières années"),
    },
    "en": {
        "complet": ("full period", "over the full period"), "2022": ("year 2022", "in 2022"),
        "hors_2022": ("full period except 2022", "over the full period except 2022"), "depuis_2023": ("since 2023", "since 2023"),
        "1_an": ("last year", "over the last year"), "3_ans": ("last 3 years", "over the last 3 years"),
    },
}
_MONTHS = {"fr": ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."],
           "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]}
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def human_date(iso: str, lang: str = "fr") -> str:
    """2021-11-12 -> « 12 nov. 2021 » (fr) ou « Nov 12, 2021 » (en). Autre chose : inchangé."""
    m = _ISO.fullmatch(str(iso)) if iso else None
    if not m:
        return iso
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    name = _MONTHS.get(lang, _MONTHS["fr"])[mo - 1]
    return f"{d} {name} {y}" if lang == "fr" else f"{name} {d}, {y}"


def window_name(code: str, lang: str = "fr") -> str:
    """Nom lisible d'une période de mesure : « année 2022 », « toute la période », ou « du 1 janv. 2023 au 12 nov. 2026 »."""
    w = WINDOWS.get(lang, WINDOWS["fr"]).get(str(code))
    if w:
        return w[0]
    if isinstance(code, str) and "/" in code:
        a, b = code.split("/", 1)
        return (f"du {human_date(a, lang)} au {human_date(b, lang)}" if lang == "fr"
                else f"{human_date(a, lang)} to {human_date(b, lang)}")
    return code


def _window_phrase(code: str, lang: str) -> str:
    w = WINDOWS.get(lang, WINDOWS["fr"]).get(code)
    if w:
        return w[1]
    name = window_name(code, lang)
    return name if name != code else (f"sur la période « {code} »" if lang == "fr" else f"over the period “{code}”")


_Q = r"[«“\"]\s?([^»”\"]+?)\s?[»”\"]"  # code entre guillemets français, anglais ou droits
_WIN_RULES = {
    "fr": [
        (re.compile(r"sur la fenêtre " + _Q), lambda m, l: _window_phrase(m.group(1), l)),
        (re.compile(r"\bfenêtre " + _Q), lambda m, l: _window_phrase(m.group(1), l)),
        (re.compile(r"selon la fenêtre \(([^)]*)\)"),
         lambda m, l: "selon la période (" + ", ".join(window_name(x.strip(), l) for x in m.group(1).split(",")) + ")"),
        (re.compile(r"\bfenêtres\b"), lambda m, l: "périodes"),
        (re.compile(r"\bfenêtre\b"), lambda m, l: "période"),
        (re.compile(r"\bFenêtres?\b"), lambda m, l: "Période"),
        (re.compile(r"\(non récupéré à la fin de la période \(durée minimale\)\)"),
         lambda m, l: "(le prix n'est pas encore revenu à ce sommet : durée minimale)"),
        (re.compile(r"\(non récupéré à la fin de la période\)"), lambda m, l: "(le prix n'est pas encore revenu à ce sommet)"),
        (re.compile(r"\(récupéré\)"), lambda m, l: "(le prix est revenu à ce sommet)"),
        (re.compile(r"(Ce repli|Le drawdown maximal) n'était pas récupéré à la fin de la période"),
         lambda m, l: "Le prix n'était pas encore revenu à son sommet à la fin de la période"),
        (re.compile(r"\bDrawdown mesuré\b"), lambda m, l: "Perte depuis un sommet mesurée"),
        (re.compile(r"\b[dD]rawdown\b"), lambda m, l: "perte depuis un sommet"),
        (re.compile(r"sans taux du même jour : dernier taux connu reporté"),
         lambda m, l: "sans taux de change publié ce jour-là : le dernier taux connu est réutilisé"),
        (re.compile(r"\(partial = vrai\)"), lambda m, l: "(période partielle)"),
    ],
    "en": [
        (re.compile(r"over window " + _Q), lambda m, l: _window_phrase(m.group(1), l)),
        (re.compile(r"\bwindow " + _Q), lambda m, l: _window_phrase(m.group(1), l)),
        (re.compile(r"depending on the window \(([^)]*)\)"),
         lambda m, l: "depending on the period (" + ", ".join(window_name(x.strip(), l) for x in m.group(1).split(",")) + ")"),
        (re.compile(r"\bwindows\b"), lambda m, l: "periods"),
        (re.compile(r"\bwindow\b"), lambda m, l: "period"),
    ],
}


_CODE = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b|(?<=« )[a-z_]+(?= »)")


def humanize_text(text: str, lang: str = "fr") -> str:
    """Remplace dans une phrase les seuls codes connus (« faible_modere », eviter_controverses...) par leur libellé.
    Un mot inconnu n'est jamais modifié : le texte des agents reste exact."""
    if not isinstance(text, str):
        return text
    table = _LABELS.get(lang, _LABELS["fr"])
    for rx, fn in _WIN_RULES.get(lang, _WIN_RULES["fr"]):  # périodes d'abord : « fenêtre « 2022 » » devient « en 2022 »
        text = rx.sub(lambda m, fn=fn: fn(m, lang), text)

    def sub(m: "re.Match") -> str:
        label = table.get(m.group(0))
        if label is None:
            return m.group(0)
        quoted = text[max(m.start() - 2, 0):m.start()] == "« "
        if quoted or m.start() == 0 or label[1:2].isupper():  # entre guillemets, en tête de phrase ou sigle (ESG) : tel quel
            return label
        return label[:1].lower() + label[1:]  # au fil de la phrase : pas de majuscule au milieu

    text = _CODE.sub(sub, text)
    return _ISO.sub(lambda m: human_date(m.group(0), lang), text)  # dates lisibles : « 12 nov. 2021 »


# Lexique affiché dans l'interface : chaque terme de l'analyse expliqué pour un débutant, sans jargon.
GLOSSARY: Dict[str, list] = {
    "fr": [
        ("Volatilité annualisée", "Ampleur habituelle des variations de prix sur un an. 15 % veut dire que le prix varie typiquement "
                                  "d'environ 15 % à la hausse ou à la baisse en un an ; plus elle est élevée, plus le parcours est agité."),
        ("Perte maximale depuis un sommet", "La plus forte baisse entre un prix record et le point le plus bas qui a suivi. "
                                            "40 % veut dire que 100 $ placés au sommet sont tombés à 60 $."),
        ("Période de mesure", "Dates sur lesquelles les chiffres sont calculés : toute la période disponible (depuis novembre 2021), "
                              "l'année 2022, toute la période sauf 2022, la dernière année ou les 3 dernières années."),
        ("Année 2022", "Année où les actions et les obligations ont baissé en même temps, ce qui est rare. "
                       "Elle sert de test de résistance réel, tiré des données."),
        ("Période la plus défavorable", "Parmi les périodes mesurées, celle où l'indicateur est le pire. "
                                        "Par prudence, c'est elle qui est comparée aux limites du profil."),
        ("Sensibilité à la période", "Indique si le résultat change beaucoup selon les dates retenues. "
                                     "« Sensible » : la conclusion dépend de la période choisie."),
        ("Corrélation", "Mesure, entre −1 et 1, à quel point deux actifs évoluent ensemble. "
                        "Des actifs peu corrélés se compensent mieux dans un portefeuille."),
        ("Limites dérivées du profil", "Seuils calculés à partir de la tolérance au risque et de l'horizon : volatilité, perte maximale, "
                                       "part d'un seul actif et part de chaque type d'actif."),
        ("Préférences et exclusions", "Une préférence est un souhait : elle oriente l'analyse sans rien bloquer. "
                                      "Une exclusion est un refus strict : un actif exclu est bloqué."),
        ("Alternative", "Autre actif de l'univers, proposé quand l'actif demandé dépasse les limites du profil. "
                        "Il est évalué avec les mêmes règles ; ce n'est pas une recommandation."),
        ("FNB", "Fonds négocié en bourse : un panier de titres (actions, obligations, or…) qui s'achète comme une seule action."),
        ("Devise de base", "Monnaie dans laquelle tous les prix sont convertis avant le calcul (CAD ou USD), "
                           "pour comparer des actifs cotés dans des monnaies différentes."),
        ("Rendement total", "Variation du prix sur la période, dividendes réinvestis."),
        ("Preuve", "Chaque chiffre affiché renvoie à un calcul enregistré, que l'on peut ouvrir et refaire à l'identique."),
        ("Fait, hypothèse", "Un fait vient directement des données ou d'un calcul. Une hypothèse est un choix de méthode "
                            "à garder en tête en lisant le résultat."),
        ("Niveau de confiance A, B, C", "Qualité de la source d'une donnée ajoutée par l'enrichissement : A = source officielle, "
                                        "B = fournisseur reconnu avec une méthode publiée, C = signal à vérifier, jamais utilisé dans un calcul."),
    ],
    "en": [
        ("Annualised volatility", "Typical size of price swings over a year. 15% means the price typically moves up or down by about 15% "
                                  "in a year; the higher it is, the bumpier the ride."),
        ("Maximum loss from a peak", "The largest fall from a record price to the lowest point that followed. "
                                     "40% means $100 invested at the peak fell to $60."),
        ("Measurement period", "Dates used for the figures: the full available period (since November 2021), the year 2022, "
                               "the full period except 2022, the last year or the last 3 years."),
        ("Year 2022", "A year when stocks and bonds fell at the same time, which is rare. It serves as a real stress test, taken from the data."),
        ("Least favourable period", "Among the periods measured, the one where the indicator is worst. "
                                    "To be prudent, it is the one compared with the profile limits."),
        ("Sensitivity to the period", "Shows whether the result changes a lot depending on the dates used. "
                                      "“Sensitive”: the conclusion depends on the period chosen."),
        ("Correlation", "Measures, between −1 and 1, how much two assets move together. "
                        "Weakly correlated assets offset each other better in a portfolio."),
        ("Limits derived from the profile", "Thresholds computed from risk tolerance and horizon: volatility, maximum loss, "
                                            "share of a single asset and share of each asset type."),
        ("Preferences and exclusions", "A preference is a wish: it guides the analysis without blocking anything. "
                                       "An exclusion is a strict refusal: an excluded asset is blocked."),
        ("Alternative", "Another asset from the universe, offered when the requested asset exceeds the profile limits. "
                        "It is assessed with the same rules; it is not a recommendation."),
        ("ETF", "Exchange-traded fund: a basket of securities (stocks, bonds, gold…) bought like a single share."),
        ("Base currency", "Currency into which all prices are converted before computing (CAD or USD), "
                          "to compare assets quoted in different currencies."),
        ("Total return", "Price change over the period, with dividends reinvested."),
        ("Evidence", "Every figure shown links to a recorded calculation that can be opened and replayed identically."),
        ("Fact, assumption", "A fact comes straight from the data or a calculation. An assumption is a method choice "
                             "to keep in mind when reading the result."),
        ("Confidence level A, B, C", "Quality of the source of data added by enrichment: A = official source, "
                                     "B = recognised provider with a published method, C = signal to check, never used in a calculation."),
    ],
}
