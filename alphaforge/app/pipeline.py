"""Assemblage des quatre agents sur les données réelles, sans logique d'interface.

`AlphaForge.analyze` exécute orchestrateur -> analyste -> gouvernance -> explicabilité et renvoie une `Analysis`.
Aucune règle propre à un persona : les profils sont des données (JSON), y compris ceux créés depuis l'interface.
"""
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..explainability import Explainer, Explanation
from ..governance import Governance
from ..orchestrator import InvestorProfile, Orchestrator, ProfileError, QuantAnalyst, RunResult, load_profile
from ..orchestrator.tools import PROFILES_DIR
from ..quant import DataStore

USER_PROFILES_DIR = Path(__file__).resolve().parent / "profiles" / "user"  # profils créés depuis l'interface (données locales)
_ID = re.compile(r"^[a-z0-9_-]{1,40}$")


class _CapturingExplainer:
    """Appelle l'agent d'explicabilité une seule fois et garde son `Explanation` structurée pour l'affichage."""

    def __init__(self, inner: Explainer):
        self._inner = inner
        self.last: Optional[Explanation] = None

    def __call__(self, profile: InvestorProfile, ledger) -> str:
        self.last = self._inner.explain(profile, ledger)
        return self.last.text


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


@dataclass
class Comparison:
    request_text: str
    left: Analysis
    right: Analysis


class AlphaForge:
    def __init__(self, store: DataStore, user_profiles_dir: Optional[Path] = None):
        self.store = store
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

    def save_profile(self, data: Dict[str, Any]) -> InvestorProfile:
        """Valide puis enregistre un profil créé dans l'interface. Aucune modification de code n'est nécessaire."""
        profile = load_profile(data)
        if not _ID.match(profile.profile_id):
            raise ProfileError("invalid_profile_id", "Identifiant de profil : lettres minuscules, chiffres, tirets, 1 à 40 caractères.")
        (self.user_dir / f"{profile.profile_id}.json").write_text(
            json.dumps(profile.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return profile

    # ---- analyse -----------------------------------------------------
    def analyze(
        self,
        request_text: str,
        profile: Union[str, Dict[str, Any], InvestorProfile],
        as_of: Optional[date] = None,
        base_currency: Optional[str] = None,
        language: Optional[str] = None,
    ) -> Analysis:
        """`base_currency` et `language` choisis dans l'interface s'appliquent à une copie du profil pour cette analyse :
        l'orchestrateur transmet la devise aux outils, l'explicabilité rédige dans la langue du profil."""
        p = self.get_profile(profile)
        p = p.model_copy(update={"base_currency": (base_currency or p.base_currency).upper(), "language": language or p.language})
        base = p.base_currency
        as_of_r = as_of or self.store.last_date
        analyst = QuantAnalyst(self.store)
        explainer = _CapturingExplainer(Explainer())
        orch = Orchestrator(analyst, governance=Governance(self.assets), explainer=explainer, as_of=as_of_r)
        result = orch.run(request_text, p, self.assets)
        verdicts = result.ledger.entries("verdict")
        verdict = verdicts[-1].payload if verdicts else None
        expl = explainer.last
        return Analysis(request_text, p, as_of_r, base, result, verdict, expl)

    def compare(self, request_text: str, left: Any, right: Any, as_of: Optional[date] = None, base_currency: Optional[str] = None,
                language: Optional[str] = None) -> Comparison:
        """Même demande, deux profils : les données sont identiques, seules les limites dérivées changent."""
        return Comparison(request_text, self.analyze(request_text, left, as_of, base_currency, language),
                          self.analyze(request_text, right, as_of, base_currency, language))
