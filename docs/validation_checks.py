"""Deux contrôles de validation, hors ligne et sans AWS : `python3 docs/validation_checks.py`.

1. Recalcul indépendant : rendement cumulatif, volatilité annualisée, perte maximale et pire jour recalculés avec pandas
   directement depuis `prices_daily.csv`, sans passer par le code de l'analyste, puis comparés à `compute_metrics`.
2. Reproductibilité : la même analyse lancée trois fois doit donner un registre identique (même empreinte SHA-256).

Code de sortie 1 si un écart est trouvé. Ne modifie aucun fichier.
"""
import hashlib
import os
import sys
from pathlib import Path

os.environ["ALPHAFORGE_LLM"] = "0"  # règles seules : aucun appel réseau, résultat identique sur tout poste
os.environ.pop("ALPHAFORGE_S3_BUCKET", None)  # aucune archive écrite
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from alphaforge.app.archive import ledger_jsonl  # noqa: E402
from alphaforge.app.pipeline import AlphaForge  # noqa: E402
from alphaforge.quant import DataStore, run_tool  # noqa: E402

TOL = 1e-9
ASSETS = [("NVDA", 252), ("RY.TO", 252), ("SPY", 252), ("GLD", 252), ("BTC-USD", 365)]  # (actif, jours par an)
REQUESTS = [("Évalue NVDA", "robert"), ("Compare NVDA et TLT", "amira"), ("60 % SPY et 40 % AGG, sans rééquilibrage", "mateo")]


def independent() -> int:
    px = pd.read_csv(ROOT / "alphaforge_starter" / "prices_daily.csv", parse_dates=["date"])
    store = DataStore.from_directory()
    bad = 0
    print("1. Recalcul indépendant (fenêtre complète, devise native)")
    print(f"   {'actif':8} {'obs':>5} {'cumulatif':>10} {'volatilité':>11} {'perte max':>10} {'pire jour':>10}  écart max")
    for ticker, ppy in ASSETS:
        ev = run_tool(store, "compute_metrics", {"ticker": ticker, "window": {"alias": "complet"}})
        v = ev.value["native"]
        p = px[px.ticker == ticker].sort_values("date").set_index("date")["adj_close"].dropna()
        p = p[p.index <= pd.Timestamp(str(ev.as_of))]
        r = p.pct_change().dropna()
        ours = [v["total_return"], v["annualized_volatility"], v["max_drawdown"]["depth"], v["worst_day"]["return_"]]
        theirs = [p.iloc[-1] / p.iloc[0] - 1, r.std(ddof=1) * ppy ** 0.5, (p / p.cummax() - 1).min(), r.min()]
        gap = max(abs(a - b) for a, b in zip(ours, theirs))
        bad += gap > TOL
        print(f"   {ticker:8} {ev.n_obs:>5} {ours[0]:>10.2%} {ours[1]:>11.2%} {ours[2]:>10.2%} {ours[3]:>10.2%}  {gap:.1e}"
              + ("" if gap <= TOL else "  ÉCART"))
    return bad


def reproducible() -> int:
    af = AlphaForge.from_directory()
    bad = 0
    print("2. Reproductibilité (3 lancements de la même analyse)")
    for text, profile in REQUESTS:
        digests = {hashlib.sha256(ledger_jsonl(af.analyze(text, profile).result.ledger).encode("utf-8")).hexdigest()[:12]
                   for _ in range(3)}
        bad += len(digests) != 1
        print(f"   {profile:7} « {text} » : {', '.join(sorted(digests))}" + ("" if len(digests) == 1 else "  DIFFÉRENT"))
    return bad


if __name__ == "__main__":
    failures = independent() + reproducible()
    print("RÉSULTAT :", "tout concorde" if not failures else f"{failures} écart(s)")
    sys.exit(1 if failures else 0)
