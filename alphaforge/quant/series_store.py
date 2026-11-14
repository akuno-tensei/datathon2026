"""Magasin de séries : les outils y déposent leurs séries et ne renvoient qu'une référence (series_id).

Les séries ne passent jamais par le LLM. Un `series_id` est déterministe (recette + empreinte des lignes lues,
jusqu'à as_of) : le même appel redonne le même identifiant, et la recette permet de recalculer la série.

Par défaut, tout reste en mémoire. Avec `path`, chaque série est aussi écrite en Parquet (`<series_id>.parquet`,
métadonnées dans `<series_id>.json`) et relue à la demande, y compris après un redémarrage. Même interface
(`put`, `get`) pour un futur backend S3. Nécessite `pyarrow`, importé seulement si `path` est fourni.
"""
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, Optional, Union

import pandas as pd

from .models import ToolInputError
from .series import PriceSeries


@dataclass
class StoredSeries:
    series_id: str
    series: PriceSeries
    recipe: dict
    parent: Optional[str] = None


@dataclass
class SeriesStore:
    path: Optional[Union[str, Path]] = None
    _items: Dict[str, StoredSeries] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.path is not None:
            self.path = Path(self.path)
            self.path.mkdir(parents=True, exist_ok=True)

    def put(self, series: PriceSeries, recipe: dict, data_sha: str, parent: Optional[str] = None) -> str:
        payload = json.dumps({"recipe": recipe, "data": data_sha}, sort_keys=True, default=str)
        sid = "sr_" + hashlib.sha256(payload.encode()).hexdigest()[:12]
        if sid not in self._items:
            self._items[sid] = StoredSeries(sid, series, recipe, parent)
            if self.path is not None:
                self._write(self._items[sid])
        return sid

    def get(self, series_id: str) -> StoredSeries:
        item = self._items.get(series_id)
        if item is None and self.path is not None:
            item = self._read(series_id)
            if item is not None:
                self._items[series_id] = item
        if item is None:
            raise ToolInputError(
                "unknown_series",
                f"series_id inconnu: {series_id!r}. Relancer l'outil qui l'a produite"
                + ("." if self.path is not None else " (les séries vivent en mémoire pour la durée de la session)."),
            )
        return item

    def __contains__(self, series_id: str) -> bool:
        try:
            self.get(series_id)
            return True
        except ToolInputError:
            return False

    # ---- Parquet -----------------------------------------------------
    def _write(self, it: StoredSeries) -> None:
        s = it.series
        df = pd.DataFrame({"date": s.prices.index, "price": s.prices.values})
        if s.fx_rates is not None:
            df["fx_rate"] = s.fx_rates.reindex(s.prices.index).values
        df.to_parquet(self.path / f"{it.series_id}.parquet", index=False)
        meta = {
            "series_id": it.series_id, "ticker": s.ticker, "currency": s.currency, "field": s.field,
            "converted_from": s.converted_from, "fx_filled_days": s.fx_filled_days, "fx_dropped_days": s.fx_dropped_days,
            "recipe": it.recipe, "parent": it.parent,
        }
        (self.path / f"{it.series_id}.json").write_text(json.dumps(meta, ensure_ascii=False, sort_keys=True, default=str))

    def _read(self, series_id: str) -> Optional[StoredSeries]:
        pq, js = self.path / f"{series_id}.parquet", self.path / f"{series_id}.json"
        if not (pq.exists() and js.exists()):
            return None
        df, meta = pd.read_parquet(pq), json.loads(js.read_text())
        idx = pd.DatetimeIndex(df["date"], name="date")
        prices = pd.Series(df["price"].values, index=idx, name=meta["ticker"])
        rates = pd.Series(df["fx_rate"].values, index=idx, name="usdcad") if "fx_rate" in df else None
        ps = PriceSeries(meta["ticker"], meta["currency"], prices, meta["field"], meta["converted_from"],
                         meta["fx_filled_days"], meta["fx_dropped_days"], rates)
        return StoredSeries(series_id, ps, meta["recipe"], meta["parent"])
