"""Outil `ledger_read` : seul accès de l'explicabilité aux faits. Lecture seule, aucun calcul.

Produit une vue typée du registre, plus un index des références (evidence_id et `seq:N`) que le vérificateur utilise
pour contrôler chaque chiffre.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

from pydantic import ValidationError

from ..governance.facts import AssetFacts, build_facts
from ..governance.models import GovernanceVerdict
from ..orchestrator.ledger import Ledger


def seq_ref(seq: int) -> str:
    return f"seq:{seq}"


@dataclass
class LedgerView:
    profile: Dict[str, Any] = field(default_factory=dict)
    profile_seq: Optional[int] = None
    request: Optional[Dict[str, Any]] = None
    request_seq: Optional[int] = None
    verdict: Optional[GovernanceVerdict] = None  # le dernier verdict fait foi
    verdict_seq: Optional[int] = None
    facts: Dict[str, AssetFacts] = field(default_factory=dict)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[Tuple[int, Dict[str, Any]]] = field(default_factory=list)  # (seq, payload)
    refs: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # evidence_id et `seq:N` -> payload
    corpus: str = ""  # toutes les chaînes du registre, pour vérifier tickers, dates et énoncés repris tels quels

    def has_ref(self, ref: str) -> bool:
        return ref in self.refs


def numeric_leaves(obj: Any) -> Iterator[float]:
    """Tous les nombres d'un payload (bool exclu)."""
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        yield float(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from numeric_leaves(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from numeric_leaves(v)


def string_leaves(obj: Any) -> Iterator[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from string_leaves(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from string_leaves(v)


def ledger_read(ledger: Ledger) -> LedgerView:
    view = LedgerView()
    strings: List[str] = []
    for e in ledger.entries():
        view.refs[seq_ref(e.seq)] = e.payload
        strings.extend(string_leaves(e.payload))
        if e.kind == "evidence":
            view.evidence.append(e.payload)
            view.refs[e.payload["evidence_id"]] = e.payload
        elif e.kind == "profile":
            view.profile, view.profile_seq = e.payload.get("profile", {}), e.seq
        elif e.kind == "request":
            view.request, view.request_seq = e.payload, e.seq
        elif e.kind == "verdict":
            try:
                view.verdict, view.verdict_seq = GovernanceVerdict.model_validate(e.payload), e.seq
            except ValidationError:
                continue  # verdict illisible : ignoré plutôt que deviné
        elif e.kind == "note":
            view.notes.append((e.seq, e.payload))
    view.facts = build_facts(view.evidence)
    view.corpus = "\n".join(strings)
    return view
