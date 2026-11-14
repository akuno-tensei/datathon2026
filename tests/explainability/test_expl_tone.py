"""Contrôle de ton du vérificateur : aucune tournure de conseil, de prédiction ou de promesse dans l'explication.

Agents.md : « Qui conclut ? Si le système dit « achète ceci », c'est un conseiller (hors cadre). »
"""
import pytest

from helpers_expl import explain

from alphaforge.explainability import Statement, claim_verifier, label_content, ledger_read
from alphaforge.explainability import explainer as explainer_mod
from alphaforge.explainability.verifier import advice_wording

ADVICE = [
    "Achetez HIGH.",
    "Vous devriez privilégier CALM.",
    "Je vous recommande de garder LOW.TO.",
    "Il vaut mieux acheter BND.TO.",
    "HIGH va monter.",
    "Un placement garanti.",
    "C'est le meilleur choix pour ce profil.",
    "Achète CALM dès maintenant.",
    "You should buy HIGH.",
    "We recommend CALM.",
    "HIGH will rise.",
    "A risk-free asset.",
]

NEUTRAL = [
    "Formuler un conseil personnalisé (« achète » ou « n'achète pas ») est hors périmètre.",
    "HIGH dépasse la limite de volatilité du profil.",
    "Aucune transaction réelle n'est possible : AlphaForge est une simulation.",
    "AlphaForge is a decision-support simulation: no real transaction, no personalised advice, no return forecast.",
    "Objectif du profil : eviter_controverses.",
    "Le rachat d'actions et les ventes passées ne sont pas couverts.",
]


@pytest.mark.parametrize("text", ADVICE)
def test_advice_wording_is_detected(text):
    assert advice_wording(text)


@pytest.mark.parametrize("text", NEUTRAL)
def test_neutral_or_quoted_wording_is_not_flagged(text):
    assert advice_wording(text) == []


@pytest.fixture
def view(run):
    return ledger_read(run("Évalue HIGH").ledger)


def stmt(text, view, basis="evidence"):
    ref = view.evidence[0]["evidence_id"]
    return label_content(Statement(sid="t.1", section="fit", text=text, basis=basis, refs=[ref]))


@pytest.mark.parametrize("basis", ["evidence", "assumption", "scenario"])
def test_advice_statement_is_removed_whatever_its_label(view, basis):
    kept, rep = claim_verifier(view, [stmt("Vous devriez privilégier CALM.", view, basis)])
    assert kept == [] and rep.removed == ["t.1"] and not rep.approved
    assert [i.code for i in rep.issues] == ["advice_wording"]


def test_quoted_mention_is_kept(view):
    kept, rep = claim_verifier(view, [stmt("Formuler un conseil (« achète » ou « n'achète pas ») est hors périmètre.", view)])
    assert rep.approved and len(kept) == 1


def test_scope_refusal_for_advice_request_survives(run):
    """Le refus de la gouvernance cite « achète » : il doit rester dans l'explication."""
    e = explain(run("Dois-je acheter LOW.TO ?"))
    assert e.report.approved, e.report.issues
    assert any(s.section == "scope" and "achète" in s.text for s in e.statements)


def test_advice_injected_in_draft_is_removed_from_final_text(run, monkeypatch):
    r = run("Évalue HIGH")
    real = explainer_mod.compose_explanation

    def with_advice(view, profile, exclude=()):
        ref = view.evidence[0]["evidence_id"]
        bad = Statement(sid="fit.99", section="fit", text="Vous devriez privilégier CALM.", refs=[ref])
        return real(view, profile, exclude) + ([] if "fit.99" in exclude else [bad])

    monkeypatch.setattr(explainer_mod, "compose_explanation", with_advice)
    e = explain(r)
    assert "Vous devriez" not in e.text and e.report.removed == ["fit.99"]
    assert "advice_wording" in {i.code for i in e.report.issues}
