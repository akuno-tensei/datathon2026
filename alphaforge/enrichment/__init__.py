"""Agent 2.1 : enrichissement. Faits publics sourcés, datés, avec niveau de confiance, écrits au registre de preuves."""
from .agent import EnrichmentAgent, EnrichmentReport
from .facts import FactExtractor, RuleBasedExtractor, extract_facts
from .fetchers import FETCH_TOOLS, fetch_esg, fetch_etf_holdings, fetch_filings, fetch_fundamentals, fetch_news
from .ledger_write import LedgerWriteError, ledger_write
from .models import EnrichmentRecord, Fact, Gap, NeedsReport, RawItem, SourceCard
from .needs import detect_needs
from .pit import point_in_time_filter
from .providers import CachedProvider, CachingProvider, EdgarProvider, Provider, ProviderError
from .source_card import SourceDefinition, SourceRegistry, source_card

__all__ = [
    "CachedProvider", "CachingProvider", "EdgarProvider", "EnrichmentAgent", "EnrichmentRecord", "EnrichmentReport",
    "FETCH_TOOLS", "Fact", "FactExtractor", "Gap", "LedgerWriteError", "NeedsReport", "Provider", "ProviderError", "RawItem",
    "RuleBasedExtractor", "SourceCard", "SourceDefinition", "SourceRegistry", "detect_needs", "extract_facts", "fetch_esg",
    "fetch_etf_holdings", "fetch_filings", "fetch_fundamentals", "fetch_news", "ledger_write", "point_in_time_filter", "source_card",
]
