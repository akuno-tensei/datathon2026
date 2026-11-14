"""Tests de l'interface Streamlit avec le banc d'essai officiel (AppTest) : l'application s'exécute sans erreur et réagit."""
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

APP = "alphaforge/app/streamlit_app.py"


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Application isolée : les profils créés vont dans un dossier temporaire, et le cache de l'instance est vidé."""
    import streamlit as st

    import alphaforge.app.pipeline as pipeline

    monkeypatch.setattr(pipeline, "USER_PROFILES_DIR", tmp_path)
    st.cache_resource.clear()
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    at.user_dir = tmp_path
    return at


def _texts(at):
    return " ".join([m.value for m in at.markdown] + [w.value for w in at.warning] + [c.value for c in at.caption])


def test_initial_screen_shows_permanent_simulation_banner_and_profiles(app):
    assert any("SIMULATION" in w.value for w in app.warning)
    assert set(app.selectbox[0].options) >= {"Amira (exemple)", "Mateo (exemple)", "Robert (exemple)"}
    assert "Noyau (4 agents)" in _texts(app)
    assert "Limites dérivées du profil" in _texts(app)


def test_send_request_renders_verdict_explanation_limits_and_trace(app):
    app.text_area(key="req_box").set_value("Évalue NVDA")
    next(b for b in app.button if b.label == "Envoyer").click().run()
    assert not app.exception, app.exception
    txt = _texts(app)
    assert "⚠ AVERTISSEMENT" in txt  # icône et texte, jamais la couleur seule
    assert "Explication" in txt and "Limites et incertitudes" in txt and "Trace des agents" in txt
    assert any(b.label.startswith("🔎 ev_") for b in app.button)  # nombres reliés à leur preuve


def test_click_on_evidence_opens_drawer_with_reproduce_command(app):
    app.text_area(key="req_box").set_value("Évalue NVDA")
    next(b for b in app.button if b.label == "Envoyer").click().run()
    next(b for b in app.button if b.label.startswith("🔎 ev_")).click().run()
    assert not app.exception, app.exception
    assert "Preuve" in _texts(app)
    assert any("run_tool(store" in c.value for c in app.code)


def test_english_switch_changes_labels(app):
    app.radio(key="lang").set_value("en").run()
    assert not app.exception
    assert any("not financial advice" in w.value for w in app.warning)
    assert any(b.label == "Send" for b in app.button)


def test_compare_tab_runs_two_profiles(app):
    next(b for b in app.button if b.label == "Comparer" and b.key == "cmp_go").click().run()
    assert not app.exception, app.exception
    assert "Pourquoi les conclusions diffèrent" in _texts(app)


def test_out_of_universe_shows_message_and_disabled_enrichment(app):
    app.text_area(key="req_box").set_value("Évalue NFLX.TO")
    next(b for b in app.button if b.label == "Envoyer").click().run()
    assert not app.exception, app.exception
    assert any("hors de l'univers" in w.value for w in app.warning)
    btn = next(b for b in app.button if b.label == "Tenter d'ajouter via une source publique")
    assert btn.disabled


def test_comparison_request_shows_correlation_heat_map(app):
    app.text_area(key="req_box").set_value("Compare NVDA et BTC-USD")
    next(b for b in app.button if b.label == "Envoyer").click().run()
    assert not app.exception, app.exception
    assert "Corrélations des rendements" in _texts(app)
    assert any(c.value.startswith("Carte de chaleur") for c in app.caption)


def test_scenario_request_shows_labelled_portfolio_view(app):
    app.text_area(key="req_box").set_value("Construis un scénario de portefeuille avec NVDA et XBB.TO")
    next(b for b in app.button if b.label == "Envoyer").click().run()
    assert not app.exception, app.exception
    assert "Scénario de portefeuille" in _texts(app)
    assert any("Scénario rétrospectif fictif" in w.value and "aucune prévision" in w.value for w in app.warning)
    assert any(m.label == "Volatilité annualisée" for m in app.metric)


def _builder(app, assets, weights):
    app.multiselect(key="sc_assets").set_value(assets).run()
    for tk, w in weights.items():
        app.number_input(key=f"w_{tk}").set_value(w)
    return app.run()


def test_scenario_builder_blocks_a_wrong_sum_and_runs_a_correct_one(app):
    _builder(app, ["NVDA", "XBB.TO"], {"NVDA": 70.0, "XBB.TO": 20.0})
    assert not app.exception, app.exception
    assert any("la somme doit valoir 100 %" in m.value for m in app.markdown)
    assert app.button(key="sc_run_btn").disabled  # l'outil refuserait cette somme : le bouton reste inactif
    app.button(key="sc_norm_btn").click().run()  # normaliser est une action explicite
    assert app.number_input(key="w_NVDA").value == pytest.approx(77.8) and not app.button(key="sc_run_btn").disabled
    _builder(app, ["NVDA", "XBB.TO"], {"NVDA": 70.0, "XBB.TO": 30.0})
    app.button(key="sc_run_btn").click().run()
    assert not app.exception, app.exception
    pf = next(e for e in app.session_state["analysis"].result.ledger.evidence() if e["tool"] == "portfolio_metrics")
    assert [(p["ticker"], p["weight"]) for p in pf["value"]["positions"]] == [("NVDA", 0.7), ("XBB.TO", 0.3)]
    assert pf["params"]["weights_basis"] == "user"
    assert any("Scénario rétrospectif fictif" in w.value for w in app.warning)


def test_scenario_builder_needs_two_assets_and_offers_equal_weights(app):
    app.multiselect(key="sc_assets").set_value(["NVDA"]).run()
    assert app.button(key="sc_run_btn").disabled
    app.multiselect(key="sc_assets").set_value(["NVDA", "XBB.TO", "SPY"]).run()
    assert not app.button(key="sc_run_btn").disabled and app.number_input(key="w_NVDA").value == pytest.approx(33.4)
    assert [round(app.number_input(key=f"w_{t}").value, 1) for t in ("NVDA", "XBB.TO", "SPY")] == [33.4, 33.3, 33.3]


def test_weights_typed_in_the_request_text_are_used(app):
    app.text_area(key="req_box").set_value("Construis un portefeuille avec 60 % NVDA et 40 % XBB.TO")
    next(b for b in app.button if b.label == "Envoyer").click().run()
    assert not app.exception, app.exception
    pf = next(e for e in app.session_state["analysis"].result.ledger.evidence() if e["tool"] == "portfolio_metrics")
    assert [p["weight"] for p in pf["value"]["positions"]] == [0.6, 0.4]


def test_export_button_appears_after_an_analysis(app):
    assert not any(getattr(e, "type", "") == "download_button" for e in app.main)
    app.text_area(key="req_box").set_value("Évalue NVDA")
    next(b for b in app.button if b.label == "Envoyer").click().run()
    assert not app.exception, app.exception
    assert any("Exporter le rapport" in str(e.proto) for e in app.get("download_button"))


# ---- raccourcis : ils préremplissent le champ de demande ---------------------
def _shortcut(app, label):
    return next(b for b in app.button if b.label == label and b.key.startswith("sc-"))


@pytest.mark.parametrize("label,expected", [
    ("Évaluer un actif", "Évalue NVDA"),
    ("Comparer", "Compare NVDA et FTS.TO"),
    ("Scénario", "Construis un scénario de portefeuille avec NVDA et FTS.TO"),
    ("Expliquer un risque", "Explique le risque de NVDA"),
])
def test_each_shortcut_fills_the_request_field(app, label, expected):
    assert app.text_area(key="req_box").value == ""
    _shortcut(app, label).click().run()
    assert not app.exception, app.exception
    assert app.text_area(key="req_box").value == expected  # le champ affiche la phrase, pas seulement l'état interne


def test_shortcuts_use_the_assets_chosen_above_and_can_be_chained(app):
    app.selectbox(key="asset_a").set_value("RY.TO")
    app.selectbox(key="asset_b").set_value("SPY").run()
    _shortcut(app, "Comparer").click().run()
    assert app.text_area(key="req_box").value == "Compare RY.TO et SPY"
    _shortcut(app, "Évaluer un actif").click().run()  # un second raccourci remplace le premier
    assert app.text_area(key="req_box").value == "Évalue RY.TO"


def test_shortcut_text_follows_the_interface_language(app):
    app.radio(key="lang").set_value("en").run()
    _shortcut(app, "Compare").click().run()
    assert app.text_area(key="req_box").value == "Compare NVDA and FTS.TO"
    assert any("A shortcut fills in the request" in c.value for c in app.caption)


def test_a_shortcut_then_send_runs_that_exact_request(app):
    _shortcut(app, "Évaluer un actif").click().run()
    next(b for b in app.button if b.label == "Envoyer").click().run()
    assert not app.exception, app.exception
    a = app.session_state["analysis"]
    assert a.request_text == "Évalue NVDA" and a.result.request.tickers == ["NVDA"]


def test_the_field_stays_editable_after_a_shortcut(app):
    _shortcut(app, "Comparer").click().run()
    app.text_area(key="req_box").set_value("Évalue XBB.TO").run()
    assert app.text_area(key="req_box").value == "Évalue XBB.TO"  # la saisie libre n'est pas écrasée au rafraîchissement


def test_shortcut_hint_tells_the_user_to_click_send(app):
    assert any("préremplit la demande" in c.value and "Envoyer" in c.value for c in app.caption)


# ---- création d'un profil quelconque -----------------------------------------
def _fill(app, name, risk="modere", hz=(5, 10)):
    app.text_input(key="pf_name").set_value(name)
    app.select_slider(key="pf_risk").set_value(risk)
    app.slider(key="pf_hz").set_value(hz)
    return app.run()


def _add_pref(app, kind, value, weight=None):
    app.selectbox(key="pf_kind_p").set_value(kind).run()
    key = f"pf_val_p_{kind}"
    (app.text_input(key=key) if kind == "theme" else app.selectbox(key=key)).set_value(value).run()
    if weight is not None:
        app.slider(key="pf_w_p").set_value(weight).run()
    return app.button(key="pf_add_p").click().run()


def test_the_form_starts_blank_not_as_a_copy_of_the_active_profile(app):
    assert app.text_input(key="pf_name").value == "" and app.text_input(key="pf_id").value == ""
    assert app.select_slider(key="pf_risk").value == "modere" and app.session_state["pf_prefs"] == []
    assert app.button(key="pf_save_btn").disabled  # ni nom ni identifiant : rien à enregistrer


def test_any_profile_can_be_created_becomes_active_and_drives_the_verdict(app):
    _fill(app, "Retraité 70 ans", risk="faible", hz=(1, 3))
    _add_pref(app, "asset_class", "crypto", 0.9)
    assert app.session_state["pf_prefs"] == [{"kind": "asset_class", "value": "crypto", "weight": 0.9}]
    assert any("Identifiant utilisé : retraite_70_ans" in c.value for c in app.caption)
    assert not app.button(key="pf_save_btn").disabled
    app.button(key="pf_save_btn").click().run()
    assert not app.exception, app.exception
    assert (app.user_dir / "retraite_70_ans.json").is_file()
    assert app.selectbox(key="profile_sel").value == "retraite_70_ans"  # actif immédiatement, sans autre action
    assert "Retraité 70 ans" in app.selectbox(key="profile_sel").options  # le nouveau profil figure dans la liste, sous son nom
    assert any("Profil enregistré" in s.value for s in app.success)
    # Le nouveau profil pilote l'analyse : la crypto n'est pas permise à ce niveau de risque, sans règle propre à ce profil.
    app.text_area(key="req_box").set_value("Évalue BTC-USD")
    next(b for b in app.button if b.label == "Envoyer").click().run()
    assert not app.exception, app.exception
    a = app.session_state["analysis"]
    assert a.profile.profile_id == "retraite_70_ans" and a.verdict["status"] == "bloqué"
    assert "CLASS_NOT_PERMITTED" in [r["code"] for r in a.verdict["assets"][0]["reasons"]]


def test_copy_and_blank_buttons_reset_the_form(app):
    app.button(key="pf_copy_btn").click().run()
    assert app.select_slider(key="pf_risk").value == "faible_modere" and app.slider(key="pf_hz").value == (3, 5)  # profil actif : Robert
    assert app.text_input(key="pf_name").value == "" and app.text_input(key="pf_id").value == ""  # nom et identifiant à saisir
    _fill(app, "Autre", risk="eleve")
    app.button(key="pf_blank_btn").click().run()
    assert app.text_input(key="pf_name").value == "" and app.select_slider(key="pf_risk").value == "modere"


def test_an_example_profile_is_never_replaced(app):
    _fill(app, "Peu importe")
    app.text_input(key="pf_id").set_value("robert").run()
    assert app.button(key="pf_save_btn").disabled
    assert any("profil d'exemple" in w.value for w in app.warning)
    assert not (app.user_dir / "robert.json").exists()


def test_replacing_a_created_profile_needs_explicit_confirmation(app):
    _fill(app, "Mon profil")
    app.button(key="pf_save_btn").click().run()
    assert (app.user_dir / "mon_profil.json").is_file()
    _fill(app, "Mon profil", risk="eleve", hz=(10, 20))
    assert app.button(key="pf_save_btn").disabled and app.checkbox(key="pf_overwrite").value is False
    assert any("existe déjà" in w.value for w in app.warning)
    app.checkbox(key="pf_overwrite").check().run()
    assert not app.button(key="pf_save_btn").disabled
    app.button(key="pf_save_btn").click().run()
    assert '"eleve"' in (app.user_dir / "mon_profil.json").read_text(encoding="utf-8")


def test_a_contradictory_profile_cannot_be_saved_and_says_why(app):
    _fill(app, "Contradictoire")
    _add_pref(app, "sector", "Energy", 0.5)
    app.selectbox(key="pf_kind_x").set_value("sector").run()
    app.selectbox(key="pf_val_x_sector").set_value("Energy").run()
    app.button(key="pf_add_x").click().run()
    assert app.button(key="pf_save_btn").disabled
    assert any("Energy" in e.value for e in app.error)  # l'aperçu en direct nomme l'incohérence
    assert list(app.user_dir.glob("*.json")) == []


def test_criteria_can_be_added_removed_and_duplicates_are_reported(app):
    _fill(app, "Critères")
    _add_pref(app, "theme", "esg", 0.8)
    _add_pref(app, "theme", "ESG", 0.8)  # même critère, casse différente
    assert any("figure déjà dans la liste" in w.value for w in app.warning) and len(app.session_state["pf_prefs"]) == 1
    app.button(key="pf_rm_p_0").click().run()
    assert app.session_state["pf_prefs"] == []
    assert not any("figure déjà" in w.value for w in app.warning)  # le message de doublon disparaît avec le critère retiré


def test_a_free_theme_is_accepted_and_flagged_as_unverifiable_for_governance(app):
    _fill(app, "Thème libre")
    _add_pref(app, "theme", "intelligence_artificielle", 0.9)
    app.button(key="pf_save_btn").click().run()
    app.text_area(key="req_box").set_value("Évalue NVDA")
    next(b for b in app.button if b.label == "Envoyer").click().run()
    reasons = [r["code"] for r in app.session_state["analysis"].verdict["assets"][0]["reasons"]]
    assert "THEME_DATA_MISSING" in reasons  # aucune donnée de thème : dit, jamais deviné


def test_profile_form_labels_follow_the_language(app):
    app.radio(key="lang").set_value("en").run()
    assert any("Create any profile" in c.value for c in app.caption)
    assert any(b.label == "Start from a blank form" for b in app.button)


# ---- suppression d'un profil créé ---------------------------------------------
def _create(app, name):
    app.text_input(key="pf_name").set_value(name).run()
    app.button(key="pf_save_btn").click().run()
    return app


def test_no_delete_option_without_created_profiles_and_examples_are_never_offered(app):
    assert any("Aucun profil créé à supprimer" in c.value for c in app.caption)
    assert not [s for s in app.selectbox if s.key == "pf_del_pick"] and not [b for b in app.button if b.key == "pf_del_btn"]
    _create(app, "Mien")
    pick = app.selectbox(key="pf_del_pick")
    assert pick.options == ["Mien"]  # seulement ce qui a été créé (noms affichés) : Amira, Mateo et Robert n'y figurent pas


def test_deleting_needs_explicit_confirmation(app):
    _create(app, "A garder")
    assert app.button(key="pf_del_btn").disabled and (app.user_dir / "a_garder.json").is_file()
    app.checkbox(key="pf_del_ok").check().run()
    assert not app.button(key="pf_del_btn").disabled
    app.checkbox(key="pf_del_ok").uncheck().run()
    assert app.button(key="pf_del_btn").disabled and (app.user_dir / "a_garder.json").is_file()  # décocher annule : rien n'est supprimé


def test_deleting_the_active_profile_falls_back_to_an_example_without_error(app):
    _create(app, "Actif")
    assert app.selectbox(key="profile_sel").value == "actif"
    app.checkbox(key="pf_del_ok").check().run()
    app.button(key="pf_del_btn").click().run()
    assert not app.exception, app.exception
    assert not (app.user_dir / "actif.json").exists()
    assert app.selectbox(key="profile_sel").value == "robert" and "Actif" not in app.selectbox(key="profile_sel").options
    assert any("Profil supprimé : actif" in s.value for s in app.success)
    assert app.session_state["pf_del_ok"] is False  # la confirmation est remise à zéro pour le profil suivant
    assert any("Aucun profil créé à supprimer" in c.value for c in app.caption)  # plus rien à supprimer : la case a disparu


def test_deleting_a_profile_used_in_the_comparison_tab_does_not_break_it(app):
    _create(app, "Compare")
    app.selectbox(key="cmp_l").set_value("compare").run()
    app.checkbox(key="pf_del_ok").check().run()
    app.button(key="pf_del_btn").click().run()
    assert not app.exception, app.exception
    assert app.selectbox(key="cmp_l").value in ("amira", "mateo", "robert")
    next(b for b in app.button if b.key == "cmp_go").click().run()
    assert not app.exception, app.exception


def test_only_the_chosen_profile_is_deleted_and_an_analysis_survives_its_profile_deletion(app):
    _create(app, "Premier")
    app.button(key="pf_blank_btn").click().run()
    _create(app, "Second")
    assert set(app.selectbox(key="pf_del_pick").options) == {"Premier", "Second"}
    app.text_area(key="req_box").set_value("Évalue NVDA")
    next(b for b in app.button if b.label == "Envoyer").click().run()
    analysis = app.session_state["analysis"]
    app.selectbox(key="pf_del_pick").set_value("second").run()
    app.checkbox(key="pf_del_ok").check().run()
    app.button(key="pf_del_btn").click().run()
    assert not app.exception, app.exception
    assert (app.user_dir / "premier.json").is_file() and not (app.user_dir / "second.json").exists()
    assert app.session_state["analysis"] is analysis and analysis.profile.profile_id == "second"  # le résultat déjà affiché reste lisible
