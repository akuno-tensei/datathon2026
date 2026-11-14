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


# ---- allocation décrite dans la demande -------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("Construis un portefeuille avec 60 % AAPL et 40 % NVDA", {"AAPL": 0.6, "NVDA": 0.4}),
    ("Portefeuille : AAPL 60 %, NVDA 40 %", {"AAPL": 0.6, "NVDA": 0.4}),
    ("Simule 50% en AAPL, 30,5 % de BTC-USD et 19,5 % dans RY.TO", {"AAPL": 0.5, "BTC-USD": 0.305, "RY.TO": 0.195}),
    ("scénario AAPL à 70 % et NVDA à 30 %", {"AAPL": 0.7, "NVDA": 0.3}),
    ("60 percent of AAPL and 40 percent of NVDA", {"AAPL": 0.6, "NVDA": 0.4}),
])
def test_weights_are_extracted_in_both_orders(assets, text, expected):
    r = RuleBasedParser().parse(text, assets)
    assert r.weights == pytest.approx(expected) and r.intent == "build_scenario"
    assert set(expected) <= set(r.tickers)


def test_no_weights_when_none_are_given_and_nothing_is_invented(assets):
    for text in ("Construis un scénario avec AAPL et NVDA", "Compare AAPL et NVDA", "Évalue AAPL en 2022"):
        assert RuleBasedParser().parse(text, assets).weights == {}


def test_incomplete_allocation_still_builds_a_scenario_so_the_analyst_can_refuse_it(assets):
    r = RuleBasedParser().parse("60 % AAPL et NVDA", assets)
    assert r.weights == {"AAPL": 0.6} and r.intent == "build_scenario" and r.tickers == ["AAPL", "NVDA"]


def test_rebalancing_hypothesis_is_read_from_the_request(assets):
    p = RuleBasedParser()
    assert p.parse("scénario 50 % AAPL et 50 % NVDA en achat-et-conservation", assets).rebalancing == "buy_and_hold"
    assert p.parse("scénario 50 % AAPL et 50 % NVDA, buy and hold", assets).rebalancing == "buy_and_hold"
    assert p.parse("scénario 50 % AAPL et 50 % NVDA rééquilibré chaque mois", assets).rebalancing == "rebalanced"
    assert p.parse("scénario 50 % AAPL et 50 % NVDA", assets).rebalancing is None


def test_llm_parser_cannot_invent_or_remove_weights(assets):
    from alphaforge.orchestrator import LLMParser

    class Client:
        def complete(self, system, user):
            return '{"intent": "compare_assets", "mentioned_symbols": ["AAPL", "NVDA"], "scope_flags": [], "weights": {"AAPL": 0.9}}'

    p = LLMParser(Client())
    assert p.parse("Compare AAPL et NVDA", assets).weights == {}  # le modèle propose des poids : ignorés
    r = p.parse("60 % AAPL et 40 % NVDA", assets)
    assert r.weights == pytest.approx({"AAPL": 0.6, "NVDA": 0.4}) and r.intent == "build_scenario"
