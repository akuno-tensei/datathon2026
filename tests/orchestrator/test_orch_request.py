import json

import pytest

from alphaforge.orchestrator import LLMParser, RuleBasedParser


class FakeClient:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def complete(self, system, user):
        self.calls.append((system, user))
        if self.error:
            raise self.error
        return self.reply


@pytest.fixture
def parse(assets):
    return lambda text: RuleBasedParser().parse(text, assets)


# ---- actifs ---------------------------------------------------------------


def test_ticker_root_and_full_symbol(parse):
    assert parse("Évalue RY").tickers == ["RY.TO"]
    assert parse("Évalue RY.TO").tickers == ["RY.TO"]
    assert parse("Évalue BTC-USD").tickers == ["BTC-USD"]
    assert parse("Évalue BTC").tickers == ["BTC-USD"]


def test_company_name_alias(parse):
    assert parse("Que penses-tu d'Apple ?").tickers == ["AAPL"]


def test_ambiguous_name_alias_is_dropped(parse):
    # « Canadian » désigne CNQ et CNR : ne doit désigner aucun des deux.
    assert parse("Parle-moi d'une société canadian").tickers == []


def test_lowercase_short_words_are_not_tickers(parse):
    r = parse("ry est un mot, pas un titre")
    assert r.tickers == [] and r.unknown_tickers == []


def test_order_of_appearance_and_no_duplicates(parse):
    assert parse("NVDA puis AAPL puis NVDA").tickers == ["NVDA", "AAPL"]


def test_unknown_symbols_are_reported_not_dropped(parse):
    r = parse("Évalue ZZZZ.TO et $FAKE")
    assert r.tickers == [] and set(r.unknown_tickers) == {"ZZZZ.TO", "FAKE"}


def test_finance_acronyms_are_not_unknown_tickers(parse):
    r = parse("Un FNB ESG en CAD ou en USD selon l'IPC et la TSX")
    assert r.unknown_tickers == []


# ---- intention ------------------------------------------------------------


@pytest.mark.parametrize(
    "text,intent",
    [
        ("Compare AAPL et NVDA", "compare_assets"),
        ("AAPL vs NVDA", "compare_assets"),
        ("Construis un scénario de portefeuille avec RY et AAPL", "build_scenario"),
        ("Quel est le risque de NVDA ?", "explain_risk"),
        ("Évalue AAPL", "evaluate_asset"),
        ("AAPL", "evaluate_asset"),
        ("AAPL NVDA", "compare_assets"),
        ("Bonjour", "unknown"),
    ],
)
def test_intent(parse, text, intent):
    assert parse(text).intent == intent


def test_compare_with_single_asset_degrades_to_evaluate(parse):
    assert parse("Compare AAPL").intent == "evaluate_asset"


# ---- signaux de périmètre -------------------------------------------------


@pytest.mark.parametrize(
    "text,flag",
    [
        ("Prédis NVDA l'an prochain", "asks_prediction"),
        ("What will NVDA do next year? Please forecast", "asks_prediction"),
        ("Achète 100 actions de NVDA", "asks_transaction"),
        ("Passe un ordre sur AAPL", "asks_transaction"),
        ("Dois-je acheter NVDA ?", "asks_personal_advice"),
        ("Que me conseilles-tu ?", "asks_personal_advice"),
    ],
)
def test_scope_flags_detected(parse, text, flag):
    assert flag in parse(text).scope_flags


def test_neutral_request_has_no_flag(parse):
    assert parse("Évalue la volatilité de AAPL en 2022").scope_flags == []


def test_parser_does_not_refuse(parse):
    # Refuser relève de la gouvernance : le parseur signale seulement.
    r = parse("Prédis NVDA l'an prochain")
    assert r.tickers == ["NVDA"] and r.intent != "unknown"


# ---- LLMParser ------------------------------------------------------------


def _reply(**kw):
    base = {"intent": "compare_assets", "mentioned_symbols": ["Apple", "NVDA"], "scope_flags": []}
    base.update(kw)
    return "Voici : " + json.dumps(base) + " fin"


def test_llm_parser_uses_valid_reply(assets):
    r = LLMParser(FakeClient(_reply())).parse("compare Apple et NVDA", assets)
    assert r.parser == "llm" and r.intent == "compare_assets" and r.tickers == ["AAPL", "NVDA"]


def test_llm_parser_receives_user_text_and_json_system_prompt(assets):
    c = FakeClient(_reply())
    LLMParser(c).parse("compare Apple et NVDA", assets)
    system, user = c.calls[0]
    assert user == "compare Apple et NVDA" and "JSON" in system


def test_llm_cannot_invent_tickers(assets):
    r = LLMParser(FakeClient(_reply(mentioned_symbols=["NOTREAL"]))).parse("Bonjour", assets)
    assert r.tickers == []


def test_llm_cannot_drop_assets_named_in_text(assets):
    r = LLMParser(FakeClient(_reply(mentioned_symbols=[]))).parse("compare AAPL et NVDA", assets)
    assert r.tickers == ["AAPL", "NVDA"]


def test_llm_cannot_disable_scope_flags(assets):
    r = LLMParser(FakeClient(_reply(scope_flags=[]))).parse("Prédis NVDA l'an prochain", assets)
    assert "asks_prediction" in r.scope_flags


def test_llm_unknown_flag_is_ignored(assets):
    r = LLMParser(FakeClient(_reply(scope_flags=["asks_everything"]))).parse("AAPL", assets)
    assert r.scope_flags == []


@pytest.mark.parametrize(
    "client",
    [
        FakeClient("pas du json"),
        FakeClient('{"intent": "hack"}'),
        FakeClient('{"mentioned_symbols": []}'),
        FakeClient(error=ValueError("boom")),
        FakeClient(error=RuntimeError("réseau ou quota Bedrock indisponible")),
    ],
)
def test_llm_invalid_reply_falls_back_to_rules(assets, client):
    r = LLMParser(client).parse("Évalue TSLA ou AAPL", assets)
    assert r.parser == "rules(fallback)" and r.tickers == ["AAPL"]
