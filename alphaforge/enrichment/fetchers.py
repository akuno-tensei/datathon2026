"""Outils de collecte : fetch_esg, fetch_filings, fetch_fundamentals, fetch_news, fetch_etf_holdings.

Chacun appelle un fournisseur et renvoie un `FetchResult` typé. Ils ne filtrent pas dans le temps et n'écrivent rien :
`point_in_time_filter` puis `ledger_write` s'en chargent. Un échec du fournisseur devient un code d'erreur, jamais une
exception ni une valeur devinée.
"""
from datetime import date
from typing import Callable, Dict

from .models import DataKind, FetchResult
from .providers import Provider, ProviderError


def _fetch(tool: str, kind: DataKind, provider: Provider, ticker: str, as_of: date) -> FetchResult:
    base = dict(tool=tool, kind=kind, ticker=ticker, as_of=as_of)
    try:
        items = provider.fetch(kind, ticker, as_of)
    except ProviderError as e:
        return FetchResult(**base, error_code=e.code, error_message=e.message)
    except Exception as e:  # noqa: BLE001  (une panne imprévue d'une source ne doit pas arrêter la simulation)
        return FetchResult(**base, error_code="provider_failure", error_message=f"{type(e).__name__}: {e}")

    wrong = [i for i in items if i.kind != kind or i.ticker != ticker]
    if wrong:  # un fournisseur qui répond à côté n'est pas cru
        return FetchResult(
            **base, error_code="provider_mismatch",
            error_message=f"{len(wrong)} élément(s) ne correspondent pas à la demande ({kind}, {ticker}) : lot rejeté.",
        )
    return FetchResult(**base, items=items)


def fetch_esg(provider: Provider, ticker: str, as_of: date) -> FetchResult:
    """Information ESG publiée (secteur, émissions déclarées, controverses documentées) avec la méthodologie du fournisseur."""
    return _fetch("fetch_esg", "esg", provider, ticker, as_of)


def fetch_filings(provider: Provider, ticker: str, as_of: date) -> FetchResult:
    """Dépôts réglementaires (SEC EDGAR, SEDAR+) et communiqués de résultats."""
    return _fetch("fetch_filings", "filings", provider, ticker, as_of)


def fetch_fundamentals(provider: Provider, ticker: str, as_of: date) -> FetchResult:
    """Revenus, bénéfices, dette, flux de trésorerie, ratios."""
    return _fetch("fetch_fundamentals", "fundamentals", provider, ticker, as_of)


def fetch_news(provider: Provider, ticker: str, as_of: date) -> FetchResult:
    """Nouvelles datées et documents publics."""
    return _fetch("fetch_news", "news", provider, ticker, as_of)


def fetch_etf_holdings(provider: Provider, ticker: str, as_of: date) -> FetchResult:
    """Titres détenus par un FNB, pondérations, frais (vérifier l'ESG « par transparence »)."""
    return _fetch("fetch_etf_holdings", "etf_holdings", provider, ticker, as_of)


FETCH_TOOLS: Dict[str, Callable[[Provider, str, date], FetchResult]] = {
    "fetch_esg": fetch_esg,
    "fetch_filings": fetch_filings,
    "fetch_fundamentals": fetch_fundamentals,
    "fetch_news": fetch_news,
    "fetch_etf_holdings": fetch_etf_holdings,
}
