"""ledger_write (enrichissement) : écrit une donnée enrichie au registre avec son niveau de confiance et sa fiche source.

Écriture directe (Agents.md 2.4) : l'explicabilité peut citer l'origine exacte de chaque donnée. Idempotent : le même
`record_id` n'est écrit qu'une fois, la même fiche source non plus.
"""
from typing import Optional

from ..orchestrator.ledger import Ledger, LedgerEntry
from .models import EnrichmentRecord, SourceCard

AGENT = "enrichissement"


class LedgerWriteError(ValueError):
    """Enregistrement refusé : il n'est pas écrit."""


def ledger_write(ledger: Ledger, record: EnrichmentRecord, card: Optional[SourceCard] = None) -> Optional[LedgerEntry]:
    """Retourne l'entrée écrite, ou None si l'enregistrement y figurait déjà.

    Un enregistrement `ok` exige sa fiche source, qui doit correspondre. Une lacune n'en a pas besoin.
    """
    if record.status == "ok":
        if card is None or card.card_id != record.source_card_id:
            raise LedgerWriteError(f"Fiche source absente ou différente pour {record.record_id} : écriture refusée.")
        if card.retrieved_on < record.date_observation:  # impossible : on ne collecte pas ce qui n'a pas encore été observé
            raise LedgerWriteError(f"Collecte ({card.retrieved_on}) antérieure à l'observation ({record.date_observation}).")

    if any(e.evidence_id == record.record_id for e in ledger.entries("enrichment")):
        return None
    if card is not None and record.status == "ok" and not any(e.payload.get("card_id") == card.card_id for e in ledger.entries("source_card")):
        ledger.write("source_card", AGENT, card.model_dump(mode="json"))
    return ledger.write("enrichment", AGENT, record.model_dump(mode="json"), evidence_id=record.record_id)
