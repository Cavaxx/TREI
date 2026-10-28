"""Pydantic request/response schemas for the API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SubjectProperty(BaseModel):
    """A property to be valued."""

    listing_source_id: str | None = None
    property_master_id: str | None = None
    comune: str
    typology: str | None = None
    price: float | None = None
    surface_m2: float = Field(gt=0)
    rooms: int | None = None
    bathrooms: int | None = None
    floor: int | None = None
    condition: str | None = None
    energy_class: str | None = None
    has_elevator: bool = False
    has_garage: bool = False
    has_garden: bool = False
    has_terrace: bool = False
    lat: float | None = None
    lon: float | None = None


class ValuationResponse(BaseModel):
    estimated_market_value: float
    estimated_market_price_m2: float | None
    prediction_interval_low: float
    prediction_interval_high: float
    deviation: float
    classification: str
    confidence: str
    comparables_count: int
    method: str
    provenance: dict


class ScenarioRequest(BaseModel):
    mortgage_rate_pp: float = 0.0
    household_growth_pct: float = 0.0
    transactions_pct: float = 0.0
    active_supply_pct: float = 0.0
    inflation_pp: float = 0.0
    income_growth_pct: float = 0.0


class AffordabilityRequest(BaseModel):
    purchase_price: float = Field(gt=0)
    down_payment: float = Field(ge=0)
    mortgage_rate: float = Field(gt=0)
    duration_years: int = 25
    monthly_net_income: float | None = None


class DirectionalRequest(BaseModel):
    series: list[float]
    horizon: int = 12
    flat_band: float = 0.02


class AuctionValuationRequest(BaseModel):
    auction_purchase_price: float
    estimated_market_value: float
    taxes: float = 0.0
    renovation: float = 0.0
    regularization: float = 0.0
    arrears: float = 0.0
    transaction_costs: float = 0.0
    possession_cost: float = 0.0
    risk_adjustment: float = 0.0
    other_costs: float = 0.0
    minimum_offer: float | None = None
    appraisal_value: float | None = None


class MacroObservationIn(BaseModel):
    """One macro observation. ``status`` defaults to OBSERVED when a value is set."""

    indicator: str
    period: str
    value: float | None = None
    status: str | None = None
    unit: str = ""
    source: str = ""
    retrieved_at: str | None = None


class MacroPanelRequest(BaseModel):
    """A macro panel submission (§36).

    A period whose official figure has not been released must be sent with
    ``status = "NOT_YET_PUBLISHED"``; the engine will never interpolate it.
    """

    rows: list[MacroObservationIn] = Field(default_factory=list)
