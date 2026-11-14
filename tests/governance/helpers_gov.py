"""Fabriques de faits pour les tests de gouvernance (nom de module unique : plusieurs conftest.py coexistent dans tests/)."""
from datetime import date

from alphaforge.governance.facts import AssetFacts, WindowMetrics


def last_verdict(result):
    return result.ledger.entries("verdict")[-1].payload


def window(alias="complet", vol=0.1, depth=-0.05, peak=None, recovery=None, recovered=True, start=date(2022, 1, 3), end=date(2023, 12, 29), eid="ev_w"):
    return WindowMetrics(
        alias=alias, start=start, end=end, volatility=vol, drawdown_depth=depth, peak_date=peak, recovery_date=recovery,
        recovered=recovered, annualization_basis="252 jours de bourse", evidence_id=eid,
    )


def facts(ticker="X", asset_class="equity", sector="Tech", windows=None, with_asset=True, **kw):
    asset = {"ticker": ticker, "asset_class": asset_class, "sector": sector} if with_asset else None
    return AssetFacts(ticker=ticker, asset=asset, lookup_evidence_id="ev_lookup" if with_asset else None, windows=windows or [], **kw)
