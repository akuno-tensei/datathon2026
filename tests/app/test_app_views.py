"""Pipeline des agents et vues de l'interface : verdict, preuves, trace, comparaison, profils, libellés."""
import json
import re

import pytest

from alphaforge.app import views
from alphaforge.app.forms import parse_criteria
from alphaforge.app.i18n import STRINGS, t
from alphaforge.quant import run_tool


def test_pipeline_runs_all_four_agents_and_keeps_structured_explanation(forge, prof):
    a = forge.analyze("Évalue HIGH", prof(risk_tolerance="faible_modere"))
    assert a.result.status == "complete" and a.verdict["status"] == "avertissement"
    assert a.explanation is not None and a.explanation.text == a.result.answer
    agents = {s.agent for s in views.agent_trace(a, "fr")}
    assert agents == {"orchestrateur", "analyste", "gouvernance", "explicabilite"}


def test_base_currency_choice_reaches_every_metric_call_including_feedback(forge, prof):
    a = forge.analyze("Évalue HIGH", prof(risk_tolerance="faible_modere"), base_currency="USD")
    calls = [e for e in a.result.ledger.evidence() if e["tool"] == "compute_metrics"]
    assert calls and {e["params"]["base_currency"] for e in calls} == {"USD"}
    assert views.feedback_iterations(a) >= 1  # alternatives chiffrées dans la même devise


def test_verdict_bars_compare_measure_to_derived_limit_with_origin(forge, prof):
    a = forge.analyze("Évalue HIGH", prof(risk_tolerance="faible_modere"))
    bars = views.verdict_measures(a, "fr")["HIGH"]
    vol, dd = bars
    assert vol.limit == a.verdict["constraints"]["max_volatility"] and not vol.ok and vol.icon == "✗"
    assert vol.ratio == 1.0 and "faible_modere" in vol.origin and vol.basis.startswith("252")
    assert vol.evidence_id in views.evidence_index(a)
    ok = views.verdict_measures(forge.analyze("Évalue LOW.TO", prof()), "fr")["LOW.TO"]
    assert all(b.ok and b.icon == "✓" for b in ok)


def test_reasons_carry_icon_and_hard_or_soft_kind(forge, prof):
    a = forge.analyze("Évalue COIN-USD", prof(risk_tolerance="faible", hard_exclusions=[{"kind": "ticker", "value": "COIN-USD"}]))
    lines = views.asset_reasons(a.verdict["assets"][0], "fr")
    hard = [l for l in lines if l.kind == t("kind_hard", "fr")]
    assert hard and all(l.icon == "⛔" for l in hard)
    assert views.status_badge(a.verdict["status"], "fr").label == "BLOQUÉ"


def test_every_evidence_reproduces_to_the_same_id(forge, prof):
    a = forge.analyze("Compare HIGH et LOW.TO", prof(risk_tolerance="faible_modere"), base_currency="USD")
    idx = views.evidence_index(a)
    assert len(idx) >= 4
    for eid, ev in idx.items():
        args = views.reproduce_arguments(eid, a)
        assert run_tool(forge.store, ev["tool"], args).evidence_id == eid
    card = views.evidence_card(next(iter(idx)), a, "fr")
    assert card.reproduce.startswith("run_tool(store, ") and card.inputs and card.fingerprint


def test_evidence_card_has_textual_alternative_for_chart(forge, prof):
    a = forge.analyze("Évalue HIGH", prof())
    eid = views.verdict_measures(a, "fr")["HIGH"][1].evidence_id
    c = views.evidence_card(eid, a, "fr")
    assert c.chart and c.chart["alt"].startswith("Graphique : sommet ")
    assert views.evidence_card("ev_inexistant", a, "fr") is None


def test_sensitivity_comes_from_stress_window_with_all_windows_and_threshold(forge, prof):
    a = forge.analyze("Évalue HIGH", prof())
    tab = views.sensitivity_table(a, "HIGH")
    stress = next(e for e in a.result.ledger.evidence() if e["tool"] == "stress_window")
    assert tab.evidence_id == stress["evidence_id"] and tab.indicator == stress["value"]["verdict"]
    assert [r["window"] for r in tab.rows] == [r["window"] for r in stress["value"]["rows"]]  # toutes, même non calculées
    assert tab.threshold == 0.25 and {"complet", "2022", "hors_2022"} <= {r["window"] for r in tab.rows}


def test_explanation_summary_is_a_subset_of_detail_and_limits_always_present(forge, prof):
    a = forge.analyze("Évalue HIGH", prof())
    short = views.explanation_sections(a, "fr", detail=False)
    full = views.explanation_sections(a, "fr", detail=True)
    assert set(short) <= {"summary", "fit", "scope"} and set(short) <= set(full)
    assert sum(map(len, short.values())) < sum(map(len, full.values()))
    lim = views.limit_sections(a, "fr")
    assert lim["limits"] and all(l.label in ("Fait", "Hypothèse", "Scénario fictif") for ls in lim.values() for l in ls)
    assert views.verification_banner(a, "fr")


def test_trace_shows_feedback_loop_and_explainability_verification(forge, prof):
    a = forge.analyze("Évalue HIGH", prof(risk_tolerance="faible_modere"))
    tr = views.agent_trace(a, "fr")
    assert any(s.detail.get("feedback") for s in tr)  # relance gouvernance -> analyste visible
    last = tr[-1]
    assert last.agent == "explicabilite" and {"attempts", "verified", "removed"} <= set(last.detail)


def test_out_of_universe_and_needs_clarification(forge, prof):
    a = forge.analyze("Évalue ZZZZ.TO", prof())
    assert views.out_of_universe(a) == ["ZZZZ.TO"] and a.verdict["status"] == "avertissement"
    b = forge.analyze("Bonjour", prof())
    assert b.result.status == "needs_clarification" and b.verdict is None and b.explanation is None


def test_comparison_same_data_different_limits(forge, prof):
    forge.save_profile(prof(profile_id="audacieux", risk_tolerance="eleve", horizon_years_min=15, horizon_years_max=30))
    forge.save_profile(prof(profile_id="prudent", risk_tolerance="faible", horizon_years_min=3, horizon_years_max=5))
    cmp = forge.compare("Évalue HIGH", "audacieux", "prudent")
    fps = {e["data_fingerprint"] for an in (cmp.left, cmp.right) for e in an.result.ledger.evidence()}
    assert len(fps) == 1  # mêmes données
    diffs = [d.text for d in views.comparison_diffs(cmp, "fr")]
    assert any("Volatilité maximale" in d for d in diffs) and any("HIGH" in d for d in diffs)


def test_new_profile_is_saved_and_usable_without_code_change(forge, prof, tmp_path):
    p = forge.save_profile(prof(profile_id="retraite_crypto", display_name="Retraité 70 ans", risk_tolerance="faible",
                                horizon_years_min=1, horizon_years_max=3,
                                soft_preferences=[{"kind": "asset_class", "value": "crypto", "weight": 0.9}]))
    assert (tmp_path / "retraite_crypto.json").is_file()
    assert "retraite_crypto" in [i.profile_id for i in forge.list_profiles() if i.origin == "créé"]
    a = forge.analyze("Évalue COIN-USD", "retraite_crypto")
    assert a.verdict["status"] == "bloqué"  # classe non permise au niveau dérivé : règle du profil, pas du persona
    with pytest.raises(Exception):
        forge.save_profile(prof(profile_id="../evil"))


def test_interface_language_and_currency_apply_to_a_copy_of_the_profile(forge):
    a = forge.analyze("Évalue HIGH", "robert", base_currency="USD", language="en")
    assert a.profile.language == "en" and a.profile.base_currency == "USD"
    assert a.explanation.language == "en"
    assert forge.get_profile("robert").base_currency == "CAD"  # le fichier du profil n'est pas modifié


def test_example_profiles_include_the_three_personas(forge):
    ids = {i.profile_id for i in forge.list_profiles()}
    assert {"amira", "mateo", "robert"} <= ids
    amira = forge.get_profile("amira")
    assert amira.risk_tolerance == "eleve" and amira.horizon_years_min >= 15


def test_parse_criteria():
    assert parse_criteria("asset_class:crypto:0,6\n\ntheme:esg", with_weight=True) == [
        {"kind": "asset_class", "value": "crypto", "weight": 0.6}, {"kind": "theme", "value": "esg"}]
    assert parse_criteria("sector:Energy", with_weight=False) == [{"kind": "sector", "value": "Energy"}]
    for bad in ("couleur:rouge", "ticker:", "theme:esg:beaucoup"):
        with pytest.raises(ValueError):
            parse_criteria(bad, with_weight=True)


def test_labels_are_bilingual_and_never_prescriptive():
    assert set(STRINGS["fr"]) == set(STRINGS["en"])
    forbidden = re.compile(r"\bachetez\b|\bvendez\b|meilleur choix|garanti|va monter|\byou should\b|guaranteed|best choice", re.I)
    for lang in STRINGS:
        for k, v in STRINGS[lang].items():
            assert not forbidden.search(v), (lang, k, v)
    assert "SIMULATION" in t("simulation_banner", "fr") and "SIMULATION" in t("simulation_banner", "en")


def test_internal_evidence_opens_only_when_recomputed_identically(forge, prof):
    a = forge.analyze("Évalue HIGH", prof())
    tab = views.sensitivity_table(a, "HIGH")
    row = next(r for r in tab.rows if r["window"] == "hors_2022")
    assert row["evidence_id"] not in views.evidence_index(a)  # preuve interne de stress_window
    assert views.evidence_card(row["evidence_id"], a, "fr") is None  # sans accès à l'analyste : rien n'est inventé
    card = views.evidence_card(row["evidence_id"], a, "fr", store=forge.store)
    assert card.recomputed and card.evidence_id == row["evidence_id"] and card.window["alias"] == "hors_2022"
    dq_id = next(e for e in a.result.ledger.evidence() if e["tool"] == "compute_metrics")["depends_on"][0]
    dq = views.evidence_card(dq_id, a, "fr", store=forge.store)
    assert dq.tool == "data_quality_check" and dq.recomputed


def test_same_request_different_conclusions_for_contrasted_personas(forge):
    cmp = forge.compare("Évalue HIGH", "amira", "robert")
    assert cmp.left.verdict["status"] != cmp.right.verdict["status"]
    assert any("HIGH" in d.text for d in views.comparison_diffs(cmp, "fr"))
