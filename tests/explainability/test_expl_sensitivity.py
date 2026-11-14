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


def test_correlation_statement_is_verified_and_keeps_sign(run):
    r = run("Compare HIGH et HIGH2", risk_tolerance="eleve", horizon_years_min=15, horizon_years_max=30)
    e = explain(r, experience="avance")
    corr = next(x for x in r.ledger.evidence() if x["tool"] == "correlation_matrix")
    (s,) = [x for x in e.statements if x.text.startswith("Corrélation des rendements")]
    assert s.refs == [corr["evidence_id"]] and any(f.unit == "corr" for f in s.figures)
    assert any(x.text.startswith("HIGH et HIGH2 ont des rendements très corrélés") for x in e.statements)
    assert e.report.approved


def test_corr_unit_formats_sign_and_two_decimals():
    from alphaforge.explainability.formatting import format_number, parse_shown, shown_value

    assert format_number(-0.3512, "corr", "fr") == "-0,35" and format_number(0.8734, "corr", "en") == "0.87"
    assert parse_shown(format_number(-0.3512, "corr", "fr")) == shown_value(-0.3512, "corr") == 0.35


def _scenario_statements(e):
    return [s for s in e.statements if s.text.startswith(("Scénario rétrospectif fictif", "Concentration du scénario", "Risque du scénario"))]


def test_portfolio_scenario_is_labelled_fictional_verified_and_discloses_equal_weights(run):
    r = run("Construis un scénario de portefeuille avec LOW.TO et CALM")
    e = explain(r, experience="avance")
    pf = next(x for x in r.ledger.evidence() if x["tool"] == "portfolio_metrics")
    main, conc, risk = _scenario_statements(e)
    assert {main.label, conc.label, risk.label} == {"scenario_fictif"} and main.refs == [pf["evidence_id"]]
    assert "rééquilibrés" in main.text and "LOW.TO 50,0 %" in main.text
    hyp = next(s for s in e.statements if "égaux par hypothèse" in s.text)
    assert hyp.label == "hypothese" and "pas une recommandation" in hyp.text
    assert any("pèse 50,0 % du portefeuille, pour un plafond de 20,0 %" in s.text for s in e.statements)  # raison de gouvernance
    assert e.report.approved and not e.report.removed


def test_portfolio_scenario_in_english_and_beginner_note(run):
    r = run("Construis un scénario de portefeuille avec LOW.TO et CALM")
    en = explain(r, language="en", experience="avance")
    assert any(s.text.startswith("Fictional retrospective scenario") for s in en.statements) and en.report.approved
    deb = explain(r, experience="debutant")
    assert any("ratio de diversification compare" in s.text for s in deb.statements)
    assert not any("ratio de diversification compare" in s.text for s in explain(r, experience="avance").statements)


def test_no_portfolio_statements_without_a_portfolio_tool_result(run):
    assert _scenario_statements(explain(run("Compare LOW.TO et CALM"))) == []
