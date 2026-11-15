"""Interface Streamlit d'AlphaForge AI. Ne contient aucune logique métier : elle rend les vues de `views.py`.

Lancement : streamlit run alphaforge/app/streamlit_app.py
"""
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Le script se lance par chemin (`streamlit run ...`) : on ajoute la racine du projet pour que `import alphaforge` marche de partout.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from alphaforge.app._reload import refresh  # noqa: E402

# Code modifié depuis le dernier affichage : les modules du projet sont réimportés à jour (voir _reload.py), sans relancer le serveur.
import os  # noqa: E402
import re  # noqa: E402

CODE_STAMP = refresh(Path(__file__).resolve().parents[1], purge=os.environ.get("ALPHAFORGE_HOT_RELOAD", "1") != "0")

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from alphaforge.app import forms, views
from alphaforge.app.i18n import t
from alphaforge.app.labels import GLOSSARY, human, human_date, humanize_text, window_name
from alphaforge.app.pipeline import AlphaForge, Analysis, enrichment_coverage
from alphaforge.explainability.config import TEMPLATES as EXPL_TEMPLATES
from alphaforge.explainability.compose import FUND_ORDER
from alphaforge.explainability.formatting import format_number
from alphaforge.governance import GovernanceConfig
from alphaforge.orchestrator import ProfileError, load_profile, validate_profile

st.set_page_config(page_title="AlphaForge AI", page_icon="⚖", layout="wide")


@st.cache_resource(max_entries=1)
def forge(code_stamp: float) -> AlphaForge:
    """Instance unique, reconstruite quand le code change (l'empreinte est la clé du cache : une ancienne classe n'est jamais réutilisée)."""
    return AlphaForge.from_directory()


F = forge(CODE_STAMP)
S = st.session_state
S.setdefault("analysis", None)
S.setdefault("comparison", None)
S.setdefault("evidence_open", None)
S.setdefault("request", "")
S.setdefault("req_box", "")  # contenu du champ « Votre demande » : seul un rappel (on_click) peut le changer après sa création


def set_request(text: str) -> None:
    """Rappel de bouton : préremplit le champ de demande. Les rappels s'exécutent avant le rendu, donc avant la création du champ."""
    S.req_box = text
    S.request = text


def pct(x: Optional[float], lang: str) -> str:
    if x is None:
        return "—"
    s = f"{x * 100:.1f} %"
    return s.replace(".", ",") if lang == "fr" else s.replace(" %", "%")


def _open_add() -> None:
    """Rappel du bouton « Ajouter » : ouvre le panneau de création avec un formulaire vierge (fonctions définies plus bas)."""
    _fill_form(dict(forms.BLANK_PROFILE, language=S.get("lang", "fr")))
    S.pf_mode = "add"


def _open_edit() -> None:
    """Rappel du bouton « Modifier » : ouvre le formulaire rempli avec le profil sélectionné (profil créé seulement)."""
    pid = S.get("profile_sel")
    if F.profile_origin(pid) != "créé":
        return
    _fill_form(forms.form_state_for_edit(F.get_profile(pid).model_dump(mode="json")))
    S.pf_edit_id = pid
    S.pf_mode = "edit"


def _open_delete() -> None:
    S.pf_del_ok = False
    S.pf_mode = "delete"


def _close_panel() -> None:
    S.pf_mode = None


# ---- bandeau supérieur --------------------------------------------------
profiles = F.list_profiles()
ids = [p.profile_id for p in profiles]
created = [p for p in profiles if F.profile_origin(p.profile_id) == "créé"]  # seuls ceux-ci se suppriment ; les exemples sont protégés
S.setdefault("pf_mode", None)  # None | "add" | "edit" | "delete" : le panneau de gestion n'apparaît que sur demande
if S.pf_mode == "delete" and not created:
    S.pf_mode = None
if S.pf_mode == "edit" and S.get("pf_edit_id") not in {p.profile_id for p in created}:
    S.pf_mode = None  # le profil en cours de modification n'existe plus
top = st.columns([2.2, 2.6, 1.6, 1.1, 1.4])
with top[0]:
    st.markdown("### ⚖ AlphaForge AI")
with top[4]:
    lang = st.radio("FR | EN", ["fr", "en"], horizontal=True, label_visibility="collapsed", key="lang")
if S.get("pending_profile") in ids:  # profil qu'on vient de créer : il devient le profil actif
    S.profile_sel = S.pop("pending_profile")
if S.get("profile_sel") not in ids:
    S.profile_sel = "robert" if "robert" in ids else ids[0]
with top[1]:
    pid = st.selectbox(t("profile", lang), ids, key="profile_sel",
                       format_func=lambda i: next(p.display_name for p in profiles if p.profile_id == i))
    pb1, pb3, pb2 = st.columns(3)
    pb1.button(t("np_add_btn", lang), key="pf_add_btn", on_click=_open_add, help=t("np_add_help", lang), width="stretch")
    editable = F.profile_origin(pid) == "créé"
    pb3.button(t("np_edit_btn", lang), key="pf_edit_btn", on_click=_open_edit, disabled=not editable, width="stretch",
               help=t("np_edit_help" if editable else "np_edit_none", lang))
    pb2.button(t("np_del_open_btn", lang), key="pf_del_open_btn", on_click=_open_delete, disabled=not created, width="stretch",
               help=t("np_del_open_help" if created else "np_delete_none", lang))
profile = F.get_profile(pid)
with top[2]:
    as_of = st.date_input(t("as_of", lang), help=t("as_of_help", lang), value=F.store.last_date, min_value=F.store.first_date, max_value=F.store.last_date)
with top[3]:
    base = st.selectbox(t("base_currency", lang), ["CAD", "USD"], index=0 if profile.base_currency == "CAD" else 1)

st.warning("⚠ " + t("simulation_banner", lang))  # jamais masquable
# Enrichissement : une ligne à part, sous l'avertissement, pour ne pas déformer le bandeau.
er1, er2 = st.columns([1.3, 6], vertical_alignment="center")
with er1:
    enrich_on = st.toggle(t("enr_toggle_short", lang), value=True, key="enrich_on", help=t("enr_toggle", lang))
with er2:
    if enrich_on:  # ce qui est réellement branché est affiché tel quel : une source absente se voit
        cov = enrichment_coverage(S.get("sec_ua") or os.environ.get("ALPHAFORGE_SEC_USER_AGENT"))
        st.caption(f"⚙ **{t('mode_enriched', lang)}**")
        st.caption("ℹ " + t("enr_cov_line", lang, line=" ; ".join(f"{t('enr_tool_' + k, lang)} : {t('enr_cov_' + v, lang)}" for k, v in cov.items())))
    else:
        st.caption(f"⚙ **{t('mode_core', lang)}**")
        st.caption("ℹ " + t("enr_mode_off", lang))
with st.expander("📖 " + t("gl_title", lang)):  # lexique pour débutant, accessible depuis tous les onglets
    st.markdown("\n".join(f"- **{term}** : {text}" for term, text in GLOSSARY.get(lang, GLOSSARY["fr"])))
# Résultat d'un enregistrement ou d'une suppression de profil : visible même quand le panneau est fermé.
if S.get("pf_flash"):
    st.success("✓ " + S.pop("pf_flash"))
if S.get("pf_flash_error"):
    st.error("⛔ " + S.pop("pf_flash_error"))
# Panneau de gestion des profils, sous le bandeau, ouvert par « Ajouter » ou « Supprimer » (contenu rempli plus bas).
profile_box = st.container(border=True) if S.pf_mode else None


# ---- rendu des éléments partagés ----------------------------------------
def evidence_button(eid: str, key: str) -> None:
    """Lien discret vers la preuve : libellé court, identifiant complet dans l'info-bulle (traçabilité intacte)."""
    if st.button(t("ev_btn", lang), key=key, icon=":material/receipt_long:", type="tertiary",
                 help=f"{t('drawer_title', lang)} · {eid}"):
        S.evidence_open = eid


def render_profile_panel(p) -> None:
    v = validate_profile(p)
    st.markdown(f"#### {p.display_name or p.profile_id}")

    def many(items: List[str]) -> str:
        """Champ à plusieurs valeurs : une valeur par ligne (pas de liste coupée au milieu d'une valeur)."""
        return "".join(f"\n    - {x}" for x in items) if items else f" {t('none', lang)}"

    def crit(x, weighted: bool) -> str:
        w = ""
        if weighted:
            num = f"{x.weight:g}"
            w = f" · {t('pf_weight', lang).lower()} {num.replace('.', ',') if lang == 'fr' else num}"
        return f"{human(x.value, lang)} ({t('ck_' + x.kind, lang).lower()}){w}"

    st.markdown(
        f"- **{t('horizon', lang)}** : {t('years_range', lang, a=p.horizon_years_min, b=p.horizon_years_max)}\n"
        f"- **{t('risk', lang)}** : {human(p.risk_tolerance, lang)}\n"
        f"- **{t('experience', lang)}** : {human(p.experience, lang)}\n"
        f"- **{t('objectives', lang)}** :{many([human(o, lang) for o in p.objectives])}\n"
        f"- **{t('preferences', lang)}** :{many([crit(x, True) for x in p.soft_preferences])}\n"
        f"- **{t('exclusions', lang)}** :{many([crit(x, False) for x in p.hard_exclusions])}"
    )
    for i in v.issues:
        (st.error if i.severity == "error" else st.warning)(("⛔ " if i.severity == "error" else "⚠ ") + humanize_text(i.message, lang))
    from alphaforge.governance import GovernanceConfig, derive_constraints

    c = derive_constraints(p, GovernanceConfig())
    st.markdown(f"**{t('derived_limits', lang)}**")
    st.markdown(
        f"- {t('max_vol', lang)} : {pct(c.max_volatility, lang)}\n- {t('max_dd', lang)} : {pct(c.max_drawdown, lang)}\n"
        f"- {t('max_weight', lang)} : {pct(c.max_single_asset_weight, lang)}\n"
        f"- {t('class_caps', lang)} :{many([f'{human(k, lang)} : {pct(x, lang)}' for k, x in c.class_caps.items()])}"
    )
    for adj in c.adjustments:
        st.caption("ℹ " + humanize_text(adj, lang))


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
        st.button(f"{opt.ticker} — {opt.name}", key=f"{key}-clar-{opt.ticker}", on_click=set_request,
                  args=(t("tpl_evaluate", lang, a=opt.ticker),))
    for tk in views.out_of_universe(a):
        st.warning("⚠ " + t("out_of_universe", lang, t=tk))
        st.button(t("enrich_btn", lang), key=f"{key}-enr-{tk}", disabled=True, help=t("oou_enrich_off", lang))
    v = a.verdict
    if not v:
        return
    b = views.status_badge(v["status"], lang)
    st.markdown(f"## {b.icon} {b.label}")
    measures = views.verdict_measures(a, lang)
    st.caption("ℹ " + t("not_suitability", lang))
    if measures:
        st.caption("ℹ " + t("limits_policy", lang))
    for asset in v["assets"] + v.get("alternatives", []):
        ab = views.status_badge(asset["status"], lang)
        alt = f" — {t('alternative_for', lang)} {', '.join(asset['alternative_for'])}" if asset.get("alternative_for") else ""
        with st.container(border=True):
            st.markdown(f"**{asset['ticker']}** · {ab.icon} {ab.label}{alt}")
            for i, m in enumerate(measures.get(asset["ticker"], [])):
                c1, c2, c3 = st.columns([3, 4, 1.2], vertical_alignment="center")
                c1.markdown(f"{m.icon} **{m.label}** : {pct(m.value, lang)} — {t('limit', lang)} {pct(m.limit, lang)}"
                            f" ({t('ok', lang) if m.ok else t('ko', lang)})")
                used = m.value / m.limit if m.limit else None
                bar = t("of_limit", lang, p=pct(used, lang)) if m.ok or used is None else t("over_limit", lang, p=pct(used, lang))
                c2.progress(m.ratio, text=bar + (f" · {views.margin_points(m.value, m.limit, lang)}" if m.limit else ""))
                with c3:
                    evidence_button(m.evidence_id, f"{key}-m-{asset['ticker']}-{i}")
                st.caption(humanize_text(f"{t('worst_window', lang)} : {window_name(m.window, lang)} · {m.basis} · {m.origin}", lang))
            for j, rl in enumerate(views.asset_reasons(asset, lang)):
                st.markdown(f"{rl.icon} *{rl.kind}* — {humanize_text(rl.message, lang)}")
    for g in v.get("global_reasons", []):
        kind = views.asset_reasons({"reasons": [g]}, lang)[0].kind
        st.markdown(f"{'⚠' if g['severity'] == 'warn' else 'ℹ'} *{kind}* — {humanize_text(g['message'], lang)}")
    for sr in v.get("scope_refusals", []):
        st.error(f"⛔ {t('scope_title', lang)} : {sr['message']} → {sr['offered_instead']}")


def render_lines(lines: List[views.ExplanationLine], key: str) -> None:
    for i, l in enumerate(lines):
        refs = [r for r in l.refs if r.startswith("ev_")]
        cols = st.columns([8, 2])
        cols[0].markdown(f"`{l.label}` {humanize_text(l.text, lang)}")
        with cols[1]:
            for j, r in enumerate(refs[:2]):
                evidence_button(r, f"{key}-{i}-{j}")


def render_enrichment(a: Analysis, key: str) -> None:
    """Besoins détectés, données enrichies avec leur niveau de confiance, lacunes non comblées. Le mode dégradé se dit."""
    ev = views.enrichment_view(a, lang)
    st.markdown(f"### 🌐 {t('enr_title', lang)}")
    if ev.mode == "off":
        st.info("ℹ " + t("enr_mode_off", lang))
        return
    if ev.mode == "idle":
        st.caption(t("enr_no_needs", lang))
        return
    banner = t(f"enr_mode_{ev.mode}", lang, n=ev.n_gaps)
    (st.success if ev.mode == "complete" else st.warning)(("✓ " if ev.mode == "complete" else "⚠ ") + banner)
    for g in ev.needs:
        st.markdown(f"- {t('enr_needs', lang)} : {human(g['kind'], lang)} · {g['ticker']}" + ("" if g["fillable"] else f" — {humanize_text(g['reason'], lang)}"))
    for i, r in enumerate(ev.rows):
        with st.container(border=True):
            if r.status == "ok":
                st.markdown(f"**{r.ticker}** · {human(r.kind, lang)} · {t('enr_conf_' + r.confidence, lang)} · {t('enr_published', lang)} {human_date(str(r.date_publication), lang)} · {t('enr_source', lang)} {r.source}")
                if r.kind == "fundamentals" and (r.value or {}).get("metrics"):  # chiffres du rapport annuel, tels qu'au registre
                    labels = EXPL_TEMPLATES[lang]["fund_labels"]
                    st.caption(t("enr_fund_period", lang, end=human_date(str(r.value.get("period_end")), lang), form=r.value.get("form", "")))
                    st.table(pd.DataFrame([{t("enr_fund_metric", lang): labels.get(k, human(k, lang)).replace("{amount}", "").strip().capitalize(), t("enr_fund_amount", lang): format_number(v, "money", lang) if v >= 0 else "−" + format_number(v, "money", lang)}
                                           for k, v in sorted(r.value["metrics"].items(), key=lambda kv: FUND_ORDER.index(kv[0]) if kv[0] in FUND_ORDER else 99)]))
                for f in r.facts[:3]:
                    st.markdown(f"> {f['excerpt']}")
                if r.url:
                    st.caption(r.url)
            else:
                st.markdown(f"**{r.ticker}** · {human(r.kind, lang)} · ⚠ {t('enr_gap', lang)} · {t('enr_reason', lang)} {human(r.reason_code, lang)}")
                st.caption(humanize_text(r.message or "", lang))
    st.caption(t("enr_rule_ab", lang))
    st.caption(t("enr_rule_c", lang))


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
            icon = '⚠' if tab.indicator == 'sensible' else '✓' if tab.indicator == 'stable' else '•'
            explain = (t("sens_explain_" + tab.indicator, lang, spread=pct(tab.relative_spread, lang), thr=pct(tab.threshold, lang))
                       if tab.relative_spread is not None else "")
            st.markdown(f"**{tab.ticker}** — {icon} {t('sens_' + tab.indicator, lang)}" + (f" : {explain}" if explain else ""))
            evidence_button(tab.evidence_id, f"{key}-stress-{tab.ticker}")
        else:
            st.markdown(f"**{tab.ticker}** — " + t("sens_pending", lang))
        df = pd.DataFrame(tab.rows).drop(columns=["evidence_id"], errors="ignore")  # identifiants : boutons « Preuve » sous le tableau
        for col in ("volatility", "max_drawdown", "total_return"):
            df[col] = df[col].map(lambda x: pct(x, lang) if x == x and x is not None else "—")  # « — » : période non calculée
        df["window"] = df["window"].map(lambda w: window_name(w, lang))
        for col in ("start", "end"):
            df[col] = df[col].map(lambda d: human_date(str(d), lang) if d else "—")
        df["status"] = df["status"].map(lambda x: t("row_status_" + str(x), lang) if t("row_status_" + str(x), lang) != "row_status_" + str(x) else human(str(x), lang))
        df = df.rename(columns={c: t("col_" + c, lang) for c in df.columns})  # en-têtes lisibles ; les données ne changent pas
        st.dataframe(df, hide_index=True, width="stretch")
        cols = st.columns(min(len(tab.rows), 5) or 1)
        for i, r in enumerate(tab.rows):
            with cols[i % len(cols)]:
                st.caption(window_name(r["window"], lang))
                evidence_button(r["evidence_id"], f"{key}-sens-{tab.ticker}-{i}")


def render_correlation(a: Analysis, key: str) -> None:
    cv = views.correlation_view(a, lang)
    if cv is None:
        return
    import altair as alt

    st.markdown(f"### {t('corr_title', lang)}")
    st.markdown(f"**{t('corr_reference', lang)}** : {t('corr_ref_' + cv.reference, lang)} · "
                + t("corr_obs", lang, n=cv.n_obs, a=human_date(str(cv.first_date), lang), b=human_date(str(cv.last_date), lang)))
    st.caption("ℹ " + t("corr_explain", lang))
    df = pd.DataFrame(cv.cells)
    base = alt.Chart(df).encode(x=alt.X("a:N", title=None, sort=cv.tickers), y=alt.Y("b:N", title=None, sort=cv.tickers))
    heat = base.mark_rect().encode(color=alt.Color("corr:Q", scale=alt.Scale(domain=[-1, 1], scheme="redblue", reverse=True),
                                                    title="corr"), tooltip=["a", "b", alt.Tooltip("corr:Q", format=".2f")])
    text = base.mark_text().encode(text=alt.Text("corr:Q", format=".2f"))  # valeur écrite dans chaque case : jamais la couleur seule
    st.altair_chart((heat + text).properties(height=60 * len(cv.tickers) + 40), use_container_width=True)
    st.caption(re.sub(r"(-?\d)\.(\d)", r"\1,\2", cv.alt) if lang == "fr" else cv.alt)
    if cv.complement:
        st.caption(t("corr_complement", lang, n=cv.complement["n_obs"]))
    for x in cv.excluded:
        st.caption("⚠ " + humanize_text(t("corr_excluded", lang, t=x["ticker"], r=x["reason"]), lang))
    evidence_button(cv.evidence_id, f"{key}-corr")


def render_portfolio(a: Analysis, key: str) -> None:
    pv = views.portfolio_view(a, lang)
    if pv is None:
        return
    import altair as alt

    st.markdown(f"### 🧪 {t('pf_title', lang)}")
    st.warning(pv.label)  # le scénario est toujours étiqueté, jamais présenté comme une prévision
    st.caption(f"{pv.rebalancing_description} · {pv.annualization_basis} · {pv.first_date} → {pv.last_date} ({pv.n_returns})")
    m = pv.metrics
    c = st.columns(4)
    c[0].metric(t("pf_ann", lang) if m["annualized_return"] is not None else t("pf_total", lang),
                pct(m["annualized_return"] if m["annualized_return"] is not None else m["total_return"], lang))
    c[1].metric(t("pf_vol", lang), pct(m["annualized_volatility"], lang))
    c[2].metric(t("pf_dd", lang), pct(m["max_drawdown"]["depth"], lang))
    c[3].metric(t("pf_dr", lang), "—" if pv.diversification_ratio is None else f"{pv.diversification_ratio:.2f}")
    st.markdown(f"**{t('pf_allocation', lang)}**")
    cols = st.columns(3)
    for col, (k, label) in zip(cols, (("asset_class", "pf_by_class"), ("sector", "pf_by_sector"), ("currency", "pf_by_currency"))):
        with col:
            st.caption(t(label, lang))
            for row in pv.breakdown[k]:
                st.progress(min(row["weight"], 1.0), text=f"{row['name']} · {pct(row['weight'], lang)}")  # valeur écrite : jamais la couleur seule
    cc = pv.concentration
    st.markdown(f"**{t('pf_concentration', lang)}** — {t('pf_max_weight', lang)} : {pct(cc['max_weight'], lang)} ({cc['max_weight_ticker']}) · "
                f"{t('pf_hhi', lang)} : {cc['hhi']:.2f} · {t('pf_neff', lang)} : {cc['effective_positions']:.1f}")
    if cc.get("final_weights"):
        st.caption(t("pf_final_weights", lang) + " : " + ", ".join(f"{k} {pct(x, lang)}" for k, x in cc["final_weights"].items()))
    if pv.contributions:
        st.markdown(f"**{t('pf_contrib', lang)}**")
        df = pd.DataFrame([{"": x["ticker"], t("pf_weight", lang): pct(x["weight"], lang), t("pf_standalone", lang): pct(x["standalone_volatility"], lang),
                            t("pf_share", lang): pct(x["risk_share"], lang)} for x in pv.contributions])
        st.dataframe(df, hide_index=True, width="stretch")
    else:
        st.caption(t("pf_zero_vol", lang))
    if pv.series_id in F.store.series:
        s = F.store.series.get(pv.series_id).series.prices
        st.markdown(f"**{t('pf_curve', lang)}**")
        d = pd.DataFrame({"date": s.index, "value": s.values})
        line = alt.Chart(d).mark_line().encode(x=alt.X("date:T", title=None), y=alt.Y("value:Q", title=None, scale=alt.Scale(zero=False)))
        band = alt.Chart(pd.DataFrame({"a": [pd.Timestamp("2022-01-01")], "b": [pd.Timestamp("2022-12-31")]})).mark_rect(opacity=0.15, color="orange").encode(x="a:T", x2="b:T")
        st.altair_chart(band + line, use_container_width=True)
        st.caption(t("pf_curve_alt", lang, a=pv.first_date, b=pv.last_date, end=f"{s.iloc[-1]:.0f}"))
    with st.expander(t("pf_assumptions", lang)):
        for x in pv.assumptions:
            st.markdown(f"- {x}")
    evidence_button(pv.evidence_id, f"{key}-pf")


def render_conclusion(a: Analysis, key: str) -> None:
    """Conclusion en langage simple, à la fin de l'analyse, quel que soit le niveau de lecture."""
    lines = views.conclusion_lines(a, lang)
    if not lines:
        return
    with st.container(border=True):
        st.markdown(f"### 🧭 {t('conclusion_title', lang)}")
        st.caption(t("conclusion_note", lang))
        for l in lines:
            st.markdown(f"- {l.text}")


def render_limits(a: Analysis, key: str) -> None:
    st.markdown(f"### {t('limits_title', lang)}")  # toujours présente, jamais repliée
    for sec, lines in views.limit_sections(a, lang).items():
        if not lines:
            continue
        if sec == "assumptions":  # formules de calcul : utiles pour vérifier, repliées pour ne pas noyer le lecteur
            with st.expander("🧮 " + t("assumptions_fold", lang, n=len(lines))):
                render_lines(lines, f"{key}-lim-{sec}")
            continue
        st.markdown(f"**{t('section_' + sec, lang)}**")
        render_lines(lines, f"{key}-lim-{sec}")


def render_trace(a: Analysis, key: str) -> None:
    st.markdown(f"### {t('trace_title', lang)}")
    decisions = views.agent_decisions(a, lang)
    if decisions:  # les choix des agents d'abord, puis la chronologie complète
        with st.container(border=True):
            st.markdown(f"**🧠 {t('decisions_title', lang)}**")
            for d in decisions:
                st.markdown(f"- {humanize_text(d, lang)}")
    for i, s in enumerate(views.agent_trace(a, lang)):
        label = f"{s.icon} {s.agent_label} · {humanize_text(s.text, lang)}" + ("  🔁" if s.detail.get("feedback") else "")
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
    st.markdown(f"**{t('tool', lang)}** : {human(card.tool, lang)} (`{card.tool}` v{card.version}) · {t('row_status_' + card.status, lang) if card.status in ('ok', 'warning', 'refused') else card.status}  \n**{t('n_obs', lang)}** : {card.n_obs}")
    if card.window:
        st.markdown(f"**{t('col_window', lang)}** : {human_date(str(card.window.get('start')), lang)} → {human_date(str(card.window.get('end')), lang)}"
                    + (f" ({window_name(card.window['alias'], lang)})" if card.window.get('alias') else ""))
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


def run_analysis(text: str, **kw: Any) -> None:
    """Lance l'analyse et affiche chaque entrée du registre au moment où elle est écrite (durées mesurées ici, jamais stockées)."""
    S.evidence_open = None
    with st.status(t("live_running", lang), expanded=True) as status:
        last = [time.perf_counter()]

        def on_entry(entry: Any) -> None:
            step = views.entry_to_step(entry, lang)
            if step is None:
                return
            now = time.perf_counter()
            status.write(f"{step.icon} **{step.agent_label}** · {humanize_text(step.text, lang)} _(+{now - last[0]:.2f} s)_")
            last[0] = now

        S.analysis = F.analyze(text, pid, as_of=as_of, base_currency=base, language=lang, on_entry=on_entry,
                                enrichment=enrich_on, sec_user_agent=S.get("sec_ua") or None, **kw)
        status.update(label=t("live_done", lang, n=len(S.analysis.result.ledger)), state="complete", expanded=False)


# ---- onglets ------------------------------------------------------------
tab_an, tab_cmp, tab_src = st.tabs([t("tab_analysis", lang), t("tab_compare", lang), t("tab_sources", lang)])

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
            col.button(t(name, lang), key=f"sc-{name}", width="stretch", on_click=set_request, args=(t(tpl, lang, a=a_tk, b=b_tk),))
        st.caption("ℹ " + t("shortcuts_hint", lang))
        req = st.text_area(t("request", lang), key="req_box", height=70)
        if st.button(t("send", lang), type="primary"):
            run_analysis(req)

        with st.expander("🧮 " + t("sc_builder", lang)):
            chosen = st.multiselect(t("sc_assets", lang), tickers, default=[], key="sc_assets")
            eq = forms.equal_weights(chosen)
            if S.get("sc_last") != chosen:  # la liste d'actifs a changé : les poids repartent de poids égaux (jamais de reliquat)
                for tk in chosen:
                    S[f"w_{tk}"] = eq[tk]
                S.sc_last = list(chosen)
            b1, b2 = st.columns(2)
            b1.button(t("sc_equal", lang), key="sc_eq_btn", on_click=lambda: S.update({f"w_{k}": v for k, v in forms.equal_weights(S.sc_assets).items()}))
            b2.button(t("sc_normalize", lang), key="sc_norm_btn",
                      on_click=lambda: S.update({f"w_{k}": v for k, v in forms.normalize({k: S.get(f"w_{k}", 0.0) for k in S.sc_assets}).items()}))
            wpct: Dict[str, float] = {}
            for tk in chosen:
                wpct[tk] = st.number_input(t("sc_weight", lang, t=tk), min_value=0.0, max_value=100.0, step=0.1, format="%.1f", key=f"w_{tk}")
            total = forms.weights_sum(wpct)
            valid = len(chosen) >= 2 and forms.weights_ok(wpct)
            if chosen:
                st.markdown(f"**{t('sc_sum', lang, s=f'{total:.1f}')}** — " + (t("sc_sum_ok", lang) if forms.weights_ok(wpct)
                                                                          else t("sc_sum_bad", lang, d=f"{abs(total - 100):.1f}")))
            reb = st.radio(t("sc_rebalancing", lang), ["rebalanced", "buy_and_hold"], key="sc_reb",
                           format_func=lambda x: t("sc_rebalanced" if x == "rebalanced" else "sc_buy_hold", lang))
            st.caption("ℹ " + t("sc_hint", lang))
            if st.button(t("sc_run", lang), type="primary", key="sc_run_btn", disabled=not valid):
                label = ", ".join(f"{k} {v:.1f} %" for k, v in wpct.items())
                text = t("sc_request", lang, w=label, r=t("sc_rebalanced" if reb == "rebalanced" else "sc_buy_hold", lang))
                S.request = text
                run_analysis(text, weights=forms.to_fractions(wpct), rebalancing=reb)
        a = S.analysis
        if a is None:
            st.info(t("no_analysis", lang))
        else:
            st.download_button(t("export_report", lang), data=views.report_markdown(a, lang, F.store),
                               file_name=f"alphaforge_{a.profile.profile_id}_{a.as_of}.md", mime="text/markdown", key="export_md")
            used = a.result.request.parser if a.result.request else "rules"
            st.caption("🧠 " + t("parser_" + {"llm": "llm", "rules(fallback)": "fallback"}.get(used, "rules"), lang,
                                  model=getattr(getattr(F.parser, "_client", None), "model_id", "")))
            for line in a.archive_notes:
                st.caption("☁ " + line)
            render_verdict(a, "an")
            render_enrichment(a, "an")
            render_explanation(a, "an")
            render_sensitivity(a, "an")
            render_correlation(a, "an")
            render_portfolio(a, "an")
            render_limits(a, "an")
            render_conclusion(a, "an")
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
    S.setdefault("cmp_req", S.request or t("tpl_evaluate", lang, a="NVDA"))
    creq = c3.text_input(t("request", lang), key="cmp_req")
    if st.button(t("compare_run", lang), type="primary", key="cmp_go"):
        S.comparison = F.compare(creq, lp, rp, as_of=as_of, base_currency=base, language=lang,  # même interrupteur que l'analyse
                                 enrichment=enrich_on, sec_user_agent=S.get("sec_ua") or None)
    cmp = S.comparison
    if cmp is not None:
        st.success("ℹ " + t("same_data", lang, m=cmp.left.result.ledger.evidence()[0]["data_fingerprint"][:12] if cmp.left.result.ledger.evidence() else "—"))
        diffs = views.comparison_diffs(cmp, lang)
        if diffs:
            st.markdown(f"**{t('differences', lang)}**")
            for d in diffs:
                st.markdown(f"- {humanize_text(d.text, lang)}")
        L, R = st.columns(2)
        for col, an, k in ((L, cmp.left, "cl"), (R, cmp.right, "cr")):
            with col:
                st.markdown(f"#### {an.profile.display_name or an.profile.profile_id}")
                render_verdict(an, k)
                render_explanation(an, k)
                render_conclusion(an, k)
        conc = views.comparison_conclusion(cmp, lang)
        if conc:
            with st.container(border=True):
                st.markdown(f"### 🧭 {t('conclusion_title', lang)}")
                for line in conc:
                    st.markdown(f"- {line}")

def _fill_form(state: Dict[str, Any]) -> None:
    """Rappel : remplit tous les champs du formulaire de profil (avant le rendu, donc avant la création des champs)."""
    S.pf_name, S.pf_id, S.pf_lang = state["display_name"], state["profile_id"], state["language"]
    S.pf_exp, S.pf_risk, S.pf_cur = state["experience"], state["risk_tolerance"], state["base_currency"]
    S.pf_hz = (state["horizon_years_min"], state["horizon_years_max"])
    S.pf_obj = list(state["objectives"])
    S.pf_prefs, S.pf_excl, S.pf_error, S.pf_overwrite = list(state["soft_preferences"]), list(state["hard_exclusions"]), None, False


def _add_row(prefix: str) -> None:
    kind = S[f"pf_kind_{prefix}"]
    key = f"pf_val_{prefix}_{kind}"
    rows_key = "pf_prefs" if prefix == "p" else "pf_excl"
    try:
        S[rows_key] = forms.add_criterion(S[rows_key], kind, S.get(key), S.get("pf_w_p") if prefix == "p" else None)
        S.pf_error = None
        if kind == "theme":
            S[key] = ""
    except ValueError as e:
        S.pf_error = str(e)


def _remove_row(rows_key: str, index: int) -> None:
    S[rows_key] = forms.remove_criterion(S[rows_key], index)
    S.pf_error = None  # l'ancien message (doublon, valeur vide) ne concerne plus la liste


def _delete_profile(profile_id: str) -> None:
    """Rappel du bouton de suppression : supprime, puis remet la confirmation à zéro. Le profil actif retombe sur un exemple si besoin."""
    try:
        F.delete_profile(profile_id)
        S.pf_flash = t("np_deleted", lang, id=profile_id)
    except ProfileError as e:
        S.pf_flash_error = e.message
    S.pf_del_ok = False


if "pf_name" not in S:
    _fill_form(dict(forms.BLANK_PROFILE, language=lang))  # départ : formulaire vierge, pas une copie du profil actif

def render_add_panel(editing: bool = False) -> None:
    edit_id = S.get("pf_edit_id") if editing else None
    if editing:
        name = next((p.display_name for p in created if p.profile_id == edit_id), edit_id)
        st.markdown(f"### {t('np_edit_title', lang, name=name)}")
        st.caption(t("np_edit_intro", lang))
        S.pf_id = edit_id  # identifiant figé : modifier remplace ce profil, rien d'autre
    else:
        st.markdown(f"### {t('profile_editor', lang)}")
        st.caption(t("np_intro", lang))
        b1, b2 = st.columns(2)
        b1.button(t("np_blank", lang), key="pf_blank_btn", on_click=_fill_form, args=(dict(forms.BLANK_PROFILE, language=lang),))
        b2.button(t("np_copy", lang), key="pf_copy_btn", on_click=_fill_form,
                  args=(forms.form_state_from_profile(profile.model_dump(mode="json")),))
    st.warning("⚠ " + t("np_fictive_only", lang))  # au moment de la saisie : le profil part avec l'analyse dans l'archive
    c1, c2 = st.columns(2)
    c1.text_input(t("np_name", lang), key="pf_name")
    c2.text_input(t("np_id", lang), key="pf_id", disabled=editing, help=t("np_edit_id_locked" if editing else "np_id_help", lang))
    c3, c4, c5 = st.columns(3)
    c3.selectbox(t("language", lang), ["fr", "en"], key="pf_lang")
    c4.selectbox(t("experience", lang), ["debutant", "intermediaire", "avance"], key="pf_exp", format_func=lambda x: human(x, lang))
    c5.selectbox(t("base_currency", lang), ["CAD", "USD"], key="pf_cur")
    st.select_slider(t("risk", lang), forms.RISK_LEVELS, key="pf_risk", format_func=lambda x: human(x, lang))
    st.slider(t("horizon_unit", lang), 0, 40, key="pf_hz")
    st.multiselect(t("objectives", lang), list(forms.OBJECTIVES), key="pf_obj", format_func=lambda x: human(x, lang))

    opts = forms.criterion_options(F.assets, list(GovernanceConfig().theme_proxies))

    def criteria_editor(prefix: str, rows_key: str, title: str, weighted: bool) -> None:
        st.markdown(f"**{title}**")
        for i, row in enumerate(S[rows_key]):
            rc1, rc2 = st.columns([8, 1])
            extra = f" · {t('pf_weight', lang)} {row['weight']:g}" if weighted else ""
            rc1.markdown(f"- {t('ck_' + row['kind'], lang)} : {human(row['value'], lang)}{extra}")
            rc2.button("✕", key=f"pf_rm_{prefix}_{i}", on_click=_remove_row, args=(rows_key, i), help=t("np_remove", lang))
        if not S[rows_key]:
            st.caption(t("none", lang))
        k1, k2, k3, k4 = st.columns([2, 3, 2, 1.4])
        kind = k1.selectbox(t("np_kind", lang), list(forms.KINDS), key=f"pf_kind_{prefix}", format_func=lambda x: t("ck_" + x, lang))
        vkey = f"pf_val_{prefix}_{kind}"
        if kind == "theme":
            k2.text_input(t("np_value", lang), key=vkey, help=t("np_theme_hint", lang, themes=", ".join(opts["theme"])))
        else:
            k2.selectbox(t("np_value", lang), opts[kind], key=vkey, format_func=lambda x: human(x, lang))
        if weighted:
            k3.slider(t("pf_weight", lang), 0.0, 1.0, 0.5, 0.05, key="pf_w_p")
        k4.button(t("np_add", lang), key=f"pf_add_{prefix}", on_click=_add_row, args=(prefix,))

    criteria_editor("p", "pf_prefs", t("preferences", lang), True)
    criteria_editor("x", "pf_excl", t("exclusions", lang), False)
    if S.get("pf_error"):
        st.warning("⚠ " + S.pf_error)

    data = forms.build_profile_dict(
        profile_id=S.pf_id, display_name=S.pf_name, language=S.pf_lang, experience=S.pf_exp, risk_tolerance=S.pf_risk,
        base_currency=S.pf_cur, horizon_years_min=S.pf_hz[0], horizon_years_max=S.pf_hz[1], objectives=S.pf_obj,
        preferences=S.pf_prefs, exclusions=S.pf_excl)
    st.divider()
    new_id = data["profile_id"]
    can_save, problem = True, None
    try:
        p_new = load_profile(data)
    except ProfileError as e:
        p_new, can_save, problem = None, False, e.message
    if p_new is not None:
        if any(i.severity == "error" for i in validate_profile(p_new).issues):
            can_save = False  # l'incohérence est affichée dans l'aperçu ci-dessous
        if not new_id or not forms.slugify(new_id) == new_id:
            can_save, problem = False, t("np_id_invalid", lang)
        origin = F.profile_origin(new_id) if new_id else None
        if origin == "exemple":
            can_save, problem = False, t("np_exists_example", lang, id=new_id)
        elif origin == "créé" and editing and new_id == edit_id:
            pass  # modification demandée explicitement avec « Modifier » : pas de seconde confirmation
        elif origin == "créé":
            S.setdefault("pf_overwrite", False)
            st.checkbox(t("np_overwrite", lang, id=new_id), key="pf_overwrite")
            if not S.pf_overwrite:
                can_save, problem = False, t("np_exists", lang, id=new_id)
    if not new_id and p_new is not None:
        problem = t("np_id_none", lang)
    elif new_id:
        st.caption(t("np_id_used", lang, id=new_id))
    if problem:
        st.warning("⚠ " + problem)
    if st.button(t("save", lang), type="primary", key="pf_save_btn", disabled=not can_save):
        saved = F.save_profile(data, overwrite=editing or bool(S.get("pf_overwrite")))
        S.pending_profile = saved.profile_id  # il devient le profil actif
        S.pf_mode = None  # le panneau se ferme : le profil est créé (ou modifié) et actif
        S.pf_flash = t("edited" if editing else "saved", lang, id=saved.profile_id)
        st.rerun()
    if p_new is not None:
        st.markdown(f"#### {t('np_preview', lang)}")  # aperçu en direct : fiche, incohérences et limites dérivées
        render_profile_panel(p_new)


def render_delete_panel() -> None:
    st.markdown(f"### {t('np_delete_title', lang)}")
    st.caption(t("np_delete_note", lang))  # les profils d'exemple ne figurent jamais dans cette liste
    victim = st.selectbox(t("np_delete_pick", lang), [p.profile_id for p in created], key="pf_del_pick",
                          format_func=lambda i: next(p.display_name for p in created if p.profile_id == i))
    st.checkbox(t("np_delete_confirm", lang, id=victim), key="pf_del_ok")
    st.button(t("np_delete_btn", lang), key="pf_del_btn", type="primary", disabled=not S.get("pf_del_ok"),
              on_click=_delete_profile, args=(victim,))


if profile_box is not None:
    with profile_box:
        head, close = st.columns([8, 1.2], vertical_alignment="center")
        head.markdown(f"**👤 {t('np_manage', lang)}**")
        close.button(t("np_close", lang), key="pf_close_btn", on_click=_close_panel, width="stretch")
        if S.pf_mode == "delete":
            render_delete_panel()
        else:
            render_add_panel(editing=S.pf_mode == "edit")

with tab_src:
    a = S.analysis
    st.markdown(f"### {t('sources_title', lang)}")
    if a is not None:
        for s in sorted({s for e in a.result.ledger.evidence() for s in e.get("sources", [])}):
            st.markdown(f"- {humanize_text(s, lang)}")
    from alphaforge.quant.config import DEFAULT_DATA_DIR

    meta = pd.read_csv(DEFAULT_DATA_DIR / "sources_metadata.csv")
    st.dataframe(meta.rename(columns={c: t("src_" + c, lang) for c in meta.columns}), hide_index=True, width="stretch")
    st.markdown(f"### {t('gaps_title', lang)}")
    gaps = [r for asset in ((a.verdict or {}).get("assets", []) if a else []) for r in asset["reasons"] if r["check"] in ("data_gaps", "soft_preferences")]
    for r in gaps:
        st.markdown(f"- {'⚠' if r['severity'] == 'warn' else 'ℹ'} {humanize_text(r['message'], lang)}")
    if a is not None and not gaps:
        st.caption(t("gaps_none", lang))
    st.caption(f"{t('fingerprint', lang)} : {F.store.fingerprint[:12]} — {t('fingerprint_help', lang)}")
    st.markdown(f"### 🌐 {t('enr_cov_title', lang)}")
    st.text_input(t("enr_sec_label", lang), key="sec_ua", help=t("enr_sec_help", lang), placeholder="Prénom Nom, prenom.nom@exemple.org")
    cov = enrichment_coverage(S.get("sec_ua") or os.environ.get("ALPHAFORGE_SEC_USER_AGENT"))
    st.table(pd.DataFrame([{t("enr_source", lang): t("enr_tool_" + k, lang), "": t("enr_cov_" + v, lang)} for k, v in cov.items()]))
    st.markdown(f"### {t('enr_cards_title', lang)}")
    cards = views.enrichment_view(a, lang).cards if a is not None else []
    if not cards:
        st.caption(t("enr_cards_none", lang))
    for c in cards:
        with st.expander(f"{c['name']} · {t('enr_card_retrieved', lang)} {c['retrieved_on']}"):
            st.markdown(f"**{t('enr_card_class', lang)}** : {c['source_class']}  \n**{t('enr_card_terms', lang)}** : {c['reuse_terms']}")
            for lim in c["limits"]:
                st.markdown(f"- {lim}")
            st.caption(c["origin_url"])
