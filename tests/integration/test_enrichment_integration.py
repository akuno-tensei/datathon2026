"""L'agent d'enrichissement intégré aux quatre autres agents, règle par règle (Agents.md 2.2 et 2.4)."""
from datetime import date

import pytest
from conftest import AS_OF, Fake, codes, esg, failing, news, reasons, verdict

from alphaforge.enrichment import EnrichmentAgent
from alphaforge.explainability import Explainer
from alphaforge.orchestrator import load_profile

TEXT = "Évalue NVDA"


def providers(esg_fn=None, news_fn=None):
    out = {}
    if esg_fn:
        out["fetch_esg"] = Fake(esg_fn)
    if news_fn:
        out["fetch_news"] = Fake(news_fn, "wire")
    return out


# ---- 2.4 : exclusion bloquante seulement sur une donnée de niveau A ou B -------------------------------------
def test_documented_controversy_in_a_level_b_source_blocks_a_hard_exclusion(run, profile):
    r, _ = run(TEXT, profile(), providers(lambda k, t, a: [esg(t)] if t == "NVDA" else []))
    b = next(x for x in reasons(r, "NVDA") if x["code"] == "EXCLUDED_THEME_CONTROVERSY")
    assert b["severity"] == "block" and b["measures"]["confidence"] == "B" and b["measures"]["date_publication"] == "2026-03-01"
    assert "Fournisseur ESG fictif" in b["message"] and verdict(r)["status"] == "bloqué"
    rec = next(e.payload for e in r.ledger.entries("enrichment") if e.payload["record_id"] == b["evidence_ids"][0])
    assert rec["confidence"] == "B" and rec["usable_for_calculation"] is True and rec["date_publication"] == "2026-03-01"
    assert "THEME_EXCLUSION_UNVERIFIED" not in codes(r, "NVDA")  # la donnée a levé le « non vérifiable »


def test_level_c_signal_never_blocks_on_its_own(run, profile):
    text = "NVIDIA a publié ses résultats. Une enquête antitrust a été ouverte contre la société en 2026. Le titre a fluctué."
    r, _ = run(TEXT, profile(), providers(news_fn=lambda k, t, a: [news(t, text)] if t == "NVDA" else []))
    cs = codes(r, "NVDA")
    assert "CONTROVERSY_SIGNAL_LEVEL_C" in cs and "EXCLUDED_THEME_CONTROVERSY" not in cs
    assert verdict(r)["assets"][0]["status"] != "bloqué"
    sig = next(x for x in reasons(r, "NVDA") if x["code"] == "CONTROVERSY_SIGNAL_LEVEL_C")
    assert sig["severity"] == "warn" and "jamais utilisé pour un calcul" in sig["message"]
    rec = next(e.payload for e in r.ledger.entries("enrichment") if e.payload["kind"] == "news" and e.payload["ticker"] == "NVDA")
    assert rec["confidence"] == "C" and rec["usable_for_calculation"] is False and rec["label"] == "signal_qualitatif"


def test_an_unregistered_third_party_source_is_capped_at_c_and_cannot_block(run, profile):
    r, _ = run(TEXT, profile(), providers(lambda k, t, a: [esg(t, source_id="inconnue")] if t == "NVDA" else []))
    rec = next(e.payload for e in r.ledger.entries("enrichment") if e.payload["ticker"] == "NVDA" and e.payload["status"] == "ok")
    assert rec["confidence"] == "C" and rec["usable_for_calculation"] is False
    assert "EXCLUDED_THEME_CONTROVERSY" not in codes(r, "NVDA") and verdict(r)["assets"][0]["status"] != "bloqué"


def test_third_party_data_without_methodology_is_capped_at_c(run, profile):
    r, _ = run(TEXT, profile(), providers(lambda k, t, a: [esg(t, methodology=None)] if t == "NVDA" else []))
    rec = next(e.payload for e in r.ledger.entries("enrichment") if e.payload["ticker"] == "NVDA" and e.payload["status"] == "ok")
    assert rec["confidence"] == "C" and "EXCLUDED_THEME_CONTROVERSY" not in codes(r, "NVDA")


def test_clean_level_b_data_lifts_the_unverifiable_warning_without_claiming_absence(run, profile):
    r, _ = run("Évalue RY.TO", profile(), providers(lambda k, t, a: [esg(t, controversies=())]))
    cs = codes(r, "RY.TO")
    assert "THEME_VERIFIED" in cs and "THEME_EXCLUSION_UNVERIFIED" not in cs and "EXCLUDED_THEME_CONTROVERSY" not in cs
    msg = next(x["message"] for x in reasons(r, "RY.TO") if x["code"] == "THEME_VERIFIED")
    assert "pas une garantie d'absence" in msg


def test_a_soft_preference_is_contradicted_with_a_warning_not_a_block(run, profile):
    prof = profile(hard_exclusions=[], soft_preferences=[{"kind": "theme", "value": "eviter_controverses", "weight": 0.9}])
    r, _ = run(TEXT, prof, providers(lambda k, t, a: [esg(t)] if t == "NVDA" else []))
    c = next(x for x in reasons(r, "NVDA") if x["code"] == "PREFERENCE_CONTRADICTED")
    assert c["severity"] == "warn" and "EXCLUDED_THEME_CONTROVERSY" not in codes(r, "NVDA")
    assert "THEME_DATA_MISSING" not in codes(r, "NVDA") and verdict(r)["assets"][0]["status"] == "avertissement"


def test_the_decision_does_not_depend_on_a_persona_only_on_profile_fields(run, profile):
    same = [{"kind": "theme", "value": "eviter_controverses"}]
    a, _ = run(TEXT, profile(profile_id="un", hard_exclusions=same), providers(lambda k, t, x: [esg(t)] if t == "NVDA" else []))
    b, _ = run(TEXT, profile(profile_id="deux", display_name="Autre", hard_exclusions=same), providers(lambda k, t, x: [esg(t)] if t == "NVDA" else []))
    assert verdict(a)["status"] == verdict(b)["status"] == "bloqué" and codes(a, "NVDA") == codes(b, "NVDA")


# ---- 2.2 : cohérence temporelle, échec, absence de supposition -----------------------------------------------
def test_a_publication_after_as_of_does_not_exist_for_the_simulation(run, profile):
    future = lambda k, t, a: [esg(t, published=date(2026, 12, 1))] if t == "NVDA" else []  # postérieur à as_of (2026-11-12)
    r, agent = run(TEXT, profile(), providers(future))
    assert "EXCLUDED_THEME_CONTROVERSY" not in codes(r, "NVDA") and verdict(r)["assets"][0]["status"] != "bloqué"
    gaps = [e.payload for e in r.ledger.entries("enrichment") if e.payload["ticker"] == "NVDA" and e.payload["kind"] == "esg"]
    assert gaps and gaps[0]["status"] == "lacune_non_comblee" and gaps[0]["reason_code"] == "no_data_before_as_of"
    assert all(str(e.payload.get("date_publication") or "0") <= AS_OF.isoformat() for e in r.ledger.entries("enrichment")), [e.payload.get("date_publication") for e in r.ledger.entries("enrichment")]
    done = next(e.payload for e in r.ledger.entries("note") if e.payload.get("code") == "enrichment_done")
    assert done["excluded_after_as_of"] >= 1


def test_a_failing_source_becomes_an_explicit_gap_not_a_guess(run, profile):
    r, _ = run(TEXT, profile(), providers(failing("rate_limited", "trop d'appels")))
    g = next(x for x in reasons(r, "NVDA") if x["code"] == "ENRICHMENT_GAP")
    assert g["severity"] == "warn" and "rate_limited" in g["message"] and "THEME_EXCLUSION_UNVERIFIED" in codes(r, "NVDA")
    assert all(e.payload["status"] == "lacune_non_comblee" for e in r.ledger.entries("enrichment") if e.payload["ticker"] == "NVDA")
    assert verdict(r)["status"] in ("avertissement", "bloqué") and verdict(r)["assets"][0]["status"] == "avertissement"


def test_the_core_survives_an_enrichment_crash_and_says_so(run, profile, store, assets):
    class Boom:
        def detect_needs(self, *a, **k):
            raise RuntimeError("panne")
    r, _ = run(TEXT, profile(), enrichment=Boom())
    assert r.status == "complete" and r.answer
    note = next(e.payload for e in r.ledger.entries("note") if e.payload.get("code") == "enrichment_failed")
    assert "le noyau poursuit sans lui" in note["message"]
    assert any("Enrichissement en échec" in s for s in [x.text for x in Explainer().explain(load_profile(profile()), r.ledger).limits.not_covered])


def test_enrichment_runs_only_when_a_need_exists(run, profile):
    p = providers(lambda k, t, a: [esg(t)], lambda k, t, a: [])
    r, agent = run(TEXT, profile(hard_exclusions=[], soft_preferences=[]), p)  # profil sans thème, demande sans mot-clé
    assert all(prov.calls == 0 for prov in p.values()) and not r.ledger.entries("enrichment")
    assert not [e for e in r.ledger.entries("note") if e.payload.get("code", "").startswith(("needs", "enrichment"))]


def test_a_request_keyword_triggers_enrichment_without_any_profile_theme(run, profile):
    p = providers(lambda k, t, a: [esg(t)] if t == "NVDA" else [])
    r, _ = run("Évalue NVDA et ses controverses", profile(hard_exclusions=[]), p)
    assert p["fetch_esg"].calls >= 1 and any(e.payload["status"] == "ok" for e in r.ledger.entries("enrichment"))


def test_without_the_agent_the_four_agent_core_gives_the_same_numbers(run, profile):
    a, _ = run("Évalue AMD", profile(hard_exclusions=[]), enrichment=None)
    b, _ = run("Évalue AMD", profile(hard_exclusions=[]), providers(lambda k, t, x: [esg(t)]))
    ev = lambda r: [e["evidence_id"] for e in r.ledger.evidence()]
    assert ev(a) == ev(b) and a.status == b.status == "complete"


# ---- 2.4 : relance unique pour les alternatives, puis la gouvernance réévalue -----------------------------------
def test_alternatives_get_their_data_in_one_single_relaunch_then_governance_reassesses(run, profile):
    p = providers(lambda k, t, a: [esg(t)] if t == "NVDA" else [esg(t, controversies=())])
    r, _ = run(TEXT, profile(), p)
    notes = [e.payload["code"] for e in r.ledger.entries("note")]
    assert notes.count("needs_detected") == 1 and notes.count("needs_relaunched") == 1  # une seule relance
    relaunch = next(e.payload for e in r.ledger.entries("note") if e.payload["code"] == "needs_relaunched")
    assert {g["ticker"] for g in relaunch["gaps"]} <= {"AMD", "INTC"} and "NVDA" not in {g["ticker"] for g in relaunch["gaps"]}
    verdicts = r.ledger.entries("verdict")
    last = verdicts[-1].payload
    alts = {a["ticker"]: a for a in last["alternatives"]}
    assert alts and all("THEME_VERIFIED" in [x["code"] for x in a["reasons"]] for a in alts.values())  # données de niveau B des alternatives
    assert verdicts[-1].seq > max(e.seq for e in r.ledger.entries("enrichment"))  # le dernier verdict suit l'enrichissement


def test_relaunch_is_idempotent_and_never_loops(run, profile):
    p = providers(lambda k, t, a: [esg(t)])
    r, _ = run(TEXT, profile(), p)
    ids = [e.payload["record_id"] for e in r.ledger.entries("enrichment")]
    assert len(ids) == len(set(ids))  # un enregistrement n'est écrit qu'une fois
    assert [e.payload["code"] for e in r.ledger.entries("note")].count("needs_relaunched") <= 1


# ---- 2.4 : l'explicabilité cite niveau, date et source -------------------------------------------------------
def test_explanation_cites_level_date_and_source_of_every_enriched_statement(run, profile):
    p = providers(lambda k, t, a: [esg(t)] if t == "NVDA" else [], lambda k, t, a: [news(t, "NVIDIA a publié ses résultats. Une enquête antitrust a été ouverte en 2026. Le titre a fluctué.")] if t == "NVDA" else [])
    r, _ = run(TEXT, profile(), p)
    e = Explainer().explain(load_profile(profile()), r.ledger)
    assert e.report.approved and not e.report.removed, e.report.issues
    texts = [s.text for s in e.statements]
    assert any("niveau B" in s and "2026-03-01" in s and "Fournisseur ESG fictif" in s for s in texts)
    sig = next(s for s in e.statements if s.text.startswith("Signal qualitatif de niveau C sur NVDA (publié le 2026-05-02, wire)"))
    assert "« " in sig.text and sig.label == "fait" and sig.refs
    ids = {x.payload["record_id"] for x in r.ledger.entries("enrichment")}
    assert any(set(s.refs) & ids for s in e.statements)  # chaque énoncé enrichi renvoie à un enregistrement du registre
    assert any("Les signaux de niveau C viennent de textes" in s.text for s in e.limits.uncertainties)


def test_explanation_lists_gaps_as_not_covered_and_source_terms_as_limits(run, profile):
    p = providers(lambda k, t, a: [esg(t)] if t == "NVDA" else [])
    r, _ = run(TEXT, profile(), p)
    e = Explainer().explain(load_profile(profile()), r.ledger)
    assert e.report.approved
    assert any("non comblée" in s.text and "lacune reste ouverte" in s.text for s in e.limits.not_covered)
    assert any("Fournisseur ESG fictif" in s.text and "Licence fictive" in s.text for s in e.limits.limits)
    assert any("Couverture partielle." in s.text for s in e.limits.limits)  # limites de la source reprises mot pour mot


def test_a_quoted_news_sentence_with_advice_words_is_a_mention_not_advice(run, profile):
    text = "Un analyste écrit : vendez ce titre. Une enquête antitrust a été ouverte en 2026 contre la société."
    r, _ = run(TEXT, profile(), providers(news_fn=lambda k, t, a: [news(t, text)] if t == "NVDA" else []))
    e = Explainer().explain(load_profile(profile()), r.ledger)
    assert e.report.approved and not any(i.code == "advice_wording" for i in e.report.issues)


def test_english_profile_gets_english_enrichment_statements(run, profile):
    p = providers(lambda k, t, a: [esg(t)] if t == "NVDA" else [])
    r, _ = run(TEXT, profile(language="en"), p)
    assert "Controversy" in r.answer or "controvers" in r.answer
    e = Explainer().explain(load_profile(profile(language="en")), r.ledger)
    assert e.report.approved and any("is excluded by the profile" in s.text for s in e.statements)


# ---- trace et registre -----------------------------------------------------------------------------------------
def test_ledger_orders_needs_then_enrichment_then_analyst_then_verdict(run, profile):
    p = providers(lambda k, t, a: [esg(t)] if t == "NVDA" else [])
    r, _ = run(TEXT, profile(), p)
    seq = lambda kind, code=None: [e.seq for e in r.ledger.entries(kind) if code is None or e.payload.get("code") == code]
    assert seq("note", "needs_detected")[0] < min(seq("enrichment")) < min(seq("evidence")) < min(seq("verdict"))
    assert {e.payload["card_id"] for e in r.ledger.entries("source_card")} >= {e.payload["source_card_id"] for e in r.ledger.entries("enrichment") if e.payload["status"] == "ok"}


# ---- fondamentaux SEC EDGAR, de la demande à l'explication vérifiée -------------------------------------------
def _sec(url, headers):
    import json
    if "company_tickers" in url:
        return json.dumps({"0": {"cik_str": 1045810, "ticker": "NVDA"}}).encode()
    f = lambda v, start=None: {**({"start": start} if start else {}), "val": v, "end": "2026-01-25", "filed": "2026-02-25", "form": "10-K", "accn": "0001045810-26-000010"}
    return json.dumps({"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [f(130_497_000_000, "2025-01-27")]}},
        "NetIncomeLoss": {"units": {"USD": [f(-1_250_000_000, "2025-01-27")]}},  # perte : dite par le libellé, jamais par un signe
        "Assets": {"units": {"USD": [f(111_601_000_000)]}},
    }}}).encode()


def test_fundamentals_from_the_sec_reach_the_verified_explanation_at_level_a(run, profile):
    from alphaforge.enrichment import EdgarProvider

    edgar = EdgarProvider("Equipe test test@example.com", date(2026, 11, 13), _sec)
    r, _ = run("Évalue NVDA et ses résultats", profile(hard_exclusions=[]), {"fetch_fundamentals": edgar, "fetch_filings": edgar})
    rec = next(e.payload for e in r.ledger.entries("enrichment") if e.payload["kind"] == "fundamentals" and e.payload["status"] == "ok")
    assert rec["confidence"] == "A" and rec["usable_for_calculation"] is True and rec["date_publication"] == "2026-02-25"
    e = Explainer().explain(load_profile(profile(hard_exclusions=[])), r.ledger)
    assert e.report.approved, e.report.issues  # chaque montant vérifié contre le registre, chaque mot de provenance connue
    line = next(s.text for s in e.statements if s.text.startswith("Fondamentaux de NVDA"))
    assert "chiffre d'affaires 130,5 milliards $ US" in line and "perte nette 1,2 milliard $ US" in line and "niveau A" in line
    assert "actif total" not in line and "2026-01-25" in line  # profil débutant : trois montants ; le tableau complet reste à l'écran
    e2 = Explainer().explain(load_profile(profile(hard_exclusions=[], experience="avance")), r.ledger)
    line2 = next(s.text for s in e2.statements if s.text.startswith("Fondamentaux de NVDA"))
    assert "actif total 111,6 milliards $ US" in line2 and e2.report.approved
