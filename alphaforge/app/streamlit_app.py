"""Interface Streamlit d'AlphaForge AI. Ne contient aucune logique métier : elle rend les vues de `views.py`.

Lancement : streamlit run alphaforge/app/streamlit_app.py
"""
from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from alphaforge.app import views
from alphaforge.app.i18n import t
from alphaforge.app.pipeline import AlphaForge, Analysis
from alphaforge.orchestrator import ProfileError, load_profile, validate_profile

st.set_page_config(page_title="AlphaForge AI", page_icon="⚖", layout="wide")


@st.cache_resource
def forge() -> AlphaForge:
    return AlphaForge.from_directory()


F = forge()
S = st.session_state
S.setdefault("analysis", None)
S.setdefault("comparison", None)
S.setdefault("evidence_open", None)
S.setdefault("request", "")


def pct(x: Optional[float], lang: str) -> str:
    if x is None:
        return "—"
    s = f"{x * 100:.1f} %"
    return s.replace(".", ",") if lang == "fr" else s.replace(" %", "%")


# ---- bandeau supérieur --------------------------------------------------
profiles = F.list_profiles()
ids = [p.profile_id for p in profiles]
top = st.columns([2.2, 2, 1.6, 1.1, 1.1, 2])
with top[0]:
    st.markdown("### ⚖ AlphaForge AI")
with top[5]:
    lang = st.radio("FR | EN", ["fr", "en"], horizontal=True, label_visibility="collapsed", key="lang")
with top[1]:
    pid = st.selectbox(t("profile", lang), ids, index=ids.index("robert") if "robert" in ids else 0,
                       format_func=lambda i: next(p.display_name for p in profiles if p.profile_id == i))
profile = F.get_profile(pid)
with top[2]:
    as_of = st.date_input(t("as_of", lang), value=F.store.last_date, min_value=F.store.first_date, max_value=F.store.last_date)
with top[3]:
    base = st.selectbox(t("base_currency", lang), ["CAD", "USD"], index=0 if profile.base_currency == "CAD" else 1)
with top[4]:
    st.markdown(f"**{t('mode', lang)}**  \n⚙ {t('mode_core', lang)}")

st.warning("⚠ " + t("simulation_banner", lang))  # jamais masquable
st.caption("ℹ " + t("mode_core_note", lang))


# ---- rendu des éléments partagés ----------------------------------------
def evidence_button(eid: str, key: str) -> None:
    if st.button(f"🔎 {eid}", key=key, help=t("drawer_title", lang)):
        S.evidence_open = eid


def render_profile_panel(p) -> None:
    v = validate_profile(p)
    st.markdown(f"#### {p.display_name or p.profile_id}")
    st.markdown(
        f"- **{t('horizon', lang)}** : {p.horizon_years_min}–{p.horizon_years_max}\n"
        f"- **{t('risk', lang)}** : {p.risk_tolerance}\n"
        f"- **{t('experience', lang)}** : {p.experience}\n"
        f"- **{t('objectives', lang)}** : {', '.join(p.objectives) or t('none', lang)}\n"
        f"- **{t('preferences', lang)}** : {', '.join(f'{x.kind}:{x.value} ({x.weight:g})' for x in p.soft_preferences) or t('none', lang)}\n"
        f"- **{t('exclusions', lang)}** : {', '.join(f'{x.kind}:{x.value}' for x in p.hard_exclusions) or t('none', lang)}"
    )
    for i in v.issues:
        (st.error if i.severity == "error" else st.warning)(("⛔ " if i.severity == "error" else "⚠ ") + i.message)
    from alphaforge.governance import GovernanceConfig, derive_constraints

    c = derive_constraints(p, GovernanceConfig())
    st.markdown(f"**{t('derived_limits', lang)}**")
    st.markdown(
        f"- {t('max_vol', lang)} : {pct(c.max_volatility, lang)}\n- {t('max_dd', lang)} : {pct(c.max_drawdown, lang)}\n"
        f"- {t('max_weight', lang)} : {pct(c.max_single_asset_weight, lang)}\n"
        f"- {t('class_caps', lang)} : {', '.join(f'{k} {pct(x, lang)}' for k, x in c.class_caps.items()) or t('none', lang)}"
    )
    for adj in c.adjustments:
        st.caption("ℹ " + adj)


def render_verdict(a: Analysis, key: str) -> None:
    r = a.result
    if r.status == "profile_invalid":
        st.error(f"⛔ {t('profile_invalid', lang)} : {r.message}")
        return
    if r.status == "needs_clarification":
        st.info("❓ " + t("needs_clarification", lang))
        return
    for opt in views.clarify_options(a):  # requête ambiguë : choix cliquables, jamais une supposition
        st.info("❓ " + t("clarify_title", lang))
        if st.button(f"{opt.ticker} — {opt.name}", key=f"{key}-clar-{opt.ticker}"):
            S.request = t("tpl_evaluate", lang, a=opt.ticker)
    for tk in views.out_of_universe(a):
        st.warning("⚠ " + t("out_of_universe", lang, t=tk))
        st.button(t("enrich_btn", lang), key=f"{key}-enr-{tk}", disabled=True, help=t("enrich_off", lang))
    v = a.verdict
    if not v:
        return
    b = views.status_badge(v["status"], lang)
    st.markdown(f"## {b.icon} {b.label}")
    measures = views.verdict_measures(a, lang)
    for asset in v["assets"] + v.get("alternatives", []):
        ab = views.status_badge(asset["status"], lang)
        alt = f" — {t('alternative_for', lang)} {', '.join(asset['alternative_for'])}" if asset.get("alternative_for") else ""
        with st.container(border=True):
            st.markdown(f"**{asset['ticker']}** · {ab.icon} {ab.label}{alt}")
            for i, m in enumerate(measures.get(asset["ticker"], [])):
                c1, c2, c3 = st.columns([3, 4, 2])
                c1.markdown(f"{m.icon} **{m.label}** : {pct(m.value, lang)} — {t('limit', lang)} {pct(m.limit, lang)}"
                            f" ({t('ok', lang) if m.ok else t('ko', lang)})")
                c2.progress(m.ratio, text=f"{t('window', lang)} « {m.window} » · {m.basis}")
                with c3:
                    evidence_button(m.evidence_id, f"{key}-m-{asset['ticker']}-{i}")
                st.caption(m.origin)
            for j, rl in enumerate(views.asset_reasons(asset, lang)):
                st.markdown(f"{rl.icon} *{rl.kind}* — {rl.message}")
    for g in v.get("global_reasons", []):
        st.markdown(f"{'⚠' if g['severity'] == 'warn' else 'ℹ'} {g['message']}")
    for sr in v.get("scope_refusals", []):
        st.error(f"⛔ {t('scope_title', lang)} : {sr['message']} → {sr['offered_instead']}")


def render_lines(lines: List[views.ExplanationLine], key: str) -> None:
    for i, l in enumerate(lines):
        refs = [r for r in l.refs if r.startswith("ev_")]
        cols = st.columns([8, 2])
        cols[0].markdown(f"`{l.label}` {l.text}")
        with cols[1]:
            for j, r in enumerate(refs[:2]):
                evidence_button(r, f"{key}-{i}-{j}")


def render_explanation(a: Analysis, key: str) -> None:
    if a.explanation is None:
        return
    st.markdown(f"### {t('explanation_title', lang)}")
    detail = st.radio(t("level", lang), ["summary", "detail"], index=0 if a.profile.experience == "debutant" else 1,
                      format_func=lambda x: t("level_" + x, lang), horizontal=True, key=f"{key}-lvl") == "detail"
    banner = views.verification_banner(a, lang)
    (st.success if a.explanation.report.approved else st.info)(("✓ " if a.explanation.report.approved else "ℹ ") + banner)
    for sec, lines in views.explanation_sections(a, lang, detail).items():
        st.markdown(f"**{t('section_' + sec, lang)}**")
        render_lines(lines, f"{key}-{sec}")


def render_sensitivity(a: Analysis, key: str) -> None:
    if not a.verdict:
        return
    st.markdown(f"### {t('sensitivity_title', lang)}")
    for asset in a.verdict["assets"]:
        tab = views.sensitivity_table(a, asset["ticker"])
        if not tab:
            continue
        if tab.indicator:
            spread = f" · {pct(tab.relative_spread, lang)}" if tab.relative_spread is not None else ""
            st.markdown(f"**{tab.ticker}** — {'⚠' if tab.indicator == 'sensible' else '✓' if tab.indicator == 'stable' else '•'} "
                        f"{t('sens_' + tab.indicator, lang)}{spread} ({t('threshold', lang)} {pct(tab.threshold, lang)})")
            evidence_button(tab.evidence_id, f"{key}-stress-{tab.ticker}")
        else:
            st.markdown(f"**{tab.ticker}** — " + t("sens_pending", lang))
        df = pd.DataFrame(tab.rows)
        for col in ("volatility", "max_drawdown", "total_return"):
            df[col] = df[col].map(lambda x: pct(x, lang) if x == x and x is not None else "—")  # « — » : fenêtre non calculée
        st.dataframe(df, hide_index=True, width="stretch")
        cols = st.columns(min(len(tab.rows), 5) or 1)
        for i, r in enumerate(tab.rows):
            with cols[i % len(cols)]:
                st.caption(r["window"])
                evidence_button(r["evidence_id"], f"{key}-sens-{tab.ticker}-{i}")


def render_limits(a: Analysis, key: str) -> None:
    st.markdown(f"### {t('limits_title', lang)}")  # toujours présente, jamais repliée
    for sec, lines in views.limit_sections(a, lang).items():
        if lines:
            st.markdown(f"**{t('section_' + sec, lang)}**")
            render_lines(lines, f"{key}-lim-{sec}")


def render_trace(a: Analysis, key: str) -> None:
    st.markdown(f"### {t('trace_title', lang)}")
    n = views.feedback_iterations(a)
    for k in range(1, n + 1):
        st.caption("🔁 " + t("loop", lang, k=k, n=2))
    for i, s in enumerate(views.agent_trace(a, lang)):
        label = f"{s.icon} {s.agent_label} · {s.text}" + ("  🔁" if s.detail.get("feedback") else "")
        with st.expander(label, expanded=False):
            st.json(s.detail or {"status": s.status})
            if s.evidence_id:
                evidence_button(s.evidence_id, f"{key}-tr-{i}")


def render_drawer(a: Analysis) -> None:
    eid = S.evidence_open
    if not eid:
        st.caption(t("drawer_hint", lang))
        return
    card = views.evidence_card(eid, a, lang, store=F.store)
    if card is None:
        return
    st.markdown(f"### 🔎 {t('drawer_title', lang)} · `{card.evidence_id}`")
    if card.recomputed:
        st.caption("ℹ " + t("recomputed", lang))
    if st.button(t("close", lang), key="drawer-close"):
        S.evidence_open = None
        st.rerun()
    st.markdown(f"**{t('tool', lang)}** : {card.tool} v{card.version} · {card.status}  \n**{t('n_obs', lang)}** : {card.n_obs}")
    if card.window:
        st.markdown(f"**{t('window', lang)}** : {card.window.get('start')} → {card.window.get('end')} ({card.window.get('alias') or '—'})")
    st.markdown(f"**{t('params', lang)}**")
    st.json(card.params)
    st.markdown(f"**{t('inputs', lang)}**")
    st.table(pd.DataFrame([{**i, "sha256": i["sha256"][:12] + "…"} for i in card.inputs]) if card.inputs else pd.DataFrame())
    st.markdown(f"**{t('sources', lang)}**")
    for s in card.sources:
        st.markdown(f"- {s}")
    st.markdown(f"**{t('warnings', lang)}** : " + ("; ".join(card.warnings) or t("no_warnings", lang)))
    st.caption(f"{t('fingerprint', lang)} : {card.fingerprint}")
    if card.chart:
        render_series_chart(a, eid, card.chart)
    if card.reproduce:
        st.markdown(f"**{t('reproduce', lang)}**")
        st.code(card.reproduce, language="python")


def render_series_chart(a: Analysis, eid: str, chart: Dict[str, Any]) -> None:
    """Prix de la série en devise de base, déjà calculée et déposée par l'analyste (series_id). Aucun recalcul de métrique."""
    ev = views.resolve_evidence(eid, a, F.store) or {}
    sid = (ev.get("value") or {}).get("series_id_base")
    if sid and sid in F.store.series:
        s = F.store.series.get(sid).series.prices
        st.line_chart(s.rename(t("chart_price", lang)))
    st.caption(chart["alt"])  # alternative textuelle du graphique


# ---- onglets ------------------------------------------------------------
tab_an, tab_cmp, tab_prof, tab_src = st.tabs([t("tab_analysis", lang), t("tab_compare", lang), t("tab_profile", lang), t("tab_sources", lang)])

with tab_an:
    left, mid, right = st.columns([1.3, 3.2, 1.8])
    with left:
        render_profile_panel(profile)
    with mid:
        tickers = list(F.store.assets["ticker"])
        c1, c2 = st.columns(2)
        a_tk = c1.selectbox(t("asset_a", lang), tickers, index=tickers.index("NVDA"), key="asset_a")
        b_tk = c2.selectbox(t("asset_b", lang), tickers, index=tickers.index("FTS.TO"), key="asset_b")
        st.caption(t("shortcuts", lang))
        sc = st.columns(4)
        for col, name, tpl in zip(sc, ["sc_evaluate", "sc_compare", "sc_scenario", "sc_risk"], ["tpl_evaluate", "tpl_compare", "tpl_scenario", "tpl_risk"]):
            if col.button(t(name, lang), key=f"sc-{name}", width="stretch"):
                S.request = t(tpl, lang, a=a_tk, b=b_tk)
        req = st.text_area(t("request", lang), value=S.request, key="req_box", height=70)
        if st.button(t("send", lang), type="primary"):
            S.request = req
            S.evidence_open = None
            with st.status("…", expanded=False) as status:
                S.analysis = F.analyze(req, pid, as_of=as_of, base_currency=base, language=lang)
                status.update(label=f"{len(S.analysis.result.ledger)} entrées au registre", state="complete")
        a = S.analysis
        if a is None:
            st.info(t("no_analysis", lang))
        else:
            render_verdict(a, "an")
            render_explanation(a, "an")
            render_sensitivity(a, "an")
            render_limits(a, "an")
    with right:
        if S.analysis is not None:
            if S.evidence_open:
                render_drawer(S.analysis)
            render_trace(S.analysis, "an")

with tab_cmp:
    st.markdown(f"### {t('compare_title', lang)}")
    c1, c2, c3 = st.columns([2, 2, 4])
    lp = c1.selectbox(t("profile_left", lang), ids, index=ids.index("amira") if "amira" in ids else 0, key="cmp_l")
    rp = c2.selectbox(t("profile_right", lang), ids, index=ids.index("robert") if "robert" in ids else 0, key="cmp_r")
    creq = c3.text_input(t("request", lang), value=S.request or t("tpl_evaluate", lang, a="NVDA"), key="cmp_req")
    if st.button(t("compare_run", lang), type="primary", key="cmp_go"):
        S.comparison = F.compare(creq, lp, rp, as_of=as_of, base_currency=base, language=lang)
    cmp = S.comparison
    if cmp is not None:
        st.success("ℹ " + t("same_data", lang, m=cmp.left.result.ledger.evidence()[0]["data_fingerprint"][:12] if cmp.left.result.ledger.evidence() else "—"))
        diffs = views.comparison_diffs(cmp, lang)
        if diffs:
            st.markdown(f"**{t('differences', lang)}**")
            for d in diffs:
                st.markdown(f"- {d.text}")
        L, R = st.columns(2)
        for col, an, k in ((L, cmp.left, "cl"), (R, cmp.right, "cr")):
            with col:
                st.markdown(f"#### {an.profile.display_name or an.profile.profile_id}")
                render_verdict(an, k)
                render_explanation(an, k)

with tab_prof:
    st.markdown(f"### {t('profile_editor', lang)}")
    src = profile.model_dump(mode="json")
    with st.form("profile_form"):
        c1, c2, c3 = st.columns(3)
        new_id = c1.text_input("profile_id", value=f"{src['profile_id']}_copie")
        name = c2.text_input("display_name", value=(src.get("display_name") or "") + " (nouveau)")
        plang = c3.selectbox(t("language", lang), ["fr", "en"], index=["fr", "en"].index(src["language"]))
        c4, c5, c6 = st.columns(3)
        exp = c4.selectbox(t("experience", lang), ["debutant", "intermediaire", "avance"], index=["debutant", "intermediaire", "avance"].index(src["experience"]))
        risk = c5.select_slider(t("risk", lang), ["faible", "faible_modere", "modere", "modere_eleve", "eleve"], value=src["risk_tolerance"])
        cur = c6.selectbox(t("base_currency", lang), ["CAD", "USD"], index=["CAD", "USD"].index(src["base_currency"]))
        hmin, hmax = st.slider(t("horizon", lang), 0, 40, (src["horizon_years_min"], src["horizon_years_max"]))
        objs = st.multiselect(t("objectives", lang), ["preservation_capital", "revenu", "croissance_moderee", "croissance_elevee"], default=src["objectives"])
        st.caption(t("criteria_help", lang))
        prefs_txt = st.text_area(t("preferences", lang), "\n".join(f"{p['kind']}:{p['value']}:{p['weight']}" for p in src["soft_preferences"]))
        excl_txt = st.text_area(t("exclusions", lang), "\n".join(f"{p['kind']}:{p['value']}" for p in src["hard_exclusions"]))
        submitted = st.form_submit_button(t("save", lang))
    if submitted:
        from alphaforge.app.forms import parse_criteria

        try:
            data = {
                "profile_id": new_id, "display_name": name, "language": plang, "experience": exp, "risk_tolerance": risk,
                "base_currency": cur, "horizon_years_min": hmin, "horizon_years_max": hmax, "objectives": objs,
                "soft_preferences": parse_criteria(prefs_txt, with_weight=True), "hard_exclusions": parse_criteria(excl_txt, with_weight=False),
            }
            p_new = load_profile(data)
            for i in validate_profile(p_new).issues:  # incohérences signalées avant l'analyse
                (st.error if i.severity == "error" else st.warning)(("⛔ " if i.severity == "error" else "⚠ ") + i.message)
            F.save_profile(data)
            st.success("✓ " + t("saved", lang, id=p_new.profile_id))
        except (ProfileError, ValueError) as e:
            st.error(f"⛔ {t('invalid', lang)} : {getattr(e, 'message', str(e))}")

with tab_src:
    a = S.analysis
    st.markdown(f"### {t('sources_title', lang)}")
    if a is not None:
        for s in sorted({s for e in a.result.ledger.evidence() for s in e.get("sources", [])}):
            st.markdown(f"- {s}")
    from alphaforge.quant.config import DEFAULT_DATA_DIR

    st.dataframe(pd.read_csv(DEFAULT_DATA_DIR / "sources_metadata.csv"), hide_index=True, width="stretch")
    st.markdown(f"### {t('gaps_title', lang)}")
    gaps = [r for asset in ((a.verdict or {}).get("assets", []) if a else []) for r in asset["reasons"] if r["check"] in ("data_gaps", "soft_preferences")]
    for r in gaps:
        st.markdown(f"- {'⚠' if r['severity'] == 'warn' else 'ℹ'} {r['message']}")
    if a is not None and not gaps:
        st.caption(t("gaps_none", lang))
    st.caption(f"{t('fingerprint', lang)} : {F.store.fingerprint[:12]}")
    st.caption("ℹ " + t("mode_core_note", lang))
