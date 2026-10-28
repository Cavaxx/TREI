"""Observation contract for real acquired data.

Every observation must be traceable to: source, URL, UTC retrieval time, reference
period, territory/code and level, indicator, value/unit, definition and
methodological version, original artifact + SHA-256, parser version, run and
quality. ``published_at`` (the source's date) and ``retrieved_at`` (our fetch) are
distinct fields.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from app.core.enums import DataStatus


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass
class Observation:
    source: str
    source_url: str = ""
    retrieved_at: str = field(default_factory=_now_iso)
    period: str = ""
    territory_code: str = ""
    geo_level: str = "province"
    indicator: str = ""
    value: float | None = None
    unit: str = ""
    definition: str = ""
    definition_version: str = ""
    published_at: str | None = None
    raw_object_id: str = ""
    content_hash: str = ""
    parser_version: str = ""
    schema_version: str = ""
    run_id: str = ""
    quality: str = "ok"
    row_ref: str = ""
    data_status: str = DataStatus.OBSERVED.value
    #: Keeps transaction series (OMI_NTN / FIMAA_AE / ISPAT_NOTARIAL) and price
    #: semantics (asking / omi_min / transaction_observed) apart. Never pooled.
    series: str = ""
    measure_type: str = ""

    def key(self) -> str:
        return observation_key(self)

    def to_dict(self) -> dict:
        return asdict(self)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.source:
            errors.append("source is required")
        if not self.period:
            errors.append("period is required")
        if not self.indicator:
            errors.append("indicator is required")
        if not self.territory_code:
            errors.append("territory_code is required")
        if self.data_status == DataStatus.OBSERVED.value and self.value is None:
            errors.append("OBSERVED requires a numeric value; a missing figure needs an explicit status")
        return errors


def observation_key(obs: Observation | dict) -> str:
    """Natural key: same source/indicator/territory/period/unit/definition/level/series.

    Includes ``geo_level`` and ``series`` so compravendite, NTN and annunci never
    collapse onto the same point."""
    if isinstance(obs, dict):
        parts = [
            obs.get("source"),
            obs.get("indicator"),
            obs.get("territory_code"),
            obs.get("period"),
            obs.get("unit"),
            obs.get("definition_version"),
            obs.get("geo_level"),
            obs.get("series"),
        ]
    else:
        parts = [
            obs.source,
            obs.indicator,
            obs.territory_code,
            obs.period,
            obs.unit,
            obs.definition_version,
            obs.geo_level,
            obs.series,
        ]
    return hashlib.sha1("|".join(str(p or "") for p in parts).encode()).hexdigest()
