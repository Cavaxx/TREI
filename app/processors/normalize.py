"""Normalization of raw, heterogeneous listings into a common shape.

Everything here is deterministic and unit-tested. Normalization never invents
values: when a field cannot be parsed it stays ``None`` and the record is
flagged by the data-quality layer (section 48/63).
"""

from __future__ import annotations

import re
import unicodedata

# --- Text helpers -----------------------------------------------------------

_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9\s\-]")
# Canonical comune names for the main Trentino centres. This is a starting set
# and is meant to be extended from the agency/source registry over time; the
# alias table is data, not logic.
COMUNE_ALIASES: dict[str, str] = {
    "trento": "Trento",
    "tn": "Trento",
    "rovereto": "Rovereto",
    "riva del garda": "Riva del Garda",
    "riva": "Riva del Garda",
    "arco": "Arco",
    "pergine valsugana": "Pergine Valsugana",
    "pergine": "Pergine Valsugana",
    "lavis": "Lavis",
    "cles": "Cles",
    "mezzolombardo": "Mezzolombardo",
    "borgo valsugana": "Borgo Valsugana",
    "levico terme": "Levico Terme",
    "mori": "Mori",
    "ala": "Ala",
    "pinzolo": "Pinzolo",
    "madonna di campiglio": "Pinzolo",
    "canazei": "Canazei",
    "moena": "Moena",
    "predazzo": "Predazzo",
    "folgaria": "Folgaria",
    "andalo": "Andalo",
    "molveno": "Molveno",
    "san martino di castrozza": "Primiero San Martino di Castrozza",
    "primiero": "Primiero San Martino di Castrozza",
    "cavalese": "Cavalese",
    "tesero": "Tesero",
    "castello di fiemme": "Castello-Molina di Fiemme",
    "cembra lisignago": "Cembra Lisignago",
    "lavarone": "Lavarone",
    "brentonico": "Brentonico",
    "nago-torbole": "Nago-Torbole",
    "torbole": "Nago-Torbole",
    "dro": "Dro",
    "varone": "Riva del Garda",
}


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def normalize_text(text: str | None) -> str:
    """Lowercase, strip accents, collapse whitespace and punctuation."""
    if not text:
        return ""
    t = strip_accents(text).lower()
    t = t.replace("'", " ").replace("’", " ")
    t = _NON_ALNUM.sub(" ", t)
    return _WS.sub(" ", t).strip()


def normalize_comune(raw: str | None) -> str | None:
    """Map a free-text comune to its canonical spelling, if known."""
    t = normalize_text(raw)
    if not t:
        return None
    # Drop a trailing province marker that survived normalization, e.g. "(TN)".
    t = re.sub(r"\s+(tn|trento)$", "", t).strip()
    return COMUNE_ALIASES.get(t)


# --- Field parsers ----------------------------------------------------------

_NUM = re.compile(r"\d[\d.,]*\d|\d")


def parse_number(raw) -> float | None:
    """Parse an Italian-formatted number: '1.234,5' -> 1234.5 ; '85 m²' -> 85."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s:
        return None
    compact = s.replace(" ", "")
    m = _NUM.search(compact)
    if not m:
        return None
    token = m.group(0).strip(".,")
    if not token:
        return None
    negative = m.start() > 0 and compact[m.start() - 1] == "-"
    # If both separators appear, '.' is thousands and ',' is decimal.
    if "." in token and "," in token:
        token = token.replace(".", "").replace(",", ".")
    elif "," in token:
        token = token.replace(",", ".")
    elif "." in token:
        parts = token.split(".")
        # A single trailing group of exactly three digits is a thousands group.
        if len(parts) >= 2 and len(parts[-1]) == 3 and all(len(p) <= 3 for p in parts[:-1]):
            token = "".join(parts)
    try:
        value = float(token)
    except ValueError:
        return None
    return -value if negative else value


def parse_price(raw) -> float | None:
    v = parse_number(raw)
    return v if v is not None and v > 0 else None


def parse_surface(raw) -> float | None:
    v = parse_number(raw)
    return v if v is not None and v > 0 else None


def parse_rooms(raw) -> int | None:
    v = parse_number(raw)
    if v is None or v < 0:
        return None
    return int(round(v))


# --- Categorical mappers ----------------------------------------------------

_ENERGY = {
    "a4": "A4", "a3": "A3", "a2": "A2", "a1": "A1", "a": "A",
    "b": "B", "c": "C", "d": "D", "e": "E", "f": "F", "g": "G",
    "classe a": "A", "classe b": "B", "classe c": "C",
}


def normalize_energy_class(raw: str | None) -> str | None:
    if not raw:
        return None
    t = normalize_text(raw)
    t = t.replace("classe ", "").strip().replace(" ", "")
    return _ENERGY.get(t)


_CONDITION = {
    "nuovo": "new",
    "nuova costruzione": "new",
    "ristrutturato": "renovated",
    "ristrutturata": "renovated",
    "ottimo": "excellent",
    "buono": "good",
    "abitabile": "good",
    "da ristrutturare": "to_renovate",
    "da restaurare": "to_renovate",
    "da ristrutturare parzialmente": "to_renovate_partial",
    "rustico": "rustic",
}


def normalize_condition(raw: str | None) -> str | None:
    t = normalize_text(raw)
    return _CONDITION.get(t) if t else None


# --- Listing normalization --------------------------------------------------

# Canonical output shape. Adapters map their source payload into a dict that
# contains these keys (any subset). normalize_listing() coerces and validates.

def price_per_m2(price: float | None, surface: float | None) -> float | None:
    if price is None or surface is None or surface <= 0:
        return None
    return price / surface


def normalize_listing(raw: dict) -> dict:
    """Coerce a raw adapter dict into the canonical normalized listing shape.

    Unknown fields pass through untouched. Never fabricates values.
    """
    out = dict(raw)
    out["comune"] = normalize_comune(raw.get("comune") or raw.get("city"))
    out["price"] = parse_price(raw.get("price") or raw.get("asking_price"))
    out["surface_m2"] = parse_surface(raw.get("surface_m2") or raw.get("surface"))
    out["rooms"] = parse_rooms(raw.get("rooms") or raw.get("locali"))
    out["bathrooms"] = parse_rooms(raw.get("bathrooms") or raw.get("bagni"))
    out["floor"] = parse_rooms(raw.get("floor") or raw.get("piano"))
    out["energy_class"] = normalize_energy_class(raw.get("energy_class"))
    out["condition"] = normalize_condition(raw.get("condition") or raw.get("stato"))
    out["price_m2"] = price_per_m2(out["price"], out["surface_m2"])
    out["measure_type"] = raw.get("measure_type", "asking")
    out["is_asking"] = out["measure_type"] == "asking"
    return out

