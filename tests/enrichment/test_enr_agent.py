from datetime import date

import pytest

from alphaforge.enrichment import EnrichmentAgent, EnrichmentRecord, LedgerWriteError, detect_needs, ledger_write, source_card
from alphaforge.enrichment.records import gap_record, structured_record
from alphaforge.explainability.ledger_read import ledger_read
from alphaforge.orchestrator import Ledger

from helpers_enr import AS_OF, FakeProvider, esg_item, failing, news_item


def enriched(mateo, parse, store, registry, providers, request="Évalue NVDA"):
    ledger = Ledger()
    agent = EnrichmentAgent(providers, sources=registry)
    report = agent.run(detect_needs(mateo, parse(request), store, AS_OF), ledger)
    return agent, ledger, report


def records(ledger, status=None):
    out = [e.payload for e in ledger.entries("enrichment")]
    return [r for r in out if status is None or r["status"] == status]


def test_full_chain_writes_sourced_dated_records(mateo, parse, store, registry):
    _, ledger, report = enriched(mateo, parse, store, registry, {
        "fetch_esg": FakeProvider(lambda k, t, a: [esg_item()]), "fetch_news": FakeProvider(lambda k, t, a: [news_item()])})
    ok = records(ledger, "ok")
    assert report.written and not report.gaps_written and not report.degraded
    esg = next(r for r in ok if r["kind"] == "esg" and r["need"] == "esg")
    assert esg["confidence"] == "B" and esg["usable_for_calculation"] is True
    assert esg["date_publication"] == "2025-03-01" and esg["url"].startswith("https://")
    news = next(r for r in ok if r["kind"] == "news")
    assert news["confidence"] == "C" and news["usable_for_calculation"] is False and news["label"] == "signal_qualitatif"
    fact = news["facts"][0]
    assert {"statement", "excerpt", "url", "date_publication", "date_observation"} <= set(fact)
    assert "enquête" in fact["statement"] and "résultats" not in fact["statement"]  # seul le sujet « controverses » est gardé
    # chaque donnée pointe vers une fiche source écrite au registre
    card_ids = {e.payload["card_id"] for e in ledger.entries("source_card")}
    assert {r["source_card_id"] for r in ok} <= card_ids


def test_future_publications_never_reach_the_ledger(mateo, parse, store, registry):
    provider = FakeProvider(lambda k, t, a: [esg_item(published=date(2025, 3, 1)), esg_item(published=date(2026, 3, 1))])
    _, ledger, report = enriched(mateo, parse, store, registry, {"fetch_esg": provider})
    assert report.excluded_after_as_of == 1  # compté une seule fois malgré deux besoins qui partagent cet appel
    assert all(r["date_publication"] <= AS_OF.isoformat() for r in records(ledger, "ok"))
    assert "2026-03-01" not in ledger_read(ledger).corpus


def test_only_future_data_becomes_a_gap_not_a_guess(mateo, parse, store, registry):
    provider = FakeProvider(lambda k, t, a: [esg_item(published=date(2026, 3, 1))])
    _, ledger, report = enriched(mateo, parse, store, registry, {"fetch_esg": provider})
    assert records(ledger, "ok") == [] and report.degraded
    assert {r["reason_code"] for r in records(ledger, "lacune_non_comblee") if r["kind"] == "esg"} == {"no_data_before_as_of"}


def test_provider_failure_writes_gap_and_never_raises(mateo, parse, store, registry):
    _, ledger, report = enriched(mateo, parse, store, registry, {
        "fetch_esg": FakeProvider(failing("rate_limited")), "fetch_news": FakeProvider(lambda k, t, a: [news_item()])})
    gaps = records(ledger, "lacune_non_comblee")
    assert any(g["reason_code"] == "rate_limited" and g["need"] == "esg" for g in gaps)
    assert all(g["value"] is None and g["confidence"] is None for g in gaps)
    # controverses : l'ESG a échoué, mais la nouvelle comble le besoin en niveau C
    assert any(r["need"] == "controversies" and r["confidence"] == "C" for r in records(ledger, "ok"))
    assert report.degraded


def test_missing_provider_is_a_gap(mateo, parse, store, registry):
    _, ledger, _ = enriched(mateo, parse, store, registry, {})
    assert {g["reason_code"] for g in records(ledger, "lacune_non_comblee")} == {"no_provider"}


def test_no_matching_text_is_an_open_gap_with_honest_message(mateo, parse, store, registry):
    quiet = news_item(text="Le titre a fluctué dans un marché calme aujourd'hui.")
    _, ledger, _ = enriched(mateo, parse, store, registry, {"fetch_news": FakeProvider(lambda k, t, a: [quiet])}, "Évalue NVDA et ses controverses")
    gap = next(g for g in records(ledger, "lacune_non_comblee") if g["need"] == "controversies" and g["kind"] == "news")
    assert gap["reason_code"] == "no_matching_data" and "pas la preuve" in gap["message"]


def test_esg_without_controversies_does_not_answer_controversies(mateo, parse, store, registry):
    item = esg_item()
    item = item.model_copy(update={"structured": {"rating": "A"}})
    _, ledger, _ = enriched(mateo, parse, store, registry, {"fetch_esg": FakeProvider(lambda k, t, a: [item])})
    ok_needs = {r["need"] for r in records(ledger, "ok")}
    assert ok_needs == {"esg"}
    assert any(g["need"] == "controversies" for g in records(ledger, "lacune_non_comblee"))


def test_third_party_without_methodology_is_capped_at_c(mateo, parse, store, registry):
    item = esg_item().model_copy(update={"methodology": None})
    _, ledger, _ = enriched(mateo, parse, store, registry, {"fetch_esg": FakeProvider(lambda k, t, a: [item])})
    rec = next(r for r in records(ledger, "ok") if r["need"] == "esg")
    assert rec["confidence"] == "C" and rec["usable_for_calculation"] is False
    assert any(n["code"] == "METHODOLOGY_MISSING" for n in rec["notices"])


def test_unregistered_source_is_capped_at_c_and_card_says_so(mateo, parse, store):
    _, ledger, _ = enriched(mateo, parse, store, None, {"fetch_esg": FakeProvider(lambda k, t, a: [esg_item()])})
    rec = next(r for r in records(ledger, "ok") if r["need"] == "esg")
    assert rec["confidence"] == "C"
    card = ledger.entries("source_card")[0].payload
    assert card["source_class"] == "unknown" and "non documentées" in card["reuse_terms"]


def test_rerun_is_idempotent(mateo, parse, store, registry):
    providers = {"fetch_esg": FakeProvider(lambda k, t, a: [esg_item()]), "fetch_news": FakeProvider(lambda k, t, a: [news_item()])}
    agent, ledger, _ = enriched(mateo, parse, store, registry, providers)
    n = len(ledger)
    agent.run(detect_needs(mateo, parse("Évalue NVDA"), store, AS_OF), ledger)
    assert len(ledger) == n


def test_shared_fetch_is_called_once_per_tool_and_ticker(mateo, parse, store, registry):
    esg = FakeProvider(lambda k, t, a: [esg_item()])
    enriched(mateo, parse, store, registry, {"fetch_esg": esg, "fetch_news": FakeProvider(lambda k, t, a: [news_item()])})
    assert esg.calls == 1  # besoins « esg » et « controversies » partagent fetch_esg


def test_unfillable_needs_are_reported_not_fetched(mateo, parse, store, registry):
    provider = FakeProvider(lambda k, t, a: [])
    _, ledger, report = enriched(mateo, parse, store, registry, {"fetch_esg": provider}, "Évalue BTC-USD")
    assert provider.calls == 0 and ledger.entries("enrichment") == []
    assert any("non_applicable_crypto" in u for u in report.unfillable)


def test_run_requires_as_of(mateo, parse, store, registry):
    gaps = detect_needs(mateo, parse("Évalue NVDA"), store, AS_OF).gaps
    with pytest.raises(ValueError):
        EnrichmentAgent({}, sources=registry).run(gaps, Ledger())


# ---- ledger_write -------------------------------------------------------------------


def _ok_record():
    item = esg_item()
    card = source_card("esg_vendor", item.retrieved_on, methodology=item.methodology)
    return structured_record(item, card.model_copy(update={"source_class": "third_party"}), "esg", AS_OF, "fetch_esg"), card


def test_ledger_write_requires_matching_card():
    rec, card = _ok_record()
    with pytest.raises(LedgerWriteError):
        ledger_write(Ledger(), rec, None)
    with pytest.raises(LedgerWriteError):
        ledger_write(Ledger(), rec, card.model_copy(update={"card_id": "sc_autre"}))


def test_ledger_write_refuses_collection_before_observation():
    rec, card = _ok_record()
    with pytest.raises(LedgerWriteError):
        ledger_write(Ledger(), rec, card.model_copy(update={"retrieved_on": date(2020, 1, 1)}))


def test_ledger_write_is_idempotent_and_writes_card_once():
    rec, card = _ok_record()
    ledger = Ledger()
    assert ledger_write(ledger, rec, card) is not None and ledger_write(ledger, rec, card) is None
    assert len(ledger.entries("source_card")) == 1 and len(ledger.entries("enrichment")) == 1
    assert ledger.entries("enrichment")[0].evidence_id == rec.record_id


def test_gap_record_needs_no_card_and_forbids_data():
    ledger = Ledger()
    assert ledger_write(ledger, gap_record("esg", "esg", "NVDA", AS_OF, "fetch_esg", "unavailable", "x")) is not None
    with pytest.raises(Exception):
        EnrichmentRecord(record_id="x", status="lacune_non_comblee", kind="esg", need="esg", ticker="NVDA", as_of=AS_OF, tool="t",
                         tool_version="1", reason_code="r", value={"a": 1})


def test_enrichment_entries_do_not_disturb_other_agents(mateo, parse, store, registry):
    from alphaforge.governance.facts import build_facts

    _, ledger, _ = enriched(mateo, parse, store, registry, {"fetch_esg": FakeProvider(lambda k, t, a: [esg_item()])})
    view = ledger_read(ledger)
    assert view.evidence == [] and build_facts(view.evidence) == {}
    assert any(ref.startswith("seq:") for ref in view.refs)  # les entrées restent citables par leur numéro
