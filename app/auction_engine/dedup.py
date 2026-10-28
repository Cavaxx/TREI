"""Auction lot identity resolution (section 3).

PVP is the source of truth for procedure id, lot, auction date and documents.
Astalegale / Fallco / Asteannunci frequently re-list the *same* lot; they must
collapse to a single canonical auction, keyed primarily on the PVP identifier
and secondarily on (tribunal, procedure number, lot number).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.processors.normalize import normalize_text

# Trust order for biographical fields; PVP wins.
SOURCE_AUTHORITY = ["pvp", "astalegale", "fallco", "asteannunci", "other"]


def canonical_procedure_key(tribunal: str | None, procedure_number: str | None, lot_number: str | None) -> str | None:
    """Deterministic key for (tribunale, n. procedura, lotto)."""
    if not tribunal or not procedure_number:
        return None
    parts = [
        normalize_text(tribunal).replace(" ", ""),
        normalize_text(procedure_number).replace(" ", ""),
        normalize_text(lot_number or "").replace(" ", ""),
    ]
    return "|".join(parts)


@dataclass
class AuctionIdentity:
    pvp_id: str | None
    procedure_key: str | None
    master_auction_id: str

    def to_dict(self) -> dict:
        return {
            "pvp_id": self.pvp_id,
            "procedure_key": self.procedure_key,
            "master_auction_id": self.master_auction_id,
        }


def auction_identity(record: dict) -> AuctionIdentity:
    """Compute a canonical identity for one auction record."""
    pvp_id = record.get("pvp_id")
    proc_key = canonical_procedure_key(
        record.get("tribunal"), record.get("procedure_number"), record.get("lot_number")
    )
    # Prefer the procedure key (it is source-agnostic) so a PVP record and a
    # re-listing on another portal collapse to the same lot; fall back to the
    # PVP id, then to a content hash.
    basis = proc_key or pvp_id
    if not basis:
        # Fall back to a content hash so a record still gets a stable id.
        basis = hashlib.sha1(
            f"{record.get('address','')}|{record.get('base_price','')}|{record.get('auction_date','')}".encode()
        ).hexdigest()
    digest = hashlib.sha1(basis.encode()).hexdigest()[:12]
    return AuctionIdentity(pvp_id=pvp_id, procedure_key=proc_key, master_auction_id=f"A{digest}")


def merge_auction_records(records: list[dict]) -> dict:
    """Merge records referring to the same lot, preferring authoritative fields.

    Returns a single dict. ``sources`` accumulates every contributing source so
    provenance is not lost when collapsing PVP + Astalegale + Fallco.
    """
    if not records:
        raise ValueError("no records to merge")
    ordered = sorted(
        records,
        key=lambda r: SOURCE_AUTHORITY.index(r.get("source"))
        if r.get("source") in SOURCE_AUTHORITY
        else len(SOURCE_AUTHORITY),
    )
    merged: dict = {}
    for rec in reversed(ordered):  # least authoritative first, overwritten by best
        for k, v in rec.items():
            if v is not None and v != "":
                merged[k] = v
    merged["sources"] = sorted({r.get("source") for r in records if r.get("source")})
    merged.update(auction_identity(merged).to_dict())
    return merged


def deduplicate_auctions(records: list[dict]) -> list[dict]:
    """Group auction records by canonical identity and merge each group."""
    groups: dict[str, list[dict]] = {}
    for rec in records:
        ident = auction_identity(rec)
        groups.setdefault(ident.master_auction_id, []).append(rec)
    return [merge_auction_records(group) for group in groups.values()]
