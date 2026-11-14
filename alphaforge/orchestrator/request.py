"""parse_request : demande libre -> intention structurée.

Deux implémentations derrière la même interface :
- `RuleBasedParser` : déterministe, sans réseau. Repli de démo et base des tests.
- `LLMParser` : un LLM propose l'intention en JSON ; les tickers sont toujours revalidés contre l'univers.

Le parseur ne décide rien : il signale (`scope_flags`) ce que la gouvernance devra examiner.
"""
import json
import re
from typing import Dict, List, Literal, Optional, Protocol, Tuple

from pydantic import BaseModel, ConfigDict

Intent = Literal["evaluate_asset", "compare_assets", "build_scenario", "explain_risk", "unknown"]
ScopeFlag = Literal["asks_prediction", "asks_transaction", "asks_personal_advice"]

INTENTS = ("evaluate_asset", "compare_assets", "build_scenario", "explain_risk", "unknown")
SCOPE_FLAGS = ("asks_prediction", "asks_transaction", "asks_personal_advice")


class ParsedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: str
    intent: Intent
    tickers: List[str] = []  # tickers de l'univers, dans l'ordre d'apparition
    unknown_tickers: List[str] = []  # symboles repérés mais hors univers
    scope_flags: List[ScopeFlag] = []
    parser: str = "rules"


# ---- repérage des actifs -------------------------------------------------

# Sigles financiers fréquents qui ne sont pas des tickers.
_NOT_TICKERS = {
    "ESG", "CAD", "USD", "FNB", "ETF", "IA", "AI", "REER", "CELI", "TSX", "IPC", "CPI", "PEA", "FX", "ISR",
    "NYSE", "SEC", "BTW", "OK", "SVP", "ET", "OU", "LE", "LA", "LES", "UN", "UNE", "DE", "DU", "DES", "EN",
}
_NAME_MIN_LEN = 5  # premier mot du nom d'un actif utilisé comme alias (Apple, Tesla, Bitcoin...)


def _universe_index(assets: List[Dict[str, str]]) -> Dict[str, str]:
    """Alias (majuscules) -> ticker. Inclut le ticker complet, sa racine (RY pour RY.TO) et le premier mot du nom."""
    idx: Dict[str, str] = {}
    for a in assets:
        t = a["ticker"]
        idx[t.upper()] = t
        root = re.split(r"[.\-]", t)[0].upper()
        idx.setdefault(root, t)

    # Alias de nom : retirés s'ils désignent plusieurs actifs ou s'ils entrent en collision avec un ticker.
    names: Dict[str, set] = {}
    for a in assets:
        first = re.split(r"[\s,.]", a["name"].strip())[0].upper()
        if len(first) >= _NAME_MIN_LEN:
            names.setdefault(first, set()).add(a["ticker"])
    for alias, tickers in names.items():
        if len(tickers) == 1 and alias not in idx:
            idx[alias] = next(iter(tickers))
    return idx


def find_assets(text: str, assets: List[Dict[str, str]]) -> Tuple[List[str], List[str]]:
    """Retourne (tickers de l'univers, symboles inconnus), dans l'ordre d'apparition."""
    idx = _universe_index(assets)
    known: List[str] = []
    unknown: List[str] = []
    for m in re.finditer(r"\$?[A-Za-z][A-Za-z0-9]*(?:[.\-][A-Za-z]{2,3})?", text):
        raw = m.group(0)
        explicit = raw.startswith("$")
        tok = raw.lstrip("$")
        key = tok.upper()
        symbol_shape = explicit or tok.isupper() or "." in tok or "-" in tok
        if key in idx and (symbol_shape or len(key) >= _NAME_MIN_LEN):
            if idx[key] not in known:
                known.append(idx[key])
        elif key not in idx and key not in _NOT_TICKERS and (explicit or "." in tok or (tok.isupper() and len(tok) >= 3)):
            if key not in unknown:
                unknown.append(key)
    return known, unknown


# ---- intention et signaux de périmètre ----------------------------------

_PATTERNS: Dict[str, "re.Pattern[str]"] = {
    "asks_prediction": re.compile(
        r"\bpr[ée]di|\bpr[ée]vois|\bprevisi|\bpredict|\bforecast|l'an prochain|l'année prochaine|next year|va (?:monter|baisser|doubler)|"
        r"\bvaudra|\bwill (?:go|rise|fall)",
        re.I,
    ),
    "asks_transaction": re.compile(
        r"\bach[eè]te\b|\bachetez\b|\bvends\b|\bvendez\b|passe(?:r)? un ordre|\bex[ée]cute|\bbuy\b|\bsell\b|\bplace an order", re.I
    ),
    "asks_personal_advice": re.compile(
        r"dois-je|devrais-je|devrais|conseille|recommande|should i|do you recommend|what should i", re.I
    ),
}

_INTENT_PATTERNS = [
    ("compare_assets", re.compile(r"compar|\bversus\b|\bvs\.?\b|plut[ôo]t que|entre .+ et |\bor\b.+\bor\b", re.I)),
    ("build_scenario", re.compile(r"sc[ée]nario|portefeuille|portfolio|r[ée]partition|allocation|construi|simul", re.I)),
    ("explain_risk", re.compile(r"risque|volatil|drawdown|baisse|perte|krach|crash|risk", re.I)),
    ("evaluate_asset", re.compile(r"[ée]valu|analys|que penses|vaut|\bachet|\bbuy\b|regarde|look at|evaluate|assess", re.I)),
]


def _detect_flags(text: str) -> List[ScopeFlag]:
    return [f for f in SCOPE_FLAGS if _PATTERNS[f].search(text)]  # type: ignore[misc]


class RuleBasedParser:
    name = "rules"

    def parse(self, text: str, assets: List[Dict[str, str]]) -> ParsedRequest:
        tickers, unknown = find_assets(text, assets)
        intent: Intent = "unknown"
        for name, pat in _INTENT_PATTERNS:
            if pat.search(text):
                intent = name  # type: ignore[assignment]
                break
        # Sans verbe reconnu, le nombre d'actifs désigne l'intention la plus simple.
        if intent == "unknown":
            if len(tickers) >= 2:
                intent = "compare_assets"
            elif len(tickers) == 1:
                intent = "evaluate_asset"
        if intent == "compare_assets" and len(tickers) == 1:
            intent = "evaluate_asset"
        return ParsedRequest(
            raw_text=text, intent=intent, tickers=tickers, unknown_tickers=unknown, scope_flags=_detect_flags(text), parser=self.name
        )


# ---- variante LLM --------------------------------------------------------


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str:
        """Retourne le texte brut de la réponse du modèle."""


_LLM_SYSTEM = (
    "Tu convertis une demande d'investisseur en JSON strict, sans commentaire. Clés: "
    '"intent" parmi ' + json.dumps(list(INTENTS)) + ', "mentioned_symbols" (liste de symboles ou noms d\'actifs cités), '
    '"scope_flags" (sous-ensemble de ' + json.dumps(list(SCOPE_FLAGS)) + "). "
    "Tu ne réponds pas à la demande, tu ne calcules rien et tu ne recommandes rien."
)


class LLMParser:
    """Le LLM propose, le code dispose : intention et signaux sont validés, les tickers revérifiés contre l'univers.

    Si la réponse est invalide, on retombe sur `RuleBasedParser` plutôt que de deviner.
    """

    name = "llm"

    def __init__(self, client: LLMClient, fallback: Optional[RuleBasedParser] = None):
        self._client = client
        self._fallback = fallback or RuleBasedParser()

    def parse(self, text: str, assets: List[Dict[str, str]]) -> ParsedRequest:
        try:
            raw = self._client.complete(_LLM_SYSTEM, text)
            start, end = raw.index("{"), raw.rindex("}") + 1
            data = json.loads(raw[start:end])
            intent = data["intent"]
            if intent not in INTENTS:
                raise ValueError(f"intention invalide: {intent!r}")
            flags = [f for f in data.get("scope_flags", []) if f in SCOPE_FLAGS]
            symbols = " ".join(str(s) for s in data.get("mentioned_symbols", []))
            tickers, unknown = find_assets(symbols, assets)
            # Les actifs cités par le texte d'origine comptent aussi : le LLM ne peut pas en retirer.
            t2, u2 = find_assets(text, assets)
            tickers += [t for t in t2 if t not in tickers]
            unknown += [u for u in u2 if u not in unknown]
            # Les signaux de périmètre détectés par règles s'ajoutent : le LLM ne peut pas les désactiver.
            flags += [f for f in _detect_flags(text) if f not in flags]
            return ParsedRequest(
                raw_text=text, intent=intent, tickers=tickers, unknown_tickers=unknown, scope_flags=flags, parser=self.name
            )
        except Exception:  # réponse invalide OU panne du client (réseau, quota, accès) : le repli par règles reste disponible
            parsed = self._fallback.parse(text, assets)
            return parsed.model_copy(update={"parser": "rules(fallback)"})


class BedrockClient:
    """Client Bedrock (API Converse). Requiert `boto3` et un accès au modèle dans la région choisie."""

    def __init__(self, model_id: str, region_name: str = "us-east-1"):
        import boto3  # import tardif : le reste du paquet fonctionne sans boto3

        self._client = boto3.client("bedrock-runtime", region_name=region_name)
        self._model_id = model_id

    def complete(self, system: str, user: str) -> str:
        resp = self._client.converse(
            modelId=self._model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"temperature": 0.0, "maxTokens": 400},
        )
        return resp["output"]["message"]["content"][0]["text"]
