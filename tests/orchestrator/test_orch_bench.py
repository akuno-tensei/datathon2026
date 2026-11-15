"""Le banc d'essai de l'interprétation tourne sans réseau pour les règles, et ses résultats publiés correspondent au fichier."""
import importlib.util
import json
from pathlib import Path

from alphaforge.orchestrator import RuleBasedParser

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("bench_llm", ROOT / "deploy" / "bench_llm.py")
bench_llm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench_llm)


def test_the_rules_score_is_recomputed_not_remembered():
    from alphaforge.quant import DataStore

    assets = DataStore.from_directory().assets.to_dict("records")  # l'univers complet, comme le banc d'essai
    r = bench_llm.run_pass(RuleBasedParser(), assets)
    assert r["max"] == 36 and r["fallbacks"] == 0
    published = json.loads((ROOT / "deploy" / "bench_results.json").read_text(encoding="utf-8"))
    rules = next(x for x in published["results"] if x["interpreter"] == "rules")
    assert rules["scores"] == [r["score"]]  # le fichier publié dit vrai pour la partie rejouable sans AWS


def test_the_readme_quotes_the_results_file():
    published = json.loads((ROOT / "deploy" / "bench_results.json").read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for x in published["results"]:
        total, best = sum(x["scores"]), x["max"] * len(x["scores"])
        assert f"{total}/{best}" in readme, (x["interpreter"], f"{total}/{best}")
