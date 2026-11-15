"""Fournisseurs de données : contrat, cache sur disque, et un fournisseur SEC EDGAR.

Un fournisseur n'est jamais cru sur parole : ses éléments passent par le filtre temporel, les validations de faits et la
fiche source. En cas d'échec il lève `ProviderError` ; l'agent écrit alors une lacune, il ne comble jamais par supposition.
"""
import json
import re
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol, Union

from pydantic import ValidationError

from .config import ANNUAL_DAYS, ANNUAL_FORMS, FILING_FORMS, FUNDAMENTAL_CONCEPTS, MAX_FILINGS
from .models import DataKind, RawItem

_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")


class ProviderError(Exception):
    """Source inaccessible, limitée ou non prise en charge. `code` est stable et consigné dans la lacune."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class Provider(Protocol):
    source_id: str

    def fetch(self, kind: DataKind, ticker: str, as_of: date) -> List[RawItem]:
        """Éléments disponibles pour `ticker`. Lève `ProviderError` si la source ne répond pas ou ne couvre pas la demande."""


# ---- cache sur disque -------------------------------------------------------


class CachedProvider:
    """Lit `<dossier>/<kind>/<ticker>.json` (liste d'éléments bruts). Sert la démo hors ligne : aucun appel réseau."""

    def __init__(self, directory: Union[str, Path], source_id: str = "cache"):
        self._dir = Path(directory)
        self.source_id = source_id

    def _path(self, kind: str, ticker: str) -> Path:
        if not _SAFE.match(ticker):  # empêche toute sortie du dossier (../)
            raise ProviderError("invalid_ticker", f"Symbole invalide pour le cache : {ticker!r}.")
        return self._dir / kind / f"{ticker}.json"

    def has(self, kind: str, ticker: str) -> bool:
        try:
            return self._path(kind, ticker).is_file()
        except ProviderError:
            return False

    def fetch(self, kind: DataKind, ticker: str, as_of: date) -> List[RawItem]:
        path = self._path(kind, ticker)
        if not path.is_file():
            raise ProviderError("not_cached", f"Aucune donnée en cache pour {kind}/{ticker}.")
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
            return [RawItem.model_validate({**r, "from_cache": True}) for r in rows]
        except (json.JSONDecodeError, ValidationError, TypeError) as e:
            raise ProviderError("cache_corrupt", f"Cache illisible ({path.name}) : {e}") from e

    def store(self, kind: str, ticker: str, items: List[RawItem]) -> None:
        path = self._path(kind, ticker)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [i.model_dump(mode="json", exclude={"from_cache"}) for i in items]
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


class CachingProvider:
    """Appelle le fournisseur en direct, mémorise le résultat, et retombe sur le cache si l'appel échoue.

    Les éléments servis par le cache sont marqués `from_cache` : la fiche source le consigne. Le filtre temporel
    s'applique de la même façon, donc un cache plus récent que `as_of` ne fuit pas dans la simulation.
    """

    def __init__(self, live: Provider, cache: CachedProvider):
        self._live = live
        self._cache = cache
        self.source_id = live.source_id

    def fetch(self, kind: DataKind, ticker: str, as_of: date) -> List[RawItem]:
        try:
            items = self._live.fetch(kind, ticker, as_of)
        except ProviderError as live_err:
            try:
                return self._cache.fetch(kind, ticker, as_of)
            except ProviderError:
                raise live_err
        self._cache.store(kind, ticker, items)
        return items


# ---- SEC EDGAR ---------------------------------------------------------------

HttpGet = Callable[[str, Dict[str, str]], bytes]

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/"
_MIN_INTERVAL_S = 0.15  # la SEC limite à 10 requêtes par seconde : on reste en dessous


def _urllib_get(url: str, headers: Dict[str, str]) -> bytes:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310  (URL fixée par ce module)
            return r.read()
    except urllib.error.HTTPError as e:
        code = "rate_limited" if e.code in (403, 429) else "unavailable"
        raise ProviderError(code, f"EDGAR a répondu HTTP {e.code} pour {url}.") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ProviderError("unavailable", f"EDGAR inaccessible : {e}.") from e


class EdgarProvider:
    """SEC EDGAR, source officielle (niveau A) :
    - `filings` : dépôts réglementaires américains, métadonnées seulement (formulaire, dates, lien vers le document) ;
    - `fundamentals` : chiffres du dernier rapport annuel déposé avant `as_of` (données XBRL « companyfacts » :
      chiffre d'affaires, bénéfice net, flux de trésorerie d'exploitation, trésorerie, dette à long terme, capitaux propres,
      actif total). Tous les chiffres viennent du même rapport ; un indicateur absent du rapport est omis, jamais estimé.

    La SEC exige un en-tête `User-Agent` qui identifie l'appelant (nom et courriel) et limite le débit : sans
    `user_agent`, le fournisseur refuse plutôt que d'appeler anonymement. SEDAR+ n'offre pas d'API publique documentée :
    les titres canadiens produisent une lacune `unsupported`, pas un contournement.
    """

    source_id = "sec_edgar"

    def __init__(self, user_agent: Optional[str], retrieved_on: date, http_get: Optional[HttpGet] = None):
        self._ua = user_agent
        self._retrieved_on = retrieved_on
        self._get = http_get or _urllib_get
        self._tickers: Optional[Dict[str, int]] = None  # liste des déclarants, téléchargée une fois par instance
        self._last_call = 0.0

    def _json(self, url: str):
        wait = _MIN_INTERVAL_S - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()
        try:
            return json.loads(self._get(url, {"User-Agent": self._ua or "", "Accept": "application/json"}))
        except json.JSONDecodeError as e:
            raise ProviderError("unavailable", f"Réponse EDGAR illisible pour {url}.") from e

    def _cik(self, ticker: str) -> int:
        if self._tickers is None:
            self._tickers = {str(r.get("ticker", "")).upper(): int(r["cik_str"]) for r in self._json(_TICKERS_URL).values()}
        cik = self._tickers.get(ticker.upper())
        if cik is None:
            raise ProviderError("not_found", f"{ticker} absent de la liste des déclarants EDGAR (FNB, fonds ou titre non déclarant).")
        return cik

    def fetch(self, kind: DataKind, ticker: str, as_of: date) -> List[RawItem]:
        if kind not in ("filings", "fundamentals"):
            raise ProviderError("unsupported", f"EDGAR ne fournit que des dépôts et des fondamentaux (demandé : {kind}).")
        if not self._ua:
            raise ProviderError("auth_required", "EDGAR exige un User-Agent identifiant l'appelant (nom et courriel).")
        if ticker.upper().endswith((".TO", ".V")):
            raise ProviderError("unsupported", f"{ticker} est coté au Canada : dépôts sur SEDAR+, sans API publique documentée.")
        if "-" in ticker:
            raise ProviderError("unsupported", f"{ticker} n'est pas une société déclarante.")
        cik = self._cik(ticker)
        if kind == "fundamentals":
            return self._fundamentals(ticker, cik, as_of)

        recent = self._json(_SUBMISSIONS_URL.format(cik=cik)).get("filings", {}).get("recent", {})
        items: List[RawItem] = []
        for form, filed, report, acc, doc in zip(
            recent.get("form", []), recent.get("filingDate", []), recent.get("reportDate", []),
            recent.get("accessionNumber", []), recent.get("primaryDocument", []),
        ):
            if form not in FILING_FORMS:
                continue
            published = date.fromisoformat(filed)
            if published > as_of:
                continue
            observed = date.fromisoformat(report) if report else published
            items.append(RawItem(
                ticker=ticker, kind="filings", source_id=self.source_id,
                url=_DOC_URL.format(cik=cik, acc=acc.replace("-", ""), doc=doc),
                title=f"{form} déposé le {filed}",
                date_publication=published, date_observation=min(observed, published), retrieved_on=self._retrieved_on,
                structured={"form": form, "accession_number": acc, "filing_date": filed, "report_date": report or None, "cik": cik},
            ))
            if len(items) >= MAX_FILINGS:
                break
        return items

    def _fundamentals(self, ticker: str, cik: int, as_of: date) -> List[RawItem]:
        facts = self._json(_FACTS_URL.format(cik=cik)).get("facts", {}).get("us-gaap", {})

        def usd(concept: str) -> List[dict]:
            return [f for f in facts.get(concept, {}).get("units", {}).get("USD", [])
                    if f.get("form") in ANNUAL_FORMS and f.get("filed") and date.fromisoformat(f["filed"]) <= as_of]

        # Le rapport annuel de référence : le plus récent déposé avant as_of, parmi ceux qui portent au moins un indicateur.
        candidates = [f for spec in FUNDAMENTAL_CONCEPTS.values() for c in spec["concepts"] for f in usd(c)]  # type: ignore[union-attr]
        if not candidates:
            raise ProviderError("no_data_before_as_of", f"Aucun rapport annuel XBRL de {ticker} déposé avant le {as_of.isoformat()}.")
        ref = max(candidates, key=lambda f: (f["filed"], f.get("end", "")))
        accn, filed, form = ref["accn"], ref["filed"], ref["form"]

        def pick(spec: dict) -> Optional[dict]:
            for concept in spec["concepts"]:
                rows = [f for f in usd(concept) if f["accn"] == accn]
                if spec["period"] == "duration":
                    rows = [f for f in rows if f.get("start")
                            and ANNUAL_DAYS[0] <= (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days <= ANNUAL_DAYS[1]]
                else:
                    rows = [f for f in rows if not f.get("start")]
                if rows:
                    best = max(rows, key=lambda f: f["end"])  # l'exercice du rapport, pas les années comparatives
                    return {"value": best["val"], "concept": concept, "period_end": best["end"]}
            return None

        metrics, concepts, ends = {}, {}, set()
        for name, spec in FUNDAMENTAL_CONCEPTS.items():
            got = pick(spec)
            if got:
                metrics[name], concepts[name] = got["value"], got["concept"]
                ends.add(got["period_end"])
        if not metrics:
            raise ProviderError("no_matching_data", f"Le rapport {form} de {ticker} déposé le {filed} ne porte aucun des indicateurs suivis.")
        period_end = max(ends)
        return [RawItem(
            ticker=ticker, kind="fundamentals", source_id=self.source_id,
            url=_INDEX_URL.format(cik=cik, acc=accn.replace("-", "")),
            title=f"{form} déposé le {filed}, exercice clos le {period_end}",
            date_publication=date.fromisoformat(filed), date_observation=min(date.fromisoformat(period_end), date.fromisoformat(filed)),
            retrieved_on=self._retrieved_on,
            structured={"form": form, "accession_number": accn, "filing_date": filed, "period_end": period_end, "cik": cik,
                        "currency": "USD", "metrics": metrics, "xbrl_concepts": concepts},
        )]
