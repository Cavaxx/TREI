"""Entity resolution / deduplication (sections 19-20).

The same physical flat is frequently listed on several portals. We must not
count it three times, but we also must not merge two genuinely different units
that happen to share an address. The approach is a transparent, weighted
similarity score with an explicit threshold, followed by connected-components
clustering to assign a stable ``property_master_id``.

Time on market is preserved across re-publishing by reconstructing a
``listing_episode_id`` (section 20).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

from app.processors.normalize import normalize_text

EARTH_RADIUS_M = 6_371_000.0


# --- Similarity primitives --------------------------------------------------

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def token_jaccard(a: str | None, b: str | None) -> float:
    """Jaccard similarity over whitespace tokens of the normalized strings."""
    ta = set(normalize_text(a).split())
    tb = set(normalize_text(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def hamming_distance_hex(h1: str | None, h2: str | None) -> int | None:
    """Hamming distance between two hex-encoded perceptual hashes."""
    if not h1 or not h2 or len(h1) != len(h2):
        return None
    try:
        return bin(int(h1, 16) ^ int(h2, 16)).count("1")
    except ValueError:
        return None


def _rel_close(a: float | None, b: float | None, tol: float) -> bool | None:
    """Relative-close test; None when either operand is missing."""
    if a is None or b is None or a == 0 or b == 0:
        return None
    return abs(a - b) / max(a, b) <= tol


@dataclass
class MatchEvidence:
    address: bool | None = None
    geo_m: float | None = None
    surface: bool | None = None
    rooms: bool | None = None
    price: bool | None = None
    description_sim: float = 0.0
    agency: bool | None = None
    image_sim: float | None = None

    def to_dict(self) -> dict:
        return {
            "address": self.address,
            "geo_m": None if self.geo_m is None else round(self.geo_m, 1),
            "surface": self.surface,
            "rooms": self.rooms,
            "price": self.price,
            "description_sim": round(self.description_sim, 3),
            "agency": self.agency,
            "image_sim": None if self.image_sim is None else round(self.image_sim, 3),
        }


@dataclass
class MatchResult:
    score: float
    is_match: bool
    evidence: MatchEvidence = field(default_factory=MatchEvidence)


# Default weights sum to 1.0. Geo and address dominate because they are the
# most discriminative for apartments.
DEFAULT_WEIGHTS = {
    "address": 0.28,
    "geo": 0.22,
    "surface": 0.16,
    "rooms": 0.06,
    "price": 0.10,
    "description": 0.10,
    "agency": 0.03,
    "image": 0.05,
}
DEFAULT_THRESHOLD = 0.72


def score_pair(
    a: dict,
    b: dict,
    weights: dict[str, float] | None = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> MatchResult:
    """Compute a weighted similarity score between two normalized listings."""
    weights = weights or DEFAULT_WEIGHTS
    ev = MatchEvidence()

    # Address: same normalized address string (token overlap of address field)
    addr_a, addr_b = a.get("address"), b.get("address")
    if addr_a and addr_b:
        ev.address = token_jaccard(addr_a, addr_b) >= 0.6 or normalize_text(addr_a) == normalize_text(addr_b)

    if None not in (a.get("lat"), a.get("lon"), b.get("lat"), b.get("lon")):
        ev.geo_m = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])

    ev.surface = _rel_close(a.get("surface_m2"), b.get("surface_m2"), 0.05)
    ev.rooms = None if a.get("rooms") is None or b.get("rooms") is None else a["rooms"] == b["rooms"]
    ev.price = _rel_close(a.get("price"), b.get("price"), 0.02)
    ev.description_sim = token_jaccard(a.get("description"), b.get("description"))
    if a.get("agency") and b.get("agency"):
        ev.agency = normalize_text(a["agency"]) == normalize_text(b["agency"])
    hd = hamming_distance_hex(a.get("image_phash"), b.get("image_phash"))
    if hd is not None:
        ev.image_sim = 1.0 - hd / (len(a["image_phash"]) * 4)

    subscores: dict[str, float | None] = {
        "address": None if ev.address is None else (1.0 if ev.address else 0.0),
        "geo": None if ev.geo_m is None else _geo_score(ev.geo_m),
        "surface": None if ev.surface is None else (1.0 if ev.surface else 0.0),
        "rooms": None if ev.rooms is None else (1.0 if ev.rooms else 0.0),
        "price": None if ev.price is None else (1.0 if ev.price else 0.0),
        "description": ev.description_sim,
        "agency": None if ev.agency is None else (1.0 if ev.agency else 0.0),
        "image": ev.image_sim,
    }

    num = 0.0
    den = 0.0
    for key, s in subscores.items():
        if s is None:
            continue
        w = weights.get(key, 0.0)
        num += w * s
        den += w
    score = num / den if den > 0 else 0.0

    # Hard veto: far-apart properties are never the same unit, regardless of
    # other similarities (protects against text-only false positives).
    is_match = score >= threshold and not (ev.geo_m is not None and ev.geo_m > 200)
    return MatchResult(score=score, is_match=is_match, evidence=ev)


def _geo_score(meters: float) -> float:
    """Decay: identical location -> 1.0, ~100 m -> ~0.5, >200 m -> ~0."""
    return max(0.0, 1.0 - meters / 200.0)


# --- Clustering -------------------------------------------------------------

class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        # path compression
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _master_id(member_keys: list[str]) -> str:
    """Deterministic master id from the sorted member keys (reproducible)."""
    digest = hashlib.sha1("|".join(sorted(member_keys)).encode()).hexdigest()[:12]
    return f"P{digest}"


def deduplicate(
    listings: list[dict],
    key_field: str = "listing_source_id",
    weights: dict[str, float] | None = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[dict]:
    """Assign ``property_master_id`` to each listing by connected components.

    Returns the input dicts each augmented with ``property_master_id`` and a
    ``dedup_group_size``. Comparison is O(n^2) which is fine for per-comune
    batches; for province-wide runs a blocking key (comune + geohash) should be
    applied first (see ``block_by_comune``).
    """
    uf = _UnionFind()
    keys = [str(lst.get(key_field, i)) for i, lst in enumerate(listings)]
    for k in keys:
        uf.find(k)

    for i in range(len(listings)):
        for j in range(i + 1, len(listings)):
            if uf.find(keys[i]) == uf.find(keys[j]):
                continue
            res = score_pair(listings[i], listings[j], weights=weights, threshold=threshold)
            if res.is_match:
                uf.union(keys[i], keys[j])

    groups: dict[str, list[str]] = {}
    for k in keys:
        groups.setdefault(uf.find(k), []).append(k)

    master_by_root = {root: _master_id(members) for root, members in groups.items()}
    out: list[dict] = []
    for lst, k in zip(listings, keys):
        root = uf.find(k)
        enriched = dict(lst)
        enriched["property_master_id"] = master_by_root[root]
        enriched["dedup_group_size"] = len(groups[root])
        out.append(enriched)
    return out


def block_by_comune(listings: list[dict]) -> dict[str, list[dict]]:
    """Blocking key: group by normalized comune to bound the pairwise cost."""
    blocks: dict[str, list[dict]] = {}
    for lst in listings:
        blocks.setdefault(str(lst.get("comune")), []).append(lst)
    return blocks


# --- Episode reconstruction (section 20) ------------------------------------

def reconstruct_episodes(events: list[dict]) -> list[dict]:
    """Attach a ``listing_episode_id`` across remove/re-add within a master.

    ``events`` must be sorted by timestamp and carry ``property_master_id``,
    ``listing_source_id``, ``timestamp`` and ``event`` (a ListingEvent value).
    A new episode starts when a ``listing_created``/``listing_relisted`` occurs
    after the previous episode for that master had been removed. The episode id
    is stable, so days-on-market is not artificially reset by re-publishing.
    """
    open_episode: dict[str, str] = {}
    episode_counter: dict[str, int] = {}
    out: list[dict] = []
    for ev in sorted(events, key=lambda e: e["timestamp"]):
        master = ev["property_master_id"]
        enriched = dict(ev)
        kind = ev.get("event")
        if kind in ("listing_created", "listing_relisted"):
            if master not in open_episode:
                n = episode_counter.get(master, 0) + 1
                episode_counter[master] = n
                open_episode[master] = f"{master}-E{n}"
            enriched["listing_episode_id"] = open_episode[master]
            enriched["starts_new_episode"] = True
        else:
            enriched["listing_episode_id"] = open_episode.get(master)
            enriched["starts_new_episode"] = False
            if kind == "listing_removed":
                open_episode.pop(master, None)
        out.append(enriched)
    return out

