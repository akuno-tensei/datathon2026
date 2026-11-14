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


def agent_trace(analysis: Analysis, lang: str) -> List[TraceStep]:
    """Chronologie observable des agents à partir des entrées du registre. Jamais le raisonnement interne du modèle."""
    steps: List[TraceStep] = []
    for e in analysis.result.ledger.entries():
        p, kind = e.payload, e.kind
        if kind == "profile":
            steps.append(TraceStep("orchestrateur", t("agent_orchestrateur", lang), "✓", "ok", t("step_profile", lang)))
        elif kind == "request":
            steps.append(TraceStep("orchestrateur", t("agent_orchestrateur", lang), "✓", "ok",
                                   t("step_request", lang, intent=p.get("intent"), tickers=", ".join(p.get("tickers", [])) or "—")))
        elif kind == "plan":
            steps.append(TraceStep("orchestrateur", t("agent_orchestrateur", lang), "✓", "ok", t("step_plan", lang, n=len(p.get("steps", [])))))
        elif kind == "step" and p.get("tool"):
            st = p.get("status", "ok")
            agent = "analyste"
            fb = str(p.get("step_id", "")).startswith("fb")
            text = t("step_tool", lang, tool=p["tool"]) + (f"  ({p['arguments'].get('ticker') or p['arguments'].get('query') or ''})").rstrip()
            steps.append(TraceStep(agent, t("agent_analyste", lang), _ICON.get(st, "•"), st, text,
                                   detail={"arguments": p.get("arguments", {}), "feedback": fb}, evidence_id=p.get("evidence_id")))
        elif kind == "verdict":
            steps.append(TraceStep("gouvernance", t("agent_gouvernance", lang), _ICON.get(p["status"], "•"), p["status"],
                                   t("step_verdict", lang, status=t("status_" + p["status"], lang))))
        elif kind == "note" and e.agent == "explicabilite":
            continue  # rendu par l'étape Explicabilité ci-dessous
        elif kind == "note":
            steps.append(TraceStep("orchestrateur", t("agent_orchestrateur", lang), "⚠", "warning", t("step_note", lang, message=p.get("message", ""))))
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
                       min(dd / c["max_drawdown"], 1.0), _wname(wd), origin, "adj_close, " + _wname(wd), wd["evidence_id"]),
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
        if keep is not None and s.section not in keep:
            continue
        out.setdefault(s.section, []).append(
            ExplanationLine(s.section, LABELS[lang].get(s.label or "fait", s.label or ""), s.text, list(s.refs),
                            [{"shown": f.shown, "ref": f.ref} for f in s.figures]))
    return out


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
