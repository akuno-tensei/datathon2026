"""limits_builder et ledger_read : hypothèses, limites, incertitudes et non-couvert tirés du registre seul."""
from helpers_expl import explain

from alphaforge.explainability import ledger_read
from alphaforge.orchestrator import Ledger


def test_ledger_read_takes_last_verdict_and_indexes_refs(run):
    r = run("Évalue HIGH", risk_tolerance="faible_modere", horizon_years_min=3, horizon_years_max=5)
    verdicts = r.ledger.entries("verdict")
    assert len(verdicts) >= 2  # boucle de retour : le dernier verdict fait foi
    view = ledger_read(r.ledger)
    assert view.verdict_seq == verdicts[-1].seq
    assert all(view.has_ref(e["evidence_id"]) for e in view.evidence)
    assert view.has_ref(f"seq:{verdicts[-1].seq}")


def test_empty_ledger_is_readable():
    view = ledger_read(Ledger())
    assert view.verdict is None and view.evidence == [] and view.facts == {}


def test_method_assumptions_and_tool_limits_are_taken_verbatim(run):
    r = run("Évalue HIGH")
    e = explain(r, experience="avance")
    ev_assumptions = {a for ev in r.ledger.evidence() for a in ev.get("assumptions", [])}
    ev_limits = {x for ev in r.ledger.evidence() for x in ev.get("limits", [])}
    assert {s.text for s in e.limits.assumptions} == ev_assumptions
    assert {s.text for s in e.limits.limits} == ev_limits
    assert all(s.label == "hypothese" for s in e.limits.assumptions)


def test_unrecovered_drawdown_is_an_uncertainty(run):
    e = explain(run("Évalue CRASH"))
    assert any("pas récupéré" in s.text for s in e.limits.uncertainties)


def test_unavailable_tool_is_not_covered(run):
    r = run("Compare MID et HIGH")
    notes = [n.payload for n in r.ledger.entries("note") if n.payload.get("code") == "tool_unavailable"]
    assert notes, "correlation_matrix devrait être consigné comme indisponible"
    e = explain(r)
    assert {n["message"] for n in notes} <= {s.text for s in e.limits.not_covered}


def test_theme_without_data_is_not_covered(run):
    e = explain(run("Évalue LOW.TO et ESGV", soft_preferences=[{"kind": "theme", "value": "esg"}]))
    assert any("« esg »" in s.text and "LOW.TO" in s.text for s in e.limits.not_covered)
    # ESGV n'est qu'un substitut déclaré : énoncé étiqueté hypothèse, pas fait.
    (proxy,) = [s for s in e.statements if "substitut déclaré" in s.text]
    assert proxy.label == "hypothese"


def test_window_sensitivity_shown_only_when_visible(run):
    e = explain(run("Évalue CRASH"))
    for s in e.limits.uncertainties:
        if "selon la fenêtre" in s.text:
            lo, hi = s.figures
            assert lo.shown != hi.shown
