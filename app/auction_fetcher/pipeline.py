"""Auction pipeline: fetch -> parse -> normalize -> deduplicate (§33, §35).

A lot appears on PVP, is re-listed on Astalegale, and appears again on an auction
aggregator. All three must collapse to one ``auction_master_id``; the strong key
is (tribunale, numero procedura, lotto), with PVP winning the biographical fields.

The pipeline also reads a **licensed PVP export** from a drop directory, which is
the supported route while direct PVP access is unverified (§STEP 5).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.auction_engine.dedup import deduplicate_auctions
from app.auction_fetcher.models import (
    AUCTION_FIELDS,
    AUCTION_OPTIONAL_FIELDS,
    AuctionIngestReport,
    AuctionRecord,
)
from app.auction_fetcher.parser import AuctionParser
from app.core.fields import ExtractionBundle
from app.processors.territory import assess_territory
from app.storage.datalake import DataLake

#: Registry source name -> dedup authority key.
AUTHORITY_ALIASES = {
    "pvp_giustizia": "pvp",
    "astalegale_net": "astalegale",
    "fallco_aste": "fallco",
    "asteannunci_it": "asteannunci",
}

#: Courts that serve the Autonomous Province of Trento.
TRENTINO_COURTS = {"trento", "rovereto"}


class AuctionPipeline:
    """Deterministic auction ingestion, with deduplication at the end."""

    def __init__(
        self,
        *,
        parser: AuctionParser | None = None,
        datalake: DataLake | None = None,
    ) -> None:
        self.parser = parser or AuctionParser()
        self.datalake = datalake or DataLake()

    # --- ingestion ---------------------------------------------------------
    def ingest_feed_items(self, items, source: str) -> AuctionIngestReport:
        report = AuctionIngestReport(source=source)
        records: list[AuctionRecord] = []
        for item in items:
            report.items_seen += 1
            try:
                records.append(self.parser.parse_feed_item(item))
            except Exception as exc:  # noqa: BLE001 - one bad item must not stop the feed
                report.errors.append(f"{getattr(item, 'guid', '')}: {exc}")
        return self._finish(records, report)

    def ingest_raw_records(self, raws: list[dict], source: str) -> AuctionIngestReport:
        """Ingest already-structured records (e.g. a licensed PVP export)."""
        report = AuctionIngestReport(source=source)
        records: list[AuctionRecord] = []
        for raw in raws:
            report.items_seen += 1
            records.append(self._record_from_mapping(raw, source))
        return self._finish(records, report)

    def read_pvp_export(
        self, directory: Path | str, source: str = "pvp_giustizia"
    ) -> AuctionIngestReport:
        """Read a licensed PVP export from a directory (JSON / JSONL)."""
        path = Path(directory)
        raws: list[dict] = []
        for file in sorted(path.glob("*")):
            if file.suffix.lower() not in {".json", ".jsonl", ".ndjson"}:
                continue
            text = file.read_text(encoding="utf-8")
            if file.suffix.lower() == ".json":
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    continue
                raws.extend(data if isinstance(data, list) else [data])
            else:
                for line in text.splitlines():
                    line = line.strip()
                    if line:
                        try:
                            raws.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        return self.ingest_raw_records(raws, source)

    # --- internals ---------------------------------------------------------
    def _record_from_mapping(self, raw: dict, source: str) -> AuctionRecord:
        """Build a record from an already-structured export row."""
        bundle = ExtractionBundle()
        for name, value in raw.items():
            if value in (None, "", []):
                continue
            bundle.set(
                name,
                value,
                method="structured_feed",
                confidence=0.99,
                evidence="licensed export field",
            )
        if not bundle.value("tribunal") and not bundle.value("address"):
            bundle = self.parser.parse_text(json.dumps(raw, ensure_ascii=False), source=source)
        return AuctionRecord(
            source=source,
            fields=bundle,
            auction_url=str(raw.get("auction_url") or raw.get("url") or ""),
            published_at=str(raw.get("published_at") or ""),
            pvp_id=str(raw.get("pvp_id") or ""),
        )

    def _finish(
        self, records: list[AuctionRecord], report: AuctionIngestReport
    ) -> AuctionIngestReport:
        in_territory: list[AuctionRecord] = []
        for record in records:
            if self._in_territory(record):
                in_territory.append(record)
            else:
                report.out_of_territory += 1

        payloads = [self._to_dedup_payload(record) for record in in_territory]
        before = len(payloads)
        merged = deduplicate_auctions(payloads) if payloads else []
        report.duplicates = max(0, before - len(merged))
        report.records = len(merged)
        report.auctions = merged
        return report

    @staticmethod
    def _in_territory(record: AuctionRecord) -> bool:
        tribunal = str(record.value("tribunal") or "").strip().lower()
        if tribunal in TRENTINO_COURTS:
            return True
        assessment = assess_territory(
            municipality=record.value("municipality"),
            address=record.value("address"),
            hints=str(record.value("municipality") or ""),
        )
        return assessment.in_territory

    @staticmethod
    def _to_dedup_payload(record: AuctionRecord) -> dict:
        """Auction-shaped payload (the listing field set does not apply here)."""
        flat = record.fields.flatten()
        payload: dict = {
            name: flat.get(name) for name in AUCTION_FIELDS + AUCTION_OPTIONAL_FIELDS
        }
        payload["source"] = AUTHORITY_ALIASES.get(record.source, "other")
        payload["observed_source"] = record.source
        payload["pvp_id"] = record.pvp_id or flat.get("pvp_id")
        payload["auction_url"] = record.auction_url
        payload["published_at"] = record.published_at
        return payload
