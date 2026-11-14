"""claim_verifier et label_content : chaque chiffre doit correspondre au registre ; un énoncé sans preuve est retiré ou rétrogradé."""
import pytest

from helpers_expl import explain

from alphaforge.explainability import Figure, Statement, claim_verifier, label_content, ledger_read
from alphaforge.explainability import explainer as explainer_mod


@pytest.fixture
def ctx(run):
    r = run("Évalue HIGH", risk_tolerance="faible_modere", horizon_years_min=3, horizon_years_max=5)
    view = ledger_read(r.ledger)
    ev = next(e for e in view.evidence if e["tool"] == "compute_metrics")
    return r, view, ev


def st(text, refs=(), figures=(), basis="evidence", literals=(), sid="t.1"):
    return label_content(Statement(sid=sid, section="data", text=text, basis=basis, refs=list(refs), figures=list(figures), literals=list(literals)))


def vol_figure(ev, shown=None, value=None):
    v = ev["value"]["base"]["annualized_volatility"] if value is None else value
    return Figure(value=v, unit="pct", shown=shown or f"{round(v * 100, 1):.1f}".replace(".", ",") + " %", ref=ev["evidence_id"])


def verify(view, *statements):
    return claim_verifier(view, list(statements))


# ---- label_content -------------------------------------------------------


@pytest.mark.parametrize("basis,label", [("evidence", "fait"), ("assumption", "hypothese"), ("scenario", "scenario_fictif")])
def test_label_follows_basis(basis, label):
    assert label_content(Statement(sid="x", section="data", text="x", basis=basis)).label == label


# ---- chiffres -----------------------------------------------------------


def test_true_figure_is_kept(ctx):
    _, view, ev = ctx
    f = vol_figure(ev)
    kept, rep = verify(view, st(f"Volatilité {f.shown}.", [ev["evidence_id"]], [f]))
    assert rep.approved and len(kept) == 1 and rep.verified == 1


def test_falsified_value_is_removed(ctx):
    _, view, ev = ctx
    f = vol_figure(ev, value=0.123456)  # valeur absente du registre
    kept, rep = verify(view, st(f"Volatilité {f.shown}.", [ev["evidence_id"]], [f]))
    assert kept == [] and rep.removed == ["t.1"] and "figure_mismatch" in {i.code for i in rep.issues}


def test_misrounded_display_is_removed(ctx):
    _, view, ev = ctx
    f = vol_figure(ev, shown="12,0 %")  # bonne valeur, mauvais affichage
    kept, rep = verify(view, st("Volatilité 12,0 %.", [ev["evidence_id"]], [f]))
    assert kept == [] and "figure_format" in {i.code for i in rep.issues}


def test_undeclared_number_in_text_is_removed(ctx):
    _, view, ev = ctx
    f = vol_figure(ev)
    kept, rep = verify(view, st(f"Volatilité {f.shown}, rendement attendu 8 %.", [ev["evidence_id"]], [f]))
    assert kept == [] and "unsupported_number" in {i.code for i in rep.issues}


def test_number_in_hypothesis_is_checked_too(ctx):
    _, view, _ = ctx
    kept, rep = verify(view, st("On pourrait gagner 10 % par an.", basis="assumption"))
    assert kept == [] and "unsupported_number" in {i.code for i in rep.issues}


def test_unknown_reference_is_removed(ctx):
    _, view, _ = ctx
    kept, rep = verify(view, st("HIGH est volatil.", ["ev_inexistant"], literals=[]))
    assert kept == [] and "unknown_ref" in {i.code for i in rep.issues}


def test_date_absent_from_ledger_is_removed(ctx):
    _, view, ev = ctx
    kept, rep = verify(view, st("Sommet atteint le 1999-01-01.", [ev["evidence_id"]]))
    assert kept == [] and "unknown_literal" in {i.code for i in rep.issues}


def test_fact_without_evidence_is_downgraded(ctx):
    _, view, _ = ctx
    kept, rep = verify(view, st("Le secteur technologique attire beaucoup d'attention."))
    assert rep.downgraded == ["t.1"] and kept[0].label == "hypothese" and kept[0].note == "downgraded_no_evidence"


def test_fact_without_evidence_with_number_is_removed(ctx):
    _, view, ev = ctx
    f = vol_figure(ev)
    f = f.model_copy(update={"ref": ev["evidence_id"]})
    s = Statement(sid="t.1", section="data", text=f"Volatilité {f.shown}.", figures=[f], label="fait")  # aucune ref déclarée
    kept, rep = verify(view, s)
    assert kept == [] and rep.removed == ["t.1"]


def test_unlabeled_statement_is_removed(ctx):
    _, view, ev = ctx
    s = Statement(sid="t.1", section="data", text="HIGH.", refs=[ev["evidence_id"]])
    kept, rep = verify(view, s)
    assert kept == [] and "unlabeled" in {i.code for i in rep.issues}


# ---- renvoi à la rédaction ----------------------------------------------


def test_rejected_draft_is_sent_back_and_repaired(ctx, monkeypatch):
    """Un brouillon contenant un chiffre inventé est rejeté ; la seconde rédaction l'exclut et passe."""
    r, _, _ = ctx
    real = explainer_mod.compose_explanation
    calls = []

    def compose_with_bad_claim(view, profile, exclude=()):
        calls.append(set(exclude))
        out = real(view, profile, exclude)
        bad = Statement(sid="data.99", section="data", text="HIGH va rapporter 25 % l'an prochain.", refs=[], basis="evidence")
        return out + ([] if "data.99" in exclude else [bad])

    monkeypatch.setattr(explainer_mod, "compose_explanation", compose_with_bad_claim)
    e = explain(r)
    assert calls == [set(), {"data.99"}]
    assert e.report.attempts == 2 and e.report.removed == ["data.99"] and not e.report.approved
    assert "25 %" not in e.text and "1 retiré(s)" in e.text
    assert r.ledger.entries("note")[-1].payload["code"] == "explanation_repaired"


def test_attempts_are_bounded(ctx, monkeypatch):
    r, _, _ = ctx
    real = explainer_mod.compose_explanation
    n = {"i": 0}

    def always_bad(view, profile, exclude=()):
        n["i"] += 1
        bad = Statement(sid=f"data.{90 + n['i']}", section="data", text=f"Chiffre inventé {n['i']} %.")
        return real(view, profile, exclude) + [bad]

    monkeypatch.setattr(explainer_mod, "compose_explanation", always_bad)
    e = explain(r)
    assert n["i"] == 2 and e.report.attempts == 2 and "Chiffre inventé" not in e.text


def test_verbatim_text_with_dates_and_window_alias_is_kept(run):
    """Une fenêtre « 2022 » et un message de l'analyste contenant une date ne doivent pas se gêner."""
    view = ledger_read(run("Évalue COIN-USD").ledger)  # crypto : avis d'écart entre observations, daté
    msg = next(n["message"] for e in view.evidence for n in e.get("notices", []) if any(c.isdigit() for c in n["message"]))
    ev = next(e for e in view.evidence if e["tool"] == "compute_metrics")
    start = ev["window"]["start"]
    s = st(f"Fenêtre « 2022 » (du {start}). {msg}", [ev["evidence_id"]], literals=["2022", msg])
    kept, rep = verify(view, s)
    assert rep.approved, rep.issues
