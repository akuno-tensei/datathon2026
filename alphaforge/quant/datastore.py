"""DataStore : accès en lecture seule aux données, filtré par as_of, avec empreinte."""
import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import pandas as pd

from .config import DEFAULT_DATA_DIR, QuantConfig
from .models import InputRef

_PRICE_COLS = ["date", "ticker", "open", "high", "low", "close", "adj_close", "volume", "currency"]


def _hash_frame(df: pd.DataFrame) -> str:
    h = hashlib.sha256()
    h.update(",".join(map(str, df.columns)).encode())
    h.update(pd.util.hash_pandas_object(df.reset_index(drop=True), index=False).values.tobytes())
    return h.hexdigest()


class DataStore:
    def __init__(
        self,
        assets: pd.DataFrame,
        prices: pd.DataFrame,
        fx: pd.DataFrame,
        actions: Optional[pd.DataFrame] = None,
        config: Optional[QuantConfig] = None,
        sources: Optional[Dict[str, str]] = None,
        macro: Optional[pd.DataFrame] = None,
        series_dir: Optional[Union[str, Path]] = None,
    ):
        self.config = config or QuantConfig()
        self._sources = sources or {}

        self.assets = assets.copy().reset_index(drop=True)
        prices = prices.copy()
        prices["date"] = pd.to_datetime(prices["date"])
        if prices.duplicated(["date", "ticker"]).any():
            raise ValueError("prices: doublons (date, ticker).")
        prices = prices.sort_values(["ticker", "date"]).reset_index(drop=True)
        unknown = set(prices["ticker"]) - set(self.assets["ticker"])
        if unknown:
            raise ValueError(f"prices: tickers absents de l'univers: {sorted(unknown)}")

        fx = fx.copy()
        fx["date"] = pd.to_datetime(fx["date"])
        fx = fx.sort_values("date").reset_index(drop=True)
        if fx["date"].duplicated().any():
            raise ValueError("fx: dates en double.")

        if actions is None:
            actions = pd.DataFrame(columns=["ex_date", "ticker", "action", "value", "unit"])
        actions = actions.copy()
        actions["ex_date"] = pd.to_datetime(actions["ex_date"])
        actions = actions.sort_values(["ticker", "ex_date"]).reset_index(drop=True)

        if macro is not None:
            macro = macro.copy()
            macro["date"] = pd.to_datetime(macro["date"])
            macro = macro.sort_values(["series_id", "date"]).reset_index(drop=True)
        self._macro = macro
        self._prices = prices
        self._px: Dict[str, pd.DataFrame] = {
            t: g.drop(columns=["ticker"]).set_index("date") for t, g in prices.groupby("ticker")
        }
        self._fx = fx.set_index("date")["usdcad"].astype(float)
        self._actions = actions

        parts = [_hash_frame(self.assets), _hash_frame(prices), _hash_frame(fx), _hash_frame(actions)]
        if macro is not None:
            parts.append(_hash_frame(macro))
        self.fingerprint = hashlib.sha256("|".join(parts).encode()).hexdigest()
        from .series_store import SeriesStore

        self.series = SeriesStore(path=series_dir)
        self.last_date: date = prices["date"].max().date()
        self.first_date: date = prices["date"].min().date()

    @classmethod
    def from_directory(
        cls, path: Union[str, Path, None] = None, config: Optional[QuantConfig] = None,
        series_dir: Union[str, Path, None] = None,
    ) -> "DataStore":
        d = Path(path) if path else DEFAULT_DATA_DIR
        sources: Dict[str, str] = {}
        meta = d / "sources_metadata.csv"
        if meta.exists():
            for _, r in pd.read_csv(meta).iterrows():
                sources.setdefault(
                    str(r["file"]), f"{r['file']} ({r['source']}; retrieved {r['retrieved_on']})"
                )
        return cls(
            assets=pd.read_csv(d / "assets_universe.csv"),
            prices=pd.read_csv(d / "prices_daily.csv"),
            fx=pd.read_csv(d / "fx_usdcad_daily.csv"),
            actions=pd.read_csv(d / "corporate_actions.csv"),
            config=config,
            sources=sources,
            macro=pd.read_csv(d / "macro_rates.csv") if (d / "macro_rates.csv").exists() else None,
            series_dir=series_dir,
        )

    # ---- métadonnées -------------------------------------------------
    def source_label(self, filename: str) -> str:
        return self._sources.get(filename, filename)

    def resolve(self, query: str) -> Tuple[str, List[dict]]:
        """Recherche d'un actif par symbole ou nom libre, sans jamais compléter de mémoire.

        Retourne (type de correspondance, candidats). Les niveaux sont essayés dans l'ordre et le premier qui
        produit des candidats gagne : ticker ou asset_id exact, racine du ticker (SHOP pour SHOP.TO),
        nom complet, premier mot du nom (Apple, Shopify, Bitcoin). Plusieurs candidats au même niveau :
        on les retourne tous et l'appelant tranche. Aucun candidat : ("none", []).
        """
        q = str(query).strip().upper()
        if not q:
            return "none", []
        a = self.assets
        rows = [r.to_dict() for _, r in a.iterrows()]

        def root(t: str) -> str:
            return re.split(r"[.\-]", t)[0].upper()

        def norm(name: str) -> str:
            return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", name)).strip().upper()

        levels = (
            ("ticker", lambda r: r["ticker"].upper() == q or str(r["asset_id"]).upper() == q),
            ("ticker_root", lambda r: root(r["ticker"]) == q),
            ("name", lambda r: norm(r["name"]) == norm(q)),
            ("name_first_word", lambda r: norm(r["name"]).split(" ")[0] == norm(q)),
        )
        for kind, test in levels:
            hits = [r for r in rows if test(r)]
            if hits:
                if kind == "ticker" and q == root(q):
                    # Symbole nu (« SHOP ») : toute cote partageant cette racine est candidate (SHOP et SHOP.TO).
                    extra = [r for r in rows if root(r["ticker"]) == q and r not in hits]
                    hits = hits + extra
                return kind, hits
        return "none", []

    def asset(self, query: str) -> Optional[dict]:
        """Actif unique pour une requête, sinon None (hors univers ou ambigu)."""
        _, hits = self.resolve(query)
        return hits[0] if len(hits) == 1 else None

    def suggest(self, query: str, n: int = 3) -> List[str]:
        """Les tickers les plus proches (par symbole ou par nom), pour aider à corriger une requête."""
        import difflib

        q = str(query).strip().upper()
        scored = {}
        for _, r in self.assets.iterrows():
            for cand in (r["ticker"].upper(), re.split(r"[.\-]", r["ticker"])[0].upper(), str(r["name"]).upper(), str(r["name"]).split(" ")[0].upper()):
                ratio = difflib.SequenceMatcher(None, q, cand).ratio()
                if len(cand) >= 4 and cand in q.split():
                    ratio = max(ratio, 0.85)  # le nom (ou son premier mot) figure dans la requête
                scored[r["ticker"]] = max(scored.get(r["ticker"], 0.0), ratio)
        ranked = sorted(((v, t) for t, v in scored.items() if v >= 0.6), reverse=True)
        return [t for _, t in ranked[:n]]

    def risk_free_rate(self, currency: str, as_of: date) -> Optional[pd.Series]:
        """Taux directeur au jour le jour (fraction annuelle), connu à chaque date ≤ as_of.
        CAD : cible du taux à un jour de la Banque du Canada (V39079). USD : taux effectif des fonds fédéraux (DFF)."""
        if self._macro is None:
            return None
        sid = {"CAD": "V39079", "USD": "DFF"}.get(currency.upper())
        m = self._macro
        m = m[(m["series_id"] == sid) & (m["date"] <= pd.Timestamp(as_of))]
        return None if m.empty else (m.set_index("date")["value"].astype(float) / 100.0).rename(sid)

    # ---- empreintes des données lues (champ `inputs` des preuves) ----------
    def input_ref(self, file: str, symbol: str, as_of: date) -> InputRef:
        if file == "prices_daily.csv":
            df = self.price_frame(symbol, as_of).reset_index()
        elif file == "fx_usdcad_daily.csv":
            df = self.fx_series(as_of).reset_index()
        elif file == "corporate_actions.csv":
            df = self.corporate_actions(symbol, as_of)
        elif file == "macro_rates.csv":
            m = self._macro
            df = m[(m["series_id"] == symbol) & (m["date"] <= pd.Timestamp(as_of))] if m is not None else pd.DataFrame()
        elif file == "assets_universe.csv":
            df = self.assets[self.assets["ticker"] == symbol]
        else:
            raise ValueError(f"fichier inconnu: {file}")
        return InputRef(file=file, symbol=symbol, sha256=_hash_frame(df))

    # ---- séries (toujours coupées à as_of) ---------------------------
    def price_frame(self, ticker: str, as_of: date) -> pd.DataFrame:
        df = self._px.get(ticker)
        if df is None:
            return pd.DataFrame(columns=_PRICE_COLS[2:]).set_index(pd.DatetimeIndex([], name="date"))
        return df.loc[df.index <= pd.Timestamp(as_of)]

    def fx_series(self, as_of: date) -> pd.Series:
        s = self._fx
        return s.loc[s.index <= pd.Timestamp(as_of)]

    def corporate_actions(self, ticker: str, as_of: date) -> pd.DataFrame:
        a = self._actions
        return a[(a["ticker"] == ticker) & (a["ex_date"] <= pd.Timestamp(as_of))]

    def peer_dates(self, ticker: str, as_of: date) -> pd.DatetimeIndex:
        """Dates de cotation de référence : union des dates des actifs du même groupe de marché."""
        me = self.asset(ticker)
        group = self._market_group(me)
        dates = pd.DatetimeIndex([])
        for _, r in self.assets.iterrows():
            if self._market_group(r.to_dict()) == group:
                dates = dates.union(self.price_frame(r["ticker"], as_of).index)
        return dates

    @staticmethod
    def _market_group(asset: dict) -> str:
        if asset["asset_class"] == "crypto":
            return "crypto"
        return "CA" if asset["currency"] == "CAD" else "US"
