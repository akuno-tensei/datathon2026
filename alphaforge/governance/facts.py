"""Lecture des preuves du registre, groupées par actif. Aucun calcul : on ne fait que relire ce que l'analyste a écrit."""
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional


@dataclass
class WindowMetrics:
    alias: Optional[str]
    start: date
    end: date
    volatility: float
    drawdown_depth: float  # <= 0
    peak_date: Optional[date]
    recovery_date: Optional[date]
    recovered: bool
    annualization_basis: str
    evidence_id: str
    as_of: Optional[str] = None
    base_currency: Optional[str] = None  # devise des mesures : reprise telle quelle pour chiffrer les alternatives


@dataclass
class Refusal:
    tool: str
    reason: str
    message: str
    evidence_id: str


@dataclass
class AssetFacts:
    ticker: str
    asset: Optional[Dict[str, Any]] = None  # métadonnées de asset_lookup ; None si hors univers ou non consulté
    lookup_evidence_id: Optional[str] = None
    suggestions: List[str] = field(default_factory=list)
    candidates: List[str] = field(default_factory=list)  # requête ambiguë : tickers candidats renvoyés par asset_lookup
    windows: List[WindowMetrics] = field(default_factory=list)
    refusals: List[Refusal] = field(default_factory=list)
    note_codes: List[str] = field(default_factory=list)  # avis de l'analyste, dédoublonnés, ordre d'apparition

    @property
    def evidence_ids(self) -> List[str]:
        ids = [self.lookup_evidence_id] if self.lookup_evidence_id else []
        return ids + [w.evidence_id for w in self.windows]


def _d(s: Optional[str]) -> Optional[date]:
    return date.fromisoformat(s) if s else None


def build_facts(evidence: List[Dict[str, Any]]) -> Dict[str, AssetFacts]:
    facts: Dict[str, AssetFacts] = {}

    def get(key: str) -> AssetFacts:
        return facts.setdefault(key, AssetFacts(ticker=key))

    for ev in evidence:
        tool, status, params = ev["tool"], ev["status"], ev.get("params", {})
        key = params.get("query") if tool == "asset_lookup" else params.get("ticker")
        if key is None:
            continue
        f = get(str(key))
        for n in ev.get("notices", []):
            if n["code"] not in f.note_codes:
                f.note_codes.append(n["code"])

        if status == "refused":
            f.refusals.append(Refusal(tool, ev.get("refusal_reason") or "", _first_message(ev), ev["evidence_id"]))
            if tool == "asset_lookup":
                v = ev.get("value") or {}
                f.suggestions = list(v.get("suggestions", []))
                f.candidates = [c["ticker"] for c in v.get("candidates", [])]
            continue

        value = ev.get("value") or {}
        if tool == "asset_lookup":
            f.asset = value
            f.lookup_evidence_id = ev["evidence_id"]
        elif tool == "compute_metrics":
            base, w = value["base"], ev["window"]
            dd = base["max_drawdown"]
            f.windows.append(
                WindowMetrics(
                    alias=w.get("alias"),
                    start=date.fromisoformat(w["start"]),
                    end=date.fromisoformat(w["end"]),
                    volatility=base["annualized_volatility"],
                    drawdown_depth=dd["depth"],
                    peak_date=_d(dd.get("peak_date")),
                    recovery_date=_d(dd.get("recovery_date")),
                    recovered=dd["recovered"],
                    annualization_basis=value["annualization_basis"],
                    evidence_id=ev["evidence_id"],
                    as_of=params.get("as_of"),
                    base_currency=params.get("base_currency"),
                )
            )
            # compute_metrics ne renvoie pas les métadonnées complètes : on garde au moins la classe d'actifs.
            if f.asset is None:
                f.asset = {"ticker": value["ticker"], "asset_class": value["asset_class"]}
    return facts


def _first_message(ev: Dict[str, Any]) -> str:
    notices = ev.get("notices") or []
    return notices[0]["message"] if notices else (ev.get("refusal_reason") or "refusé")


def ledger_notes(entries: List[Any], code: str) -> List[Dict[str, Any]]:
    """Notes de l'orchestrateur d'un code donné (ex. tool_unavailable)."""
    return [e.payload for e in entries if e.kind == "note" and e.payload.get("code") == code]
