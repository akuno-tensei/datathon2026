"""Tests de l'interface Streamlit avec le banc d'essai officiel (AppTest) : l'application s'exécute sans erreur et réagit."""
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

APP = "alphaforge/app/streamlit_app.py"


@pytest.fixture
def app():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception, at.exception
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
