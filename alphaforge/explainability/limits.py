"""Outil `limits_builder` : compile hypothèses, limites, incertitudes et ce qui n'est pas couvert, depuis le registre seul.

Les hypothèses et limites des outils de l'analyste sont reprises mot pour mot (elles font partie des preuves).
Les textes repris tels quels sont déclarés comme chaînes autorisées : le vérificateur contrôle qu'ils figurent dans le registre.
"""
from collections import defaultdict
from typing import Dict, Iterable, List, Optional

from ..orchestrator.profile import InvestorProfile
from .compose import Builder, has_digit
from .config import TEMPLATES, ExplainConfig
from .formatting import format_number
from .ledger_read import LedgerView, seq_ref
from .models import Limits, Statement


def _verbatim(b: Builder, text: str) -> str:
    if has_digit(text):
        b.lit(text)
    return text


class _Collector:
    def __init__(self) -> None:
        self.out: Dict[str, List[Statement]] = defaultdict(list)

    def add(self, section: str, text: str, b: Builder, basis: str = "evidence") -> None:
        n = len(self.out[section]) + 1
        self.out[section].append(
            Statement(sid=f"{section}.{n}", section=section, text=text, basis=basis,  # type: ignore[arg-type]
                      refs=list(b.refs), figures=list(b.figures), literals=list(b.literals))
        )


def _collect_strings(view: LedgerView, key: str) -> Dict[str, List[str]]:
    """texte -> evidence_id des preuves qui le portent, dans l'ordre d'apparition."""
    found: Dict[str, List[str]] = {}
    for ev in view.evidence:
        for text in ev.get(key) or []:
            found.setdefault(text, [])
            if ev["evidence_id"] not in found[text]:
                found[text].append(ev["evidence_id"])
    return found


def limits_builder(
    view: LedgerView, profile: InvestorProfile, config: Optional[ExplainConfig] = None, exclude: Iterable[str] = ()
) -> Limits:
    cfg = config or ExplainConfig()
    lang, T = profile.language, TEMPLATES[profile.language]
    col = _Collector()

    def b() -> Builder:
        return Builder(lang)

    # Hypothèses de méthode (réduites pour un débutant, complètes pour un avancé).
    cap = cfg.assumptions_cap.get(profile.experience)
    for i, (text, ids) in enumerate(_collect_strings(view, "assumptions").items()):
        if cap is not None and i >= cap:
            break
        bb = b()
        bb.ref(*ids)
        col.add("assumptions", _verbatim(bb, text), bb, basis="assumption")

    # Limites documentées par les outils : jamais tronquées.
    for text, ids in _collect_strings(view, "limits").items():
        bb = b()
        bb.ref(*ids)
        col.add("limits", _verbatim(bb, text), bb)

    # Incertitudes : avis de l'analyste, sensibilité à la fenêtre, limites du modèle.
    seen = set()
    for ev in view.evidence:
        for n in ev.get("notices") or []:
            if n.get("severity", "warning") != "warning" and n["code"] != "DRAWDOWN_NOT_RECOVERED":
                continue
            if n["message"] in seen:
                continue
            seen.add(n["message"])
            bb = b()
            bb.ref(ev["evidence_id"])
            col.add("uncertainties", _verbatim(bb, n["message"]), bb)
    for t, f in view.facts.items():
        if len(f.windows) < 2:
            continue
        lo, hi = min(f.windows, key=lambda w: w.volatility), max(f.windows, key=lambda w: w.volatility)
        bb = b()
        if format_number(lo.volatility, "pct", lang) == format_number(hi.volatility, "pct", lang):
            continue  # écart invisible à l'arrondi affiché : pas de fourchette trompeuse
        names = ", ".join(bb.lit(w.alias) if w.alias else f"{w.start} / {w.end}" for w in f.windows)
        text = T["range"].format(
            t=bb.lit(t), lo=bb.num(lo.volatility, "pct", lo.evidence_id), hi=bb.num(hi.volatility, "pct", hi.evidence_id), names=names
        )
        col.add("uncertainties", text, bb)
    for seq, note in view.notes:
        if note.get("code") == "feedback_limit_reached":
            bb = b()
            bb.ref(seq_ref(seq))
            col.add("uncertainties", _verbatim(bb, note["message"]), bb)
    col.add("uncertainties", T["past"], b(), basis="assumption")

    # Non couvert : outils absents, actifs sans données, thèmes non vérifiables, cadre de simulation.
    for seq, note in view.notes:
        if note.get("code") == "tool_unavailable":
            bb = b()
            bb.ref(seq_ref(seq))
            col.add("not_covered", _verbatim(bb, note["message"]), bb)
    if view.verdict is not None:
        vref = seq_ref(view.verdict_seq)  # type: ignore[arg-type]
        done = set()
        for a in view.verdict.assets + view.verdict.alternatives:
            for r in a.reasons:
                if r.code == "OUT_OF_UNIVERSE" and ("oou", a.ticker) not in done:
                    done.add(("oou", a.ticker))
                    bb = b()
                    bb.ref(vref, *r.evidence_ids)
                    col.add("not_covered", T["nc_oou"].format(t=bb.lit(a.ticker)), bb)
                elif r.code in ("THEME_DATA_MISSING", "THEME_EXCLUSION_UNVERIFIED"):
                    theme = str(r.measures.get("theme", ""))
                    if ("theme", a.ticker, theme) in done:
                        continue
                    done.add(("theme", a.ticker, theme))
                    bb = b()
                    bb.ref(vref, *r.evidence_ids)
                    col.add("not_covered", T["nc_theme"].format(t=bb.lit(a.ticker), theme=bb.lit(theme)), bb)
    # Agent d'enrichissement : lacunes non comblées (jamais remplacées par une supposition), limites et conditions des sources.
    seen_gap = set()
    for rec in view.enrichment:
        if rec.get("status") != "lacune_non_comblee":
            continue
        key = (rec["ticker"], rec["kind"], rec.get("reason_code"))
        if key in seen_gap:
            continue
        seen_gap.add(key)
        bb = b()
        bb.ref(rec["record_id"])
        col.add("not_covered", T["nc_enrich_gap"].format(kind=bb.lit(rec["kind"]), t=bb.lit(rec["ticker"]), codes=bb.lit(rec.get("reason_code", ""))), bb)
    for seq, note in view.notes:
        if note.get("code") == "enrichment_failed":
            bb = b()
            bb.ref(seq_ref(seq))
            col.add("not_covered", _verbatim(bb, note["message"]), bb)
    has_facts = False
    used_cards = {r.get("source_card_id") for r in view.enrichment if r.get("status") == "ok"}
    for card_id in used_cards:
        if card_id not in view.cards:
            continue
        seq, card = view.cards[card_id]
        for text in card.get("limits", []):
            bb = b()
            bb.ref(seq_ref(seq))
            col.add("limits", _verbatim(bb, text), bb)
        bb = b()
        bb.ref(seq_ref(seq))
        col.add("limits", T["enr_card"].format(name=bb.lit(card["name"]), terms=_verbatim(bb, card["reuse_terms"])), bb)
    for r in view.enrichment:
        has_facts = has_facts or (r.get("status") == "ok" and bool(r.get("facts")))
    if has_facts:
        col.add("uncertainties", T["enr_c_note"], b(), basis="assumption")
    col.add("not_covered", T["sim"], b(), basis="assumption")

    skip = set(exclude)
    return Limits.from_statements([s for ss in col.out.values() for s in ss if s.sid not in skip])
