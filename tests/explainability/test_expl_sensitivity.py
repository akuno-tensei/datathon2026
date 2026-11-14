"""L'explication cite la sensibilité à la période mesurée par stress_window, chiffres vérifiés contre cette preuve."""
from helpers_expl import explain


def _sens(e):
    return [s for s in e.statements if s.text.startswith(("Sensibilité à la période", "Sensitivity to the period"))]


def test_sensitivity_statement_cites_stress_window_evidence(run):
    r = run("Évalue HIGH")
    e = explain(r, experience="intermediaire")
    stress = next(x for x in r.ledger.evidence() if x["tool"] == "stress_window")
    (s,) = _sens(e)
    assert s.refs == [stress["evidence_id"]] and s.label == "fait" and s.section == "data"
    assert {f.ref for f in s.figures} == {stress["evidence_id"]}
    assert stress["value"]["verdict"] in s.text and e.report.approved  # tous les chiffres vérifiés par claim_verifier


def test_sensitivity_statement_in_english_and_beginner_note(run):
    r = run("Évalue HIGH")
    en = _sens(explain(r, language="en", experience="avance"))
    assert en and en[0].text.startswith("Sensitivity to the period")
    deb = _sens(explain(r, experience="debutant"))[0].text
    verdict = next(x for x in r.ledger.evidence() if x["tool"] == "stress_window")["value"]["verdict"]
    assert ("un seul chiffre ne la résume pas" in deb) == (verdict == "sensible")
