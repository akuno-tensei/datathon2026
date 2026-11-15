"""Outil `compose_explanation` : rédige des énoncés structurés à partir de la vue du registre, puis les met en forme.

Aucun nombre n'est écrit à la main : chaque chiffre passe par `_Builder.num`, qui l'enregistre comme `Figure` rattachée à une
entrée du registre. La langue (`profile.language`) choisit le gabarit ; l'expérience (`profile.experience`) règle le niveau de
détail : définitions intégrées pour le débutant, base d'annualisation, ratios et sources pour l'avancé.
Aucune branche par persona : tout vient du profil.
"""
import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..governance.models import AssetAssessment, Reason
from ..orchestrator.profile import InvestorProfile
from .config import LABELS, SECTIONS, TEMPLATES
from .formatting import format_number
from .ledger_read import LedgerView, numeric_leaves, seq_ref
from .models import Figure, Limits, Statement, Unit, VerificationReport

# Ordre de lecture des fondamentaux : résultats, puis bilan.
FUND_ORDER = ("revenue", "net_income", "operating_cash_flow", "cash", "long_term_debt", "equity", "total_assets")
FUND_BEGINNER = ("revenue", "net_income", "long_term_debt")  # profil débutant : trois montants ; le tableau complet reste à l'écran
MAX_ENRICHED_FACTS = 3  # faits de niveau C cités par enregistrement : l'explication reste lisible
_WINDOW_NONE = {"fr": "analysée", "en": "analysed"}
_QUOTES = {"fr": ("« ", " »"), "en": ('"', '"')}


def has_digit(s: str) -> bool:
    return bool(re.search(r"\d", s))


class Builder:
    """Accumule chiffres, références et chaînes autorisées d'un énoncé."""

    def __init__(self, lang: str):
        self.lang = lang
        self.figures: List[Figure] = []
        self.refs: List[str] = []
        self.literals: List[str] = []

    def ref(self, *refs: Optional[str]) -> None:
        for r in refs:
            if r and r not in self.refs:
                self.refs.append(r)

    def num(self, value: float, unit: Unit, ref: str) -> str:
        shown = format_number(value, unit, self.lang)
        self.figures.append(Figure(value=float(value), unit=unit, shown=shown, ref=ref))
        self.ref(ref)
        return shown

    def lit(self, s: Any) -> str:
        s = str(s)
        if has_digit(s) and s not in self.literals:
            self.literals.append(s)
        return s


def match_ref(view: LedgerView, ids: Iterable[str], value: float, default: str) -> str:
    """Préfère la preuve de l'analyste qui contient la valeur ; à défaut, l'entrée par défaut (le verdict)."""
    target = abs(value)
    for i in ids:
        payload = view.refs.get(i)
        if payload is not None and any(abs(abs(x) - target) <= 1e-9 * max(1.0, target) for x in numeric_leaves(payload)):
            return i
    return default


class _Composer:
    def __init__(self, view: LedgerView, profile: InvestorProfile):
        self.view, self.profile = view, profile
        self.lang, self.exp = profile.language, profile.experience
        self.T = TEMPLATES[self.lang]
        self.out: List[Statement] = []
        self._n: Dict[str, int] = defaultdict(int)
        self.vref = seq_ref(view.verdict_seq) if view.verdict_seq else None

    def b(self) -> Builder:
        return Builder(self.lang)

    def add(self, section: str, text: str, b: Builder, basis: str = "evidence") -> None:
        self._n[section] += 1
        self.out.append(
            Statement(sid=f"{section}.{self._n[section]}", section=section, text=text, basis=basis,  # type: ignore[arg-type]
                      refs=list(b.refs), figures=list(b.figures), literals=list(b.literals))
        )

    def quote(self, b: Builder, s: str) -> str:
        l, r = _QUOTES[self.lang]
        return f"{l}{b.lit(s)}{r}"

    # -- résumé ------------------------------------------------------------
    def summary(self) -> None:
        T, v = self.T, self.view.verdict
        if self.exp == "debutant":
            self.add("summary", T["intro_debutant"], self.b(), basis="assumption")
        if v is None:
            self.add("summary", T["no_verdict"], self.b(), basis="assumption")
            return
        b = self.b()
        b.ref(self.vref)
        self.add("summary", T["verdict"].format(status=T["statuses"][v.status]), b)

        if self.view.request is not None:
            b = self.b()
            b.ref(seq_ref(self.view.request_seq))  # type: ignore[arg-type]
            tickers = list(self.view.request.get("tickers", [])) + list(self.view.request.get("unknown_tickers", []))
            intent = T["intents"].get(self.view.request.get("intent", "unknown"), T["intents"]["unknown"])
            self.add("summary", T["request"].format(intent=intent, tickers=", ".join(b.lit(t) for t in tickers)), b)

        p = self.view.profile
        if self.view.profile_seq and p:
            b = self.b()
            pref = seq_ref(self.view.profile_seq)
            text = T["profile"].format(
                hmin=b.num(p["horizon_years_min"], "years", pref), hmax=b.num(p["horizon_years_max"], "years", pref),
                risk=b.lit(str(p["risk_tolerance"])),
            )
            self.add("summary", text, b)

        c = v.constraints
        b = self.b()
        text = T["constraints"].format(
            level=b.lit(c.risk_level_applied), maxvol=b.num(c.max_volatility, "pct", self.vref), maxdd=b.num(c.max_drawdown, "pct", self.vref)
        )
        self.add("summary", text, b)
        if c.adjustments:
            b = self.b()
            b.ref(self.vref)
            self.add("summary", T["constraints_adjusted"], b)

    # -- données observées -------------------------------------------------
    def data(self, section: str, ticker: str) -> None:
        f = self.view.facts.get(ticker)
        if f is None:
            return
        T = self.T
        for w in f.windows:
            b = self.b()
            b.ref(w.evidence_id)
            wname = self.quote(b, w.alias) if w.alias else _WINDOW_NONE[self.lang]
            text = T[f"data_{self.exp}"].format(
                t=b.lit(ticker), w=wname, start=w.start.isoformat(), end=w.end.isoformat(),
                vol=b.num(w.volatility, "pct", w.evidence_id), dd=b.num(w.drawdown_depth, "pct", w.evidence_id),
                basis=b.lit(w.annualization_basis),
            )
            if not w.recovered and w.peak_date is not None:
                text += T["unrecovered"]
            self.add(section, text, b)

    # -- sensibilité à la période (preuve stress_window de l'analyste) -------
    def sensitivity(self, section: str, ticker: str) -> None:
        evs = [e for e in self.view.evidence
               if e.get("tool") == "stress_window" and e.get("status") != "refused" and (e.get("params") or {}).get("ticker") == ticker]
        if not evs:
            return
        ev, T = evs[-1], self.T
        v, ref = ev["value"], ev["evidence_id"]
        b = self.b()
        b.ref(ref)
        if v.get("verdict") not in ("stable", "sensible") or v.get("relative_spread") is None:
            self.add(section, T["sensitivity_undetermined"].format(t=b.lit(ticker)), b)
            return
        rows = {r["window"]: r for r in v["rows"] if "value" in r}
        lo, hi = rows[v["min_window"]], rows[v["max_window"]]
        text = T["sensitivity"].format(
            n=b.num(v["n_computed"], "ratio", ref), metric=T["sens_metrics"].get(v["metric"], b.lit(v["metric"])), t=b.lit(ticker),
            lo=b.num(lo["value"], "pct", ref), wlo=self.quote(b, lo["window"]), hi=b.num(hi["value"], "pct", ref),
            whi=self.quote(b, hi["window"]), spread=b.num(v["relative_spread"], "pct", ref), thr=b.num(v["threshold"], "pct", ref),
            verdict=T["sens_verdicts"][v["verdict"]],
        )
        if v.get("floor_applied"):
            text += T["sensitivity_floor"].format(abs=b.num(v["absolute_spread"], "pct", ref), floor=b.num(v["min_abs_spread"], "pct", ref))
        if self.exp == "debutant" and v["verdict"] == "sensible":
            text += T["sensitivity_debutant"]
        self.add(section, text, b)

    # -- corrélations (preuve correlation_matrix de l'analyste) ---------------
    def correlation(self, section: str) -> None:
        evs = [e for e in self.view.evidence if e.get("tool") == "correlation_matrix" and e.get("status") != "refused"]
        if not evs:
            return
        ev, T = evs[-1], self.T
        v, ref = ev["value"], ev["evidence_id"]
        m = v["matrices"][v["reference"]]
        pairs = m.get("pairs") or []
        if not pairs:
            return
        b = self.b()
        b.ref(ref)
        method = T["corr_methods"][v["reference"]]
        n = b.num(m["n_obs"], "ratio", ref)
        if len(pairs) == 1:
            p0 = pairs[0]
            text = T["corr_one"].format(a=b.lit(p0["a"]), b=b.lit(p0["b"]), c=b.num(p0["corr"], "corr", ref), method=method, n=n)
        else:
            hi, lo = pairs[0], pairs[-1]
            text = T["corr_many"].format(
                tickers=", ".join(b.lit(t) for t in v["tickers"]), method=method, n=n,
                a=b.lit(hi["a"]), b=b.lit(hi["b"]), cmax=b.num(hi["corr"], "corr", ref),
                c=b.lit(lo["a"]), d=b.lit(lo["b"]), cmin=b.num(lo["corr"], "corr", ref),
            )
        if v.get("mixed_calendars"):
            text += T["corr_mixed"]
        if self.exp == "debutant":
            text += T["corr_debutant"]
        self.add(section, text, b)

    # -- scénario de portefeuille (preuve portfolio_metrics : rejeu historique, jamais une prévision) --
    def portfolio(self, section: str) -> None:
        evs = [e for e in self.view.evidence if e.get("tool") == "portfolio_metrics" and e.get("status") != "refused"]
        if not evs:
            return
        ev, T = evs[-1], self.T
        v, ref = ev["value"], ev["evidence_id"]
        mt, cc, rk = v["metrics"], v["concentration"], v["risk"]
        b = self.b()
        b.ref(ref)
        w = (ev.get("window") or {})
        wname = self.quote(b, w["alias"]) if w.get("alias") else _WINDOW_NONE[self.lang]
        rebal = T["pf_rebal"][v["rebalancing"]].format(step=T["pf_steps"].get(v["period_label"], v["period_label"]))
        weights = ", ".join(f"{b.lit(x['ticker'])} {b.num(x['weight'], 'pct', ref)}" for x in v["positions"])
        common = dict(rebal=rebal, basis=b.lit(v["annualization_basis"]), n=b.num(cc["n_positions"], "ratio", ref), weights=weights,
                      w=wname, vol=b.num(mt["annualized_volatility"], "pct", ref), dd=b.num(mt["max_drawdown"]["depth"], "pct", ref))
        if mt["annualized_return"] is not None:
            text = T["pf_main_ann"].format(ann=b.num(mt["annualized_return"], "pct", ref), **common)
        else:
            text = T["pf_main"].format(total=b.num(mt["total_return"], "pct", ref), **common)
        self.add(section, text, b, basis="scenario")
        b = self.b()
        b.ref(ref)
        self.add(section, T["pf_conc"].format(maxw=b.num(cc["max_weight"], "pct", ref), t=b.lit(cc["max_weight_ticker"]),
                                             neff=b.num(cc["effective_positions"], "ratio", ref)), b, basis="scenario")
        if rk.get("contributions") and rk.get("diversification_ratio") is not None:
            top = max(rk["contributions"], key=lambda c: c["risk_share"])
            b = self.b()
            b.ref(ref)
            text = T["pf_risk"].format(t=b.lit(top["ticker"]), share=b.num(top["risk_share"], "pct", ref),
                                      dr=b.num(rk["diversification_ratio"], "ratio", ref))
            if self.exp == "debutant":
                text += T["pf_risk_debutant"]
            self.add(section, text, b, basis="scenario")
        ws = [x["weight"] for x in v["positions"]]
        basis = v.get("weights_basis") or ("assumed_equal" if len(ws) > 1 and max(ws) - min(ws) < 1e-9 else "user")
        if basis == "assumed_equal":
            self.add(section, T["pf_equal"], self.b(), basis="assumption")
        else:
            b = self.b()
            b.ref(ref)
            self.add(section, T["pf_user"], b)  # fait : l'origine des poids est consignée dans les paramètres de la preuve

    # -- données enrichies : chaque énoncé porte son niveau de confiance, sa date de publication et sa source ---------
    def enrichment(self, section: str) -> None:
        T = self.T
        cards = self.view.cards
        for rec in self.view.enrichment:
            if rec.get("status") != "ok":
                continue
            card = cards.get(rec.get("source_card_id"), (None, {}))[1]
            source = card.get("name", "source non documentée")
            t = rec["ticker"]
            if rec["kind"] == "filings" and rec.get("value"):
                v = rec["value"]
                b = self.b()
                b.ref(rec["record_id"])
                k = dict(form=b.lit(v.get("form", "")), t=b.lit(t), filed=b.lit(v.get("filing_date", "")), level=rec["confidence"], source=b.lit(source))
                if v.get("report_date"):
                    self.add(section, T["enr_filing_period"].format(period=b.lit(v["report_date"]), **k), b)
                else:
                    self.add(section, T["enr_filing"].format(**k), b)
            if rec["kind"] == "fundamentals" and (rec.get("value") or {}).get("metrics"):
                v = rec["value"]
                b = self.b()
                b.ref(rec["record_id"])
                labels, parts = T["fund_labels"], []
                order = FUND_BEGINNER if self.exp == "debutant" else FUND_ORDER
                ordered = [(n, v["metrics"][n]) for n in order if n in v["metrics"]] + (
                    [] if self.exp == "debutant" else [(n, x) for n, x in v["metrics"].items() if n not in FUND_ORDER])
                for name, amount in ordered:  # ordre de lecture d'un rapport ; le signe se dit par le libellé, jamais par le chiffre
                    key = {"net_income": "net_loss", "operating_cash_flow": "negative_operating_cash_flow",
                           "equity": "negative_equity"}.get(name, name) if amount < 0 else name
                    if key in labels:
                        parts.append(labels[key].format(amount=b.num(amount, "money", rec["record_id"])))  # libellé et montant : un gabarit
                if parts:
                    self.add(section, T["enr_fundamentals"].format(
                        t=b.lit(t), period=b.lit(v.get("period_end", "")), form=b.lit(v.get("form", "")), filed=b.lit(v.get("filing_date", "")),
                        level=rec["confidence"], source=b.lit(source), metrics=", ".join(parts)), b)
                    if not getattr(self, "_fund_note", False):  # la précision ne se répète pas pour chaque actif
                        self._fund_note = True
                        self.add(section, T["enr_fund_note"], self.b(), basis="assumption")
            for fact in rec.get("facts", [])[:MAX_ENRICHED_FACTS]:
                b = self.b()
                b.ref(rec["record_id"])
                text = T["enr_fact"].format(t=b.lit(t), date=b.lit(rec["date_publication"]), source=b.lit(source), excerpt=b.lit(fact["excerpt"]))
                self.add(section, text, b)

    # -- raisons de gouvernance ---------------------------------------------
    def reason(self, section: str, ticker: Optional[str], r: Reason) -> None:
        if r.code == "TOOL_UNAVAILABLE":
            return  # traité par limits_builder (non couvert)
        T, m = self.T, r.measures
        b = self.b()
        b.ref(self.vref, *r.evidence_ids)
        k = dict(t=b.lit(ticker or ""))
        basis = "evidence"
        code = r.code
        tpl = T["reasons"].get(code)

        if code in ("VOLATILITY_ABOVE_LIMIT", "DRAWDOWN_ABOVE_LIMIT") and tpl:
            k["value"] = b.num(m["value"], "pct", match_ref(self.view, r.evidence_ids, m["value"], self.vref))
            k["limit"] = b.num(m["limit"], "pct", self.vref)
            k["where"] = T["where"].format(w=b.lit(m["window"])) if m.get("window") else ""
            k["ratio"] = T["ratio"].format(r=b.num(m["ratio"], "ratio", self.vref)) if self.exp != "debutant" and m.get("ratio") else ""
        elif code == "RECOVERY_LONGER_THAN_HORIZON" and tpl:
            k["years"] = b.num(m["recovery_years"], "years", self.vref)
            k["h"] = b.num(m["horizon_years_min"], "years", self.vref)
            k["peak"], k["end"] = m["peak_date"], m["end_date"]
            k["state"] = T["recovered"] if m.get("recovered") else T["not_recovered"]
        elif code in ("EXCLUDED_ASSET_CLASS", "EXCLUDED_SECTOR", "EXCLUDED_TICKER") and tpl:
            k["value"] = b.lit(m.get("excluded_value", ""))
        elif code == "CLASS_NOT_PERMITTED" and tpl:
            k["cls"], k["level"] = b.lit(m.get("asset_class", "")), b.lit(m.get("risk_level_applied", ""))
        elif code in ("THEME_EXCLUSION_UNVERIFIED", "THEME_PROXY", "THEME_DATA_MISSING") and tpl:
            k["theme"] = b.lit(m.get("theme", ""))
            basis = "assumption" if code == "THEME_PROXY" else "evidence"
        elif code == "PREFERENCE_MATCH" and tpl:
            k["kind"], k["value"] = b.lit(m.get("kind", "")), b.lit(m.get("value", ""))
        elif code == "OUT_OF_UNIVERSE" and tpl:
            sugg = m.get("suggestions") or []
            k["suggestions"] = T["suggestions"].format(s=", ".join(b.lit(x) for x in sugg)) if sugg else ""
        elif code == "HIGH_CORRELATION" and tpl:
            cref = r.evidence_ids[0] if r.evidence_ids else self.vref
            k.update(a=b.lit(m["a"]), b=b.lit(m["b"]), corr=b.num(m["corr"], "corr", cref), thr=b.num(m["threshold"], "corr", self.vref))
        elif code in ("PORTFOLIO_WEIGHT_ABOVE_CAP", "PORTFOLIO_CLASS_CAP", "PORTFOLIO_VOLATILITY_ABOVE_LIMIT",
                      "PORTFOLIO_DRAWDOWN_ABOVE_LIMIT") and tpl:
            k["value"] = b.num(m["value"], "pct", match_ref(self.view, r.evidence_ids, m["value"], self.vref))
            k["limit"] = b.num(m["limit"], "pct", self.vref)
            k["level"] = b.lit(m.get("risk_level_applied", ""))
            if code == "PORTFOLIO_WEIGHT_ABOVE_CAP":
                k["t"] = b.lit(m["ticker"])
            if code == "PORTFOLIO_CLASS_CAP":
                k["cls"] = b.lit(m["asset_class"])
        elif code in ("ENRICHED_DATA", "ENRICHMENT_GAP", "EXCLUDED_THEME_CONTROVERSY", "PREFERENCE_CONTRADICTED", "THEME_VERIFIED",
                      "CONTROVERSY_SIGNAL_LEVEL_C", "ESG_DATA_NO_THRESHOLD") and tpl:
            k.update(kind=b.lit(m.get("kind", "")), level=m.get("confidence", ""), date=b.lit(m.get("date_publication", "")),
                     source=b.lit(m.get("source", "")), themes=", ".join(b.lit(x) for x in m.get("themes", [])),
                     codes=", ".join(b.lit(x) for x in m.get("reason_codes", [])))
        elif code == "AMBIGUOUS_ASSET" and tpl:
            k["candidates"] = ", ".join(b.lit(x) for x in m.get("candidates") or [])
        elif tpl and code in ("METRICS_UNAVAILABLE", "NO_METRICS"):
            pass
        elif code == "NO_ALTERNATIVE" and tpl:
            pass
        elif code == "EXCLUSION_VALUE_UNKNOWN" and tpl:
            k["kind"] = T["exclusion_kinds"].get(m.get("kind", ""), b.lit(m.get("kind", "")))
            k["value"] = b.lit(m.get("value", ""))
            sugg, known = m.get("suggestions") or [], m.get("known_values") or []
            if sugg:
                k["hint"] = T["exclusion_close"].format(s=", ".join(b.lit(x) for x in sugg))
            elif known:
                k["hint"] = T["exclusion_known"].format(s=", ".join(b.lit(x) for x in known))
            else:
                k["hint"] = T["exclusion_file"]
        else:
            tpl = None

        if tpl is None:
            if self.lang == "fr" and not has_digit(r.message):
                text = r.message
            else:
                text = T["reasons"]["_generic"].format(code=b.lit(code))
        else:
            text = tpl.format(**k)
        self.add(section, text, b, basis=basis)

    def assessment(self, section: str, a: AssetAssessment, skip_info: bool = False) -> None:
        for r in a.reasons:
            if skip_info and r.severity == "info":
                continue
            self.reason(section, a.ticker, r)

    # -- assemblage --------------------------------------------------------
    def run(self) -> List[Statement]:
        T, v = self.T, self.view.verdict
        self.summary()
        if v is None:
            for t in self.view.facts:
                self.data("data", t)
            return self.out
        for a in v.assets:
            self.data("data", a.ticker)
            self.sensitivity("data", a.ticker)
        self.correlation("data")
        self.portfolio("data")
        self.enrichment("data")
        for a in v.assets:
            b = self.b()
            b.ref(self.vref)
            self.add("fit", T["asset_status"].format(t=b.lit(a.ticker), status=T["statuses"][a.status]), b)
            self.assessment("fit", a)
        for r in v.global_reasons:
            self.reason("fit", None, r)
        for alt in v.alternatives:
            b = self.b()
            b.ref(self.vref)
            if alt.alternative_for:
                text = T["alt_status"].format(
                    t=b.lit(alt.ticker), owners=", ".join(b.lit(o) for o in alt.alternative_for), status=T["statuses"][alt.status]
                )
            else:
                text = T["alt_status_no_owner"].format(t=b.lit(alt.ticker), status=T["statuses"][alt.status])
            self.add("alternatives", text, b)
            self.data("alternatives", alt.ticker)
            self.assessment("alternatives", alt, skip_info=True)
        for s in v.scope_refusals:
            if has_digit(s.message) or has_digit(s.offered_instead):
                continue
            b = self.b()
            b.ref(self.vref)
            self.add("scope", T["scope"].format(message=s.message, offered=s.offered_instead), b)
        if (self.view.request or {}).get("intent") == "build_scenario":
            self.add("scope", T["scenario"], self.b(), basis="scenario")
        order = {s: i for i, s in enumerate(SECTIONS)}
        self.out.sort(key=lambda st: order[st.section])  # tri stable : l'ordre d'écriture est conservé dans une section
        return self.out


def compose_explanation(view: LedgerView, profile: InvestorProfile, exclude: Iterable[str] = ()) -> List[Statement]:
    """Énoncés du corps (hors limites). `exclude` : identifiants rejetés par le vérificateur lors d'une tentative précédente."""
    skip = set(exclude)
    return [s for s in _Composer(view, profile).run() if s.sid not in skip]


# ---- mise en forme -----------------------------------------------------


def render(
    statements: List[Statement], limits: Limits, profile: InvestorProfile, report: VerificationReport
) -> str:
    T, labels = TEMPLATES[profile.language], LABELS[profile.language]
    show_refs = profile.experience == "avance"

    def line(s: Statement) -> str:
        out = f"- [{labels[s.label or 'hypothese']}] {s.text}"
        if show_refs and s.refs:
            sep = " :" if profile.language == "fr" else ":"
            out += f"\n  (sources{sep} {', '.join(s.refs)})"
        return out

    parts: List[str] = [f"# {T['heading']}"]
    for section in SECTIONS:
        items = [s for s in statements if s.section == section]
        if items:
            parts += ["", f"## {T['titles'][section]}"] + [line(s) for s in items]
    groups = [(k, getattr(limits, k)) for k in ("assumptions", "limits", "uncertainties", "not_covered")]
    if any(items for _, items in groups):
        parts += ["", f"## {T['titles']['limits_group']}"]
        for key, items in groups:
            if items:
                parts += ["", f"### {T['titles'][key]}"] + [line(s) for s in items]
    conclusion = [s for s in statements if s.section == "conclusion"]  # toujours en dernier : le résumé en langage simple
    if conclusion:
        parts += ["", f"## {T['titles']['conclusion']}"] + [line(s) for s in conclusion]
    n = report.verified + len(report.downgraded)
    parts += ["", "---", T["footer"].format(n=n, r=len(report.removed), d=len(report.downgraded))]
    return "\n".join(parts) + "\n"
