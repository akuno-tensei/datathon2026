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


def test_correlation_view_reads_reference_matrix(forge, prof):
    a = forge.analyze("Compare HIGH et COIN-USD", prof(risk_tolerance="eleve", horizon_years_min=15, horizon_years_max=30))
    cv = views.correlation_view(a, "fr")
    assert cv.reference == "weekly" and cv.complement is not None  # crypto + marché coté : hebdomadaire de référence
    assert {(c["a"], c["b"]) for c in cv.cells} == {(x, y) for x in cv.tickers for y in cv.tickers}
    assert cv.alt.startswith("Carte de chaleur") and cv.evidence_id in views.evidence_index(a)
    assert views.correlation_view(forge.analyze("Évalue HIGH", prof()), "fr") is None


def test_portfolio_view_reads_the_scenario_evidence(forge, prof):
    a = forge.analyze("Construis un scénario de portefeuille avec HIGH, CALM et LOW.TO", prof())
    pv = views.portfolio_view(a, "fr")
    assert "aucune prévision" in pv.label and pv.rebalancing == "rebalanced" and pv.evidence_id in views.evidence_index(a)
    assert [p["ticker"] for p in pv.positions] == ["HIGH", "CALM", "LOW.TO"] and abs(sum(p["weight"] for p in pv.positions) - 1) < 1e-9
    assert {b["name"] for b in pv.breakdown["currency"]} == {"USD", "CAD"}
    assert pv.series_id in forge.store.series and forge.store.series.get(pv.series_id).series.prices.iloc[0] == 100.0
    assert any("Aucun frais" in x for x in pv.assumptions)
    assert views.portfolio_view(forge.analyze("Compare HIGH et CALM", prof()), "fr") is None
    kinds = {r.kind for r in views.asset_reasons({"reasons": a.verdict["global_reasons"]}, "fr")}
    assert t("kind_portfolio", "fr") in kinds


def test_scenario_evidence_reproduces_to_the_same_id(forge, prof):
    a = forge.analyze("Construis un scénario de portefeuille avec HIGH et CALM", prof(), base_currency="USD")
    pf = next(e for e in a.result.ledger.evidence() if e["tool"] == "portfolio_metrics")
    args = views.reproduce_arguments(pf["evidence_id"], a)
    assert args["rebalancing"] == "rebalanced" and args["base_currency"] == "USD"
    assert run_tool(forge.store, "portfolio_metrics", args).evidence_id == pf["evidence_id"]


# ---- saisie des poids, trace en direct, export ------------------------------
def test_weights_entered_in_the_form_reach_the_analyst_as_given(forge, prof):
    a = forge.analyze("Scénario saisi", prof(), weights={"HIGH": 0.7, "CALM": 0.3}, rebalancing="buy_and_hold")
    pf = next(e for e in a.result.ledger.evidence() if e["tool"] == "portfolio_metrics")
    assert [(p["ticker"], p["weight"]) for p in pf["value"]["positions"]] == [("HIGH", 0.7), ("CALM", 0.3)]
    assert pf["params"]["weights_basis"] == "user" and pf["value"]["rebalancing"] == "buy_and_hold"
    assert a.request_text == "Scénario saisi" and a.result.request.intent == "build_scenario"
    assert views.portfolio_view(a, "fr").rebalancing == "buy_and_hold"
    assert any("fournis dans la demande" in s.text for s in a.explanation.statements)


def test_form_weights_win_over_the_text_and_wrong_sums_are_refused_not_fixed(forge, prof):
    a = forge.analyze("60 % HIGH et 40 % CALM", prof(), weights={"HIGH": 0.5, "CALM": 0.5})
    pf = next(e for e in a.result.ledger.evidence() if e["tool"] == "portfolio_metrics")
    assert [p["weight"] for p in pf["value"]["positions"]] == [0.5, 0.5]
    bad = forge.analyze("Scénario saisi", prof(), weights={"HIGH": 0.7, "CALM": 0.2})  # somme 0,9 : jamais normalisée en silence
    pf = next(e for e in bad.result.ledger.evidence() if e["tool"] == "portfolio_metrics")
    assert pf["status"] == "refused" and pf["error_code"] == "INVALID_WEIGHTS" and views.portfolio_view(bad, "fr") is None


def test_weights_for_unknown_assets_are_dropped_not_invented(forge, prof):
    a = forge.analyze("Scénario saisi", prof(), weights={"HIGH": 0.5, "CALM": 0.5, "NOPE": 0.0})
    pf = next(e for e in a.result.ledger.evidence() if e["tool"] == "portfolio_metrics")
    assert {p["ticker"] for p in pf["value"]["positions"]} == {"HIGH", "CALM"}


def test_live_trace_receives_every_ledger_entry_in_order(forge, prof):
    seen = []
    a = forge.analyze("Évalue HIGH", prof(), on_entry=lambda e: seen.append((e.seq, e.kind)))
    ledger = a.result.ledger.entries()
    assert seen == [(e.seq, e.kind) for e in ledger] and [s for s, _ in seen] == list(range(1, len(ledger) + 1))
    steps = [views.entry_to_step(e, "fr") for e in ledger]
    assert {s.agent for s in steps if s} == {"orchestrateur", "analyste", "gouvernance", "explicabilite"}
    final = [s.text for s in views.agent_trace(a, "fr")]
    assert len([x for x in final if "Explication rédigée" in x]) == 1  # une seule ligne d'explicabilité dans la trace finale


def test_observer_is_optional_and_does_not_change_results(forge, prof):
    plain = forge.analyze("Évalue HIGH", prof())
    watched = forge.analyze("Évalue HIGH", prof(), on_entry=lambda e: None)
    assert [e["evidence_id"] for e in plain.result.ledger.evidence()] == [e["evidence_id"] for e in watched.result.ledger.evidence()]


def test_report_export_contains_banner_evidence_and_no_new_number(forge, prof):
    a = forge.analyze("Compare HIGH et CALM", prof(risk_tolerance="faible_modere"))
    md = views.report_markdown(a, "fr", forge.store)
    assert md.startswith("# AlphaForge AI — Rapport d'analyse") and "SIMULATION" in md
    for eid in views.evidence_index(a):
        assert eid in md
    assert "run_tool(store, 'compute_metrics'" in md
    assert "limites et incertitudes" in md.lower()  # les limites sont dans le rapport, pas seulement à l'écran
    assert not re.search(r"\bachetez\b|\bvendez\b|\bmeilleur choix\b|\bgaranti(?:e|s|es)?\b", md, re.I)
    assert "Analysis report" in views.report_markdown(a, "en", forge.store) and views.report_markdown(a, "fr").count("\n# ") <= 1
    refused = forge.analyze("Bonjour", prof())
    assert "SIMULATION" in views.report_markdown(refused, "fr") and "précisez" in views.report_markdown(refused, "fr")


def test_weight_form_helpers():
    from alphaforge.app import forms

    assert forms.equal_weights(["A", "B", "C"]) == {"A": 33.4, "B": 33.3, "C": 33.3} and forms.weights_ok(forms.equal_weights(["A", "B", "C"]))
    assert forms.weights_ok({"A": 70.0, "B": 30.0}) and not forms.weights_ok({"A": 70.0, "B": 29.9})
    assert not forms.weights_ok({"A": 100.0, "B": 0.0}) and not forms.weights_ok({})  # un poids nul n'est pas une position
    assert forms.weights_ok({"A": 33.3, "B": 33.3, "C": 33.4}) and not forms.weights_ok({"A": 33.3, "B": 33.3, "C": 33.3})
    assert forms.normalize({"A": 50.0, "B": 30.0}) == {"A": 62.5, "B": 37.5} and forms.weights_ok(forms.normalize({"A": 1.0, "B": 1.0, "C": 1.0}))
    assert forms.to_fractions({"A": 60.0, "B": 40.0}) == {"A": 0.6, "B": 0.4}


# ---- création d'un profil quelconque : logique pure -------------------------
def test_slugify_makes_safe_identifiers():
    from alphaforge.app.forms import slugify

    assert slugify("Retraité 70 ans, crypto !") == "retraite_70_ans_crypto" and slugify("  ") == "" and slugify("../etc") == "etc"
    assert slugify("É" * 60) == "e" * 40 and slugify("Amira") == "amira"


def test_add_and_remove_criteria_validate_every_input():
    from alphaforge.app.forms import add_criterion, remove_criterion

    rows = add_criterion([], "asset_class", "crypto", 0.9)
    assert rows == [{"kind": "asset_class", "value": "crypto", "weight": 0.9}]
    assert add_criterion(rows, "sector", " Energy ") == rows + [{"kind": "sector", "value": "Energy"}]  # sans poids pour une exclusion
    for args in (("asset_class", "CRYPTO", 0.5), ("theme", "  ", 0.5), ("couleur", "x", 0.5), ("theme", "esg", 1.5), ("theme", "esg", -0.1)):
        with pytest.raises(ValueError):
            add_criterion(rows, *args)
    assert add_criterion(rows, "theme", "x", 0.5) is not rows and len(rows) == 1  # la liste d'origine n'est pas modifiée
    assert remove_criterion(rows, 0) == [] and remove_criterion(rows, 5) == rows


def test_built_profile_loads_and_derives_its_limits_without_code(forge, prof):
    from alphaforge.app.forms import add_criterion, build_profile_dict
    from alphaforge.governance import GovernanceConfig, derive_constraints
    from alphaforge.orchestrator import load_profile

    d = build_profile_dict(profile_id="", display_name="Étudiante 22 ans", language="fr", experience="debutant", risk_tolerance="modere_eleve",
                           base_currency="USD", horizon_years_min=20, horizon_years_max=40, objectives=["croissance_elevee"],
                           preferences=add_criterion([], "sector", "Information Technology", 0.7), exclusions=add_criterion([], "ticker", "TSLA"))
    p = load_profile(d)
    assert p.profile_id == "etudiante_22_ans" and p.base_currency == "USD" and p.soft_preferences[0].weight == 0.7
    assert p.hard_exclusions[0].value == "TSLA" and derive_constraints(p, GovernanceConfig()).max_volatility == 0.32
    saved = forge.save_profile(d)
    a = forge.analyze("Évalue HIGH", saved.profile_id)
    assert a.profile.base_currency == "USD" and a.verdict is not None


def test_form_state_from_a_profile_clears_name_and_id_for_a_new_one(forge):
    from alphaforge.app.forms import form_state_from_profile

    st = form_state_from_profile(forge.get_profile("amira").model_dump(mode="json"))
    assert st["display_name"] == "" and st["profile_id"] == "" and st["risk_tolerance"] == "eleve"
    assert st["soft_preferences"] and all("weight" in x for x in st["soft_preferences"])


def test_criterion_options_come_from_the_universe(forge):
    from alphaforge.app.forms import criterion_options

    o = criterion_options(forge.assets, ["esg"])
    assert "equity" in o["asset_class"] and "Tech" in o["sector"] and "HIGH" in o["ticker"] and o["theme"] == ["esg"]


def test_example_profiles_cannot_be_shadowed_and_created_ones_need_confirmation(forge, prof):
    from alphaforge.orchestrator import ProfileError

    with pytest.raises(ProfileError) as e:
        forge.save_profile(prof(profile_id="robert", risk_tolerance="eleve"))
    assert e.value.reason == "profile_exists_example"
    assert forge.get_profile("robert").risk_tolerance == "faible_modere"  # l'exemple est intact
    assert forge.profile_origin("robert") == "exemple" and forge.profile_origin("inconnu") is None
    forge.save_profile(prof(profile_id="mien"))
    assert forge.profile_origin("mien") == "créé"
    with pytest.raises(ProfileError) as e:
        forge.save_profile(prof(profile_id="mien", risk_tolerance="eleve"))
    assert e.value.reason == "profile_exists" and forge.get_profile("mien").risk_tolerance == "modere"
    forge.save_profile(prof(profile_id="mien", risk_tolerance="eleve"), overwrite=True)
    assert forge.get_profile("mien").risk_tolerance == "eleve"


# ---- suppression d'un profil créé ---------------------------------------------
def test_a_created_profile_can_be_deleted_and_disappears_everywhere(forge, prof):
    forge.save_profile(prof(profile_id="jetable", display_name="Jetable"))
    assert "jetable" in [i.profile_id for i in forge.list_profiles()] and forge.profile_origin("jetable") == "créé"
    forge.delete_profile("jetable")
    assert forge.profile_origin("jetable") is None and "jetable" not in [i.profile_id for i in forge.list_profiles()]
    assert not (forge.user_dir / "jetable.json").exists()
    from alphaforge.orchestrator import ProfileError

    with pytest.raises(ProfileError) as e:
        forge.get_profile("jetable")
    assert e.value.reason == "profile_not_found"
    forge.save_profile(prof(profile_id="jetable"))  # l'identifiant est de nouveau libre : pas besoin de confirmer un remplacement


def test_example_profiles_cannot_be_deleted(forge):
    from alphaforge.orchestrator import ProfileError
    from alphaforge.orchestrator.tools import PROFILES_DIR

    for pid in ("amira", "mateo", "robert"):
        with pytest.raises(ProfileError) as e:
            forge.delete_profile(pid)
        assert e.value.reason == "profile_not_deletable" and (PROFILES_DIR / f"{pid}.json").is_file()


def test_delete_refuses_unknown_and_dangerous_identifiers_and_never_leaves_the_user_folder(forge, prof, tmp_path):
    from alphaforge.orchestrator import ProfileError

    outside = tmp_path.parent / "secret.json"
    outside.write_text("{}")
    try:
        for bad in ("../secret", "..", "a/b", "robert.json", "", "x y", "/etc/passwd"):
            with pytest.raises(ProfileError) as e:
                forge.delete_profile(bad)
            assert e.value.reason == "invalid_profile_id", bad
        with pytest.raises(ProfileError) as e:
            forge.delete_profile("inconnu")
        assert e.value.reason == "profile_not_found"
        assert outside.exists()  # rien n'a été supprimé hors du dossier des profils créés
    finally:
        outside.unlink()


def test_deleting_one_profile_leaves_the_others_untouched(forge, prof):
    forge.save_profile(prof(profile_id="un"))
    forge.save_profile(prof(profile_id="deux"))
    forge.delete_profile("un")
    assert forge.profile_origin("deux") == "créé" and {"amira", "mateo", "robert", "deux"} <= {i.profile_id for i in forge.list_profiles()}
