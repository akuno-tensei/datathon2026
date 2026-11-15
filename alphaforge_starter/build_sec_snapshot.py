"""Instantané SEC EDGAR pour la démo hors ligne : dépôts et fondamentaux des actions américaines de l'univers.

    ALPHAFORGE_SEC_USER_AGENT="Nom de l'équipe contact@exemple.org" python3 alphaforge_starter/build_sec_snapshot.py

L'identification exigée par la SEC est lue dans l'environnement et n'est écrite nulle part : les fichiers produits ne
contiennent que des données publiques de la SEC (formulaires, dates, liens, chiffres XBRL) et la date de collecte.
Les données sont arrêtées à la dernière date du jeu de départ (as_of) : rien de publié après n'est conservé.
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alphaforge.enrichment import CachedProvider, EdgarProvider, ProviderError  # noqa: E402
from alphaforge.quant import DataStore  # noqa: E402

OUT = Path(__file__).resolve().parent / "enrichment_snapshot"


def main() -> None:
    ua = os.environ.get("ALPHAFORGE_SEC_USER_AGENT", "").strip()
    if not ua:
        sys.exit("ALPHAFORGE_SEC_USER_AGENT manquant : la SEC exige un nom et un courriel de contact.")
    store = DataStore.from_directory()
    as_of = store.last_date
    tickers = [a["ticker"] for a in store.assets.to_dict("records") if a["asset_class"] == "equity" and a["currency"] == "USD"]
    edgar, snap = EdgarProvider(ua, date.today()), CachedProvider(OUT, source_id="sec_edgar_snapshot")
    report = {"as_of": as_of.isoformat(), "retrieved_on": date.today().isoformat(), "source": "SEC EDGAR (data.sec.gov, www.sec.gov)",
              "tickers": {}}
    for t in tickers:
        report["tickers"][t] = {}
        for kind in ("filings", "fundamentals"):
            try:
                items = edgar.fetch(kind, t, as_of)
                snap.store(kind, t, items)
                report["tickers"][t][kind] = len(items)
            except ProviderError as e:
                report["tickers"][t][kind] = f"lacune : {e.code}"  # consignée, jamais comblée
        print(t, report["tickers"][t])
    (OUT / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
