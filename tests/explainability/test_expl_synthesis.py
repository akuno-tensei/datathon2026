"""Synthèse placée après le verdict : gabarits seulement, aucun fait nouveau, étiquette « synthèse », vérifiée comme le reste."""
from datetime import date

import pytest

from helpers_expl import explain

from alphaforge.explainability import Figure, Statement, ledger_read
from alphaforge.explainability import explainer as explainer_mod
from alphaforge.explainability.config import TEMPLATES
from alphaforge.explainability.formatting import format_number
from alphaforge.explainability.label_content import label_content
from alphaforge.explainability.synthesis import verify_synthesis
from alphaforge.orchestrator import Orchestrator
from alphaforge.orchestrator.tools import load_profile

RISKY = {"risk_tolerance": "faible_modere", "horizon_years_min": 3, "horizon_years_max": 5}
S_FR = TEMPLATES["fr"]["synthesis"]


def synth(e):
    return [s for s in e.statements if s.label == "synthese"]


# ---- place, étiquette, preuves ---------------------------------------------


def test_synthesis_follows_the_verdict_and_is_labeled(run):
    e = explain(run("Évalue HIGH", **RISKY))
    summary = [s for s in e.statements if s.section == "summary"]
    i = next(k for k, s in enumerate(summary) if s.text.startswith("Verdict de la gouvernance"))
    syn = synth(e)
    assert syn and summary[i + 1: i + 1 + len(syn)] == syn
    assert all(s.refs for s in syn) and e.report.approved
    assert "- [Synthèse] HIGH dépasse des limites de risque de votre profil" in e.text


def test_every_synthesis_figure_already_appears_in_the_body(run):
    e = explain(run("Évalue HIGH", **RISKY))
    body = {(f.ref, f.value, f.unit) for s in e.statements if s.label != "synthese" for f in s.figures}
    figures = [f for s in synth(e) for f in s.figures]
    assert len(figures) == 4  # volatilité et perte maximale, chacune avec sa limite
    assert all((f.ref, f.value, f.unit) in body for f in figures)


# ---- contenu selon le verdict ----------------------------------------------


def test_within_limits(run):
    texts = [s.text for s in synth(explain(run("Évalue LOW.TO")))]
    assert texts[0] == S_FR["within"].format(t="LOW.TO") and texts[-1] == S_FR["close"]


def test_blocked_by_exclusion(run):
    texts = [s.text for s in synth(explain(run("Évalue HIGH", hard_exclusions=[{"kind": "sector", "value": "Tech"}])))]
    assert "HIGH est bloqué par une contrainte de votre profil : secteur exclu." in texts


def test_alternative_with_compliant_status_is_named(run):
    texts = [s.text for s in synth(explain(run("Évalue HIGH", **RISKY)))]
    assert any(t.startswith("Alternatives évaluées au statut conforme :") and "CALM" in t for t in texts)


def test_out_of_universe_does_not_talk_about_measures(run):
    texts = [s.text for s in synth(explain(run("Évalue ZZZZ.TO")))]
    assert texts == [S_FR["nodata"].format(t="ZZZZ.TO"), S_FR["close_nodata"]]


def test_scope_refusal_is_pointed_to(run):
    texts = [s.text for s in synth(explain(run("Prédis LOW.TO l'an prochain")))]
    assert S_FR["scope"] in texts


def test_english(run):
    e = explain(run("Évalue HIGH", **RISKY), language="en")
    assert e.report.approved and "- [Synthesis] HIGH exceeds risk limits of your profile:" in e.text
    assert " ; " not in "".join(s.text for s in synth(e))


def test_no_verdict_no_synthesis(analyst, assets, profile_dict):
    r = Orchestrator(analyst, as_of=date(2023, 12, 29)).run("Évalue HIGH", profile_dict(), assets)  # sans gouvernance
    e = explain(r)
    assert synth(e) == [] and "[Synthèse]" not in e.text


# ---- vérification : retirée, jamais réparée -----------------------------------


@pytest.fixture
def ctx(run):
    r = run("Évalue HIGH", **RISKY)
    e = explain(r)
    view = ledger_read(r.ledger)
    body = [s for s in e.statements if s.label != "synthese"]
    return r, view, body


def draft(text, refs, figures=()):
    return label_content(Statement(sid="summary.syn9", section="summary", text=text, basis="synthesis", refs=refs, figures=list(figures)))


def test_new_fact_is_removed(ctx):
    """Un chiffre exact et présent au registre, mais absent du corps vérifié, n'entre pas dans la synthèse."""
    _, view, body = ctx
    cited = {(x.ref, x.value) for s in body for x in s.figures}
    ev = next(e for e in view.evidence if e["tool"] == "compute_metrics")
    base = ev["value"]["base"]
    value = next(base[k] for k in ("total_return", "annualized_return", "annualized_volatility")
                 if isinstance(base.get(k), float) and base[k] and (ev["evidence_id"], base[k]) not in cited)
    shown = format_number(value, "pct", "fr")
    fig = Figure(value=value, unit="pct", shown=shown, ref=ev["evidence_id"])
    kept, removed, issues = verify_synthesis(view, [draft(f"HIGH a varié de {shown}.", [ev["evidence_id"]], [fig])], body)
    assert kept == [] and removed == ["summary.syn9"] and "synthesis_new_fact" in {i.code for i in issues}


def test_synthesis_without_evidence_is_removed_not_downgraded(ctx):
    _, view, body = ctx
    kept, removed, issues = verify_synthesis(view, [draft("HIGH est intéressant.", [])], body)
    assert kept == [] and removed == ["summary.syn9"] and "no_evidence" in {i.code for i in issues}


def test_advice_in_synthesis_is_removed(ctx):
    _, view, body = ctx
    kept, removed, issues = verify_synthesis(view, [draft("Vous devriez privilégier CALM.", [view.evidence[0]["evidence_id"]])], body)
    assert kept == [] and "advice_wording" in {i.code for i in issues}


def test_rejected_synthesis_is_reported_and_absent_from_text(ctx, monkeypatch):
    r, _, _ = ctx
    real = explainer_mod.build_synthesis

    def with_bad(view, profile):
        return real(view, profile) + [Statement(sid="summary.syn99", section="summary", text="Vous devriez privilégier CALM.",
                                                basis="synthesis", refs=[view.evidence[0]["evidence_id"]])]

    monkeypatch.setattr(explainer_mod, "build_synthesis", with_bad)
    e = explain(r)
    assert "summary.syn99" in e.report.removed and not e.report.approved
    assert "Vous devriez" not in e.text and synth(e)  # le reste de la synthèse est conservé


# ---- interface : affichée en mode résumé, sans changement de l'application -------


def test_interface_short_mode_shows_synthesis(ctx):
    from alphaforge.app import views
    from alphaforge.app.pipeline import Analysis

    r, _, _ = ctx
    profile = load_profile(r.ledger.entries("profile")[0].payload["profile"])
    a = Analysis("Évalue HIGH", profile, date(2023, 12, 29), "CAD", r, r.ledger.entries("verdict")[-1].payload, explain(r))
    lines = views.explanation_sections(a, "fr", detail=False)["summary"]
    assert any(line.label == "Synthèse" and line.refs for line in lines)
