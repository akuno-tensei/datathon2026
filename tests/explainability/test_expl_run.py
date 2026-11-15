"""Explicabilité de bout en bout : branchée sur l'orchestrateur, explication vérifiée, adaptée au profil, sans logique par persona."""
import re

import pytest

from helpers_expl import body_lines, explain

from alphaforge.explainability.config import LABELS

# Demandes représentatives : actif conforme, dépassement, krach, hors univers, exclusion, préférence ESG, prédiction, scénario.
CASES = [
    ("Évalue LOW.TO", {}),
    ("Évalue HIGH", {"risk_tolerance": "faible_modere", "horizon_years_min": 3, "horizon_years_max": 5}),
    ("Évalue CRASH", {}),
    ("Évalue ZZZZ.TO", {}),
    ("Évalue HIGH", {"hard_exclusions": [{"kind": "sector", "value": "Tech"}]}),
    ("Évalue COIN-USD", {"risk_tolerance": "faible"}),
    ("Évalue LOW.TO et ESGV", {"soft_preferences": [{"kind": "theme", "value": "esg"}]}),
    ("Prédis LOW.TO l'an prochain", {}),
    ("Compare MID et HIGH", {}),
    ("Construis un portefeuille avec LOW.TO et BND.TO", {}),
]


# ---- branchement --------------------------------------------------------


def test_explainer_completes_the_run(run):
    r = run("Évalue LOW.TO")
    assert r.status == "complete" and r.pending_agents == []
    assert r.answer and r.answer.startswith("# ")


@pytest.mark.parametrize("text,profile", CASES)
def test_every_case_passes_verification_without_removal(run, text, profile):
    """Les gabarits ne doivent produire aucun énoncé que le vérificateur rejette."""
    e = explain(run(text, **profile))
    assert e.report.approved, e.report.issues
    assert e.report.removed == [] and e.report.attempts == 1


@pytest.mark.parametrize("text,profile", CASES)
def test_every_line_is_labeled(run, text, profile):
    r = run(text, **profile)
    labels = "|".join(re.escape(x) for x in LABELS["fr"].values())
    lines = body_lines(r.answer)
    assert lines and all(re.match(rf"- \[({labels})\] ", line) for line in lines)


def test_explainer_writes_a_note_to_the_ledger(run):
    r = run("Évalue LOW.TO")
    notes = [e for e in r.ledger.entries("note") if e.agent == "explicabilite"]
    assert len(notes) == 1 and notes[0].payload["code"] == "explanation_verified"


def test_deterministic(run):
    assert run("Évalue HIGH").answer == run("Évalue HIGH").answer


# ---- contenu fidèle au registre -----------------------------------------


def test_figures_match_verdict_measures(run):
    r = run("Évalue HIGH", risk_tolerance="faible_modere", horizon_years_min=3, horizon_years_max=5)
    v = r.ledger.entries("verdict")[-1].payload
    (vol,) = [x for x in v["assets"][0]["reasons"] if x["code"] == "VOLATILITY_ABOVE_LIMIT"]
    e = explain(r)
    (st,) = [s for s in e.statements if "dépasse la limite de volatilité" in s.text and s.text.startswith("HIGH ")]
    shown = {f.value for f in st.figures}
    assert vol["measures"]["value"] in shown and vol["measures"]["limit"] in shown
    assert st.label == "fait" and st.refs


def test_verdict_status_is_stated(run):
    r = run("Évalue HIGH", hard_exclusions=[{"kind": "sector", "value": "Tech"}])
    assert r.ledger.entries("verdict")[-1].payload["status"] == "bloqué"
    assert "Verdict de la gouvernance : bloqué." in r.answer
    assert "HIGH est exclu par le profil (secteur : Tech)." in r.answer


def test_out_of_universe_is_explained_and_not_covered(run):
    e = explain(run("Évalue ZZZZ.TO"))
    assert any("ZZZZ.TO est hors de l'univers" in s.text for s in e.statements)
    assert any("ZZZZ.TO" in s.text for s in e.limits.not_covered)


def test_prediction_refusal_is_explained(run):
    a = run("Prédis LOW.TO l'an prochain").answer
    assert "## Périmètre" in a and "Hors périmètre" in a


def test_scenario_request_is_labeled_fictional(run):
    e = explain(run("Construis un portefeuille avec LOW.TO et BND.TO"))
    assert any(s.label == "scenario_fictif" for s in e.statements)
    assert "[Scénario fictif]" in e.text


def test_alternatives_are_explained(run):
    r = run("Évalue HIGH", risk_tolerance="faible_modere", horizon_years_min=3, horizon_years_max=5)
    assert "## Alternatives évaluées" in r.answer
    assert "CALM a été évalué comme alternative à HIGH" in r.answer


# ---- adaptation au profil (langue, expérience), sans branche par persona -----


def test_language_switches_templates_and_labels(run):
    r = run("Évalue HIGH")
    fr, en = explain(r, language="fr").text, explain(r, language="en").text
    assert "[Fait]" in fr and "Ce que montrent les données" in fr
    assert "[Fact]" in en and "What the data show" in en and "[Fait]" not in en
    assert "47,7 %" in fr and "47.7%" in en  # même valeur, formatée selon la langue


def test_beginner_gets_definitions_and_no_sources(run):
    r = run("Évalue HIGH")
    deb, adv = explain(r, experience="debutant").text, explain(r, experience="avance").text
    assert "c'est sa volatilité" in deb and "(sources :" not in deb
    assert "(sources :" in adv and "ev_" in adv and "base 252" in adv


def test_beginner_sees_fewer_method_assumptions(run):
    r = run("Évalue HIGH")
    deb, adv = explain(r, experience="debutant"), explain(r, experience="avance")
    assert len(deb.limits.assumptions) == 2 < len(adv.limits.assumptions)
    assert deb.limits.limits == adv.limits.limits or len(deb.limits.limits) == len(adv.limits.limits)  # limites jamais tronquées


def test_same_data_same_facts_whatever_the_profile_name(run):
    """Seuls langue et expérience changent la forme : l'identifiant du profil n'a aucun effet."""
    a = run("Évalue HIGH", profile_id="robert").answer
    b = run("Évalue HIGH", profile_id="inconnu").answer
    assert a == b


def test_limits_always_include_past_and_simulation(run):
    e = explain(run("Évalue LOW.TO"))
    texts = [s.text for s in e.limits.statements()]
    assert any("Le passé ne prédit pas l'avenir" in t for t in texts)
    assert any("simulation" in t for t in texts)


# ---- exclusion dont la valeur n'existe pas dans l'univers ------------------


def test_unknown_exclusion_value_is_explained_in_both_languages(run):
    r = run("Évalue HIGH", hard_exclusions=[{"kind": "sector", "value": "Technologie"}])
    fr, en = explain(r, language="fr"), explain(r, language="en")
    assert fr.report.approved and en.report.approved, (fr.report.issues, en.report.issues)
    assert "L'exclusion « secteur : Technologie » ne correspond à aucun actif" in fr.text
    assert "Valeurs proches dans l'univers : Tech." in fr.text
    assert 'The exclusion "sector: Technologie" matches no asset' in en.text and "Close values in the universe: Tech." in en.text
