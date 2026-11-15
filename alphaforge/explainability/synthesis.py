"""Synthèse : quelques phrases placées juste après le verdict, qui relient ce verdict à ses raisons principales.

Règles (Agents.md, « Qui conclut ? ») :
- rédigée par gabarits (`config.TEMPLATES[lang]["synthesis"]`) à partir du verdict et des énoncés déjà vérifiés : aucun texte libre ;
- aucun fait nouveau : chaque chiffre cité figure déjà, avec la même preuve, dans un énoncé vérifié du corps ;
- étiquetée « synthèse », jamais « fait », et soumise au vérificateur comme le reste (preuve citée obligatoire, ton, provenance) ;
- l'incertitude est dans la synthèse elle-même (« ces mesures décrivent le passé ») ;
- pas de synthèse sans base : sans verdict, on n'en écrit pas ; un énoncé de synthèse rejeté est retiré, jamais réparé.

Elle constate des écarts (« NVDA dépasse des limites de risque de votre profil »), elle ne prescrit rien.
"""
from typing import List, Optional, Set, Tuple

from ..governance.models import AssetAssessment
from ..orchestrator.profile import InvestorProfile
from .compose import Builder, match_ref
from .config import TEMPLATES
from .ledger_read import LedgerView, seq_ref
from .models import Issue, Statement
from .provenance import Vocabulary
from .verifier import check_statement

SECTION = "summary"
_RISK_CODES = ("VOLATILITY_ABOVE_LIMIT", "DRAWDOWN_ABOVE_LIMIT")

FigureKey = Tuple[str, float, str]


def _key(ref: str, value: float, unit: str) -> FigureKey:
    return (ref, round(float(value), 12), unit)


class _Synth:
    def __init__(self, view: LedgerView, profile: InvestorProfile):
        self.view, self.lang = view, profile.language
        self.T = TEMPLATES[self.lang]
        self.S = self.T["synthesis"]
        self.vref = seq_ref(view.verdict_seq) if view.verdict_seq else None
        self.out: List[Statement] = []

    def add(self, text: str, b: Builder) -> None:
        self.out.append(Statement(sid=f"{SECTION}.syn{len(self.out) + 1}", section=SECTION, text=text, basis="synthesis",
                                  refs=list(b.refs), figures=list(b.figures), literals=list(b.literals)))

    def b(self, *refs: Optional[str]) -> Builder:
        b = Builder(self.lang)
        b.ref(self.vref, *refs)
        return b

    def asset(self, a: AssetAssessment) -> None:
        S, f = self.S, self.view.facts.get(a.ticker)
        blocks = [r for r in a.reasons if r.severity == "block"]
        risks = [r for r in a.reasons if r.code in _RISK_CODES]
        if blocks:
            b = self.b(*[i for r in blocks for i in r.evidence_ids])
            why = list(dict.fromkeys(S["why"].get(r.code, S["why"]["_other"]) for r in blocks))
            self.add(S["blocked"].format(t=b.lit(a.ticker), why=self.S["sep"].join(why)), b)
        elif risks:
            b = self.b(*[i for r in risks for i in r.evidence_ids])
            items = []
            for r in risks:
                m = r.measures
                value = b.num(m["value"], "pct", match_ref(self.view, r.evidence_ids, m["value"], self.vref))
                limit = b.num(m["limit"], "pct", self.vref)
                items.append(S["item_vol" if r.code == "VOLATILITY_ABOVE_LIMIT" else "item_dd"].format(value=value, limit=limit))
            self.add(S["exceeds"].format(t=b.lit(a.ticker), items=self.S["sep"].join(items)), b)
        elif f is None or not f.windows:
            b = self.b(*[i for r in a.reasons for i in r.evidence_ids])
            self.add(S["nodata"].format(t=b.lit(a.ticker)), b)
        elif a.status == "conforme":
            b = self.b(*[w.evidence_id for w in f.windows])
            self.add(S["within"].format(t=b.lit(a.ticker)), b)
        else:  # avertissement sans dépassement de limite : données, horizon, préférences
            b = self.b(*[i for r in a.reasons for i in r.evidence_ids])
            self.add(S["other"].format(t=b.lit(a.ticker), status=self.T["statuses"][a.status]), b)

    def run(self) -> List[Statement]:
        v = self.view.verdict
        if v is None:
            return []
        for a in v.assets:
            self.asset(a)
        if v.alternatives:
            ok = [x.ticker for x in v.alternatives if x.status == "conforme"]
            b = self.b()
            if ok:
                self.add(self.S["alt_ok"].format(lst=", ".join(b.lit(t) for t in ok)), b)
            else:
                self.add(self.S["alt_none"], b)
        if v.scope_refusals:
            self.add(self.S["scope"], self.b())
        # L'incertitude fait partie de la synthèse ; sans aucune mesure, on ne parle pas de « ces mesures ».
        measured = any(self.view.facts.get(a.ticker) and self.view.facts[a.ticker].windows for a in v.assets)
        self.add(self.S["close" if measured else "close_nodata"], self.b())
        return self.out


def build_synthesis(view: LedgerView, profile: InvestorProfile) -> List[Statement]:
    """Brouillon de synthèse, non étiqueté et non vérifié. Vide sans verdict."""
    return _Synth(view, profile).run()


def verify_synthesis(
    view: LedgerView, drafts: List[Statement], body: List[Statement], vocabulary: Optional[Vocabulary] = None
) -> Tuple[List[Statement], List[str], List[Issue]]:
    """Contrôles du vérificateur + règle « aucun fait nouveau ». Un énoncé fautif est retiré, jamais rétrogradé."""
    allowed: Set[FigureKey] = {_key(f.ref, f.value, f.unit) for s in body for f in s.figures}
    kept: List[Statement] = []
    removed: List[str] = []
    issues: List[Issue] = []
    for s in drafts:
        found = check_statement(view, s, vocabulary)
        for f in s.figures:
            if _key(f.ref, f.value, f.unit) not in allowed:
                found.append(Issue(sid=s.sid, code="synthesis_new_fact",
                                   message=f"Synthèse : le chiffre {f.shown} ne figure dans aucun énoncé vérifié du corps."))
        if found:
            removed.append(s.sid)
            issues += found
        else:
            kept.append(s)
    return kept, removed, issues


def insert_after_verdict(body: List[Statement], synthesis: List[Statement], view: LedgerView, profile: InvestorProfile) -> List[Statement]:
    """Place la synthèse juste après la phrase du verdict (à défaut, en fin de résumé)."""
    if not synthesis:
        return body
    T, v = TEMPLATES[profile.language], view.verdict
    verdict_text = T["verdict"].format(status=T["statuses"][v.status]) if v is not None else None
    idx = next((i for i, s in enumerate(body) if s.section == SECTION and s.text == verdict_text), None)
    if idx is None:
        idx = max((i for i, s in enumerate(body) if s.section == SECTION), default=-1)
    return body[: idx + 1] + synthesis + body[idx + 1:]
