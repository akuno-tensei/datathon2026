"""Univers synthétique à volatilités connues, fournisseurs simulés et registre de sources pour tester l'enrichissement intégré."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from alphaforge.enrichment import EnrichmentAgent, ProviderError, RawItem
from alphaforge.enrichment.source_card import DEFAULT_SOURCES, SourceDefinition, SourceRegistry
from alphaforge.explainability import Explainer
from alphaforge.governance import Governance
from alphaforge.orchestrator import Orchestrator, QuantAnalyst
from alphaforge.quant import DataStore

AS_OF = date(2026, 11, 12)
DATES = pd.bdate_range("2021-11-12", "2026-11-12")
# ticker, secteur, |r| quotidien : NVDA dépasse les limites d'un profil prudent, ses pairs non (la gouvernance cherche des alternatives)
SPEC = [("NVDA", "Tech", 0.030), ("AMD", "Tech", 0.003), ("INTC", "Tech", 0.003), ("RY.TO", "Finance", 0.003)]


@pytest.fixture(scope="session")
def assets():
    return [{"asset_id": f"A{i}", "ticker": t, "name": t, "asset_class": "equity", "market": "X",
             "currency": "CAD" if t.endswith(".TO") else "USD", "sector": s} for i, (t, s, _) in enumerate(SPEC, 1)]


@pytest.fixture(scope="session")
def store(assets):
    frames = []
    for (t, _, r), a in zip(SPEC, assets):
        px = 100 * np.cumprod(1 + np.where(np.arange(len(DATES)) % 2 == 0, r, -r))
        frames.append(pd.DataFrame({"date": DATES, "ticker": t, "open": px, "high": px, "low": px, "close": px, "adj_close": px,
                                    "volume": 1000, "currency": a["currency"]}))
    return DataStore(pd.DataFrame(assets), pd.concat(frames, ignore_index=True), pd.DataFrame({"date": DATES, "usdcad": 1.25, "cadusd": 0.8}))


@pytest.fixture(scope="session")
def registry():
    vendor = SourceDefinition(source_id="esg_vendor", name="Fournisseur ESG fictif", origin_url="https://vendor.example",
                              source_class="third_party", reuse_terms="Licence fictive : usage interne", limits=["Couverture partielle."])
    return SourceRegistry(DEFAULT_SOURCES + [vendor])


def raw(**kw) -> RawItem:
    base = dict(source_id="esg_vendor", retrieved_on=date(2026, 11, 13))
    base.update(kw)
    return RawItem(**base)


def esg(ticker, controversies=("enquête X",), published=date(2026, 3, 1), source_id="esg_vendor", methodology="Méthodologie fictive 2025") -> RawItem:
    return raw(ticker=ticker, kind="esg", source_id=source_id, url="https://vendor.example/esg/" + ticker, date_publication=published,
               date_observation=min(date(2025, 12, 31), published), structured={"rating": "BBB", "controversies": list(controversies)},
               methodology=methodology)


def news(ticker, text, published=date(2026, 5, 2)) -> RawItem:
    return raw(ticker=ticker, kind="news", source_id="wire", url="https://news.example/" + ticker, date_publication=published,
               date_observation=published, text=text)


class Fake:
    """Fournisseur simulé : `fn(kind, ticker, as_of)` renvoie des éléments ou lève ProviderError. Compte ses appels."""

    def __init__(self, fn, source_id="esg_vendor"):
        self.fn, self.source_id, self.calls = fn, source_id, 0

    def fetch(self, kind, ticker, as_of):
        self.calls += 1
        return self.fn(kind, ticker, as_of)


def failing(code="unavailable", message="source en panne"):
    def fn(kind, ticker, as_of):
        raise ProviderError(code, message)

    return fn


@pytest.fixture
def profile():
    """Profil prudent qui exclut durement les controverses : aucune règle propre à un persona."""
    def make(**over):
        p = {"profile_id": "prudent_ethique", "horizon_years_min": 8, "horizon_years_max": 12, "risk_tolerance": "faible_modere",
             "objectives": ["croissance_moderee"], "hard_exclusions": [{"kind": "theme", "value": "eviter_controverses"}]}
        p.update(over)
        return p
    return make


@pytest.fixture
def run(store, assets, registry):
    """run(texte, profil, providers=None, **orch) -> (résultat, agent). Gouvernance et explicabilité branchées."""
    def _run(text, prof, providers=None, enrichment="auto", **kw):
        agent = EnrichmentAgent(providers or {}, sources=registry) if enrichment == "auto" else enrichment
        o = Orchestrator(QuantAnalyst(store), governance=Governance(assets), explainer=Explainer(), as_of=AS_OF, enrichment=agent, **kw)
        return o.run(text, prof, assets), agent
    return _run


def reasons(result, ticker=None):
    v = result.ledger.entries("verdict")[-1].payload
    pool = v["assets"] + v.get("alternatives", [])
    return [r for a in pool if ticker in (None, a["ticker"]) for r in a["reasons"]]


def codes(result, ticker=None):
    return [r["code"] for r in reasons(result, ticker)]


def verdict(result):
    return result.ledger.entries("verdict")[-1].payload
