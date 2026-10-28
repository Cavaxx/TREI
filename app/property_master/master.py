"""The Property Master store (§20-22, §32).

Channels deposit observations here; the store answers the identity questions.
It is deliberately storage-agnostic: an identical class will back the Postgres
implementation, so callers never change when the repository is swapped.

Stability of ``property_master_id`` matters. The clustering engine returns a group
per connected component, but an id derived from the component's membership would
change whenever a portal adds a listing. The store therefore keeps a persistent
map from listing to master and only mints an id for a component that contains no
previously-known member (§20: "do not create a new property each time").
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.enums import ListingEvent, ObservationSource
from app.core.lineage import make_lineage
from app.processors.dedup import (
    DEFAULT_THRESHOLD,
    block_by_comune,
    deduplicate,
    reconstruct_episodes,
)
from app.property_master.coverage import SourceCoverage, coverage_report
from app.property_master.history import listing_intelligence, price_history
from app.property_master.models import (
    ListingRecord,
    ListingSnapshot,
    TerritoryAssessment,
    content_hashes,
    listing_key,
)


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _mint_master_id(basis: str) -> str:
    return "P" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


class PropertyMasterStore:
    """In-memory Property Master (the Postgres implementation mirrors this API)."""

    def __init__(
        self,
        *,
        dedup_threshold: float = DEFAULT_THRESHOLD,
        review_threshold: float | None = None,
    ) -> None:
        self._listings: dict[str, ListingRecord] = {}
        self._snapshots: dict[str, list[ListingSnapshot]] = defaultdict(list)
        self._events: list[dict] = []
        self._dedup_threshold = dedup_threshold
        self._review_threshold = (
            review_threshold
            if review_threshold is not None
            else get_settings().review_confidence_threshold
        )

    # --- ingestion ----------------------------------------------------------
    def observe(
        self,
        payload: dict,
        *,
        source: str,
        source_listing_id: str,
        url: str = "",
        observed_at: str | None = None,
        channel: str = ObservationSource.AGENT_IMPORT.value,
        availability: str = "active",
        raw_object_id: str = "",
        territory: TerritoryAssessment | None = None,
        geocoding: dict | None = None,
        review_fields: list[str] | None = None,
    ) -> ListingRecord:
        """Record one observation of one listing on one source.

        Idempotent for identical content: re-observing an unchanged listing bumps
        ``last_seen_at`` instead of adding a redundant snapshot.
        """
        if not source_listing_id:
            raise ValueError("source_listing_id is required: a listing without an id is not traceable")
        observed_at = observed_at or _now_iso()
        key = listing_key(source, source_listing_id)
        desc_hash, images_hash = content_hashes(payload)
        record = self._listings.get(key)

        if record is None:
            record = ListingRecord(
                listing_source_id=source_listing_id,
                source=source,
                url=url,
                first_seen_at=observed_at,
                last_seen_at=observed_at,
                first_seen_source=channel,
            )
            self._listings[key] = record
            self._events.append(
                {
                    "listing_key": key,
                    "listing_source_id": source_listing_id,
                    "timestamp": observed_at,
                    "event": ListingEvent.LISTING_CREATED.value,
                }
            )
        else:
            self._record_transition(record, key, observed_at, availability, payload)

        record.last_seen_at = observed_at
        record.is_active = availability != "removed"
        record.price = payload.get("price")
        record.surface_m2 = payload.get("surface_m2")
        record.description_hash = desc_hash
        record.images_hash = images_hash
        record.payload = dict(payload)
        record.payload.setdefault("source", source)
        record.payload.setdefault("source_listing_id", source_listing_id)
        if url:
            record.url = url
        if raw_object_id:
            record.raw_object_id = raw_object_id
        if territory is not None:
            record.territory = territory
        if geocoding:
            record.geocoding_source = geocoding.get("source", "")
            record.geocoded_at = geocoding.get("geocoded_at")
            precision = geocoding.get("precision")
            if precision is not None:
                record.geocoding_precision = precision
        if review_fields:
            record.review_required = True
            record.review_fields = sorted(set(record.review_fields) | set(review_fields))
        if channel == ObservationSource.EMAIL_ALERT.value:
            record.email_received_at = record.email_received_at or observed_at
        record.source_published_at = record.source_published_at or payload.get("published_at")
        email_ts = payload.get("email_received_at")
        if email_ts:
            record.email_received_at = record.email_received_at or email_ts

        self._append_snapshot(key, record, observed_at, availability, channel, raw_object_id)
        return record

    def _record_transition(
        self,
        record: ListingRecord,
        key: str,
        observed_at: str,
        availability: str,
        payload: dict,
    ) -> None:
        """Emit lifecycle events for a *changed* observation (§18/§21)."""
        if availability == "removed" and record.is_active:
            self._emit(key, record, observed_at, ListingEvent.LISTING_REMOVED)
            return
        if availability != "removed" and not record.is_active:
            self._emit(key, record, observed_at, ListingEvent.LISTING_RELISTED)
        price = payload.get("price")
        if price is not None and record.price is not None and price != record.price:
            self._emit(key, record, observed_at, ListingEvent.PRICE_CHANGED)

    def _emit(self, key: str, record: ListingRecord, observed_at: str, event: ListingEvent) -> None:
        self._events.append(
            {
                "listing_key": key,
                "listing_source_id": record.listing_source_id,
                "timestamp": observed_at,
                "event": event.value,
            }
        )

    def _append_snapshot(
        self,
        key: str,
        record: ListingRecord,
        observed_at: str,
        availability: str,
        channel: str,
        raw_object_id: str,
    ) -> None:
        """Snapshot only when something changed - re-observation is not new data."""
        existing = self._snapshots[key]
        if existing:
            last = existing[-1]
            unchanged = (
                last.price == record.price
                and last.surface_m2 == record.surface_m2
                and last.description_hash == record.description_hash
                and last.images_hash == record.images_hash
                and last.availability == availability
            )
            if unchanged:
                return
        existing.append(
            ListingSnapshot(
                captured_at=observed_at,
                listing_source_id=record.listing_source_id,
                source=record.source,
                price=record.price,
                surface_m2=record.surface_m2,
                description_hash=record.description_hash,
                images_hash=record.images_hash,
                availability=availability,
                source_url=record.url,
                raw_object_id=raw_object_id,
                observed_via=channel,
            )
        )

    # --- entity resolution (§20-21) ----------------------------------------
    def resolve(self) -> None:
        """(Re)cluster listings into physical units and rebuild episode ids.

        Master ids are stable: a component that contains a known member keeps the
        id it already had, so adding a portal listing does not orphan the history.
        """
        self._assign_master_ids()
        self._reconstruct_episodes()

    def _assign_master_ids(self) -> None:
        payloads = [
            {**rec.payload, "listing_key": key, "listing_source_id": rec.listing_source_id}
            for key, rec in self._listings.items()
        ]
        proposals: dict[str, list[str]] = defaultdict(list)
        for block in block_by_comune(payloads).values():
            if len(block) < 2:
                continue
            for enriched in deduplicate(
                block, key_field="listing_key", threshold=self._dedup_threshold
            ):
                proposals[enriched["property_master_id"]].append(enriched["listing_key"])

        # Reuse an existing master id when a component already contains one.
        known: dict[str, str] = {
            rec.key: rec.property_master_id
            for rec in self._listings.values()
            if rec.property_master_id
        }
        assigned: set[str] = set()
        for member_keys in proposals.values():
            existing = sorted({known[k] for k in member_keys if known.get(k)})
            master_id = existing[0] if existing else _mint_master_id("|".join(sorted(member_keys)))
            for key in member_keys:
                self._listings[key].property_master_id = master_id
                assigned.add(key)

        # Singletons are still properties: they get their own stable master id.
        for key, rec in self._listings.items():
            if key in assigned:
                continue
            rec.property_master_id = known.get(key) or _mint_master_id(key)

        for rec in self._listings.values():
            rec.sources = sorted(
                {
                    other.source
                    for other in self._listings.values()
                    if other.property_master_id and other.property_master_id == rec.property_master_id
                }
            )

    def _reconstruct_episodes(self) -> None:
        by_key = {rec.key: rec for rec in self._listings.values()}
        events = []
        for ev in self._events:
            rec = by_key.get(ev["listing_key"])
            if rec is None or not rec.property_master_id:
                continue
            events.append({**ev, "property_master_id": rec.property_master_id})
        for enriched in reconstruct_episodes(events):
            record = by_key.get(enriched["listing_key"])
            if record is not None:
                record.listing_episode_id = enriched.get("listing_episode_id")

    # --- queries ------------------------------------------------------------
    def listings(self, *, active_only: bool = False) -> list[ListingRecord]:
        rows = [r for r in self._listings.values() if not active_only or r.is_active]
        return sorted(rows, key=lambda r: (r.first_seen_at or "", r.key))

    def get(self, source: str, source_listing_id: str) -> ListingRecord | None:
        return self._listings.get(listing_key(source, source_listing_id))

    def get_by_key(self, key: str) -> ListingRecord | None:
        return self._listings.get(key)

    def snapshots(self, key: str) -> list[ListingSnapshot]:
        return list(self._snapshots.get(key, []))

    def master(self, property_master_id: str) -> list[ListingRecord]:
        return [r for r in self.listings() if r.property_master_id == property_master_id]

    def price_history(self, key: str) -> list[dict]:
        return [e.to_dict() for e in price_history(self.snapshots(key))]

    def intelligence(self, key: str, *, now: datetime | None = None) -> dict | None:
        record = self._listings.get(key)
        if record is None:
            return None
        return listing_intelligence(record, self.snapshots(key), now=now).to_dict()

    def coverage(self) -> SourceCoverage:
        return coverage_report(self.listings())

    def review_queue(self) -> list[dict]:
        """Listings whose extraction fell below the confidence threshold (§31)."""
        rows = []
        for record in self.listings():
            if not record.review_required:
                continue
            rows.append(
                {
                    "listing_key": record.key,
                    "source": record.source,
                    "source_listing_id": record.listing_source_id,
                    "url": record.url,
                    "property_master_id": record.property_master_id,
                    "review_fields": record.review_fields,
                    "first_seen_at": record.first_seen_at,
                    "in_territory": record.territory.in_territory,
                    "raw_object_id": record.raw_object_id,
                }
            )
        return rows

    def summary(self) -> dict:
        listings = self.listings()
        coverage = self.coverage()
        return {
            "unique_property_master": coverage.unique_property_master,
            "raw_listings": coverage.raw_listings,
            "active_listings": sum(1 for r in listings if r.is_active),
            "sources": coverage.sources,
            "review_required": sum(1 for r in listings if r.review_required),
            "coverage_confidence": round(coverage.coverage_confidence, 3),
        }

    def lineage_for(self, key: str) -> dict | None:
        record = self._listings.get(key)
        if record is None:
            return None
        return make_lineage(
            record.source,
            source_url=record.url,
            source_listing_id=record.listing_source_id,
            published_at=record.source_published_at,
            last_seen_at=record.last_seen_at,
            raw_object_id=record.raw_object_id or None,
        ).to_dict()
