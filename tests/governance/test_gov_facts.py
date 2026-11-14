"""build_facts : relecture du registre, sans calcul."""
import pytest

from alphaforge.governance.facts import build_facts
from alphaforge.orchestrator import ToolCall


def ev(analyst, tool, **args):
    return analyst.run(ToolCall(tool=tool, arguments=args)).model_dump(mode="json")


def test_lookup_fills_asset_metadata(analyst):
    f = build_facts([ev(analyst, "asset_lookup", query="HIGH")])["HIGH"]
    assert f.asset["sector"] == "Tech" and f.lookup_evidence_id and f.windows == []


def test_metrics_become_windows_with_source_values(analyst):
    e = ev(analyst, "compute_metrics", ticker="HIGH", window={"alias": "complet"})
    w = build_facts([e])["HIGH"].windows[0]
    base = e["value"]["base"]
    assert w.alias == "complet" and w.volatility == base["annualized_volatility"]
    assert w.drawdown_depth == base["max_drawdown"]["depth"] and w.evidence_id == e["evidence_id"]
    assert w.as_of == e["params"]["as_of"]


def test_metrics_without_lookup_still_give_asset_class(analyst):
    f = build_facts([ev(analyst, "compute_metrics", ticker="COIN-USD", window={"alias": "complet"})])["COIN-USD"]
    assert f.asset["asset_class"] == "crypto"


def test_lookup_overrides_partial_metadata_regardless_of_order(analyst):
    m = ev(analyst, "compute_metrics", ticker="HIGH", window={"alias": "complet"})
    lk = ev(analyst, "asset_lookup", query="HIGH")
    assert build_facts([m, lk])["HIGH"].asset["sector"] == "Tech"
    assert build_facts([lk, m])["HIGH"].asset["sector"] == "Tech"


def test_unknown_asset_keeps_refusal_and_suggestions(analyst):
    f = build_facts([ev(analyst, "asset_lookup", query="HIG")])["HIG"]
    assert f.asset is None and f.refusals[0].reason == "asset_not_found" and "HIGH" in f.suggestions


def test_assets_are_grouped_separately(analyst):
    facts = build_facts([ev(analyst, "asset_lookup", query="HIGH"), ev(analyst, "asset_lookup", query="MID")])
    assert set(facts) == {"HIGH", "MID"}


def test_notice_codes_are_deduplicated_in_order(analyst):
    a = ev(analyst, "compute_metrics", ticker="HIGH", window={"alias": "complet"})
    b = ev(analyst, "compute_metrics", ticker="HIGH", window={"alias": "2022"})
    codes = build_facts([a, b])["HIGH"].note_codes
    assert len(codes) == len(set(codes))
    assert set(codes) >= {n["code"] for n in a["notices"]}


def test_evidence_ids_lists_lookup_then_windows(analyst):
    lk = ev(analyst, "asset_lookup", query="HIGH")
    m = ev(analyst, "compute_metrics", ticker="HIGH", window={"alias": "complet"})
    assert build_facts([lk, m])["HIGH"].evidence_ids == [lk["evidence_id"], m["evidence_id"]]


def test_refused_metrics_do_not_create_windows(analyst):
    e = ev(analyst, "compute_metrics", ticker="NOPE", window={"alias": "complet"})
    f = build_facts([e])["NOPE"]
    assert f.windows == [] and f.refusals[0].tool == "compute_metrics"


@pytest.mark.parametrize("payload", [{"tool": "autre_outil", "status": "ok", "params": {}, "evidence_id": "e"}])
def test_evidence_without_asset_key_is_ignored(payload):
    assert build_facts([payload]) == {}
