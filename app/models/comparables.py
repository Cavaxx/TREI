"""Comparable engine (section 22).

Finds the most similar listings to a subject property. Deliberately does NOT
fall back to "average of everything in the comune": candidates must be in the
same micro-area, same broad typology, similar surface and condition. Each
comparable carries an explicit similarity score and its differences so the UI
can show *why* it was chosen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.processors.dedup import haversine_m


@dataclass
class Comparable:
    listing_source_id: str
    property_master_id: str | None
    source: str
    comune: str | None
    surface_m2: float | None
    price: float | None
    price_m2: float | None
    distance_m: float | None
    similarity: float
    differences: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "listing_source_id": self.listing_source_id,
            "property_master_id": self.property_master_id,
            "source": self.source,
            "comune": self.comune,
            "surface_m2": self.surface_m2,
            "price": self.price,
            "price_m2": None if self.price_m2 is None else round(self.price_m2, 1),
            "distance_m": None if self.distance_m is None else round(self.distance_m, 1),
            "similarity": round(self.similarity, 3),
            "differences": self.differences,
        }


def _price_m2(lst: dict) -> float | None:
    if lst.get("price_m2") is not None:
        return lst["price_m2"]
    p, s = lst.get("price"), lst.get("surface_m2")
    if p and s:
        return p / s
    return None


def similarity(subject: dict, candidate: dict) -> tuple[float, dict]:
    """Similarity in [0, 1] plus a dict describing the largest differences."""
    diffs: dict = {}
    score = 0.0
    weight_sum = 0.0

    # Same comune / microzone
    if subject.get("comune") and candidate.get("comune"):
        same = subject["comune"] == candidate["comune"]
        score += 0.30 * (1.0 if same else 0.0)
        weight_sum += 0.30
        diffs["same_comune"] = same

    # Geographic proximity (if coordinates available)
    if None not in (subject.get("lat"), subject.get("lon"), candidate.get("lat"), candidate.get("lon")):
        d = haversine_m(subject["lat"], subject["lon"], candidate["lat"], candidate["lon"])
        geo = max(0.0, 1.0 - d / 1000.0)  # ~1 km half-decay
        score += 0.20 * geo
        weight_sum += 0.20
        diffs["distance_m"] = round(d, 1)

    # Surface similarity (tolerance 20%)
    ss, cs = subject.get("surface_m2"), candidate.get("surface_m2")
    if ss and cs:
        rel = abs(ss - cs) / max(ss, cs)
        surf = max(0.0, 1.0 - rel / 0.20)
        score += 0.22 * surf
        weight_sum += 0.22
        diffs["surface_delta_m2"] = round(cs - ss, 1)

    # Same rooms
    if subject.get("rooms") is not None and candidate.get("rooms") is not None:
        same_rooms = subject["rooms"] == candidate["rooms"]
        score += 0.08 * (1.0 if same_rooms else 0.0)
        weight_sum += 0.08
        diffs["rooms_delta"] = candidate["rooms"] - subject["rooms"]

    # Same condition
    if subject.get("condition") and candidate.get("condition"):
        same_cond = subject["condition"] == candidate["condition"]
        score += 0.12 * (1.0 if same_cond else 0.0)
        weight_sum += 0.12
        diffs["same_condition"] = same_cond

    # Same energy class
    if subject.get("energy_class") and candidate.get("energy_class"):
        same_e = subject["energy_class"] == candidate["energy_class"]
        score += 0.08 * (1.0 if same_e else 0.0)
        weight_sum += 0.08
        diffs["same_energy_class"] = same_e

    total = score / weight_sum if weight_sum > 0 else 0.0
    return total, diffs


def find_comparables(
    subject: dict,
    pool: list[dict],
    top_n: int = 10,
    min_similarity: float = 0.45,
    exclude_master: str | None = None,
) -> list[Comparable]:
    """Return up to ``top_n`` comparables sorted by similarity desc."""
    results: list[Comparable] = []
    subj_id = subject.get("listing_source_id")
    for cand in pool:
        if cand.get("listing_source_id") == subj_id:
            continue
        if exclude_master and cand.get("property_master_id") == exclude_master:
            continue
        sim, diffs = similarity(subject, cand)
        if sim < min_similarity:
            continue
        results.append(
            Comparable(
                listing_source_id=str(cand.get("listing_source_id")),
                property_master_id=cand.get("property_master_id"),
                source=str(cand.get("source", "")),
                comune=cand.get("comune"),
                surface_m2=cand.get("surface_m2"),
                price=cand.get("price"),
                price_m2=_price_m2(cand),
                distance_m=diffs.get("distance_m"),
                similarity=sim,
                differences=diffs,
            )
        )
    results.sort(key=lambda c: c.similarity, reverse=True)
    return results[:top_n]
