"""Deterministic SYNTHETIC demo data.

This exists so the MVP can be demonstrated and tested end-to-end before any
real source is legally wired in. Every generated record carries
``synthetic_demo: True`` and MUST never be presented as real market data.
The distributions below are illustrative only.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from app.processors.normalize import normalize_listing

# Illustrative reference €/m² per comune (NOT official figures).
_COMUNI = {
    "Trento": {"lat": 46.0679, "lon": 11.1211, "base_pm2": 2600, "spread": 0.20},
    "Rovereto": {"lat": 45.8896, "lon": 11.0390, "base_pm2": 2200, "spread": 0.20},
    "Riva del Garda": {"lat": 45.8853, "lon": 10.8419, "base_pm2": 3600, "spread": 0.25},
    "Arco": {"lat": 45.9180, "lon": 10.8850, "base_pm2": 2900, "spread": 0.22},
    "Pergine Valsugana": {"lat": 46.0620, "lon": 11.2420, "base_pm2": 1900, "spread": 0.18},
    "Lavis": {"lat": 46.1390, "lon": 11.1110, "base_pm2": 2100, "spread": 0.18},
    "Canazei": {"lat": 46.4770, "lon": 11.7700, "base_pm2": 5000, "spread": 0.30},
    "Pinzolo": {"lat": 46.1630, "lon": 10.7670, "base_pm2": 4300, "spread": 0.30},
    "Predazzo": {"lat": 46.3110, "lon": 11.6000, "base_pm2": 2600, "spread": 0.22},
    "Folgaria": {"lat": 45.9160, "lon": 11.1700, "base_pm2": 2100, "spread": 0.22},
}
_CONDITIONS = ["new", "excellent", "good", "renovated", "to_renovate", "rustic"]
_ENERGY = ["A4", "A2", "A", "B", "C", "D", "E", "F", "G"]
_TYPOLOGIES = ["appartamento", "attico", "villa", "rustico", "bilocale", "trilocale"]


def generate_demo_listings(
    n_per_comune: int = 40,
    rng_seed: int = 20260930,
    start: date | None = None,
) -> list[dict]:
    """Return a list of normalized synthetic listings."""
    rng = random.Random(rng_seed)
    start = start or date(2024, 1, 1)
    out: list[dict] = []
    counter = 0
    for comune, meta in _COMUNI.items():
        for _ in range(n_per_comune):
            counter += 1
            surface = round(rng.gauss(95, 35), 0)
            surface = max(28.0, min(surface, 320.0))
            rooms = max(1, int(round(surface / rng.uniform(28, 42))))
            condition = rng.choice(_CONDITIONS)
            energy = rng.choices(_ENERGY, weights=[2, 3, 4, 8, 12, 14, 16, 18, 12])[0]
            pm2 = meta["base_pm2"] * (1 + rng.gauss(0, meta["spread"]))
            # typology & condition multipliers (illustrative)
            mult = {"to_renovate": 0.78, "rustic": 0.82, "good": 1.0,
                    "renovated": 1.08, "excellent": 1.15, "new": 1.25}[condition]
            price = round(pm2 * mult * surface / 500) * 500
            lat = meta["lat"] + rng.gauss(0, 0.01)
            lon = meta["lon"] + rng.gauss(0, 0.012)
            first_seen = start + timedelta(days=rng.randint(0, 900))
            raw = {
                "listing_source_id": f"DEMO-{comune[:3].upper()}-{counter:05d}",
                "source": "synthetic_demo",
                "comune": comune,
                "typology": rng.choice(_TYPOLOGIES),
                "price": price,
                "surface_m2": surface,
                "rooms": rooms,
                "bathrooms": max(1, min(rooms - 1, rng.randint(1, 3))),
                "floor": rng.randint(0, 6),
                "condition": condition,
                "energy_class": energy,
                "has_elevator": rng.random() < 0.55,
                "has_garage": rng.random() < 0.4,
                "has_garden": rng.random() < 0.3,
                "has_terrace": rng.random() < 0.45,
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "address": f"Via Demo {rng.randint(1, 200)}",
                "description": f"{rng.choice(_TYPOLOGIES)} a {comune}",
                "measure_type": "asking",
                "first_seen_at": first_seen.isoformat(),
                "synthetic_demo": True,
            }
            out.append(normalize_listing(raw))
    return out


def generate_demo_auctions(n: int = 12, rng_seed: int = 7) -> list[dict]:
    """Synthetic auction lots for the demo (clearly flagged)."""
    rng = random.Random(rng_seed)
    out = []
    for i in range(n):
        base = rng.randint(60000, 320000)
        out.append(
            {
                "master_auction_id": f"ADEMO{i:04d}",
                "pvp_id": f"PVP-DEMO-{i:04d}",
                "source": "synthetic_demo",
                "tribunal": rng.choice(["Trento", "Rovereto"]),
                "procedure_number": f"{rng.randint(1, 500)}/2025",
                "lot_number": str(rng.randint(1, 5)),
                "address": f"Via Asta {rng.randint(1, 99)}, Trento",
                "base_price": base,
                "minimum_offer": round(base * 0.75, -2),
                "appraisal_value": round(base * 1.4, -2),
                "auction_date": (date(2026, 1, 1) + timedelta(days=rng.randint(0, 300))).isoformat(),
                "occupancy_status": rng.choice(["free", "occupied"]),
                "has_building_abuse": rng.random() < 0.2,
                "has_condominium_arrears": rng.random() < 0.25,
                "synthetic_demo": True,
            }
        )
    return out
