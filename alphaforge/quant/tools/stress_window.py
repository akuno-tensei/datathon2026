"""stress_window : rejoue compute_metrics sur plusieurs fenêtres et dit si la mesure est stable ou sensible à la période."""
from datetime import date
from typing import List, Literal, Optional

from ..datastore import DataStore
from ..models import Evidence, Notice, StrictModel, ToolInputError, WindowSpec
from ..registry import build_evidence, refused, tool
from ..windows import resolve_as_of
from ._common import LIMIT_PAST, prelude
from .compute_metrics import ComputeMetricsParams, compute_metrics

Metric = Literal["annualized_volatility", "max_drawdown"]


class StressWindowParams(StrictModel):
    ticker: str
    tool: Literal["compute_metrics"] = "compute_metrics"  # seul outil rejouable pour l'instant
    windows: Optional[List[WindowSpec]] = None  # None : fenêtres par défaut de la configuration
    metric: Metric = "annualized_volatility"
    threshold: Optional[float] = None  # None : seuil de la configuration
    min_abs_spread: Optional[float] = None  # None : plancher de la configuration ; 0 le désactive
    base_currency: Optional[str] = None
    as_of: Optional[date] = None


def _measure(block: dict, metric: str) -> float:
    if metric == "max_drawdown":
        return -block["max_drawdown"]["depth"]  # perte positive : comparable d'une fenêtre à l'autre
    return block["annualized_volatility"]


@tool(
    "stress_window",
    "1.0.0",
    "Rejoue compute_metrics sur une liste de fenêtres nommées ou libres (par défaut : complet, 2022, hors 2022, 1 an, "
    "3 ans) et compare une mesure (volatilité ou perte maximale) entre elles. Verdict « stable » si l'écart relatif "
    "(max − min) ÷ min reste sous le seuil affiché, « sensible » sinon. Toutes les fenêtres sont consignées, y compris "
    "celles qui n'ont pas pu être calculées.",
    StressWindowParams,
)
def stress_window(store: DataStore, p: StressWindowParams) -> Evidence:
    cfg = store.config
    as_of = resolve_as_of(store, p.as_of)
    base = (p.base_currency or cfg.default_base_currency).upper()
    threshold = cfg.stress_threshold if p.threshold is None else p.threshold
    floor = cfg.stress_min_abs_spread if p.min_abs_spread is None else p.min_abs_spread
    specs = p.windows if p.windows is not None else [WindowSpec(alias=a) for a in cfg.stress_default_windows]
    params = {
        "ticker": p.ticker, "tool": p.tool, "windows": [w.model_dump(mode="json", exclude_none=True) for w in specs],
        "metric": p.metric, "threshold": threshold, "min_abs_spread": floor, "base_currency": base, "as_of": as_of.isoformat(),
    }
    try:
        if threshold <= 0:
            raise ToolInputError("invalid_threshold", f"Seuil invalide : {threshold} (doit être > 0).")
        if floor < 0:
            raise ToolInputError("invalid_threshold", f"Plancher absolu invalide : {floor} (doit être >= 0).")
        if not specs:
            raise ToolInputError("no_windows", "Aucune fenêtre à rejouer.")
        asset, _, _, _ = prelude(store, p.ticker, specs[0], as_of)  # valide l'actif ; chaque fenêtre est validée ci-dessous
    except ToolInputError as e:
        return refused(store, "stress_window", params, as_of, e)
    params["ticker"] = asset["ticker"]

    rows, depends, notices = [], [], []
    for spec in specs:
        ev = compute_metrics(store, ComputeMetricsParams(ticker=asset["ticker"], window=spec, base_currency=base, as_of=as_of))
        depends.append(ev.evidence_id)
        label = spec.alias or f"{spec.start}/{spec.end}"
        row = {"window": label, "evidence_id": ev.evidence_id, "status": ev.status}
        if ev.window is not None:
            row.update(start=ev.window.start.isoformat(), end=ev.window.end.isoformat(),
                       excluded=[{"start": x.start.isoformat(), "end": x.end.isoformat()} for x in ev.window.excluded])
        if ev.status == "refused":
            row.update(error_code=ev.error_code, message=ev.notices[-1].message if ev.notices else ev.refusal_reason)
            notices.append(Notice(code="WINDOW_NOT_COMPUTED", message=f"Fenêtre « {label} » non calculée : {row['message']}"))
        else:
            b = ev.value["base"]
            row.update(value=_measure(b, p.metric), annualized_volatility=b["annualized_volatility"],
                       max_drawdown=b["max_drawdown"]["depth"], total_return=b["total_return"],
                       annualized_return=b["annualized_return"], n_returns=ev.value["n_returns"],
                       annualization_basis=ev.value["annualization_basis"])
        rows.append(row)

    ok = [r for r in rows if "value" in r]
    abs_spread, floor_applied = None, False
    if len(ok) < 2:
        verdict, spread, lo, hi = "indetermine", None, None, None
        notices.append(Notice(code="STRESS_UNDETERMINED", message=f"{len(ok)} fenêtre(s) chiffrée(s) : il en faut au moins deux pour comparer."))
    else:
        lo, hi = min(ok, key=lambda r: r["value"]), max(ok, key=lambda r: r["value"])
        if lo["value"] <= 0:
            verdict, spread = "indetermine", None
            notices.append(Notice(code="STRESS_UNDETERMINED", message=f"Mesure nulle sur la fenêtre « {lo['window']} » : écart relatif non défini."))
        else:
            spread = (hi["value"] - lo["value"]) / lo["value"]
            abs_spread = hi["value"] - lo["value"]
            floor_applied = spread > threshold and abs_spread < floor  # écart relatif grand, mais écart réel négligeable
            verdict = "sensible" if spread > threshold and not floor_applied else "stable"

    metric_label = "volatilité annualisée" if p.metric == "annualized_volatility" else "perte maximale depuis un sommet"
    value = {
        "ticker": asset["ticker"],
        "replayed_tool": p.tool,
        "metric": p.metric,
        "threshold": threshold,
        "verdict": verdict,  # stable | sensible | indetermine
        "relative_spread": spread,
        "absolute_spread": abs_spread,
        "min_abs_spread": floor,
        "floor_applied": floor_applied,
        "min_window": lo["window"] if lo else None,
        "max_window": hi["window"] if hi else None,
        "n_windows": len(rows),
        "n_computed": len(ok),
        "rows": rows,
    }
    if spread is not None:
        value["summary"] = (
            f"{metric_label} : de {lo['value']:.1%} (« {lo['window']} ») à {hi['value']:.1%} (« {hi['window']} »), "
            f"écart relatif {spread:.0%} pour un seuil de {threshold:.0%} : {verdict}."
        )
        if floor_applied:
            value["summary"] += f" L'écart absolu ({abs_spread:.1%}) est sous le plancher de {floor:.1%} : la mesure reste stable."
    return build_evidence(
        store, "stress_window", params, as_of, value=value, n_obs=len(ok), depends_on=depends,
        inputs=[store.input_ref("prices_daily.csv", asset["ticker"], as_of)],
        sources=[store.source_label("prices_daily.csv")], notices=notices,
        assumptions=[
            f"Écart relatif = (max − min) ÷ min de la {metric_label} entre les fenêtres chiffrées ; « sensible » au-delà de {threshold:.0%}, "
            f"sauf si l'écart absolu (max − min) reste sous le plancher de {floor:.1%} (une mesure presque nulle n'est pas sensible).",
            "Chaque ligne est une preuve compute_metrics distincte (evidence_id dans rows), recalculable à l'identique.",
            "Fenêtre « hors 2022 » : rendements datés en 2022 retirés, sans faux rendement entre fin 2021 et début 2023.",
        ],
        limits=[
            LIMIT_PAST,
            "La sensibilité dépend des fenêtres choisies : une mesure stable sur ces fenêtres peut changer sur une autre période.",
        ],
    )
