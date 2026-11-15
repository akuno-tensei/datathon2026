"""Vues : transforment une Analysis en structures d'affichage. Pures et testables, sans dépendance à Streamlit.

L'application Streamlit ne fait que rendre ces structures. Les tests couvrent la logique d'affichage ici.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .i18n import t
from .pipeline import Analysis, Comparison

STATUS_KEYS = {"conforme": ("icon_conforme", "status_conforme"), "avertissement": ("icon_avertissement", "status_avertissement"),
               "bloqué": ("icon_bloqué", "status_bloqué")}


@dataclass
class Badge:
    icon: str
    label: str
    status: str


def status_badge(status: str, lang: str) -> Badge:
    icon, label = STATUS_KEYS.get(status, ("•", status))
    return Badge(icon=t(icon, lang), label=t(label, lang), status=status)


@dataclass
class TraceStep:
    agent: str
    agent_label: str
    icon: str  # ✓ ⚠ ⛔ ✗
    status: str
    text: str
    detail: Dict[str, Any] = field(default_factory=dict)
    evidence_id: Optional[str] = None


_ICON = {"ok": "✓", "complete": "✓", "warning": "⚠", "refused": "⛔", "error": "✗", "bloqué": "⛔", "avertissement": "⚠", "conforme": "✓"}


def entry_to_step(e: Any, lang: str) -> Optional[TraceStep]:
    """Une entrée du registre en étape de trace. Sert à la trace finale et à l'affichage en direct (même rendu)."""
    p, kind = e.payload, e.kind
    if kind == "profile":
        return TraceStep("orchestrateur", t("agent_orchestrateur", lang), "✓", "ok", t("step_profile", lang))
    if kind == "request":
        return TraceStep("orchestrateur", t("agent_orchestrateur", lang), "✓", "ok",
                         t("step_request", lang, intent=p.get("intent"), tickers=", ".join(p.get("tickers", [])) or "—"))
    if kind == "plan":
        return TraceStep("orchestrateur", t("agent_orchestrateur", lang), "✓", "ok", t("step_plan", lang, n=len(p.get("steps", []))))
    if kind == "step" and p.get("tool"):
        st = p.get("status", "ok")
        fb = str(p.get("step_id", "")).startswith("fb")
        args = p.get("arguments", {})
        target = args.get("ticker") or args.get("query") or ", ".join(x["ticker"] for x in args.get("positions", [])) or ", ".join(args.get("tickers", []))
        return TraceStep("analyste", t("agent_analyste", lang), _ICON.get(st, "•"), st,
                         t("step_tool", lang, tool=p["tool"]) + (f"  ({target})" if target else ""),
                         detail={"arguments": args, "feedback": fb}, evidence_id=p.get("evidence_id"))
    if kind == "verdict":
        return TraceStep("gouvernance", t("agent_gouvernance", lang), _ICON.get(p["status"], "•"), p["status"],
                         t("step_verdict", lang, status=t("status_" + p["status"], lang)))
    if kind == "enrichment":
        ok = p.get("status") == "ok"
        text = (t("step_enrich_rec", lang, kind=p["kind"], t=p["ticker"], level=p.get("confidence")) if ok
                else t("step_enrich_gap", lang, kind=p["kind"], t=p["ticker"], code=p.get("reason_code")))
        return TraceStep("enrichissement", t("agent_enrichissement", lang), "✓" if ok else "⚠", "ok" if ok else "warning", text,
                         detail={"record_id": p.get("record_id"), "confidence": p.get("confidence"), "date_publication": p.get("date_publication"),
                                 "reason_code": p.get("reason_code")})
    if kind == "note" and p.get("code") in ("needs_detected", "needs_relaunched"):
        return TraceStep("orchestrateur", t("agent_orchestrateur", lang), "✓", "ok", t("step_needs", lang, message=p["message"]),
                         detail={"gaps": [{k: g[k] for k in ("kind", "ticker", "fillable", "reason")} for g in p.get("gaps", [])]})
    if kind == "note" and p.get("code") in ("enrichment_done", "enrichment_failed"):
        bad = p.get("code") == "enrichment_failed" or p.get("degraded")
        return TraceStep("enrichissement", t("agent_enrichissement", lang), "⚠" if bad else "✓", "warning" if bad else "ok", p["message"])
    if kind == "source_card":
        return None  # la fiche est listée dans « Données et sources » ; elle n'est pas une étape
    if kind == "note" and e.agent == "explicabilite":
        ok = p.get("code") == "explanation_verified"
        return TraceStep("explicabilite", t("agent_explicabilite", lang), "✓" if ok else "⚠", "ok" if ok else "warning",
                         t("step_explain", lang), detail={"removed": p.get("removed", []), "downgraded": p.get("downgraded", [])})
    if kind == "note":
        return TraceStep("orchestrateur", t("agent_orchestrateur", lang), "⚠", "warning", t("step_note", lang, message=p.get("message", "")))
    return None


def agent_trace(analysis: Analysis, lang: str) -> List[TraceStep]:
    """Chronologie observable des agents à partir des entrées du registre. Jamais le raisonnement interne du modèle."""
    steps: List[TraceStep] = []
    for e in analysis.result.ledger.entries():
        if e.kind == "note" and e.agent == "explicabilite":
            continue  # rendu par l'étape Explicabilité ci-dessous, avec le rapport complet
        step = entry_to_step(e, lang)
        if step is not None:
            steps.append(step)
    if analysis.explanation is not None:
        rep = analysis.explanation.report
        icon = "✓" if rep.approved else "⚠"
        steps.append(TraceStep("explicabilite", t("agent_explicabilite", lang), icon, "ok" if rep.approved else "warning",
                               t("step_explain", lang),
                               detail={"attempts": rep.attempts, "verified": rep.verified, "removed": rep.removed, "downgraded": rep.downgraded}))
    return steps


def feedback_iterations(analysis: Analysis) -> int:
    return len({str(e.payload.get("step_id", ""))[:3] for e in analysis.result.ledger.entries("step")
                if str(e.payload.get("step_id", "")).startswith("fb")})


def agent_decisions(analysis: Analysis, lang: str) -> List[str]:
    """Les choix faits par les agents pendant l'analyse, lus dans le registre et le rapport de vérification.

    Ne montre que ce qui a été décidé et pourquoi : solliciter ou non l'enrichissement, transmettre un signal hors
    périmètre, demander des alternatives, rédiger à nouveau l'explication. Rien n'est déduit au-delà du registre.
    """
    from ..explainability.config import ExplainConfig
    from ..orchestrator.orchestrator import MAX_FEEDBACK_ITERATIONS

    def need(kind: str) -> str:
        label = t("need_" + kind, lang)
        return kind if label == "need_" + kind else label

    ledger = analysis.result.ledger
    out: List[str] = []
    req = analysis.result.request
    flags = list(getattr(req, "scope_flags", []) or []) if req else []
    if flags:
        out.append(t("dec_scope", lang, flags=", ".join(t("flag_" + f, lang) for f in flags)))
    notes = [e.payload for e in ledger.entries("note")]
    detected = [n for n in notes if n.get("code") == "needs_detected"]
    if not analysis.enrichment_enabled:
        out.append(t("dec_enr_off", lang))
    elif req is not None and analysis.verdict is not None:
        if not detected:
            out.append(t("dec_enr_idle", lang))
        for n in detected:
            gaps = n.get("gaps", [])
            fill = [g for g in gaps if g.get("fillable")]
            if fill:
                out.append(t("dec_enr_on", lang, needs=", ".join(f"{need(g['kind'])} · {g['ticker']}" for g in fill)))
    verdicts = [e.payload for e in ledger.entries("verdict")]
    steps = [e.payload for e in ledger.entries("step")]
    n_iter = feedback_iterations(analysis)
    for k in range(1, n_iter + 1):
        before = verdicts[k - 1] if len(verdicts) >= k else {}
        trig = [f"{a['ticker']} ({t('status_' + a['status'], lang).lower()})" for a in before.get("assets", []) if a.get("status") != "conforme"]
        alts = list(dict.fromkeys(s["arguments"].get("ticker") for s in steps
                                  if str(s.get("step_id", "")).startswith(f"fb{k}.") and s.get("arguments", {}).get("ticker")))
        out.append(t("dec_alt", lang, trig=", ".join(trig) or "—", alts=", ".join(alts) or "—", k=k, n=MAX_FEEDBACK_ITERATIONS))
    if verdicts and not n_iter:
        out.append(t("dec_alt_none_ok" if verdicts[-1].get("status") == "conforme" else "dec_alt_none", lang))
    if analysis.enrichment_enabled:  # après la gouvernance : relance pour les alternatives, puis bilan des lacunes
        if any(n.get("code") == "needs_relaunched" for n in notes):
            out.append(t("dec_enr_relaunch", lang))
        # lacunes : entrées d'enrichissement écrites au registre sans donnée (source absente ou sans réponse)
        n_gaps = len([e for e in ledger.entries("enrichment") if e.payload.get("status") != "ok"])
        if n_gaps:
            out.append(t("dec_enr_gaps", lang, n=n_gaps))
    if analysis.explanation is not None:
        rep = analysis.explanation.report
        if rep.removed:
            out.append(t("dec_expl_removed", lang, n=len(rep.removed)) + (
                t("dec_expl_redraft", lang, k=rep.attempts, n=ExplainConfig().max_attempts) if rep.attempts > 1 else "") + ".")
        else:
            out.append(t("dec_expl_clean", lang, n=rep.verified))
    return out


def margin_points(value: float, limit: float, lang: str) -> str:
    """Distance entre une mesure et sa limite, en points de pourcentage, avec le sens écrit. Affichage seulement."""
    d = abs(limit - value) * 100
    s = f"{d:.1f}"
    if lang == "fr":
        s = s.replace(".", ",")
        unit = "point" if d < 2 else "points"
    else:
        unit = "point" if s == "1.0" else "points"
    return t("margin_under" if value <= limit else "margin_over", lang, d=s, unit=unit)


@dataclass
class MeasureBar:
    label: str
    value: float
    limit: float
    ok: bool
    icon: str
    ratio: float  # longueur de barre, bornée à 1
    window: Optional[str]
    origin: str
    basis: str
    evidence_id: str


def _metric_evs(analysis: Analysis, ticker: str) -> List[dict]:
    return [e for e in analysis.result.ledger.evidence()
            if e["tool"] == "compute_metrics" and e["status"] != "refused" and e["params"].get("ticker") == ticker]


def _wname(ev: dict) -> str:
    w = ev.get("window") or {}
    return w.get("alias") or f"{w.get('start')}/{w.get('end')}"


def verdict_measures(analysis: Analysis, lang: str) -> Dict[str, List[MeasureBar]]:
    """Mesure contre limite, par actif. Valeurs lues dans les preuves de l'analyste, limites dans le verdict.

    Même lecture que la gouvernance : la pire fenêtre mesurée pour chaque indicateur. Aucun calcul de marché ici.
    """
    v = analysis.verdict
    if not v:
        return {}
    c = v["constraints"]
    origin = t("kind_derived", lang) + f" : « {c['risk_level_applied']} »"
    if c.get("adjustments"):
        origin += " ; " + " ".join(c["adjustments"])
    out: Dict[str, List[MeasureBar]] = {}
    for a in v["assets"] + v.get("alternatives", []):
        evs = _metric_evs(analysis, a["ticker"])
        if not evs:
            continue
        wv = max(evs, key=lambda e: e["value"]["base"]["annualized_volatility"])
        wd = max(evs, key=lambda e: -e["value"]["base"]["max_drawdown"]["depth"])
        vol, dd = wv["value"]["base"]["annualized_volatility"], -wd["value"]["base"]["max_drawdown"]["depth"]
        out[a["ticker"]] = [
            MeasureBar(t("max_vol", lang), vol, c["max_volatility"], vol <= c["max_volatility"], "✓" if vol <= c["max_volatility"] else "✗",
                       min(vol / c["max_volatility"], 1.0), _wname(wv), origin, wv["value"]["annualization_basis"], wv["evidence_id"]),
            MeasureBar(t("max_dd", lang), dd, c["max_drawdown"], dd <= c["max_drawdown"], "✓" if dd <= c["max_drawdown"] else "✗",
                       min(dd / c["max_drawdown"], 1.0), _wname(wd), origin, t("dd_basis", lang), wd["evidence_id"]),
        ]
    return out


@dataclass
class ReasonLine:
    icon: str
    kind: str  # libellé : contrainte dure, préférence souple, limite dérivée, donnée, périmètre, profil
    code: str
    message: str
    evidence_ids: List[str]


_KIND = {"hard_constraints": "kind_hard", "soft_preferences": "kind_soft", "risk_limits": "kind_derived", "horizon_fit": "kind_derived",
         "data_gaps": "kind_data", "scope_limits": "kind_scope", "profile": "kind_profile", "diversification": "kind_diversification", "portfolio": "kind_portfolio"}
_SEV_ICON = {"block": "⛔", "warn": "⚠", "info": "ℹ"}


def asset_reasons(asset: dict, lang: str) -> List[ReasonLine]:
    """Raisons de la gouvernance avec leur nature (dure ou souple), toujours avec icône et texte."""
    return [ReasonLine(_SEV_ICON.get(r["severity"], "•"), t(_KIND.get(r["check"], "kind_data"), lang), r["code"], r["message"],
                       list(r.get("evidence_ids", []))) for r in asset.get("reasons", [])]


@dataclass
class SensitivityTable:
    ticker: str
    rows: List[Dict[str, Any]]
    indicator: Optional[str]  # stable / sensible / indetermine : fourni par stress_window, jamais calculé par l'interface
    threshold: Optional[float] = None
    relative_spread: Optional[float] = None
    evidence_id: Optional[str] = None


def sensitivity_table(analysis: Analysis, ticker: str) -> Optional[SensitivityTable]:
    """Toutes les fenêtres testées, pas seulement les flatteuses. Valeurs lues dans les preuves, sans recalcul.

    Source principale : la preuve stress_window (fenêtres rejouées, verdict stable / sensible et seuil).
    À défaut, les fenêtres compute_metrics du plan, sans indicateur.
    """
    stress = [e for e in analysis.result.ledger.evidence()
              if e["tool"] == "stress_window" and e["status"] != "refused" and e["params"].get("ticker") == ticker]
    if stress:
        v = stress[-1]["value"]
        rows = [{"window": r["window"], "start": r.get("start"), "end": r.get("end"), "status": r["status"],
                 "volatility": r.get("annualized_volatility"), "max_drawdown": r.get("max_drawdown"),
                 "total_return": r.get("total_return"), "evidence_id": r["evidence_id"]} for r in v["rows"]]
        return SensitivityTable(ticker, rows, v["verdict"], v["threshold"], v.get("relative_spread"), stress[-1]["evidence_id"])
    evs = _metric_evs(analysis, ticker)
    if not evs:
        return None
    rows = [{"window": _wname(e), "start": e["window"]["start"], "end": e["window"]["end"], "status": e["status"],
             "volatility": e["value"]["base"]["annualized_volatility"], "max_drawdown": e["value"]["base"]["max_drawdown"]["depth"],
             "total_return": e["value"]["base"]["total_return"], "evidence_id": e["evidence_id"]} for e in evs]
    return SensitivityTable(ticker, rows, None)


@dataclass
class CorrelationView:
    evidence_id: str
    tickers: List[str]
    reference: str  # weekly | daily_common
    method: str
    n_obs: int
    first_date: Optional[str]
    last_date: Optional[str]
    cells: List[Dict[str, Any]]  # {"a", "b", "corr"} pour chaque couple (matrice complète, diagonale comprise)
    complement: Optional[Dict[str, Any]]  # quotidien en dates communes quand l'hebdomadaire est la référence
    excluded: List[Dict[str, Any]]
    alt: str  # alternative textuelle du graphique


def correlation_view(analysis: Analysis, lang: str) -> Optional[CorrelationView]:
    """Matrice de référence de la dernière preuve correlation_matrix, telle quelle (aucun recalcul)."""
    evs = [e for e in analysis.result.ledger.evidence() if e["tool"] == "correlation_matrix" and e["status"] != "refused"]
    if not evs:
        return None
    ev = evs[-1]
    v = ev["value"]
    ref = v["matrices"][v["reference"]]
    cells = [{"a": a, "b": b, "corr": c} for a, row in ref["matrix"].items() for b, c in row.items()]
    other = {k: m for k, m in v["matrices"].items() if k != v["reference"] and m.get("computed")}
    comp = next(iter(other.values())) if other else None
    hi, lo = ref.get("max_pair"), ref.get("min_pair")
    alt = t("corr_alt", lang, n=len(v["tickers"]), hi=f"{hi['a']}/{hi['b']} {hi['corr']:.2f}" if hi else "—",
            lo=f"{lo['a']}/{lo['b']} {lo['corr']:.2f}" if lo else "—")
    return CorrelationView(ev["evidence_id"], v["tickers"], v["reference"], ref["method"], ref["n_obs"], ref["first_date"],
                           ref["last_date"], cells, comp, v.get("excluded_assets", []), alt)


@dataclass
class PortfolioView:
    evidence_id: str
    label: str
    rebalancing: str
    rebalancing_description: str
    calendar_basis: str
    annualization_basis: str
    n_returns: int
    first_date: str
    last_date: str
    positions: List[Dict[str, Any]]
    metrics: Dict[str, Any]
    concentration: Dict[str, Any]
    breakdown: Dict[str, List[Dict[str, Any]]]
    contributions: Optional[List[Dict[str, Any]]]
    diversification_ratio: Optional[float]
    series_id: str
    assumptions: List[str]
    warnings: List[str]


def portfolio_view(analysis: Analysis, lang: str) -> Optional[PortfolioView]:
    """Dernier scénario de portefeuille du registre, tel quel : valeurs de la preuve portfolio_metrics, aucun recalcul."""
    evs = [e for e in analysis.result.ledger.evidence() if e["tool"] == "portfolio_metrics" and e["status"] != "refused"]
    if not evs:
        return None
    ev = evs[-1]
    v = ev["value"]
    return PortfolioView(
        ev["evidence_id"], t("pf_label", lang), v["rebalancing"], v["rebalancing_description"], v["calendar_basis"],
        v["annualization_basis"], v["n_returns"], v["first_date"], v["last_date"], v["positions"], v["metrics"], v["concentration"],
        v["breakdown"], v["risk"]["contributions"], v["risk"]["diversification_ratio"], v["series_id_portfolio"],
        ev.get("assumptions", []), ev.get("warnings", []),
    )


@dataclass
class EnrichmentRow:
    record_id: str
    ticker: str
    kind: str
    need: str
    status: str  # "ok" | "lacune"
    confidence: Optional[str]
    date_publication: Optional[str]
    source: str
    url: Optional[str]
    reason_code: Optional[str]
    message: Optional[str]
    facts: List[Dict[str, Any]]
    value: Optional[Dict[str, Any]]
    usable_for_calculation: bool


@dataclass
class EnrichmentView:
    mode: str  # off | idle | complete | partial | degraded
    needs: List[Dict[str, Any]]
    rows: List[EnrichmentRow]
    cards: List[Dict[str, Any]]
    n_ok: int
    n_gaps: int
    messages: List[str]


def enrichment_view(analysis: Analysis, lang: str) -> EnrichmentView:
    """Ce que l'agent d'enrichissement a détecté et écrit, tel qu'au registre. Aucune donnée n'est ajoutée ici."""
    led = analysis.result.ledger
    cards = {e.payload["card_id"]: e.payload for e in led.entries("source_card")}
    rows: List[EnrichmentRow] = []
    for e in led.entries("enrichment"):
        p = e.payload
        card = cards.get(p.get("source_card_id") or "", {})
        rows.append(EnrichmentRow(
            p["record_id"], p["ticker"], p["kind"], p["need"], "ok" if p["status"] == "ok" else "lacune", p.get("confidence"),
            p.get("date_publication"), card.get("name", "—"), p.get("url"), p.get("reason_code"), p.get("message"),
            list(p.get("facts", [])), p.get("value"), bool(p.get("usable_for_calculation"))))
    notes = [e.payload for e in led.entries("note")]
    needs = [g for n in notes if n.get("code") in ("needs_detected", "needs_relaunched") for g in n.get("gaps", [])]
    messages = [n["message"] for n in notes if n.get("code") in ("enrichment_done", "enrichment_failed")]
    failed = any(n.get("code") == "enrichment_failed" for n in notes)
    n_ok, n_gaps = sum(r.status == "ok" for r in rows), sum(r.status == "lacune" for r in rows)
    if not analysis.enrichment_enabled:
        mode = "off"
    elif failed or (n_gaps and not n_ok):
        mode = "degraded"
    elif n_gaps:
        mode = "partial"
    elif n_ok:
        mode = "complete"
    else:
        mode = "idle"
    return EnrichmentView(mode, needs, rows, list(cards.values()), n_ok, n_gaps, messages)


@dataclass
class ExplanationLine:
    section: str
    label: str
    text: str
    refs: List[str]  # evidence_id cliquables (les références `seq:N` désignent une entrée du registre)
    figures: List[Dict[str, str]]


def explanation_sections(analysis: Analysis, lang: str, detail: bool) -> Dict[str, List[ExplanationLine]]:
    """Énoncés vérifiés de l'agent d'explicabilité, groupés par section. En résumé : résumé et cohérence seulement."""
    e = analysis.explanation
    if e is None:
        return {}
    keep = None if detail else {"summary", "fit", "scope"}
    from ..explainability.config import LABELS

    out: Dict[str, List[ExplanationLine]] = {}
    for s in e.statements:
        if s.section == "conclusion" or (keep is not None and s.section not in keep):
            continue  # la conclusion est affichée à part, à la fin (conclusion_lines)
        out.setdefault(s.section, []).append(
            ExplanationLine(s.section, LABELS[lang].get(s.label or "fait", s.label or ""), s.text, list(s.refs),
                            [{"shown": f.shown, "ref": f.ref} for f in s.figures]))
    return out


def conclusion_lines(analysis: Analysis, lang: str) -> List[ExplanationLine]:
    """Conclusion en langage simple (agent d'explicabilité, vérifiée), affichée à la fin de l'analyse dans tous les modes."""
    e = analysis.explanation
    if e is None:
        return []
    from ..explainability.config import LABELS
    return [ExplanationLine(s.section, LABELS[lang].get(s.label or "conclusion", ""), s.text, list(s.refs), [])
            for s in e.statements if s.section == "conclusion"]


def limit_sections(analysis: Analysis, lang: str) -> Dict[str, List[ExplanationLine]]:
    """Hypothèses, limites, incertitudes, non couvert : toujours affichées en entier."""
    e = analysis.explanation
    if e is None:
        return {}
    from ..explainability.config import LABELS

    out: Dict[str, List[ExplanationLine]] = {}
    for key in ("limits", "uncertainties", "assumptions", "not_covered"):
        out[key] = [ExplanationLine(key, LABELS[lang].get(s.label or "fait", ""), s.text, list(s.refs),
                                    [{"shown": f.shown, "ref": f.ref} for f in s.figures]) for s in getattr(e.limits, key)]
    return out


def verification_banner(analysis: Analysis, lang: str) -> Optional[str]:
    e = analysis.explanation
    if e is None:
        return None
    if e.report.approved:
        return t("verified", lang)
    return t("corrected", lang, n=len(e.report.removed))


@dataclass
class EvidenceCard:
    evidence_id: str
    tool: str
    version: str
    window: Optional[Dict[str, Any]]
    params: Dict[str, Any]
    inputs: List[Dict[str, Any]]
    sources: List[str]
    warnings: List[str]
    n_obs: Optional[int]
    fingerprint: str
    status: str
    reproduce: str
    chart: Optional[Dict[str, Any]] = None
    recomputed: bool = False  # preuve interne (citée par une autre preuve), recalculée à l'identique pour l'affichage


def evidence_index(analysis: Analysis) -> Dict[str, dict]:
    return {e["evidence_id"]: e for e in analysis.result.ledger.evidence()}


def _dd_chart(ev: dict, lang: str) -> Optional[Dict[str, Any]]:
    v = ev.get("value") or {}
    base = v.get("base") if isinstance(v, dict) else None
    if not base:
        return None
    dd = base["max_drawdown"]
    rec = t("recovered", lang, d=dd["recovery_date"]) if dd.get("recovered") else t("not_recovered", lang)
    summary = t("dd_summary", lang, peak=dd.get("peak_date"), trough=dd.get("trough_date"), rec=rec)
    return {"type": "drawdown", "depth": dd["depth"], "peak": dd.get("peak_date"), "trough": dd.get("trough_date"),
            "recovery": dd.get("recovery_date"), "alt": t("chart_alt", lang, summary=summary)}


def reproduce_arguments(evidence_id: str, analysis: Analysis) -> Optional[Dict[str, Any]]:
    """Arguments exacts de l'appel qui a produit la preuve, tels que consignés par l'orchestrateur."""
    for e in analysis.result.ledger.entries("step"):
        if e.payload.get("evidence_id") == evidence_id:
            args = dict(e.payload.get("arguments", {}))
            ev = evidence_index(analysis)[evidence_id]
            if ev["tool"] == "compute_metrics":
                args.setdefault("base_currency", ev["params"].get("base_currency"))  # ajouté par le port de l'interface
            return args
    return None


def _window_arg(label: str) -> Dict[str, Any]:
    if "/" in label:
        start, end = label.split("/", 1)
        return {"start": start, "end": end}
    return {"alias": label}


def related_call(evidence_id: str, analysis: Analysis) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Appel qui régénère une preuve interne, citée par une preuve du registre sans y être écrite elle-même.

    - ligne d'un stress_window -> compute_metrics sur la fenêtre de la ligne ;
    - contrôle de qualité d'un compute_metrics (depends_on) -> data_quality_check sur la même fenêtre.
    """
    for parent in analysis.result.ledger.evidence():
        if evidence_id not in parent.get("depends_on", []):
            continue
        pp = parent["params"]
        if parent["tool"] == "stress_window":
            row = next((r for r in parent["value"]["rows"] if r["evidence_id"] == evidence_id), None)
            if row is not None:
                return "compute_metrics", {"ticker": pp["ticker"], "window": _window_arg(row["window"]),
                                           "base_currency": pp["base_currency"], "as_of": pp["as_of"]}
        if parent["tool"] == "compute_metrics":
            args = reproduce_arguments(parent["evidence_id"], analysis) or {}
            window = args.get("window") or _window_arg(parent["window"].get("alias") or f"{parent['window']['start']}/{parent['window']['end']}")
            return "data_quality_check", {"ticker": pp["ticker"], "window": window, "as_of": pp["as_of"]}
    return None


def resolve_evidence(evidence_id: str, analysis: Analysis, store: Any = None) -> Optional[dict]:
    """Preuve du registre ; à défaut, preuve interne recalculée par l'analyste, gardée seulement si son evidence_id est identique."""
    ev = evidence_index(analysis).get(evidence_id)
    if ev is not None or store is None:
        return ev
    call = related_call(evidence_id, analysis)
    if call is None:
        return None
    from ..quant import run_tool

    again = run_tool(store, call[0], call[1])
    if again.evidence_id != evidence_id:
        return None  # données ou version d'outil changées : on n'affiche pas une preuve différente
    d = again.model_dump(mode="json")
    d["_recomputed"] = {"tool": call[0], "arguments": call[1]}
    return d


def evidence_card(evidence_id: str, analysis: Analysis, lang: str, store: Any = None) -> Optional[EvidenceCard]:
    ev = resolve_evidence(evidence_id, analysis, store)
    if ev is None:
        return None
    args = reproduce_arguments(evidence_id, analysis) if "_recomputed" not in ev else ev["_recomputed"]["arguments"]
    reproduce = f"run_tool(store, {ev['tool']!r}, {args!r})" if args is not None else ""
    return EvidenceCard(
        evidence_id=ev["evidence_id"], tool=ev["tool"], version=ev["tool_version"], window=ev.get("window"), params=ev["params"],
        inputs=ev.get("inputs", []), sources=ev.get("sources", []), warnings=ev.get("warnings", []), n_obs=ev.get("n_obs"),
        fingerprint=ev["data_fingerprint"][:12], status=ev["status"], reproduce=reproduce, chart=_dd_chart(ev, lang),
        recomputed="_recomputed" in ev,
    )


@dataclass
class ClarifyOption:
    ticker: str
    name: str


def clarify_options(analysis: Analysis) -> List[ClarifyOption]:
    """Si asset_lookup a renvoyé une requête ambiguë, propose les candidats cliquables."""
    for e in analysis.result.ledger.evidence():
        if e["tool"] == "asset_lookup" and (e.get("value") or {}).get("ambiguous"):
            return [ClarifyOption(c["ticker"], c.get("name", c["ticker"])) for c in e["value"]["candidates"]]
    return []


def out_of_universe(analysis: Analysis) -> List[str]:
    return [e["params"].get("query") for e in analysis.result.ledger.evidence()
            if e["tool"] == "asset_lookup" and e.get("error_code") == "NOT_IN_UNIVERSE"]


@dataclass
class DiffRow:
    text: str


def comparison_diffs(cmp: Comparison, lang: str) -> List[DiffRow]:
    """Différences saillantes entre deux profils sur la même demande : limites dérivées et statuts par actif."""
    rows: List[DiffRow] = []
    lc, rc = cmp.left.verdict, cmp.right.verdict
    if not (lc and rc):
        return rows
    pa, pb = cmp.left.profile.profile_id, cmp.right.profile.profile_id
    for key, metric in (("max_volatility", t("max_vol", lang)), ("max_drawdown", t("max_dd", lang))):
        a, b = lc["constraints"][key], rc["constraints"][key]
        if abs(a - b) > 1e-9:
            rows.append(DiffRow(t("diff_limit", lang, metric=metric, a=f"{a:.0%}", b=f"{b:.0%}", pa=pa, pb=pb)))
    la = {x["ticker"]: x["status"] for x in lc["assets"]}
    rb = {x["ticker"]: x["status"] for x in rc["assets"]}
    for tk in la:
        if tk in rb and la[tk] != rb[tk]:
            rows.append(DiffRow(t("diff_status", lang, t=tk, sa=t("status_" + la[tk], lang), sb=t("status_" + rb[tk], lang), pa=pa, pb=pb)))
    return rows


def comparison_conclusion(cmp: Comparison, lang: str) -> List[str]:
    """Conclusion en langage simple de l'onglet « Comparer deux profils ». Construite à partir des deux verdicts seulement :
    aucun chiffre, aucune donnée nouvelle ; chaque analyse garde par ailleurs sa propre conclusion vérifiée."""
    lc, rc = cmp.left.verdict, cmp.right.verdict
    if not (lc and rc):
        return []
    na = cmp.left.profile.display_name or cmp.left.profile.profile_id
    nb = cmp.right.profile.profile_id if not cmp.right.profile.display_name else cmp.right.profile.display_name
    out = [t("cc_intro", lang)]
    left = {x["ticker"]: x["status"] for x in lc["assets"]}
    right = {x["ticker"]: x["status"] for x in rc["assets"]}
    differ = False
    for tk, sa in left.items():
        if tk not in right:
            continue
        sb = right[tk]
        differ |= sa != sb
        out.append(t("cc_asset", lang, t=tk, pa=na, pb=nb, sa=t("cc_status_" + sa, lang), sb=t("cc_status_" + sb, lang)))
    out.append(t("cc_differ" if differ else "cc_same", lang))
    out.append(t("cc_close", lang))
    return out


def _pc(x: Optional[float], lang: str) -> str:
    if x is None:
        return "—"
    s = f"{x * 100:.1f} %"
    return s.replace(".", ",") if lang == "fr" else s.replace(" %", "%")


def report_markdown(analysis: Analysis, lang: str, store: Any = None) -> str:
    """Rapport d'analyse exportable (Markdown). Reprend uniquement ce qui est au registre : aucun chiffre nouveau.

    Contenu : bandeau de simulation, demande et profil, verdict, explication vérifiée, sensibilité, limites, preuves
    (identifiant, outil, fenêtre, empreinte, commande pour reproduire) et sources.
    """
    fr = lang == "fr"
    p, v = analysis.profile, analysis.verdict
    title = "Rapport d'analyse" if fr else "Analysis report"
    out: List[str] = [f"# {t('app_title', lang)} — {title}", "", f"> **{t('simulation_banner', lang)}**", ""]
    out += [f"- **{t('profile', lang)}** : {p.display_name or p.profile_id}",
            f"- **{t('as_of', lang)}** : {analysis.as_of}",
            f"- **{t('base_currency', lang)}** : {analysis.base_currency}",
            f"- **{'Demande' if fr else 'Request'}** : {analysis.request_text}", ""]
    if v is None:
        out += [f"_{analysis.result.message or t('no_analysis', lang)}_", ""]
        return "\n".join(out)
    b = status_badge(v["status"], lang)
    out += [f"## {t('verdict_title', lang)} : {b.icon} {b.label}", ""]
    bars = verdict_measures(analysis, lang)
    for asset in v["assets"] + v.get("alternatives", []):
        ab = status_badge(asset["status"], lang)
        out.append(f"### {asset['ticker']} — {ab.icon} {ab.label}")
        for m in bars.get(asset["ticker"], []):
            out.append(f"- {m.icon} {m.label} : {_pc(m.value, lang)} ({t('limit', lang)} {_pc(m.limit, lang)}) — {t('window', lang)} « {m.window} » [{m.evidence_id}]")
        for r in asset_reasons(asset, lang):
            out.append(f"- {r.icon} *{r.kind}* — {r.message}")
        out.append("")
    for g in v.get("global_reasons", []):
        out.append(f"- {'⚠' if g['severity'] == 'warn' else 'ℹ'} {g['message']}")
    if analysis.result.answer:
        body = [("##" + ln if ln.startswith("#") else ln) for ln in analysis.result.answer.splitlines()]  # titres rabaissés d'un niveau
        out += ["", f"## {t('explanation_title', lang)}", ""] + body
    for asset in v["assets"]:
        tab = sensitivity_table(analysis, asset["ticker"])
        if tab and tab.indicator:
            out += ["", f"## {t('sensitivity_title', lang)} — {tab.ticker} : {t('sens_' + tab.indicator, lang)}", "",
                    f"| {t('window', lang)} | vol. | drawdown | ev |", "|---|---|---|---|"]
            for r in tab.rows:
                out.append(f"| {r['window']} | {_pc(r['volatility'], lang)} | {_pc(r['max_drawdown'], lang)} | {r['evidence_id']} |")
    pv = portfolio_view(analysis, lang)
    if pv is not None:
        out += ["", f"## {t('pf_title', lang)}", "", f"_{pv.label}_", "", f"- {pv.rebalancing_description} ({pv.annualization_basis})"]
        out += [f"- {x['ticker']} : {_pc(x['weight'], lang)}" for x in pv.positions]
    ev = enrichment_view(analysis, lang)
    if ev.rows:
        out += ["", f"## {t('enr_title', lang)}", ""]
        for r in ev.rows:
            if r.status == "ok":
                out.append(f"- `{r.record_id}` · {r.ticker} · {r.kind} · {t('enr_level', lang)} {r.confidence} · {t('enr_published', lang)} "
                           f"{r.date_publication} · {t('enr_source', lang)} {r.source}")
            else:
                out.append(f"- `{r.record_id}` · {r.ticker} · {r.kind} · {t('enr_gap', lang)} ({r.reason_code})")
        out += ["", f"_{t('enr_rule_c', lang)}_"]
    out += ["", f"## {'Preuves' if fr else 'Evidence'}", ""]
    for eid, ev in evidence_index(analysis).items():
        args = reproduce_arguments(eid, analysis)
        call = f" — `run_tool(store, {ev['tool']!r}, {args!r})`" if args is not None else ""
        out.append(f"- `{eid}` · {ev['tool']} v{ev['tool_version']} · {t('fingerprint', lang)} {ev['data_fingerprint'][:12]}{call}")
    sources = sorted({s for ev in evidence_index(analysis).values() for s in ev.get("sources", [])})
    out += ["", f"## {t('sources_title', lang)}", ""] + [f"- {s}" for s in sources]
    return "\n".join(out) + "\n"
