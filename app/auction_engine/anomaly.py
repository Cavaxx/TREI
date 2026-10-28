"""Automatic auction anomaly detection (section 31).

The engine must never label a lot an "affare" merely because the base price is
low. It surfaces the legal/physical risk factors that make a cheap lot
expensive in practice.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Each flag carries the field on the normalized auction record that triggers it.
_FLAG_FIELDS: dict[str, str] = {
    "occupied": "occupancy_status",        # occupied => possession risk
    "surface_right": "has_surface_right",  # diritto di superficie
    "usufruct": "has_usufruct",
    "bare_ownership": "has_bare_ownership",
    "co_ownership": "has_co_ownership",
    "undivided_share": "has_undivided_share",
    "building_abuse": "has_building_abuse",
    "cadastral_discrepancy": "has_cadastral_discrepancy",
    "needs_regularization": "needs_regularization",
    "mortgages": "has_mortgages",
    "foreclosures": "has_foreclosures",
    "condominium_arrears": "has_condominium_arrears",
    "not_full_ownership": "not_full_ownership",
    "timeshare": "has_timeshare",
    "missing_documents": "missing_documents",
}


@dataclass
class AuctionAnomalyReport:
    flags: dict[str, bool] = field(default_factory=dict)
    risk_level: str = "LOW"  # LOW / MEDIUM / HIGH
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "flags": self.flags,
            "risk_level": self.risk_level,
            "notes": self.notes,
        }


# Weights used only to summarize risk; they are transparent and tunable.
_RISK_WEIGHTS = {
    "occupied": 2,
    "surface_right": 2,
    "usufruct": 2,
    "bare_ownership": 1,
    "co_ownership": 1,
    "undivided_share": 2,
    "building_abuse": 2,
    "cadastral_discrepancy": 1,
    "needs_regularization": 1,
    "mortgages": 1,
    "foreclosures": 1,
    "condominium_arrears": 1,
    "not_full_ownership": 2,
    "timeshare": 3,
    "missing_documents": 2,
}


def detect_anomalies(record: dict) -> AuctionAnomalyReport:
    """Scan a normalized auction record and return the risk flags."""
    flags: dict[str, bool] = {}
    notes: list[str] = []
    for flag, field_name in _FLAG_FIELDS.items():
        val = record.get(field_name)
        if flag == "occupied":
            active = str(val).lower() in {"occupied", "occupato", "true", "1"}
        else:
            active = bool(val)
        flags[flag] = active
        if active:
            notes.append(f"{flag} present")

    score = sum(_RISK_WEIGHTS.get(f, 1) for f, on in flags.items() if on)
    if score >= 6:
        level = "HIGH"
    elif score >= 3:
        level = "MEDIUM"
    else:
        level = "LOW"
    return AuctionAnomalyReport(flags=flags, risk_level=level, notes=notes)
