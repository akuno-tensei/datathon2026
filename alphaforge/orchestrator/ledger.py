"""Registre de preuves : journal ordonné, en ajout seul. Enveloppe les `Evidence` de l'analyste sans les modifier."""
import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict

from ..quant.models import Evidence

EntryKind = Literal["profile", "request", "plan", "step", "evidence", "verdict", "note"]


class LedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seq: int
    kind: EntryKind
    agent: str
    payload: Dict[str, Any]
    evidence_id: Optional[str] = None


class Ledger:
    """Aucune horloge système : l'ordre vient de `seq`, ce qui garde les traces reproductibles."""

    def __init__(self) -> None:
        self._entries: List[LedgerEntry] = []
        self._evidence_ids: set = set()

    def write(self, kind: EntryKind, agent: str, payload: Dict[str, Any], evidence_id: Optional[str] = None) -> LedgerEntry:
        entry = LedgerEntry(seq=len(self._entries) + 1, kind=kind, agent=agent, payload=payload, evidence_id=evidence_id)
        self._entries.append(entry)
        return entry

    def write_evidence(self, agent: str, evidence: Evidence) -> Optional[LedgerEntry]:
        """Idempotent : une preuve déjà enregistrée (même `evidence_id`) n'est pas dupliquée."""
        if evidence.evidence_id in self._evidence_ids:
            return None
        self._evidence_ids.add(evidence.evidence_id)
        return self.write("evidence", agent, evidence.model_dump(mode="json"), evidence.evidence_id)

    def entries(self, kind: Optional[EntryKind] = None) -> List[LedgerEntry]:
        return [e for e in self._entries if kind is None or e.kind == kind]

    def evidence(self) -> List[Dict[str, Any]]:
        return [e.payload for e in self._entries if e.kind == "evidence"]

    def to_jsonl(self, path: Union[str, Path]) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for e in self._entries:
                f.write(json.dumps(e.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n")

    def __len__(self) -> int:
        return len(self._entries)
