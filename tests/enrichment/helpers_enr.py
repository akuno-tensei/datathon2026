"""Fabriques et fournisseurs simulés partagés par les tests de l'enrichissement."""
from datetime import date

from alphaforge.enrichment import ProviderError, RawItem

AS_OF = date(2025, 6, 30)


def raw(**kw) -> RawItem:
    base = dict(source_id="esg_vendor", retrieved_on=date(2026, 11, 13))
    base.update(kw)
    return RawItem(**base)


def esg_item(ticker="NVDA", published=date(2025, 3, 1), **kw) -> RawItem:
    return raw(ticker=ticker, kind="esg", url="https://vendor.example/esg/" + ticker, date_publication=published,
               date_observation=min(date(2024, 12, 31), published), structured={"rating": "BBB", "controversies": ["enquête X"]},
               methodology="Méthodologie fictive 2024", **kw)


def news_item(ticker="NVDA", published=date(2025, 5, 2), text=None) -> RawItem:
    text = text or ("NVIDIA a publié ses résultats. Une enquête antitrust a été ouverte contre la société en 2025. "
                    "Le titre a fluctué.")
    return raw(source_id="wire", ticker=ticker, kind="news", url="https://news.example/1", date_publication=published,
               date_observation=published, text=text)


class FakeProvider:
    """Fournisseur simulé : `fn(kind, ticker, as_of)` renvoie des éléments ou lève."""

    def __init__(self, fn, source_id="esg_vendor"):
        self.fn, self.source_id, self.calls = fn, source_id, 0

    def fetch(self, kind, ticker, as_of):
        self.calls += 1
        return self.fn(kind, ticker, as_of)


def failing(code="unavailable"):
    def fn(kind, ticker, as_of):
        raise ProviderError(code, "source en panne")
    return fn
