"""Banc d'essai de l'interprétation des demandes : justesse et temps de réponse, par interpréteur.

    python3 deploy/bench_llm.py                                  # règles + modèles par défaut, 3 passages, écrit deploy/bench_results.json
    python3 deploy/bench_llm.py --repeats 1 --models rules       # règles seules, sans réseau ni AWS

Score d'un passage : pour chacune des 12 demandes, 3 points (intention juste, actifs justes, signaux de périmètre attendus
présents), soit 36. Un appel Bedrock en échec qui retombe sur les règles est compté à part (`fallbacks`) : ce n'est pas le modèle.
Les résultats publiés dans le README viennent de ce fichier de résultats, jamais d'un chiffre recopié.
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alphaforge.orchestrator.request import BedrockClient, LLMParser, RuleBasedParser  # noqa: E402
from alphaforge.quant import DataStore  # noqa: E402

# (demande, intention attendue, actifs attendus, signaux de périmètre attendus)
CASES = [
    ("Évalue NVDA", "evaluate_asset", {"NVDA"}, set()),
    ("Est-ce que Nvidia convient à mon profil ?", "evaluate_asset", {"NVDA"}, set()),
    ("Compare la Banque Royale et TD", "compare_assets", {"RY.TO", "TD.TO"}, set()),
    ("Lequel est le moins risqué entre l'or et les obligations américaines à long terme ?", "compare_assets", {"GLD", "TLT"}, set()),
    ("Pourquoi Shopify est-il risqué pour moi ?", "explain_risk", {"SHOP.TO"}, set()),
    ("60 % SPY et 40 % AGG, sans rééquilibrage", "build_scenario", {"SPY", "AGG"}, set()),
    ("Tout le monde parle de Tesla, je devrais en acheter ?", "evaluate_asset", {"TSLA"}, {"asks_transaction"}),
    ("Combien vaudra le bitcoin l'an prochain ?", "evaluate_asset", {"BTC-USD"}, {"asks_prediction"}),
    ("Achète-moi 100 actions d'Apple", "evaluate_asset", {"AAPL"}, {"asks_transaction"}),
    ("Analyse Ether pour un profil comme le mien, je veux de la croissance", "evaluate_asset", {"ETH-USD"}, set()),
    ("Should I put my savings into Microsoft and Amazon?", "compare_assets", {"MSFT", "AMZN"}, {"asks_personal_advice"}),
    ("Je veux comparer Enbridge, Fortis et le FNB obligataire canadien", "compare_assets", {"ENB.TO", "FTS.TO", "XBB.TO"}, set()),
]
DEFAULT_MODELS = ["global.anthropic.claude-haiku-4-5-20251001-v1:0", "us.amazon.nova-2-lite-v1:0"]


def run_pass(parser: Any, assets: List[Dict[str, Any]]) -> Dict[str, Any]:
    lat, misses, fallbacks = [], [], 0
    pts = {"intent": 0, "assets": 0, "scope": 0}
    for text, intent, tickers, flags in CASES:
        t0 = time.perf_counter()
        r = parser.parse(text, assets)
        lat.append(time.perf_counter() - t0)
        fallbacks += r.parser == "rules(fallback)"
        ok = {"intent": r.intent == intent, "assets": set(r.tickers) == tickers, "scope": flags <= set(r.scope_flags)}
        for k, v in ok.items():
            pts[k] += v
        if not all(ok.values()):
            misses.append({"request": text, "intent": r.intent, "tickers": r.tickers, "scope_flags": r.scope_flags, "parser": r.parser})
    return {"score": sum(pts.values()), "max": 3 * len(CASES), **pts, "fallbacks": fallbacks,
            "latency_median_s": round(statistics.median(lat), 3), "latency_max_s": round(max(lat), 3), "misses": misses}


def bench(name: str, parser: Any, assets: List[Dict[str, Any]], repeats: int) -> Dict[str, Any]:
    if name != "rules":
        parser.parse("Évalue NVDA", assets)  # préchauffage : la première connexion n'est pas comptée
    passes = [run_pass(parser, assets) for _ in range(repeats)]
    return {"interpreter": name, "repeats": repeats, "scores": [p["score"] for p in passes], "max": passes[0]["max"],
            "fallbacks": sum(p["fallbacks"] for p in passes),
            "latency_median_s": round(statistics.median(p["latency_median_s"] for p in passes), 3), "passes": passes}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", default=["rules"] + DEFAULT_MODELS, help="« rules » ou identifiants Bedrock")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--out", default=str(ROOT / "deploy" / "bench_results.json"))
    a = ap.parse_args()
    assets = DataStore.from_directory().assets.to_dict("records")
    results = []
    for m in a.models:
        parser = RuleBasedParser() if m == "rules" else LLMParser(BedrockClient(m, region_name=a.region))
        r = bench(m, parser, assets, a.repeats if m != "rules" else 1)  # les règles sont déterministes : un passage suffit
        results.append(r)
        print(f"{m:55} scores {r['scores']} / {r['max']} · repli {r['fallbacks']} · médiane {r['latency_median_s']} s")
    out = {"run_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "n_requests": len(CASES), "results": results}
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"→ {a.out}")


if __name__ == "__main__":
    main()
