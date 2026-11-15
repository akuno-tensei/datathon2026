"""Conclusion : un résumé en langage simple, placé à la fin de l'explication, pour un lecteur débutant.

Elle reprend ce que disent le verdict et ses raisons, sans chiffre et sans jargon, selon la demande :
évaluer un actif, comparer des actifs, simuler un portefeuille. Règles, les mêmes que la synthèse (`synthesis.py`) :
- rédigée par gabarits (`config.TEMPLATES[lang]["conclusion"]`) à partir du verdict seul : aucun texte libre ;
- aucun chiffre, donc aucun fait nouveau ; chaque phrase cite le verdict comme preuve ;
- étiquetée « conclusion », vérifiée comme le reste (preuve citée, ton, provenance) ; une phrase rejetée est retirée ;
- elle constate, elle ne prescrit rien ; elle rappelle que le passé ne prédit pas l'avenir. Sans verdict, pas de conclusion.
"""
from typing import List

from ..governance.models import AssetAssessment
from ..orchestrator.profile import InvestorProfile
from .compose import Builder
from .config import TEMPLATES
from .ledger_read import LedgerView, seq_ref
from .models import Statement

SECTION = "conclusion"
_BLOCK_WHY = ("EXCLUDED_ASSET_CLASS", "EXCLUDED_SECTOR", "EXCLUDED_TICKER", "CLASS_NOT_PERMITTED", "EXCLUDED_THEME_CONTROVERSY")
_PORTFOLIO = ("PORTFOLIO_WEIGHT_ABOVE_CAP", "PORTFOLIO_CLASS_CAP", "PORTFOLIO_VOLATILITY_ABOVE_LIMIT", "PORTFOLIO_DRAWDOWN_ABOVE_LIMIT")


class _Conclusion:
    def __init__(self, view: LedgerView, profile: InvestorProfile):
        self.view, self.lang = view, profile.language
        self.C = TEMPLATES[self.lang]["conclusion"]
        self.vref = seq_ref(view.verdict_seq) if view.verdict_seq else None
        self.out: List[Statement] = []

    def add(self, text: str, b: Builder) -> None:
        self.out.append(Statement(sid=f"{SECTION}.{len(self.out) + 1}", section=SECTION, text=text, basis="conclusion",
                                  refs=list(b.refs), figures=[], literals=list(b.literals)))

    def b(self) -> Builder:
        b = Builder(self.lang)
        b.ref(self.vref)
        return b

    def asset(self, a: AssetAssessment) -> None:
        C, f = self.C, self.view.facts.get(a.ticker)
        codes = [r.code for r in a.reasons]
        b = self.b()
        t = b.lit(a.ticker)
        blocks = [c for c in codes if c in _BLOCK_WHY]
        if blocks:
            why = C["sep"].join(dict.fromkeys(C["why"][c] for c in blocks))
            self.add(C["asset_blocked"].format(t=t, why=why), b)
            return
        if f is None or not f.windows:
            self.add(C["asset_nodata"].format(t=t), b)
            return
        parts = []
        if "VOLATILITY_ABOVE_LIMIT" in codes:
            parts.append(C["risk_vol"])
        if "DRAWDOWN_ABOVE_LIMIT" in codes:
            parts.append(C["risk_dd"])
        if "RECOVERY_LONGER_THAN_HORIZON" in codes:
            parts.append(C["risk_recovery"])
        if parts:
            self.add(C["asset_risky"].format(t=t, items=C["sep"].join(parts)), b)
        elif a.status == "conforme":
            self.add(C["asset_ok"].format(t=t), b)
        else:
            self.add(C["asset_partial"].format(t=t), b)

    def portfolio(self) -> bool:
        v = self.view.verdict
        pf = [e for e in self.view.evidence if e.get("tool") == "portfolio_metrics"]
        if pf and all(e.get("status") == "refused" for e in pf):
            self.add(self.C["pf_refused"], self.b())  # le scénario n'a pas pu être rejoué : les actifs sont décrits un par un
            return False
        if not pf:
            return False
        found = [r.code for r in v.global_reasons if r.code in _PORTFOLIO]
        b = self.b()
        if found:
            items = self.C["sep"].join(dict.fromkeys(self.C["pf"][c] for c in found))
            self.add(self.C["pf_over"].format(items=items), b)
        else:
            self.add(self.C["pf_ok"], b)
        self.add(self.C["pf_note"], self.b())
        return True

    def run(self) -> List[Statement]:
        v = self.view.verdict
        if v is None:
            return []
        C = self.C
        intent = (self.view.request or {}).get("intent")
        # Bloqué seulement par la demande (transaction) : le dire, plutôt que de laisser croire que l'actif est en cause.
        only_scope = (v.status == "bloqué" and all(a.status != "bloqué" for a in v.assets)
                      and all(r.severity != "block" for r in v.global_reasons))
        self.add(C["headline"]["scope_blocked" if only_scope else v.status], self.b())
        is_portfolio = intent == "build_scenario" and self.portfolio()
        if not is_portfolio:
            if len(v.assets) >= 2:
                self.add(C["compare_intro"], self.b())
            for a in v.assets:
                self.asset(a)
        for r in v.global_reasons:
            if r.code == "HIGH_CORRELATION" and r.measures.get("a") and r.measures.get("b"):
                b = self.b()
                self.add(C["correlated"].format(a=b.lit(r.measures["a"]), b=b.lit(r.measures["b"])), b)
        if v.alternatives:
            ok = [x.ticker for x in v.alternatives if x.status == "conforme"]
            b = self.b()
            if ok:
                self.add(C["alt_ok"].format(lst=", ".join(b.lit(t) for t in ok)), b)
            else:
                self.add(C["alt_none"], b)
        if v.scope_refusals:
            self.add(C["scope"], self.b())
        self.add(C["close"], self.b())
        return self.out


def build_conclusion(view: LedgerView, profile: InvestorProfile) -> List[Statement]:
    """Brouillon de conclusion, non étiqueté et non vérifié. Vide sans verdict."""
    return _Conclusion(view, profile).run()


def conclusion_statements(statements: List[Statement]) -> List[Statement]:
    return [s for s in statements if s.section == SECTION]
