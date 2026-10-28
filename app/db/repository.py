"""In-memory market repository.

Stands in for the Postgres/PostGIS store during the MVP and in tests. It speaks
the same vocabulary as the SQL schema (property_master_id, price_measure_type,
...) so swapping in a SQL implementation later does not change callers. It is
NOT a cache-as-database: in production the authoritative store is PostgreSQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.versions import MODEL_VERSION
from app.market_engine.prices import aggregate_price_per_m2
from app.models.hedonic import HedonicModel
from app.models.valuation import value_property


@dataclass
class MarketRepository:
    listings: list[dict] = field(default_factory=list)
    auctions: list[dict] = field(default_factory=list)
    _model: HedonicModel | None = None

    # --- listings -----------------------------------------------------------
    def add_listings(self, listings: list[dict]) -> None:
        self.listings.extend(listings)
        self._model = None  # invalidate cached model

    def add_auctions(self, auctions: list[dict]) -> None:
        self.auctions.extend(auctions)

    def filter_listings(
        self,
        comune: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        min_surface: float | None = None,
        max_surface: float | None = None,
        measure_type: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        out = []
        for lst in self.listings:
            if comune and lst.get("comune") != comune:
                continue
            if min_price is not None and (lst.get("price") or 0) < min_price:
                continue
            if max_price is not None and (lst.get("price") or 0) > max_price:
                continue
            if min_surface is not None and (lst.get("surface_m2") or 0) < min_surface:
                continue
            if max_surface is not None and (lst.get("surface_m2") or 0) > max_surface:
                continue
            if measure_type and lst.get("measure_type") != measure_type:
                continue
            out.append(lst)
        return out[:limit] if limit else out

    def get_listing(self, listing_source_id: str) -> dict | None:
        for lst in self.listings:
            if lst.get("listing_source_id") == listing_source_id:
                return lst
        return None

    def comuni(self) -> list[str]:
        return sorted({lst["comune"] for lst in self.listings if lst.get("comune")})

    # --- aggregates ---------------------------------------------------------
    def price_per_m2(self, comune: str | None = None) -> dict:
        rows = self.filter_listings(comune=comune, measure_type="asking")
        return aggregate_price_per_m2([r["price_m2"] for r in rows if r.get("price_m2")])

    def summary_by_comune(self) -> list[dict]:
        out = []
        for comune in self.comuni():
            stats = self.price_per_m2(comune=comune)
            out.append({"comune": comune, **stats})
        return out

    # --- model & valuation --------------------------------------------------
    def model(self, force: bool = False) -> HedonicModel:
        if self._model is None or force:
            rows = [r for r in self.listings if r.get("price_m2")]
            self._model = HedonicModel().fit(rows, target="price")
        return self._model

    def value(self, subject: dict) -> dict:
        pool = self.filter_listings(comune=subject.get("comune")) or self.listings
        model = None
        try:
            model = self.model()
        except Exception:  # noqa: BLE001 - fall back to comparables-only
            model = None
        valuation = value_property(subject, pool, model=model)
        return valuation.to_dict()

    @property
    def model_version(self) -> str:
        return MODEL_VERSION
