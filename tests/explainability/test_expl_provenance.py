"""Contrôle structurel de provenance (verifier, contrôle 7) : tout mot vient d'un gabarit ou du registre.

Il attrape ce que la liste de mots interdits laisse passer : recommandation déguisée, faute de frappe, caractère d'apparence
identique. Et il ne retire aucun énoncé produit par les gabarits.
"""
import pytest

from helpers_expl import explain

from alphaforge.explainability import Statement, label_content, ledger_read
from alphaforge.explainability import explainer as explainer_mod
from alphaforge.explainability.config import TEMPLATES
from alphaforge.explainability.provenance import Vocabulary, provenance_issues
from alphaforge.explainability.verifier import advice_wording, claim_verifier

# Aucune de ces phrases ne contient un mot de la liste interdite : seul le contrôle de provenance les arrête.
DISGUISED = [
    "CALM est le placement idéal pour vous.",          # recommandation déguisée
    "\u0410chetez HIGH.",                               # « А » cyrillique : invisible à l'œil, inconnu de la liste
    "Vous devriiez acheter HIGH.",                      # faute de frappe
    "HIGH progressera sûrement.",                       # prédiction sans verbe interdit
    "Ne ratez pas cette occasion sur HIGH.",            # incitation
]


def _inject(monkeypatch, text):
    real = explainer_mod.compose_explanation

    def with_extra(view, profile, exclude=()):
        ref = view.evidence[0]["evidence_id"]
        extra = Statement(sid="fit.99", section="fit", text=text, refs=[ref])
        return real(view, profile, exclude) + ([] if "fit.99" in exclude else [extra])

    monkeypatch.setattr(explainer_mod, "compose_explanation", with_extra)


@pytest.mark.parametrize("text", DISGUISED)
def test_disguised_advice_is_removed_even_without_a_forbidden_word(run, monkeypatch, text):
    assert advice_wording(text) == []  # la liste de mots interdits ne voit rien
    r = run("Évalue HIGH")
    _inject(monkeypatch, text)
    e = explain(r)
    assert text not in e.text and e.report.removed == ["fit.99"]
    assert "unsourced_wording" in {i.code for i in e.report.issues}


def test_the_request_text_never_enters_the_allowed_vocabulary(run, monkeypatch):
    r = run("Évalue HIGH, le placement idéal pour moi")  # l'utilisateur écrit lui-même les mots de la recommandation
    _inject(monkeypatch, "HIGH est le placement idéal pour vous.")
    e = explain(r)
    assert e.report.removed == ["fit.99"]


@pytest.mark.parametrize("request_text", ["Évalue HIGH", "Compare HIGH et CALM", "Dois-je acheter LOW.TO ?", "Évalue INCONNU"])
@pytest.mark.parametrize("lang", ["fr", "en"])
@pytest.mark.parametrize("experience", ["debutant", "intermediaire", "avance"])
def test_every_template_sentence_passes(run, request_text, lang, experience):
    e = explain(run(request_text), language=lang, experience=experience)
    assert "unsourced_wording" not in {i.code for i in e.report.issues}, [i.message for i in e.report.issues]


def test_quotes_must_come_from_the_ledger_or_a_template(run):
    view = ledger_read(run("Évalue HIGH").ledger)
    vocab = Vocabulary.build(TEMPLATES["fr"], view.vocab_strings)
    risk = view.profile["risk_tolerance"]
    assert provenance_issues(f"Profil appliqué : tolérance au risque « {risk} ».", [], vocab) == []
    assert any("citation" in p for p in provenance_issues("Profil appliqué : « foncez ».", [], vocab))


def test_direct_calls_without_vocabulary_keep_the_six_original_checks(run):
    view = ledger_read(run("Évalue HIGH").ledger)
    s = label_content(Statement(sid="t.1", section="fit", text="Texte libre de test.", refs=[view.evidence[0]["evidence_id"]]))
    kept, rep = claim_verifier(view, [s])
    assert rep.approved and len(kept) == 1  # sans vocabulaire, le contrôle 7 ne s'applique pas
