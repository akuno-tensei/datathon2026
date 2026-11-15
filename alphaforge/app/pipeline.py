"""Assemblage des agents sur les données réelles, sans logique d'interface : le noyau à quatre agents, plus l'agent
d'enrichissement en mode « Enrichi » (cinq agents).

`AlphaForge.analyze` exécute orchestrateur -> (enrichissement) -> analyste -> gouvernance -> explicabilité et renvoie une `Analysis`.
Aucune règle propre à un persona : les profils sont des données (JSON), y compris ceux créés depuis l'interface.
"""
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from ..enrichment import CachedProvider, CachingProvider, EdgarProvider, EnrichmentAgent, ProviderError
from ..explainability import Explainer, Explanation
from ..governance import Governance
from ..orchestrator import InvestorProfile, LLMParser, Orchestrator, ProfileError, QuantAnalyst, RuleBasedParser, RunResult, load_profile
from ..orchestrator.request import BedrockClient
from ..orchestrator.tools import PROFILES_DIR
from ..quant import DataStore
from .archive import archive_notes, build_archive

ENRICHMENT_CACHE_DIR = Path(__file__).resolve().parent / "cache" / "enrichment"  # données publiques déjà collectées (hors git)
# Instantané SEC EDGAR versionné (dépôts et fondamentaux des actions américaines) : la démo hors ligne garde l'enrichissement.
ENRICHMENT_SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "alphaforge_starter" / "enrichment_snapshot"
USER_PROFILES_DIR = Path(__file__).resolve().parent / "profiles" / "user"  # profils créés depuis l'interface (données locales)
# Interprétation des demandes par Amazon Bedrock. Modèle retenu par le banc d'essai deploy/bench_llm.py
# (scores dans deploy/bench_results.json, jamais recopiés ici) : aucun signal de périmètre manqué.
DEFAULT_LLM_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"


def aws_credentials_available() -> bool:
    """Des identifiants AWS sont-ils disponibles (variables, fichier, rôle de la machine ou de la tâche) ? Aucun appel à un service."""
    try:
        import boto3

        return boto3.session.Session().get_credentials() is not None
    except Exception:  # boto3 absent ou configuration illisible
        return False


def build_parser() -> Any:
    """LLMParser (Bedrock) si activé et disponible ; sinon interprétation par règles. Configuration par variables d'environnement :
    ALPHAFORGE_LLM (« 0 » pour désactiver), ALPHAFORGE_LLM_MODEL, AWS_REGION. Aucun identifiant AWS dans le code :
    boto3 lit le rôle de la machine (profil d'instance ou rôle de tâche)."""
    if os.environ.get("ALPHAFORGE_LLM", "1") == "0":
        return RuleBasedParser()
    if not aws_credentials_available():  # poste sans compte AWS (par exemple après l'événement) : règles, sans tentative à chaque demande
        return RuleBasedParser()
    try:
        client = BedrockClient(os.environ.get("ALPHAFORGE_LLM_MODEL") or DEFAULT_LLM_MODEL,
                               region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1")
    except Exception:  # boto3 absent ou configuration invalide : le noyau fonctionne sans Bedrock
        return RuleBasedParser()
    return LLMParser(client)


_ID = re.compile(r"^[a-z0-9_-]{1,40}$")


class _ScenarioParser:
    """Ajoute à la demande les poids saisis dans l'interface (formulaire de scénario), sans rien déduire.

    Les actifs et poids du formulaire s'ajoutent à ce que le texte contient ; en cas de conflit, la saisie explicite gagne.
    L'intention devient « scénario » : l'utilisateur a décrit une allocation.
    """

    def __init__(self, inner: Any, weights: Dict[str, float], rebalancing: Optional[str]):
        self._inner, self._weights, self._rebalancing = inner, dict(weights), rebalancing
        self.name = getattr(inner, "name", "rules")

    def parse(self, text: str, assets: List[Dict[str, Any]]):
        req = self._inner.parse(text, assets)
        known = {a["ticker"] for a in assets}
        weights = {t: w for t, w in {**req.weights, **self._weights}.items() if t in known}
        tickers = list(dict.fromkeys(list(weights) + [t for t in req.tickers if t not in weights]))
        return req.model_copy(update={
            "weights": weights, "tickers": tickers, "intent": "build_scenario" if len(tickers) >= 2 else req.intent,
            "rebalancing": self._rebalancing or req.rebalancing,
        })


class _CapturingExplainer:
    """Appelle l'agent d'explicabilité une seule fois et garde son `Explanation` structurée pour l'affichage."""

    def __init__(self, inner: Explainer):
        self._inner = inner
        self.last: Optional[Explanation] = None

    def __call__(self, profile: InvestorProfile, ledger) -> str:
        self.last = self._inner.explain(profile, ledger)
        return self.last.text


class _FirstAvailable:
    """Essaie chaque fournisseur dans l'ordre et rend le premier qui répond ; sinon l'erreur du premier (la plus parlante).
    Sert la chaîne « SEC en direct -> cache local -> instantané du dépôt » : une source qui manque se voit dans la lacune."""

    def __init__(self, *providers: Any):
        self._providers = [p for p in providers if p is not None]
        self.source_id = self._providers[0].source_id

    def fetch(self, kind, ticker, as_of):
        first = None
        for p in self._providers:
            try:
                return p.fetch(kind, ticker, as_of)
            except ProviderError as e:
                first = first or e
        raise first  # type: ignore[misc]


def build_enrichment_agent(sec_user_agent: Optional[str] = None, cache_dir: Optional[Path] = None, today: Optional[date] = None,
                           snapshot_dir: Optional[Path] = None) -> EnrichmentAgent:
    """Agent d'enrichissement avec les sources réellement disponibles ; une source absente donne une lacune explicite, jamais une valeur.

    - dépôts (`fetch_filings`) et fondamentaux (`fetch_fundamentals`, dernier rapport annuel XBRL) : SEC EDGAR en direct si un
      identifiant (nom et courriel) est fourni ; à défaut, ou si l'appel échoue, le cache local, puis l'instantané versionné
      dans `alphaforge_starter/enrichment_snapshot/` (démo hors ligne, données arrêtées au 12 novembre 2026) ;
    - ESG, nouvelles, composition des FNB : aucune source en direct n'est branchée ; seules des données déjà présentes dans
      le cache (`<cache>/<type>/<symbole>.json`) sont servies.
    `today` : date de collecte consignée sur les fiches source (l'agent n'a pas d'horloge).
    """
    root = Path(cache_dir) if cache_dir else ENRICHMENT_CACHE_DIR
    snap_root = Path(snapshot_dir) if snapshot_dir else ENRICHMENT_SNAPSHOT_DIR
    cache = CachedProvider(root, source_id="cache")
    snapshot = CachedProvider(snap_root, source_id="sec_edgar")
    providers: Dict[str, Any] = {}
    live = (CachingProvider(EdgarProvider(sec_user_agent.strip(), today or date.today()), cache)  # une instance : liste des déclarants lue une fois
            if sec_user_agent and sec_user_agent.strip() else None)
    for tool, kind in (("fetch_filings", "filings"), ("fetch_fundamentals", "fundamentals")):
        chain = [live, cache if (live is None and (root / kind).is_dir()) else None, snapshot if (snap_root / kind).is_dir() else None]
        if any(chain):
            providers[tool] = _FirstAvailable(*chain)
    for tool, kind in (("fetch_esg", "esg"), ("fetch_news", "news"), ("fetch_etf_holdings", "etf_holdings")):
        if (root / kind).is_dir():
            providers[tool] = cache
    return EnrichmentAgent(providers)


def enrichment_coverage(sec_user_agent: Optional[str] = None, cache_dir: Optional[Path] = None,
                        snapshot_dir: Optional[Path] = None) -> Dict[str, str]:
    """Pour chaque outil de collecte : « live », « cache », « snapshot » ou « absent ». Affiché tel quel : ce qui n'est pas branché se voit."""
    root = Path(cache_dir) if cache_dir else ENRICHMENT_CACHE_DIR
    snap_root = Path(snapshot_dir) if snapshot_dir else ENRICHMENT_SNAPSHOT_DIR
    out: Dict[str, str] = {}
    for tool, kind in (("fetch_filings", "filings"), ("fetch_esg", "esg"), ("fetch_fundamentals", "fundamentals"), ("fetch_news", "news"), ("fetch_etf_holdings", "etf_holdings")):
        sec = tool in ("fetch_filings", "fetch_fundamentals")
        if sec and sec_user_agent and sec_user_agent.strip():
            out[tool] = "live"
        elif (root / kind).is_dir():
            out[tool] = "cache"
        elif sec and (snap_root / kind).is_dir():
            out[tool] = "snapshot"
        else:
            out[tool] = "absent"
    return out


@dataclass
class ProfileInfo:
    profile_id: str
    display_name: str
    origin: str  # "exemple" ou "créé"


@dataclass
class Analysis:
    request_text: str
    profile: InvestorProfile
    as_of: date
    base_currency: str
    result: RunResult
    verdict: Optional[Dict[str, Any]]
    explanation: Optional[Explanation]
    enrichment_enabled: bool = False
    archive_notes: List[str] = field(default_factory=list)  # où le registre a été archivé (S3), ou pourquoi il ne l'a pas été


@dataclass
class Comparison:
    request_text: str
    left: Analysis
    right: Analysis


class AlphaForge:
    def __init__(self, store: DataStore, user_profiles_dir: Optional[Path] = None, parser: Optional[Any] = None,
                 archive: Optional[Any] = None):
        self.store = store
        self.parser = parser if parser is not None else build_parser()
        self.archive = archive if archive is not None else build_archive()
        self.assets: List[Dict[str, Any]] = store.assets.to_dict("records")
        self.user_dir = Path(user_profiles_dir) if user_profiles_dir else USER_PROFILES_DIR
        self.user_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_directory(cls, data_dir: Optional[Union[str, Path]] = None, **kw) -> "AlphaForge":
        return cls(DataStore.from_directory(data_dir), **kw)

    # ---- profils -----------------------------------------------------
    def list_profiles(self) -> List[ProfileInfo]:
        out: Dict[str, ProfileInfo] = {}
        for d, origin in ((PROFILES_DIR, "exemple"), (self.user_dir, "créé")):
            for f in sorted(Path(d).glob("*.json")):
                try:
                    p = load_profile(json.loads(f.read_text(encoding="utf-8")))
                except (ProfileError, json.JSONDecodeError):
                    continue
                out[p.profile_id] = ProfileInfo(p.profile_id, p.display_name or p.profile_id, origin)
        return sorted(out.values(), key=lambda i: i.profile_id)

    def get_profile(self, source: Union[str, Dict[str, Any], InvestorProfile]) -> InvestorProfile:
        if isinstance(source, str):
            for d in (self.user_dir, PROFILES_DIR):
                if (Path(d) / f"{source}.json").is_file():
                    return load_profile(source, profiles_dir=Path(d))
        return load_profile(source)

    def profile_origin(self, profile_id: str) -> Optional[str]:
        """« exemple » (livré avec le projet), « créé » (enregistré depuis l'interface), ou None s'il n'existe pas."""
        if (PROFILES_DIR / f"{profile_id}.json").is_file():
            return "exemple"
        return "créé" if (self.user_dir / f"{profile_id}.json").is_file() else None

    def save_profile(self, data: Dict[str, Any], overwrite: bool = False) -> InvestorProfile:
        """Valide puis enregistre un profil créé dans l'interface. Aucune modification de code n'est nécessaire.

        Un profil d'exemple n'est jamais remplacé (il serait masqué en silence) ; un profil créé n'est remplacé que sur demande.
        """
        profile = load_profile(data)
        if not _ID.match(profile.profile_id):
            raise ProfileError("invalid_profile_id", "Identifiant de profil : lettres minuscules, chiffres, tirets, 1 à 40 caractères.")
        origin = self.profile_origin(profile.profile_id)
        if origin == "exemple":
            raise ProfileError("profile_exists_example", f"« {profile.profile_id} » est un profil d'exemple livré avec le projet : choisir un autre identifiant.")
        if origin == "créé" and not overwrite:
            raise ProfileError("profile_exists", f"Un profil « {profile.profile_id} » existe déjà : confirmer le remplacement ou choisir un autre identifiant.")
        (self.user_dir / f"{profile.profile_id}.json").write_text(
            json.dumps(profile.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return profile

    def delete_profile(self, profile_id: str) -> None:
        """Supprime un profil créé depuis l'interface. Un profil d'exemple n'est jamais supprimé ; rien hors du dossier des profils créés.

        Lève ProfileError : `invalid_profile_id` (identifiant douteux, comme `../x`), `profile_not_found`, `profile_not_deletable` (exemple).
        """
        if not _ID.match(str(profile_id)):
            raise ProfileError("invalid_profile_id", f"Identifiant de profil invalide : {profile_id!r}.")
        origin = self.profile_origin(profile_id)
        if origin is None:
            raise ProfileError("profile_not_found", f"Profil introuvable : {profile_id!r}.")
        if origin == "exemple":
            raise ProfileError("profile_not_deletable", f"« {profile_id} » est un profil d'exemple livré avec le projet : il ne peut pas être supprimé.")
        path = (self.user_dir / f"{profile_id}.json").resolve()
        if path.parent != self.user_dir.resolve():  # ceinture et bretelles : jamais de suppression hors du dossier des profils créés
            raise ProfileError("invalid_profile_id", f"Identifiant de profil invalide : {profile_id!r}.")
        path.unlink()

    # ---- analyse -----------------------------------------------------
    def analyze(
        self,
        request_text: str,
        profile: Union[str, Dict[str, Any], InvestorProfile],
        as_of: Optional[date] = None,
        base_currency: Optional[str] = None,
        language: Optional[str] = None,
        weights: Optional[Dict[str, float]] = None,
        rebalancing: Optional[str] = None,
        on_entry: Optional[Callable[[Any], None]] = None,
        enrichment: bool = True,
        sec_user_agent: Optional[str] = None,
    ) -> Analysis:
        """`base_currency` et `language` choisis dans l'interface s'appliquent à une copie du profil pour cette analyse :
        l'orchestrateur transmet la devise aux outils, l'explicabilité rédige dans la langue du profil."""
        p = self.get_profile(profile)
        p = p.model_copy(update={"base_currency": (base_currency or p.base_currency).upper(), "language": language or p.language})
        base = p.base_currency
        as_of_r = as_of or self.store.last_date
        analyst = QuantAnalyst(self.store)
        explainer = _CapturingExplainer(Explainer())
        extra: Dict[str, Any] = {}
        if weights or rebalancing:  # scénario saisi dans l'interface : poids en fractions, jamais normalisés ici
            extra["parser"] = _ScenarioParser(self.parser, weights or {}, rebalancing)
        else:
            extra["parser"] = self.parser
        if on_entry is not None:
            extra["on_entry"] = on_entry  # trace en direct : chaque entrée du registre est transmise à l'écriture
        if enrichment:  # mode « Enrichi » (5 agents) ; sinon le noyau à 4 agents travaille seul
            extra["enrichment"] = build_enrichment_agent(sec_user_agent or os.environ.get("ALPHAFORGE_SEC_USER_AGENT"))
        orch = Orchestrator(analyst, governance=Governance(self.assets), explainer=explainer, as_of=as_of_r, **extra)
        result = orch.run(request_text, p, self.assets)
        verdicts = result.ledger.entries("verdict")
        verdict = verdicts[-1].payload if verdicts else None
        expl = explainer.last
        an = Analysis(request_text, p, as_of_r, base, result, verdict, expl, enrichment_enabled=enrichment)
        an.archive_notes = archive_notes(self.archive, an, p.language)
        return an

    def compare(self, request_text: str, left: Any, right: Any, as_of: Optional[date] = None, base_currency: Optional[str] = None,
                language: Optional[str] = None, enrichment: bool = True, sec_user_agent: Optional[str] = None) -> Comparison:
        """Même demande, deux profils : les données sont identiques, seules les limites dérivées changent.
        `enrichment` suit l'interrupteur de l'interface, comme pour une analyse simple."""
        kw = dict(enrichment=enrichment, sec_user_agent=sec_user_agent)
        return Comparison(request_text, self.analyze(request_text, left, as_of, base_currency, language, **kw),
                          self.analyze(request_text, right, as_of, base_currency, language, **kw))
