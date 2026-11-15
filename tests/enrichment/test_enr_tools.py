import json
from datetime import date

import pytest
from pydantic import ValidationError

from alphaforge.enrichment import (
    CachedProvider, CachingProvider, EdgarProvider, EnrichmentRecord, ProviderError, detect_needs, extract_facts,
    fetch_esg, fetch_filings, point_in_time_filter, source_card,
)
from alphaforge.enrichment.facts import Candidate
from alphaforge.enrichment.records import assign_confidence

from helpers_enr import AS_OF, FakeProvider, esg_item, failing, news_item, raw


# ---- detect_needs ---------------------------------------------------------------


def test_profile_themes_trigger_needs_without_persona_logic(store, mateo, parse):
    report = detect_needs(mateo, parse("Évalue NVDA"), store, AS_OF)
    kinds = {(g.kind, g.ticker) for g in report.fillable}
    assert kinds == {("esg", "NVDA"), ("controversies", "NVDA")}
    assert all(g.triggered_by for g in report.gaps)


def test_neutral_profile_and_plain_request_need_nothing(store, neutral_profile, parse):
    assert detect_needs(neutral_profile, parse("Évalue NVDA"), store, AS_OF).gaps == []


def test_request_text_triggers_needs(store, neutral_profile, parse):
    kinds = {g.kind for g in detect_needs(neutral_profile, parse("Évalue NVDA : dette et résultats récents"), store, AS_OF).gaps}
    assert kinds == {"fundamentals", "news"}


def test_not_applicable_classes_are_listed_but_not_fillable(store, mateo, parse):
    report = detect_needs(mateo, parse("Compare BTC-USD et AGG"), store, AS_OF)
    assert report.fillable == [g for g in report.gaps if g.kind == "etf_holdings"]  # seul l'AGG a un besoin de composition
    btc = [g for g in report.unfillable if g.ticker == "BTC-USD"]
    assert btc and all(g.reason == "non_applicable_crypto" for g in btc)


def test_etf_gets_holdings_need_for_esg_by_transparency(store, mateo, parse):
    report = detect_needs(mateo, parse("Évalue ESGV"), store, AS_OF)
    assert ("etf_holdings", "ESGV") in {(g.kind, g.ticker) for g in report.fillable}


def test_gap_disappears_once_ledger_has_ok_data(store, mateo, parse, registry):
    from alphaforge.enrichment import EnrichmentAgent
    from alphaforge.orchestrator import Ledger

    ledger = Ledger()
    needs = detect_needs(mateo, parse("Évalue NVDA"), store, AS_OF, ledger)
    EnrichmentAgent({"fetch_esg": FakeProvider(lambda k, t, a: [esg_item()]), "fetch_news": FakeProvider(lambda k, t, a: [news_item()])},
                    sources=registry).run(needs, ledger)
    assert detect_needs(mateo, parse("Évalue NVDA"), store, AS_OF, ledger).gaps == []


# ---- point_in_time_filter -------------------------------------------------------


def test_pit_filter_drops_future_publications_and_keeps_the_boundary():
    on_day, later = esg_item(published=AS_OF), esg_item(published=date(2025, 7, 1))
    res = point_in_time_filter([on_day, later], AS_OF)
    assert res.kept == [on_day] and res.excluded == [later]


def test_record_model_refuses_post_as_of_publication():
    with pytest.raises(ValidationError):
        EnrichmentRecord(record_id="x", status="ok", kind="esg", need="esg", ticker="NVDA", as_of=AS_OF, tool="t", tool_version="1",
                         date_publication=date(2025, 7, 1), date_observation=date(2025, 7, 1), confidence="B",
                         usable_for_calculation=True, source_card_id="s", value={"a": 1})


# ---- extract_facts --------------------------------------------------------------


TEXT = "Le revenu a atteint 5 milliards de dollars. Une enquête a été ouverte en 2025. Phrase neutre sans intérêt ici."


def test_rule_extractor_keeps_only_topic_sentences_verbatim():
    res = extract_facts(TEXT, url="https://x.y/a", date_publication=date(2025, 5, 1), topic="controversies", as_of=AS_OF)
    assert [f.statement for f in res.facts] == ["Une enquête a été ouverte en 2025."]
    f = res.facts[0]
    assert f.url == "https://x.y/a" and f.excerpt in TEXT and f.date_observation == f.date_publication


@pytest.mark.parametrize("kw,fragment", [
    (dict(url=""), "URL"),
    (dict(url="ftp://x.y"), "URL"),
    (dict(url="https://x.y", date_publication=None), "date de publication"),
    (dict(url="https://x.y", date_publication=date(2025, 8, 1)), "après as_of"),
])
def test_fact_without_trace_is_rejected(kw, fragment):
    kw = {"date_publication": date(2025, 5, 1), **kw}
    res = extract_facts(TEXT, as_of=AS_OF, **kw)
    assert res.facts == [] and fragment in res.rejected[0].reason


def test_hallucinated_candidates_are_rejected_with_reason():
    class Llm:
        def propose(self, text, topic):
            return [
                Candidate(statement="Revenu de 7 milliards", excerpt="Le revenu a atteint 5 milliards de dollars."),  # nombre inventé
                Candidate(statement="Fraude massive", excerpt="Une fraude massive a été révélée."),  # extrait inexistant
                Candidate(statement="Revenu de 5 milliards", excerpt="Le revenu a atteint 5 milliards de dollars."),  # valide
                Candidate(statement="Revenu de 5 milliards", excerpt="Le revenu a atteint 5 milliards de dollars."),  # doublon
                Candidate(statement="Long", excerpt=TEXT * 5),  # extrait trop long
            ]
    res = extract_facts(TEXT, url="https://x.y", date_publication=date(2025, 5, 1), extractor=Llm())
    assert [f.statement for f in res.facts] == ["Revenu de 5 milliards"]
    reasons = " | ".join(r.reason for r in res.rejected)
    assert "absent(s) de l'extrait" in reasons and "introuvable" in reasons and "trop long" in reasons


def test_broken_extractor_yields_no_fact():
    class Boom:
        def propose(self, text, topic):
            raise RuntimeError("x")
    res = extract_facts(TEXT, url="https://x.y", date_publication=date(2025, 5, 1), extractor=Boom())
    assert res.facts == [] and "échec" in res.rejected[0].reason


# ---- source_card et confiance -----------------------------------------------------


def test_source_card_known_and_unknown(registry):
    known = source_card("sec_edgar", date(2026, 11, 13), processing=["filtre"])
    assert known.source_class == "official" and known.terms_url and known.limits
    unknown = source_card("blog_x", date(2026, 11, 13))
    assert unknown.source_class == "unknown" and "non documentées" in unknown.reuse_terms
    assert source_card("sec_edgar", date(2026, 11, 13), processing=["filtre"]).card_id == known.card_id  # déterministe


@pytest.mark.parametrize("kind,cls,structured,method,expected", [
    ("filings", "official", True, False, "A"),
    ("esg", "third_party", True, True, "B"),
    ("esg", "third_party", True, False, "C"),  # sans méthodologie
    ("esg", "unknown", True, True, "C"),
    ("news", "official", True, True, "C"),
    ("esg", "third_party", False, True, "C"),  # texte
])
def test_confidence_levels(kind, cls, structured, method, expected):
    assert assign_confidence(kind, cls, structured, method)[0] == expected


# ---- fetch_* et fournisseurs --------------------------------------------------------


def test_fetch_turns_provider_errors_into_codes():
    assert fetch_esg(FakeProvider(failing("rate_limited")), "NVDA", AS_OF).error_code == "rate_limited"

    def boom(k, t, a):
        raise RuntimeError("inattendu")
    assert fetch_esg(FakeProvider(boom), "NVDA", AS_OF).error_code == "provider_failure"


def test_fetch_rejects_mismatched_provider_answer():
    res = fetch_esg(FakeProvider(lambda k, t, a: [esg_item("AAPL")]), "NVDA", AS_OF)
    assert res.error_code == "provider_mismatch" and res.items == []


def test_cached_provider_roundtrip_and_path_safety(tmp_path):
    cache = CachedProvider(tmp_path)
    cache.store("esg", "NVDA", [esg_item()])
    items = cache.fetch("esg", "NVDA", AS_OF)
    assert items[0].from_cache and items[0].ticker == "NVDA"
    with pytest.raises(ProviderError) as e:
        cache.fetch("esg", "../../etc/passwd", AS_OF)
    assert e.value.code == "invalid_ticker"
    with pytest.raises(ProviderError) as e:
        cache.fetch("esg", "AAPL", AS_OF)
    assert e.value.code == "not_cached"
    (tmp_path / "esg" / "BAD.json").write_text("{pas du json", encoding="utf-8")
    with pytest.raises(ProviderError) as e:
        cache.fetch("esg", "BAD", AS_OF)
    assert e.value.code == "cache_corrupt"


def test_caching_provider_falls_back_to_cache_when_live_fails(tmp_path):
    cache = CachedProvider(tmp_path)
    live_ok = FakeProvider(lambda k, t, a: [esg_item()])
    assert not CachingProvider(live_ok, cache).fetch("esg", "NVDA", AS_OF)[0].from_cache
    items = CachingProvider(FakeProvider(failing()), cache).fetch("esg", "NVDA", AS_OF)
    assert items[0].from_cache
    with pytest.raises(ProviderError) as e:  # ni direct ni cache : l'erreur d'origine remonte
        CachingProvider(FakeProvider(failing("rate_limited")), cache).fetch("esg", "AAPL", AS_OF)
    assert e.value.code == "rate_limited"


def _edgar_http(url, headers):
    if "company_tickers" in url:
        return json.dumps({"0": {"cik_str": 1045810, "ticker": "NVDA"}}).encode()
    return json.dumps({"filings": {"recent": {
        "form": ["10-Q", "4", "10-K", "8-K"], "filingDate": ["2026-05-01", "2025-03-01", "2025-02-26", "2025-06-30"],
        "reportDate": ["2026-03-29", "", "2025-01-26", ""], "accessionNumber": ["1-26-1", "1-25-2", "1-25-3", "1-25-4"],
        "primaryDocument": ["a.htm", "b.xml", "c.htm", "d.htm"]}}}).encode()


def test_edgar_provider_filters_forms_and_dates_and_refuses_unsupported():
    p = EdgarProvider("Equipe test test@example.com", date(2026, 11, 13), _edgar_http)
    res = fetch_filings(p, "NVDA", AS_OF)
    assert [i.structured["form"] for i in res.items] == ["10-K", "8-K"]  # 10-Q postérieur et formulaire 4 écartés
    assert res.items[0].url == "https://www.sec.gov/Archives/edgar/data/1045810/1253/c.htm"
    assert res.items[0].date_observation == date(2025, 1, 26)
    assert fetch_filings(p, "RY.TO", AS_OF).error_code == "unsupported"
    assert fetch_filings(p, "BTC-USD", AS_OF).error_code == "unsupported"
    assert fetch_filings(p, "ZZZZ", AS_OF).error_code == "not_found"
    assert fetch_filings(EdgarProvider(None, date(2026, 11, 13), _edgar_http), "NVDA", AS_OF).error_code == "auth_required"
    assert fetch_esg(p, "NVDA", AS_OF).error_code == "unsupported"
