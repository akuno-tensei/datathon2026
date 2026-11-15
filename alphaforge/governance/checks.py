"""Contrôles de gouvernance. Chaque fonction est pure : profil + faits issus du registre -> raisons chiffrées.

Seuls les contrôles de contraintes dures peuvent bloquer. Un dépassement de limite dérivée ou une donnée
manquante produit un avertissement, jamais un blocage, sauf classe d'actifs non permise (plafond à 0).
"""
import re
from typing import Dict, List

from ..enrichment.config import CONTROVERSY_THEMES, ESG_THEMES
from ..orchestrator.profile import RISK_RANK, Criterion, InvestorProfile
from .config import RISK_LEVELS, GovernanceConfig
from .facts import AssetFacts
from .models import Constraints, Reason, ScopeRefusal

# Codes de raison qui justifient de chercher une alternative.
ALTERNATIVE_TRIGGER_CODES = {
    "EXCLUDED_ASSET_CLASS", "EXCLUDED_SECTOR", "EXCLUDED_TICKER", "CLASS_NOT_PERMITTED", "EXCLUDED_THEME_CONTROVERSY",
    "VOLATILITY_ABOVE_LIMIT", "DRAWDOWN_ABOVE_LIMIT",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().casefold())


def _root(ticker: str) -> str:
    return re.split(r"[.\-]", ticker)[0].casefold()


def _dec2(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


def _pct(x: float) -> str:
    return f"{x * 100:.1f} %".replace(".", ",")


# ---- derive_constraints --------------------------------------------------


def derive_constraints(profile: InvestorProfile, cfg: GovernanceConfig) -> Constraints:
    """Traduit le profil en limites numériques. Même fonction pour tous les profils, aucune branche par persona."""
    rank = RISK_RANK[profile.risk_tolerance]
    adjustments: List[str] = []
    if profile.horizon_years_max < cfg.short_horizon_years and rank > 0:
        rank -= 1
        adjustments.append(
            f"Horizon d'au plus {profile.horizon_years_max} an(s), inférieur à {cfg.short_horizon_years} : "
            f"limites resserrées d'un niveau (« {profile.risk_tolerance} » -> « {RISK_LEVELS[rank]} »)."
        )
    level = RISK_LEVELS[rank]
    lim = cfg.risk_limits[level]
    return Constraints(
        profile_id=profile.profile_id,
        risk_level_applied=level,
        max_volatility=lim.max_volatility,
        max_drawdown=lim.max_drawdown,
        max_single_asset_weight=lim.max_single_asset_weight,
        class_caps=dict(cfg.class_caps.get(level, {})),
        adjustments=adjustments,
    )


# ---- check_hard_constraints ----------------------------------------------


def _matches(c: Criterion, f: AssetFacts) -> bool:
    a = f.asset or {}
    v = _norm(c.value)
    if c.kind == "asset_class":
        return _norm(str(a.get("asset_class", ""))) == v
    if c.kind == "sector":
        return _norm(str(a.get("sector", ""))) == v
    if c.kind == "ticker":
        return v in (_norm(f.ticker), _root(f.ticker))
    return False


def check_hard_constraints(profile: InvestorProfile, constraints: Constraints, f: AssetFacts) -> List[Reason]:
    if f.asset is None:
        return []
    out: List[Reason] = []
    ev = f.evidence_ids
    codes = {"asset_class": "EXCLUDED_ASSET_CLASS", "sector": "EXCLUDED_SECTOR", "ticker": "EXCLUDED_TICKER"}
    for c in profile.hard_exclusions:
        if c.kind == "theme":
            out.append(
                Reason(
                    check="hard_constraints", code="THEME_EXCLUSION_UNVERIFIED", severity="warn",
                    message=f"Exclusion thématique « {c.value} » : le jeu de données ne permet pas de la vérifier pour {f.ticker}.",
                    measures={"theme": c.value}, evidence_ids=ev,
                )
            )
        elif _matches(c, f):
            out.append(
                Reason(
                    check="hard_constraints", code=codes[c.kind], severity="block",
                    message=f"{f.ticker} est exclu par le profil ({c.kind} : {c.value})." + (f" Motif : {c.reason}" if c.reason else ""),
                    measures={"kind": c.kind, "excluded_value": c.value}, evidence_ids=ev,
                )
            )
    cls = f.asset.get("asset_class")
    if cls is not None and constraints.class_caps.get(cls) == 0.0:
        out.append(
            Reason(
                check="hard_constraints", code="CLASS_NOT_PERMITTED", severity="block",
                message=f"La classe « {cls} » n'est pas permise au niveau de risque « {constraints.risk_level_applied} » appliqué à ce profil.",
                measures={"asset_class": cls, "class_cap": 0.0, "risk_level_applied": constraints.risk_level_applied},
                evidence_ids=ev,
            )
        )
    return out


# ---- check_soft_preferences ----------------------------------------------


def check_soft_preferences(profile: InvestorProfile, cfg: GovernanceConfig, f: AssetFacts) -> List[Reason]:
    if f.asset is None:
        return []
    out: List[Reason] = []
    ev = f.evidence_ids
    for p in profile.soft_preferences:
        if p.kind == "theme":
            theme = _norm(p.value)
            proxies = [_norm(x) for x in cfg.theme_proxies.get(theme, [])]
            if _norm(f.ticker) in proxies:
                out.append(
                    Reason(
                        check="soft_preferences", code="THEME_PROXY", severity="info",
                        message=(
                            f"Aucune donnée « {p.value} » dans le jeu de départ. {f.ticker} sert de substitut déclaré "
                            "(fonds filtré par son émetteur) : ce n'est pas une cote."
                        ),
                        measures={"theme": p.value, "weight": p.weight}, evidence_ids=ev,
                    )
                )
            else:
                out.append(
                    Reason(
                        check="soft_preferences", code="THEME_DATA_MISSING", severity="warn",
                        message=f"Préférence « {p.value} » : aucune donnée disponible pour {f.ticker}, l'alignement n'est pas vérifié.",
                        measures={"theme": p.value, "weight": p.weight, "substitutes": cfg.theme_proxies.get(theme, [])},
                        evidence_ids=ev,
                    )
                )
        elif _matches(p, f):
            out.append(
                Reason(
                    check="soft_preferences", code="PREFERENCE_MATCH", severity="info",
                    message=f"{f.ticker} correspond à une préférence du profil ({p.kind} : {p.value}).",
                    measures={"kind": p.kind, "value": p.value, "weight": p.weight}, evidence_ids=ev,
                )
            )
    return out


# ---- check_risk_limits ---------------------------------------------------


def check_risk_limits(constraints: Constraints, f: AssetFacts) -> List[Reason]:
    """Compare volatilité et drawdown mesurés (dans la pire fenêtre) aux limites dérivées."""
    out: List[Reason] = []
    if not f.windows:
        return out
    specs = [
        ("VOLATILITY_ABOVE_LIMIT", "annualized_volatility", "Volatilité annualisée", constraints.max_volatility, lambda w: w.volatility),
        ("DRAWDOWN_ABOVE_LIMIT", "max_drawdown", "Perte maximale depuis un sommet", constraints.max_drawdown, lambda w: -w.drawdown_depth),
    ]
    for code, metric, label, limit, get in specs:
        per_window = {(w.alias or f"{w.start}/{w.end}"): get(w) for w in f.windows}
        worst = max(f.windows, key=get)
        value = get(worst)
        if value > limit:
            wname = worst.alias or f"{worst.start} au {worst.end}"
            out.append(
                Reason(
                    check="risk_limits", code=code, severity="warn",
                    message=(
                        f"{label} de {f.ticker} : {_pct(value)} (fenêtre « {wname} »), pour une limite de {_pct(limit)} "
                        f"au niveau « {constraints.risk_level_applied} »."
                    ),
                    measures={
                        "metric": metric, "value": value, "limit": limit, "ratio": value / limit, "window": worst.alias,
                        "per_window": per_window, "annualization_basis": worst.annualization_basis,
                    },
                    evidence_ids=[w.evidence_id for w in f.windows],
                )
            )
    return out


# ---- check_horizon_fit ---------------------------------------------------


def check_horizon_fit(profile: InvestorProfile, f: AssetFacts) -> List[Reason]:
    """Compare le temps de récupération du pire drawdown mesuré (fenêtre la plus longue) à l'horizon minimal du profil."""
    if not f.windows:
        return []
    w = max(f.windows, key=lambda x: (x.end - x.start).days)
    if w.peak_date is None:
        return []  # aucun drawdown dans la fenêtre
    end = w.recovery_date or w.end
    years = (end - w.peak_date).days / 365.25
    if years <= profile.horizon_years_min:
        return []
    state = "récupéré" if w.recovered else "non récupéré à la fin de la fenêtre (durée minimale)"
    return [
        Reason(
            check="horizon_fit", code="RECOVERY_LONGER_THAN_HORIZON", severity="warn",
            message=(
                f"Le pire repli de {f.ticker} a duré {years:.1f}".replace(".", ",") + f" an(s) du sommet ({w.peak_date}) à {end} ({state}), "
                f"soit plus que l'horizon minimal du profil ({profile.horizon_years_min} an(s))."
            ),
            measures={
                "recovery_years": years, "horizon_years_min": profile.horizon_years_min, "peak_date": str(w.peak_date),
                "end_date": str(end), "recovered": w.recovered, "max_drawdown": w.drawdown_depth, "window": w.alias,
            },
            evidence_ids=[w.evidence_id],
        )
    ]


# ---- check_data_gaps -----------------------------------------------------


def check_data_gaps(f: AssetFacts) -> List[Reason]:
    out: List[Reason] = []
    ids = [r.evidence_id for r in f.refusals]
    if f.asset is None and any(r.reason == "asset_not_found" for r in f.refusals):
        sugg = f" Suggestions : {', '.join(f.suggestions)}." if f.suggestions else ""
        out.append(
            Reason(
                check="data_gaps", code="OUT_OF_UNIVERSE", severity="warn",
                message=f"{f.ticker} est hors de l'univers de données : aucune donnée disponible, aucune évaluation possible.{sugg}",
                measures={"suggestions": f.suggestions}, evidence_ids=ids,
            )
        )
        return out
    if f.asset is None and any(r.reason == "ambiguous_query" for r in f.refusals):
        out.append(
            Reason(
                check="data_gaps", code="AMBIGUOUS_ASSET", severity="warn",
                message=f"« {f.ticker} » désigne plusieurs actifs ({', '.join(f.candidates)}) : aucune évaluation tant que l'actif n'est pas précisé.",
                measures={"candidates": f.candidates}, evidence_ids=ids,
            )
        )
        return out
    if f.asset is None and any(r.tool == "asset_lookup" for r in f.refusals):
        r0 = next(r for r in f.refusals if r.tool == "asset_lookup")
        out.append(
            Reason(
                check="data_gaps", code="ASSET_NOT_IDENTIFIED", severity="warn",
                message=f"{f.ticker} n'a pas pu être identifié : {r0.message}",
                measures={"reason": r0.reason}, evidence_ids=ids,
            )
        )
        return out
    for r in f.refusals:
        if r.tool == "compute_metrics":
            out.append(
                Reason(
                    check="data_gaps", code="METRICS_UNAVAILABLE", severity="warn",
                    message=f"Les métriques de {f.ticker} n'ont pas pu être calculées : {r.message}",
                    measures={"reason": r.reason}, evidence_ids=[r.evidence_id],
                )
            )
    if f.asset is not None and not f.windows and not any(r.tool == "compute_metrics" for r in f.refusals):
        out.append(
            Reason(
                check="data_gaps", code="NO_METRICS", severity="warn",
                message=f"Aucune métrique n'a été produite pour {f.ticker} : les limites de risque n'ont pas pu être vérifiées.",
                evidence_ids=f.evidence_ids,
            )
        )
    return out


# ---- check_diversification -----------------------------------------------


def check_diversification(evidence: List[dict], tickers: List[str], cfg: GovernanceConfig) -> List[Reason]:
    """Paires d'actifs demandés dont les rendements ont été très corrélés (preuve correlation_matrix de l'analyste).

    Informatif : une corrélation passée élevée signale une diversification faible, sans bloquer ni avertir.
    """
    out: List[Reason] = []
    wanted = set(tickers)
    for ev in evidence:
        if ev.get("tool") != "correlation_matrix" or ev.get("status") == "refused":
            continue
        v = ev["value"]
        ref = v["matrices"][v["reference"]]
        for pair in ref.get("pairs", []):
            if pair["a"] in wanted and pair["b"] in wanted and pair["corr"] >= cfg.high_correlation:
                out.append(
                    Reason(
                        check="diversification", code="HIGH_CORRELATION", severity="info",
                        message=(f"{pair['a']} et {pair['b']} : corrélation des rendements de {_dec2(pair['corr'])} "
                                 f"(au-delà de {_dec2(cfg.high_correlation)}) ; les associer diversifie peu."),
                        measures={"a": pair["a"], "b": pair["b"], "corr": pair["corr"], "threshold": cfg.high_correlation,
                                  "reference": v["reference"], "n_obs": ref["n_obs"]},
                        evidence_ids=[ev["evidence_id"]],
                    )
                )
    return out


# ---- apply_enrichment ----------------------------------------------------


def _theme_set(criteria, vocabulary) -> set:
    return {c.value.strip().lower() for c in criteria if c.kind == "theme"} & set(vocabulary)


def _source_name(cards: Dict[str, dict], rec: dict) -> str:
    card = cards.get(rec.get("source_card_id") or "")
    return card["name"] if card else "source non documentée"


def apply_enrichment(reasons: List[Reason], profile: InvestorProfile, ticker: str, records: List[dict], cards: Dict[str, dict]) -> List[Reason]:
    """Intègre les données de l'agent d'enrichissement au verdict d'un actif (Agents.md 2.4).

    - Une exclusion thématique (controverses) ne **bloque** que sur une donnée de niveau A ou B qui documente des controverses.
      Un signal de niveau C (texte, nouvelle) ne produit qu'un avertissement, jamais un blocage.
    - Une donnée A ou B qui ne documente aucune controverse lève l'avertissement « non vérifiable » correspondant.
    - Une cote ESG disponible mais sans seuil défini n'est pas jugée : on le dit au lieu de conclure.
    - Chaque lacune non comblée est un avertissement explicite ; chaque donnée enrichie est rappelée avec son niveau et sa date.
    Aucun seuil n'est inventé : sans donnée enrichie, les raisons existantes restent inchangées.
    """
    mine = [r for r in records if r.get("ticker") == ticker]
    if not mine:
        return reasons
    ok = [r for r in mine if r["status"] == "ok"]
    gaps = [r for r in mine if r["status"] == "lacune_non_comblee"]
    ab = lambda r: r["confidence"] in ("A", "B")  # noqa: E731
    ctrl = [r for r in ok if r["need"] == "controversies"]
    documented = [r for r in ctrl if ab(r) and (r.get("value") or {}).get("controversies")]
    clean = [r for r in ctrl if ab(r) and "controversies" in (r.get("value") or {}) and not r["value"]["controversies"]]
    signals = [r for r in ctrl if r["confidence"] == "C" and r.get("facts")]
    esg_ab = [r for r in ok if r["need"] == "esg" and r["kind"] == "esg" and ab(r)]

    hard_ctrl = _theme_set(profile.hard_exclusions, CONTROVERSY_THEMES)
    soft_ctrl = _theme_set(profile.soft_preferences, CONTROVERSY_THEMES)
    soft_esg = _theme_set(profile.soft_preferences, ESG_THEMES)

    def theme_of(r: Reason) -> str:
        return str(r.measures.get("theme", "")).strip().lower()

    drop = lambda r: (  # noqa: E731
        (r.code == "THEME_EXCLUSION_UNVERIFIED" and theme_of(r) in hard_ctrl and (documented or clean))
        or (r.code == "THEME_DATA_MISSING" and theme_of(r) in soft_ctrl and (documented or clean))
        or (r.code == "THEME_DATA_MISSING" and theme_of(r) in soft_esg and esg_ab)
    )
    out = [r for r in reasons if not drop(r)]

    def add(check: str, code: str, severity: str, message: str, recs: List[dict], **measures) -> None:
        out.append(Reason(check=check, code=code, severity=severity, message=message, measures=measures,  # type: ignore[arg-type]
                          evidence_ids=[r["record_id"] for r in recs]))

    if documented and hard_ctrl:
        r0 = documented[0]
        add("hard_constraints", "EXCLUDED_THEME_CONTROVERSY", "block",
            f"{ticker} est exclu par le profil (thème : {', '.join(sorted(hard_ctrl))}) : des controverses sont documentées dans une source de "
            f"niveau {r0['confidence']} ({_source_name(cards, r0)}, publiée le {r0['date_publication']}).",
            documented, themes=sorted(hard_ctrl), confidence=r0["confidence"], date_publication=r0["date_publication"], source=_source_name(cards, r0))
    if documented and soft_ctrl:
        r0 = documented[0]
        add("soft_preferences", "PREFERENCE_CONTRADICTED", "warn",
            f"Préférence « {', '.join(sorted(soft_ctrl))} » : des controverses sont documentées pour {ticker} dans une source de niveau "
            f"{r0['confidence']} ({_source_name(cards, r0)}, publiée le {r0['date_publication']}).",
            documented, themes=sorted(soft_ctrl), confidence=r0["confidence"], date_publication=r0["date_publication"], source=_source_name(cards, r0))
    if clean and (hard_ctrl or soft_ctrl) and not documented:
        r0 = clean[0]
        add("data_gaps", "THEME_VERIFIED", "info",
            f"Aucune controverse documentée pour {ticker} dans {_source_name(cards, r0)} (niveau {r0['confidence']}, publiée le "
            f"{r0['date_publication']}) : ce n'est pas une garantie d'absence, seulement ce que cette source déclare.",
            clean, confidence=r0["confidence"], date_publication=r0["date_publication"], source=_source_name(cards, r0))
    if signals and (hard_ctrl or soft_ctrl):
        r0 = signals[0]
        add("data_gaps", "CONTROVERSY_SIGNAL_LEVEL_C", "warn",
            f"Signal qualitatif de niveau C sur {ticker} (publié le {r0['date_publication']}) : à vérifier. Un signal tiré d'un texte "
            "n'est jamais utilisé pour un calcul et ne bloque jamais à lui seul.",
            signals, confidence="C", date_publication=r0["date_publication"], source=_source_name(cards, r0))
    if esg_ab and soft_esg:
        r0 = esg_ab[0]
        add("soft_preferences", "ESG_DATA_NO_THRESHOLD", "warn",
            f"Donnée ESG de niveau {r0['confidence']} disponible pour {ticker} ({_source_name(cards, r0)}, publiée le "
            f"{r0['date_publication']}), mais aucun seuil ESG n'est défini : l'alignement avec la préférence n'est pas jugé.",
            esg_ab, confidence=r0["confidence"], date_publication=r0["date_publication"], source=_source_name(cards, r0))

    for r in ok:
        add("data_gaps", "ENRICHED_DATA", "info",
            f"Donnée enrichie « {r['kind']} » pour {ticker} : niveau {r['confidence']}, publiée le {r['date_publication']}, "
            f"source {_source_name(cards, r)}.",
            [r], kind=r["kind"], need=r["need"], confidence=r["confidence"], date_publication=r["date_publication"], source=_source_name(cards, r))
    by_kind: Dict[tuple, List[dict]] = {}
    for g in gaps:
        by_kind.setdefault((g["kind"], g["need"]), []).append(g)
    for (kind, need), gs in by_kind.items():
        codes = sorted({g["reason_code"] for g in gs})
        add("data_gaps", "ENRICHMENT_GAP", "warn",
            f"Donnée « {kind} » non comblée pour {ticker} ({', '.join(codes)}) : {gs[0]['message']}",
            gs, kind=kind, need=need, reason_codes=codes)
    return out


# ---- check_portfolio_limits ----------------------------------------------


def check_portfolio_limits(evidence: List[dict], constraints: Constraints) -> List[Reason]:
    """Compare un scénario de portefeuille (preuve portfolio_metrics) aux limites dérivées du profil.

    Plafond de poids par actif et par classe, volatilité et perte maximale du portefeuille. Avertissements seulement :
    le scénario est fictif, rien n'est bloqué. Une classe au plafond nul est déjà bloquée actif par actif.
    """
    out: List[Reason] = []
    lvl = constraints.risk_level_applied
    for ev in evidence:
        if ev.get("tool") != "portfolio_metrics" or ev.get("status") == "refused":
            continue
        v, eid = ev["value"], ev["evidence_id"]
        c = v["concentration"]
        if c["max_weight"] > constraints.max_single_asset_weight + 1e-9:
            out.append(Reason(
                check="portfolio", code="PORTFOLIO_WEIGHT_ABOVE_CAP", severity="warn",
                message=(f"Dans le scénario, {c['max_weight_ticker']} pèse {_pct(c['max_weight'])} du portefeuille, "
                         f"pour un plafond de {_pct(constraints.max_single_asset_weight)} par actif au niveau « {lvl} »."),
                measures={"ticker": c["max_weight_ticker"], "value": c["max_weight"], "limit": constraints.max_single_asset_weight,
                          "risk_level_applied": lvl},
                evidence_ids=[eid]))
        for row in v["breakdown"]["asset_class"]:
            cap = constraints.class_caps.get(row["name"])
            if cap is not None and cap > 0 and row["weight"] > cap + 1e-9:
                out.append(Reason(
                    check="portfolio", code="PORTFOLIO_CLASS_CAP", severity="warn",
                    message=(f"Dans le scénario, la classe « {row['name']} » pèse {_pct(row['weight'])}, "
                             f"pour un plafond de {_pct(cap)} au niveau « {lvl} »."),
                    measures={"asset_class": row["name"], "value": row["weight"], "limit": cap, "risk_level_applied": lvl},
                    evidence_ids=[eid]))
        m = v["metrics"]
        vol, dd = m["annualized_volatility"], -m["max_drawdown"]["depth"]
        if vol > constraints.max_volatility:
            out.append(Reason(
                check="portfolio", code="PORTFOLIO_VOLATILITY_ABOVE_LIMIT", severity="warn",
                message=(f"Volatilité annualisée du scénario : {_pct(vol)}, pour une limite de {_pct(constraints.max_volatility)} "
                         f"au niveau « {lvl} »."),
                measures={"value": vol, "limit": constraints.max_volatility, "risk_level_applied": lvl}, evidence_ids=[eid]))
        if dd > constraints.max_drawdown:
            out.append(Reason(
                check="portfolio", code="PORTFOLIO_DRAWDOWN_ABOVE_LIMIT", severity="warn",
                message=(f"Perte maximale du scénario depuis un sommet : {_pct(dd)}, pour une limite de {_pct(constraints.max_drawdown)} "
                         f"au niveau « {lvl} »."),
                measures={"value": dd, "limit": constraints.max_drawdown, "risk_level_applied": lvl}, evidence_ids=[eid]))
    return out


# ---- check_scope_limits --------------------------------------------------

_SCOPE = {
    "asks_prediction": ScopeRefusal(
        kind="prediction", status="avertissement",
        message="Prédire les rendements futurs est hors périmètre : les données décrivent le passé et ne le prolongent pas.",
        offered_instead="Description historique chiffrée et scénarios simulés étiquetés comme tels.",
    ),
    "asks_transaction": ScopeRefusal(
        kind="transaction", status="bloqué",
        message="Aucune transaction réelle n'est possible : AlphaForge est une simulation.",
        offered_instead="Analyse de l'actif et simulation, sans ordre ni exécution.",
    ),
    "asks_personal_advice": ScopeRefusal(
        kind="personal_advice", status="avertissement",
        message="Formuler un conseil personnalisé (« achète » ou « n'achète pas ») est hors périmètre.",
        offered_instead="Ce que montrent les données, où l'actif cadre ou non avec le profil, et ce qui reste incertain.",
    ),
}


def check_scope_limits(flags: List[str]) -> List[ScopeRefusal]:
    return [_SCOPE[f] for f in flags if f in _SCOPE]


# ---- issue_verdict -------------------------------------------------------


def status_of(reasons: List[Reason]) -> str:
    if any(r.severity == "block" for r in reasons):
        return "bloqué"
    if any(r.severity == "warn" for r in reasons):
        return "avertissement"
    return "conforme"


def worst_status(statuses: List[str]) -> str:
    order: Dict[str, int] = {"conforme": 0, "avertissement": 1, "bloqué": 2}
    return max(statuses, key=lambda s: order[s], default="conforme")
