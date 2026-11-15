"""Agent 4 : explicabilité. Rédige l'explication finale à partir du seul registre, puis la fait vérifier.

Boucle : rédaction -> vérification -> (si des énoncés sont rejetés) nouvelle rédaction sans ces énoncés -> vérification.
Aucun calcul, aucun rappel de mémoire : tout passe par `ledger_read`.
Appelable : `Explainer()(profile, ledger) -> str`, compatible avec `Orchestrator(explainer=...)`.
"""
from typing import List, Optional, Set

from ..orchestrator.ledger import Ledger
from ..orchestrator.profile import InvestorProfile
from .compose import compose_explanation, render
from .config import TEMPLATES, ExplainConfig
from .label_content import label_all
from .ledger_read import ledger_read
from .limits import limits_builder
from .models import Explanation, Issue, Limits, VerificationReport
from .provenance import Vocabulary
from .synthesis import build_synthesis, insert_after_verdict, verify_synthesis
from .verifier import claim_verifier

AGENT = "explicabilite"


class Explainer:
    def __init__(self, config: Optional[ExplainConfig] = None):
        self._cfg = config or ExplainConfig()

    def explain(self, profile: InvestorProfile, ledger: Ledger) -> Explanation:
        view = ledger_read(ledger)
        # Liste blanche de rédaction : gabarits de la langue + chaînes du registre (sans le texte libre de la demande).
        vocab = Vocabulary.build(TEMPLATES.get(profile.language, TEMPLATES["fr"]), view.vocab_strings) if self._cfg.provenance_check else None
        exclude: Set[str] = set()
        removed_all: List[str] = []
        issues_all: List[Issue] = []
        attempt = 0
        while True:
            attempt += 1
            body = compose_explanation(view, profile, exclude)
            lim = limits_builder(view, profile, self._cfg, exclude)
            cleaned, report = claim_verifier(view, label_all(body + lim.statements()), attempts=attempt, vocabulary=vocab)
            removed_all += [s for s in report.removed if s not in removed_all]
            issues_all += report.issues
            if report.approved or not report.removed or attempt >= self._cfg.max_attempts:
                break
            exclude |= set(report.removed)  # le vérificateur renvoie l'explication à la rédaction

        limit_keys = {"assumptions", "limits", "uncertainties", "not_covered"}
        body_ok = [s for s in cleaned if s.section not in limit_keys]
        limits = Limits.from_statements([s for s in cleaned if s.section in limit_keys])

        # Synthèse : rédigée après la vérification du corps, à partir de ses seuls énoncés retenus, puis vérifiée à son tour.
        syn_kept, syn_removed, syn_issues = verify_synthesis(view, label_all(build_synthesis(view, profile)), body_ok, vocab)
        body_ok = insert_after_verdict(body_ok, syn_kept, view, profile)
        removed_all += syn_removed
        issues_all += syn_issues

        final = VerificationReport(
            approved=report.approved and not removed_all,
            attempts=attempt, verified=report.verified + len(syn_kept), removed=removed_all, downgraded=report.downgraded,
            issues=issues_all,
        )
        text = render(body_ok, limits, profile, final)
        ledger.write(
            "note", AGENT,
            {"code": "explanation_verified" if final.approved else "explanation_repaired",
             "message": "Explication vérifiée contre le registre.", "removed": final.removed, "downgraded": final.downgraded},
        )
        return Explanation(language=profile.language, experience=profile.experience, statements=body_ok, limits=limits, report=final, text=text)

    def __call__(self, profile: InvestorProfile, ledger: Ledger) -> str:
        return self.explain(profile, ledger).text
