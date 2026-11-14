"""DataStore : accès en lecture seule aux données, filtré par as_of, avec empreinte."""
import hashlib
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Union

import pandas as pd

from .config import DEFAULT_DATA_DIR, QuantConfig

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

        self._prices = prices
        self._px: Dict[str, pd.DataFrame] = {
            t: g.drop(columns=["ticker"]).set_index("date") for t, g in prices.groupby("ticker")
        }
        self._fx = fx.set_index("date")["usdcad"].astype(float)
        self._actions = actions

        parts = [_hash_frame(self.assets), _hash_frame(prices), _hash_frame(fx), _hash_frame(actions)]
        self.fingerprint = hashlib.sha256("|".join(parts).encode()).hexdigest()
        self.last_date: date = prices["date"].max().date()
        self.first_date: date = prices["date"].min().date()

    @classmethod
    def from_directory(cls, path: Union[str, Path, None] = None, config: Optional[QuantConfig] = None) -> "DataStore":
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
        )

    # ---- métadonnées -------------------------------------------------
    def source_label(self, filename: str) -> str:
        return self._sources.get(filename, filename)

    def asset(self, query: str) -> Optional[dict]:
        """Retrouve un actif par ticker ou asset_id, sans tenir compte de la casse."""
        q = str(query).strip().upper()
        a = self.assets
        hit = a[(a["ticker"].str.upper() == q) | (a["asset_id"].str.upper() == q)]
        return None if hit.empty else hit.iloc[0].to_dict()

    def suggest(self, query: str, n: int = 3) -> List[str]:
        import difflib

        return difflib.get_close_matches(str(query).upper(), list(self.assets["ticker"]), n=n, cutoff=0.5)

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
