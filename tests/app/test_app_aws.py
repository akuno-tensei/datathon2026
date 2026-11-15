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


def test_without_aws_credentials_the_rules_are_used_directly(monkeypatch):
    monkeypatch.setenv("ALPHAFORGE_LLM", "1")
    monkeypatch.setattr(pipeline, "aws_credentials_available", lambda: False)  # poste sans compte AWS, par exemple après l'événement
    assert isinstance(pipeline.build_parser(), RuleBasedParser)


def test_offline_snapshot_serves_sec_data_without_network_or_identification(tmp_path):
    f = AlphaForge.from_directory(user_profiles_dir=tmp_path, parser=RuleBasedParser(), archive=None)
    a = f.analyze("Évalue AAPL et ses résultats", "robert", sec_user_agent=None)
    recs = [e.payload for e in a.result.ledger.entries("enrichment") if e.payload["ticker"] == "AAPL" and e.payload["status"] == "ok"]
    fund = next(r for r in recs if r["kind"] == "fundamentals")
    assert fund["confidence"] == "A" and fund["date_publication"] <= "2026-11-12" and fund["value"]["metrics"]["revenue"] > 0
    assert any(r["kind"] == "filings" for r in recs)
    assert a.explanation.report.approved
    assert pipeline.enrichment_coverage(None)["fetch_fundamentals"] == "snapshot"


def test_the_snapshot_never_contains_the_sec_identification():
    for path in pipeline.ENRICHMENT_SNAPSHOT_DIR.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert "@" not in text.replace("sec.gov", ""), path  # aucun courriel : seulement des données publiques de la SEC


def test_root_requirements_pin_the_same_versions_as_the_deployed_image():
    from pathlib import Path

    root = Path(pipeline.__file__).resolve().parents[2]
    pins = lambda p: sorted(l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#"))
    assert pins(root / "requirements.txt") == pins(root / "deploy" / "requirements.lock.txt")
