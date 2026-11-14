import json

import pytest

from alphaforge.orchestrator import Ledger, QuantAnalyst, ToolCall, ledger_write


@pytest.fixture
def evidence(analyst):
    return analyst.run(ToolCall(tool="asset_lookup", arguments={"query": "AAPL"}))


def test_sequence_is_monotonic_from_one():
    led = Ledger()
    a = ledger_write(led, "note", "orchestrateur", {"x": 1})
    b = ledger_write(led, "note", "orchestrateur", {"x": 2})
    assert (a.seq, b.seq) == (1, 2) and len(led) == 2


def test_write_evidence_is_idempotent(evidence):
    led = Ledger()
    assert led.write_evidence("analyste", evidence) is not None
    assert led.write_evidence("analyste", evidence) is None
    assert len(led.entries("evidence")) == 1


def test_evidence_entry_keeps_id_and_payload(evidence):
    led = Ledger()
    e = led.write_evidence("analyste", evidence)
    assert e.evidence_id == evidence.evidence_id
    assert e.payload["tool"] == "asset_lookup" and e.payload["value"]["ticker"] == "AAPL"
    assert led.evidence() == [e.payload]


def test_entries_filter_by_kind(evidence):
    led = Ledger()
    ledger_write(led, "note", "orchestrateur", {})
    led.write_evidence("analyste", evidence)
    assert [e.kind for e in led.entries("note")] == ["note"]
    assert len(led.entries()) == 2


def test_jsonl_roundtrip_and_determinism(tmp_path, evidence):
    def build():
        led = Ledger()
        ledger_write(led, "note", "orchestrateur", {"b": 2, "a": "é"})
        led.write_evidence("analyste", evidence)
        return led

    p1, p2 = tmp_path / "1.jsonl", tmp_path / "2.jsonl"
    build().to_jsonl(p1)
    build().to_jsonl(p2)
    assert p1.read_bytes() == p2.read_bytes()  # mêmes entrées, même fichier : pas d'horloge
    rows = [json.loads(line) for line in p1.read_text(encoding="utf-8").splitlines()]
    assert [r["seq"] for r in rows] == [1, 2] and rows[0]["payload"]["a"] == "é"


def test_unknown_kind_rejected():
    with pytest.raises(Exception):
        Ledger().write("inconnu", "x", {})  # type: ignore[arg-type]
