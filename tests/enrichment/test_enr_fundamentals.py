"""Fondamentaux SEC EDGAR (données XBRL « companyfacts ») : dernier rapport annuel déposé avant as_of, sans réseau.

La réponse de la SEC est simulée avec la forme réelle de l'API : facts -> us-gaap -> concept -> units -> USD -> [faits].
"""
import json
from datetime import date

from alphaforge.enrichment import EdgarProvider, fetch_fundamentals

AS_OF = date(2025, 6, 30)


def fact(val, end, filed, form="10-K", accn="A-2025", start=None):
    f = {"val": val, "end": end, "filed": filed, "form": form, "accn": accn, "fy": int(end[:4]), "fp": "FY"}
    if start:
        f["start"] = start
    return f


FACTS = {"facts": {"us-gaap": {
    "Revenues": {"units": {"USD": [
        fact(130_500_000_000, "2025-01-26", "2025-02-26", start="2024-01-29"),                 # exercice du rapport retenu
        fact(60_900_000_000, "2024-01-28", "2025-02-26", start="2023-01-30"),                  # année comparative, même rapport
        fact(44_100_000_000, "2025-04-27", "2025-05-28", form="10-Q", accn="Q-2025", start="2025-01-27"),  # trimestriel : écarté
        fact(200_000_000_000, "2026-01-25", "2026-02-25", accn="A-2026", start="2025-01-27"),  # déposé après as_of : n'existe pas
    ]}},
    "NetIncomeLoss": {"units": {"USD": [fact(72_900_000_000, "2025-01-26", "2025-02-26", start="2024-01-29")]}},
    "Assets": {"units": {"USD": [fact(111_600_000_000, "2025-01-26", "2025-02-26")]}},
    "LongTermDebtNoncurrent": {"units": {"USD": [fact(8_460_000_000, "2025-01-26", "2025-02-26")]}},
}}}


class Http:
    def __init__(self, facts=FACTS):
        self.calls, self.facts = [], facts

    def __call__(self, url, headers):
        self.calls.append(url)
        assert headers["User-Agent"], "la SEC exige un User-Agent identifiant l'appelant"
        if "company_tickers" in url:
            return json.dumps({"0": {"cik_str": 1045810, "ticker": "NVDA"}, "1": {"cik_str": 320193, "ticker": "AAPL"}}).encode()
        return json.dumps(self.facts).encode()


def provider(http=None):
    return EdgarProvider("Equipe test test@example.com", date(2026, 11, 13), http or Http())


def test_figures_come_from_the_latest_annual_report_filed_before_as_of():
    res = fetch_fundamentals(provider(), "NVDA", AS_OF)
    assert res.error_code is None and len(res.items) == 1
    v = res.items[0].structured
    assert v["form"] == "10-K" and v["filing_date"] == "2025-02-26" and v["period_end"] == "2025-01-26" and v["accession_number"] == "A-2025"
    assert v["metrics"] == {"revenue": 130_500_000_000, "net_income": 72_900_000_000, "long_term_debt": 8_460_000_000,
                            "total_assets": 111_600_000_000}  # année comparative, trimestriel et rapport futur écartés
    assert v["xbrl_concepts"]["revenue"] == "Revenues" and v["currency"] == "USD"
    item = res.items[0]
    assert item.date_publication == date(2025, 2, 26) and item.date_observation == date(2025, 1, 26)
    assert item.url == "https://www.sec.gov/Archives/edgar/data/1045810/A2025/"


def test_a_missing_indicator_is_omitted_never_estimated():
    v = fetch_fundamentals(provider(), "NVDA", AS_OF).items[0].structured
    assert "cash" not in v["metrics"] and "operating_cash_flow" not in v["metrics"] and "equity" not in v["metrics"]


def test_a_later_as_of_sees_the_next_report():
    v = fetch_fundamentals(provider(), "NVDA", date(2026, 3, 1)).items[0].structured
    assert v["accession_number"] == "A-2026" and v["metrics"] == {"revenue": 200_000_000_000}


def test_no_annual_report_before_as_of_is_an_explicit_gap():
    res = fetch_fundamentals(provider(), "NVDA", date(2025, 1, 1))
    assert res.items == [] and res.error_code == "no_data_before_as_of"


def test_unsupported_or_unidentified_requests_never_call_the_sec():
    http = Http()
    p = provider(http)
    assert fetch_fundamentals(p, "RY.TO", AS_OF).error_code == "unsupported"
    assert fetch_fundamentals(p, "BTC-USD", AS_OF).error_code == "unsupported"
    assert fetch_fundamentals(EdgarProvider(None, date(2026, 11, 13), http), "NVDA", AS_OF).error_code == "auth_required"
    assert http.calls == []
    assert fetch_fundamentals(p, "ZZZZ", AS_OF).error_code == "not_found"


def test_the_list_of_filers_is_downloaded_once_per_provider():
    http = Http()
    p = provider(http)
    fetch_fundamentals(p, "NVDA", AS_OF)
    fetch_fundamentals(p, "AAPL", AS_OF)
    assert sum("company_tickers" in u for u in http.calls) == 1
