"""Intégration AWS sans réseau : Bedrock et S3 sont remplacés par des doublures. Une panne ne bloque jamais l'analyse."""
import json

import pytest

from alphaforge.app import pipeline
from alphaforge.app.archive import S3Archive, archive_notes
from alphaforge.app.pipeline import AlphaForge
from alphaforge.orchestrator import LLMParser, RuleBasedParser


class FakeS3:
    def __init__(self, fail=False):
        self.objects, self.fail = {}, fail

    def put_object(self, Bucket, Key, Body, ContentType, ServerSideEncryption):
        if self.fail:
            raise PermissionError("AccessDenied")
        assert ServerSideEncryption == "AES256"
        self.objects[(Bucket, Key)] = Body


class FakeLLM:
    model_id = "fake-model"

    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.systems = reply, error, []

    def complete(self, system, user):
        self.systems.append(system)
        if self.error:
            raise self.error
        return self.reply


@pytest.fixture
def forge(tmp_path):
    return AlphaForge.from_directory(user_profiles_dir=tmp_path, parser=RuleBasedParser())


def test_bedrock_is_off_under_tests_and_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ALPHAFORGE_LLM", "0")
    assert isinstance(pipeline.build_parser(), RuleBasedParser)


def test_the_llm_sees_the_universe_so_it_can_map_names_to_symbols(forge):
    llm = FakeLLM(json.dumps({"intent": "compare_assets", "mentioned_symbols": ["GLD", "TLT"], "scope_flags": []}))
    r = LLMParser(llm).parse("Lequel est le moins risqué entre l'or et les obligations américaines à long terme ?", forge.assets)
    assert r.parser == "llm" and r.tickers == ["GLD", "TLT"]
    assert "GLD=SPDR Gold Shares" in llm.systems[0] and "Ne déduis jamais" in llm.systems[0]


def test_an_unknown_intent_with_assets_becomes_a_simple_analysis_like_the_rules(forge):
    llm = FakeLLM(json.dumps({"intent": "unknown", "mentioned_symbols": ["BTC-USD"], "scope_flags": ["asks_prediction"]}))
    r = LLMParser(llm).parse("Combien vaudra le bitcoin l'an prochain ?", forge.assets)
    assert r.intent == "evaluate_asset" and "asks_prediction" in r.scope_flags  # le refus de prédire reste porté par le signal


def test_a_bedrock_failure_falls_back_to_rules_and_the_analysis_completes(tmp_path):
    f = AlphaForge.from_directory(user_profiles_dir=tmp_path, parser=LLMParser(FakeLLM(error=TimeoutError("lent"))))
    a = f.analyze("Évalue NVDA", "robert", enrichment=False)
    assert a.result.status == "complete" and a.result.request.parser == "rules(fallback)"


def test_each_analysis_is_archived_encrypted_with_ledger_and_summary(tmp_path):
    s3 = FakeS3()
    f = AlphaForge.from_directory(user_profiles_dir=tmp_path, parser=RuleBasedParser(), archive=S3Archive("bkt", client=s3))
    a = f.analyze("Évalue NVDA", "robert", enrichment=False)
    keys = sorted(k for _, k in s3.objects)
    assert len(keys) == 2 and keys[0].endswith("/ledger.jsonl") and keys[1].endswith("/summary.json")
    lines = s3.objects[("bkt", keys[0])].decode().splitlines()
    assert len(lines) == len(a.result.ledger) and json.loads(lines[0])["seq"] == 1
    summary = json.loads(s3.objects[("bkt", keys[1])])
    assert summary["profile_id"] == "robert" and summary["tickers"] == ["NVDA"] and summary["n_evidence"] == len(a.result.ledger.evidence())
    assert summary["ledger_sha256_12"] in keys[0]  # l'empreinte du registre est dans le chemin : rejeu vérifiable
    assert a.archive_notes and a.archive_notes[0].startswith("Registre archivé dans Amazon S3 : s3://bkt/runs/")


def test_an_s3_failure_is_said_and_never_breaks_the_analysis(tmp_path):
    f = AlphaForge.from_directory(user_profiles_dir=tmp_path, parser=RuleBasedParser(), archive=S3Archive("bkt", client=FakeS3(fail=True)))
    a = f.analyze("Évalue NVDA", "robert", enrichment=False)
    assert a.result.status == "complete" and "Archive S3 indisponible" in a.archive_notes[0]


def test_no_archive_configured_means_no_note(forge):
    assert archive_notes(None, None, "fr") == [] and forge.analyze("Évalue NVDA", "robert", enrichment=False).archive_notes == []
