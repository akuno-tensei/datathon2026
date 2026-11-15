"""Conclusion en langage simple, à la fin : gabarits seulement, aucun chiffre, étiquette « conclusion », vérifiée comme le reste."""
import re
from datetime import date

from helpers_expl import explain

from alphaforge.explainability import Statement, ledger_read
from alphaforge.explainability.conclusion import build_conclusion
from alphaforge.explainability.config import TEMPLATES
from alphaforge.explainability.label_content import label_content
from alphaforge.explainability.provenance import Vocabulary
from alphaforge.explainability.synthesis import verify_synthesis
from alphaforge.orchestrator import Orchestrator
from alphaforge.orchestrator.tools import load_profile

RISKY = {"risk_tolerance": "faible_modere", "horizon_years_min": 3, "horizon_years_max": 5}
C_FR = TEMPLATES["fr"]["conclusion"]


def concl(e):
    return [s for s in e.statements if s.section == "conclusion"]


def test_conclusion_is_last_labeled_and_cites_the_verdict(run):
    e = explain(run("Évalue HIGH", **RISKY))
    c = concl(e)
    assert c and e.statements[-len(c):] == c  # toujours à la fin
    assert all(s.label == "conclusion" and s.refs for s in c) and e.report.approved
    assert e.text.index("## En résumé") > e.text.index("## Hypothèses, limites et incertitudes")  # après les limites, dans le texte aussi
    assert c[0].text == C_FR["headline"]["avertissement"] and c[-1].text == C_FR["close"]


def test_conclusion_never_contains_a_number(run):
    for req, prof in (("Évalue HIGH", RISKY), ("Compare HIGH et CALM", {}), ("Évalue LOW.TO", {}), ("Évalue ZZZZ.TO", {})):
        for s in concl(explain(run(req, **prof))):
            assert not re.search(r"\d", s.text.replace("LOW.TO", "").replace("ZZZZ.TO", "")), s.text


def test_an_asset_over_the_limits_is_explained_in_plain_words(run):
    texts = [s.text for s in concl(explain(run("Évalue HIGH", **RISKY)))]
    line = next(t for t in texts if t.startswith("HIGH :"))
    assert C_FR["risk_vol"] in line and "pas une interdiction" in line


def test_a_compliant_asset_and_a_comparison(run):
    texts = [s.text for s in concl(explain(run("Évalue LOW.TO")))]
    assert texts[0] == C_FR["headline"]["conforme"] and C_FR["asset_ok"].format(t="LOW.TO") in texts
    texts = [s.text for s in concl(explain(run("Compare HIGH et CALM", **RISKY)))]
    assert C_FR["compare_intro"] in texts and any(t.startswith("HIGH :") for t in texts) and any(t.startswith("CALM :") for t in texts)


def test_a_blocked_asset_names_the_explicit_rule(run):
    texts = [s.text for s in concl(explain(run("Évalue HIGH", hard_exclusions=[{"kind": "sector", "value": "Tech"}])))]
    assert texts[0] == C_FR["headline"]["bloqué"]
    assert C_FR["asset_blocked"].format(t="HIGH", why=C_FR["why"]["EXCLUDED_SECTOR"]) in texts


def test_a_transaction_request_is_not_blamed_on_the_asset(run):
    texts = [s.text for s in concl(explain(run("Achète LOW.TO")))]
    assert texts[0] == C_FR["headline"]["scope_blocked"] and C_FR["scope"] in texts


def test_a_portfolio_scenario_gets_its_own_plain_summary(run):
    texts = [s.text for s in concl(explain(run("Construis un portefeuille avec LOW.TO et BND.TO", **RISKY)))]
    assert C_FR["pf_note"] in texts
    assert C_FR["pf_ok"] in texts or any(t.startswith("Le portefeuille simulé dépasse") for t in texts)


def test_english_and_no_verdict(run, analyst, assets, profile_dict):
    e = explain(run("Évalue HIGH", **RISKY), language="en")
    assert e.report.approved and "## In short" in e.text and concl(e)[0].text == TEMPLATES["en"]["conclusion"]["headline"]["avertissement"]
    r = Orchestrator(analyst, as_of=date(2023, 12, 29)).run("Évalue HIGH", profile_dict(), assets)  # sans gouvernance
    assert concl(explain(r)) == [] and "En résumé" not in explain(r).text


def test_a_conclusion_with_advice_or_a_new_number_is_removed(run):
    r = run("Évalue HIGH", **RISKY)
    e = explain(r)
    view = ledger_read(r.ledger)
    vocab = Vocabulary.build(TEMPLATES["fr"], view.vocab_strings)
    ref = [f"seq:{view.verdict_seq}"]
    bad = [label_content(Statement(sid="conclusion.9", section="conclusion", text=t, basis="conclusion", refs=ref))
           for t in ("En bref : achetez HIGH.", "HIGH est le placement idéal pour vous.")]
    kept, removed, issues = verify_synthesis(view, bad, [s for s in e.statements if s.section != "conclusion"], vocab)
    assert kept == [] and removed == ["conclusion.9", "conclusion.9"] and issues
    no_ref = label_content(Statement(sid="conclusion.8", section="conclusion", text=C_FR["close"], basis="conclusion", refs=[]))
    assert verify_synthesis(view, [no_ref], [], vocab)[1] == ["conclusion.8"]  # une conclusion sans preuve citée est retirée


def test_conclusion_drafts_are_built_from_the_verdict_only(run):
    r = run("Évalue HIGH", **RISKY)
    profile = load_profile(r.ledger.entries("profile")[0].payload["profile"])
    drafts = build_conclusion(ledger_read(r.ledger), profile)
    assert drafts and all(s.figures == [] and s.basis == "conclusion" for s in drafts)
