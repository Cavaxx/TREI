"""FastAPI application for Trentino Real Estate Intelligence.

All estimates returned here carry a ``provenance`` block (model version, data
cutoff) and the demo repository is clearly flagged as synthetic. The API never
mixes asking prices with transaction prices.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.agent_import.policy import PolicyResolver
from app.agent_import.state import CrawlStateStore
from app.api import schemas
from app.api.deps import (
    get_crawl_state,
    get_licences,
    get_property_master,
    get_registry,
    get_repository,
)
from app.api.real_dashboard import REAL_DASHBOARD_HTML, REAL_DETAIL_HTML
from app.api.realdata import router as real_router
from app.auction_engine.anomaly import detect_anomalies
from app.auction_engine.valuation import AuctionCostInputs, value_auction
from app.core.config import get_settings
from app.core.versions import model_metadata
from app.db.repository import MarketRepository
from app.fetchers.licence import LicenceRegistry
from app.fetchers.registry import SourceRegistry
from app.market_engine.depth import (
    active_supply_ratio,
    annualized_inventory_turnover,
    months_of_supply,
    stock_turnover,
    visible_turnover,
)
from app.market_engine.macro import build_panel, market_context
from app.market_engine.regime import classify
from app.models.affordability import affordability
from app.models.forecast import directional_probabilities
from app.models.scenarios import ScenarioInput, run_scenario
from app.property_master.master import PropertyMasterStore
from app.property_master.pressure import seller_pressure_score

settings = get_settings()

app = FastAPI(title=settings.api_title, version=settings.api_version)

app.include_router(real_router)

DEMO_WARNING = (
    "Demo repository is seeded with SYNTHETIC data for illustration only; "
    "it is not real market data."
)


@app.get("/health", tags=["ops"])
def health() -> dict:
    repo = get_repository()
    reg = get_registry()
    return {
        "status": "ok",
        "listings": len(repo.listings),
        "auctions": len(repo.auctions),
        "sources_registered": len(reg.all()),
        "sources_auto_enabled": len(reg.auto_enabled()),
        "demo_data": True,
        "demo_warning": DEMO_WARNING,
    }


@app.get("/api/v1/meta", tags=["ops"])
def meta() -> dict:
    return {
        "api_version": settings.api_version,
        "provenance": model_metadata(),
        "demo_data": True,
        "notes": [
            "Asking / OMI / transacted prices are separate measures (never mixed).",
            "Every estimate reports a confidence level and an interval.",
        ],
    }


@app.get("/api/v1/sources", tags=["sources"])
def sources(
    reg: SourceRegistry = Depends(get_registry),
    licences: LicenceRegistry = Depends(get_licences),
) -> dict:
    return {
        "sources": [
            {
                "name": s.name,
                "domain": s.domain,
                "kind": s.kind,
                "tier": s.tier,
                "status": s.status,
                "recommended_mode": s.recommended_mode,
                "auto_acquisition_allowed": s.auto_acquisition_allowed,
                "robots_txt": s.robots_txt,
                "licence_status": licences.status(s.name),
                "last_verified": s.last_verified,
            }
            for s in reg.all()
        ]
    }


@app.get("/api/v1/licences", tags=["sources"])
def licences_endpoint(licences: LicenceRegistry = Depends(get_licences)) -> dict:
    """The licence register: what we are contractually allowed to fetch.

    An empty register is the expected state until agreements are signed, and it
    is precisely what makes automated access to restricted sources impossible.
    """
    return {
        "licences": licences.to_dicts(),
        "rule": "no active licence record => no automated request",
        "note": (
            "Open-data sources (tier 3) are covered by their own public licence "
            "and do not require an entry here."
        ),
    }


@app.get("/api/v1/market/comuni", tags=["market"])
def comuni(repo: MarketRepository = Depends(get_repository)) -> dict:
    return {"comuni": repo.comuni()}


@app.get("/api/v1/market/summary", tags=["market"])
def market_summary(repo: MarketRepository = Depends(get_repository)) -> dict:
    return {
        "by_comune": repo.summary_by_comune(),
        "measure_type": "asking",
        "demo_data": True,
        "demo_warning": DEMO_WARNING,
    }


@app.get("/api/v1/market/depth", tags=["market"])
def market_depth(
    annual_transactions: float = Query(..., description="annual NTN transactions"),
    total_dwellings: float = Query(..., description="total dwelling stock"),
    avg_active_listings: float = Query(..., description="avg monthly active listings"),
    avg_monthly_closed_sales: float = Query(..., description="avg monthly closed sales"),
) -> dict:
    """Depth metrics for a given area. Inputs are supplied by the caller until
    the official transaction/dwelling series are wired in."""
    annual_closed = avg_monthly_closed_sales * 12
    return {
        "stock_turnover": stock_turnover(annual_transactions, total_dwellings),
        "visible_turnover": visible_turnover(annual_transactions, avg_active_listings),
        "active_supply_ratio": active_supply_ratio(avg_active_listings, total_dwellings),
        "months_of_supply": months_of_supply(avg_active_listings, avg_monthly_closed_sales),
        "annualized_inventory_turnover": annualized_inventory_turnover(
            annual_closed, avg_active_listings
        ),
        "notes": [
            "active_supply_ratio = observed listings / dwellings (market-visible supply).",
            "visible_turnover is NOT the same as stock_turnover.",
        ],
    }


@app.get("/api/v1/listings", tags=["listings"])
def listings(
    comune: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    min_surface: float | None = None,
    max_surface: float | None = None,
    limit: int = Query(50, le=500),
    repo: MarketRepository = Depends(get_repository),
) -> dict:
    rows = repo.filter_listings(
        comune=comune,
        min_price=min_price,
        max_price=max_price,
        min_surface=min_surface,
        max_surface=max_surface,
        measure_type="asking",
        limit=limit,
    )
    return {"count": len(rows), "measure_type": "asking", "listings": rows, "demo_data": True}


@app.get("/api/v1/listings/{listing_source_id}", tags=["listings"])
def listing_detail(
    listing_source_id: str, repo: MarketRepository = Depends(get_repository)
) -> dict:
    lst = repo.get_listing(listing_source_id)
    if lst is None:
        raise HTTPException(status_code=404, detail="listing not found")
    return lst


@app.post("/api/v1/valuation", response_model=schemas.ValuationResponse, tags=["valuation"])
def valuation(
    subject: schemas.SubjectProperty, repo: MarketRepository = Depends(get_repository)
) -> dict:
    return repo.value(subject.model_dump())


@app.get("/api/v1/auctions", tags=["auctions"])
def auctions(repo: MarketRepository = Depends(get_repository)) -> dict:
    enriched = []
    for lot in repo.auctions:
        rec = dict(lot)
        rec["anomaly"] = detect_anomalies(lot).to_dict()
        enriched.append(rec)
    return {"count": len(enriched), "auctions": enriched, "demo_data": True}


@app.post("/api/v1/auctions/value", tags=["auctions"])
def auction_value(req: schemas.AuctionValuationRequest) -> dict:
    inputs = AuctionCostInputs(
        auction_purchase_price=req.auction_purchase_price,
        estimated_market_value=req.estimated_market_value,
        taxes=req.taxes,
        renovation=req.renovation,
        regularization=req.regularization,
        arrears=req.arrears,
        transaction_costs=req.transaction_costs,
        possession_cost=req.possession_cost,
        risk_adjustment=req.risk_adjustment,
        other_costs=req.other_costs,
    )
    val = value_auction(inputs, minimum_offer=req.minimum_offer, appraisal_value=req.appraisal_value)
    return val.to_dict()


@app.post("/api/v1/forecast/directional", tags=["forecast"])
def forecast_directional(req: schemas.DirectionalRequest) -> dict:
    return directional_probabilities(req.series, req.horizon, flat_band=req.flat_band).to_dict()


@app.post("/api/v1/scenarios/run", tags=["scenarios"])
def scenarios(req: schemas.ScenarioRequest) -> dict:
    return run_scenario(ScenarioInput(**req.model_dump())).to_dict()


@app.post("/api/v1/affordability", tags=["scenarios"])
def affordability_endpoint(req: schemas.AffordabilityRequest) -> dict:
    res = affordability(
        purchase_price=req.purchase_price,
        down_payment=req.down_payment,
        mortgage_rate=req.mortgage_rate,
        duration_years=req.duration_years,
        monthly_net_income=req.monthly_net_income,
    )
    return res.to_dict()


@app.get("/api/v1/regime/demo", tags=["market"])
def regime_demo() -> dict:
    """Regime classification on an illustrative history (demo only)."""
    history = {
        "price_momentum": [0.02, 0.025, 0.03, 0.028, 0.02, 0.015],
        "transaction_momentum": [0.01, 0.015, 0.02, 0.018, 0.01, 0.005],
        "liquidity": [0.3, 0.32, 0.33, 0.31, 0.28, 0.27],
        "supply": [100, 105, 110, 118, 126, 133],
        "credit": [0.015, 0.018, 0.022, 0.028, 0.033, 0.036],
    }
    components = {k: v[-1] for k, v in history.items()}
    return classify(components, history).to_dict()


@app.get("/api/v1/coverage", tags=["coverage"])
def coverage(store: PropertyMasterStore = Depends(get_property_master)) -> dict:
    """What TREI observes, through which sources (§43).

    Deliberately phrased as an observation count, never as a claim of
    completeness.
    """
    return {"demo_data": True, **store.coverage().to_dict()}


@app.get("/api/v1/review-queue", tags=["coverage"])
def review_queue(store: PropertyMasterStore = Depends(get_property_master)) -> dict:
    """Listings whose extraction fell below the confidence threshold (§31)."""
    rows = store.review_queue()
    return {"count": len(rows), "review_required": rows, "demo_data": True}


@app.get("/api/v1/crawl/metrics", tags=["crawler"])
def crawl_metrics(state: CrawlStateStore = Depends(get_crawl_state)) -> dict:
    """Per-source crawler health for the admin dashboard (§44)."""
    return state.metrics()


@app.get("/api/v1/agent/sources", tags=["crawler"])
def agent_sources(
    reg: SourceRegistry = Depends(get_registry),
    licences: LicenceRegistry = Depends(get_licences),
) -> dict:
    """The Agent Import's access verdict for every registered source (§26).

    This endpoint makes the policy auditable *before* anything runs: a source
    with no licence, or marked UNKNOWN, is visibly not crawlable.
    """
    resolver = PolicyResolver(reg, licences=licences)
    decisions = []
    for source in reg.all():
        extra = source.extra if isinstance(source.extra, dict) else {}
        crawl = extra.get("crawl") if isinstance(extra.get("crawl"), dict) else {}
        urls = [str(u) for u in (crawl.get("search_urls") or [])]
        decisions.append(
            {
                "source": source.name,
                "kind": source.kind,
                "tier": source.tier,
                "status": source.status,
                **resolver.decide(source.name, urls=urls or None).to_dict(),
            }
        )
    return {
        "sources": decisions,
        "rule": "no positive policy decision => no request is made",
    }


@app.get("/api/v1/property/{property_master_id}", tags=["listings"])
def property_detail(
    property_master_id: str,
    store: PropertyMasterStore = Depends(get_property_master),
    repo: MarketRepository = Depends(get_repository),
) -> dict:
    """The listing page for one physical property (§42).

    Shows the asking price, the estimated fair value with its interval, the
    observed time on market, the price history, the neutral price-pressure
    evidence, and - the point of the whole platform - *source coverage*.
    """
    listings = store.master(property_master_id)
    if not listings:
        raise HTTPException(status_code=404, detail="property not found")

    primary = listings[0]
    intelligence = store.intelligence(primary.key) or {}
    valuation = repo.value({**primary.payload, "property_master_id": property_master_id})
    pressure = seller_pressure_score(
        days_on_market=intelligence.get("days_on_market"),
        price_cut_count=intelligence.get("price_cut_count"),
        cumulative_discount=intelligence.get("cumulative_discount"),
        days_since_last_cut=intelligence.get("days_since_last_cut"),
    )

    sources = []
    for record in sorted(listings, key=lambda r: r.first_seen_at or ""):
        sources.append(
            {
                "source": record.source,
                "source_listing_id": record.listing_source_id,
                "url": record.url,
                "first_seen_at": record.first_seen_at,
                "last_seen_at": record.last_seen_at,
                "first_seen_source": record.first_seen_source,
                "email_received_at": record.email_received_at,
                "source_published_at": record.source_published_at,
                "is_active": record.is_active,
                "raw_object_id": record.raw_object_id,
            }
        )

    observed_from = sorted({r.source for r in listings})
    return {
        "property_master_id": property_master_id,
        "comune": primary.payload.get("comune"),
        "typology": primary.payload.get("typology"),
        "asking_price": primary.price,
        "surface_m2": primary.surface_m2,
        "price_m2": primary.payload.get("price_m2"),
        "valuation": valuation,
        "listing_intelligence": intelligence,
        "seller_pressure": pressure.to_dict(),
        "market_regime": {
            "status": "UNAVAILABLE",
            "note": (
                "A local regime needs the macro panel; call "
                "POST /api/v1/market/macro/context with the observed series."
            ),
        },
        "source_coverage": {
            "observed_from": observed_from,
            "first_observed": min((r.first_seen_at for r in listings if r.first_seen_at), default=None),
            "last_observed": max((r.last_seen_at for r in listings if r.last_seen_at), default=None),
            "listing_count": len(listings),
            "observations": sources,
        },
        "demo_data": True,
    }


@app.post("/api/v1/market/macro/context", tags=["market"])
def macro_context_endpoint(req: schemas.MacroPanelRequest) -> dict:
    """Assemble macro inputs for the Market Engine (§36-37).

    A period that is not published yet must be sent with
    ``status = NOT_YET_PUBLISHED``; it will never be interpolated.
    """
    panel = build_panel([row.model_dump() for row in req.rows])
    return {
        "context": market_context(panel),
        "panel": panel.to_dict(),
    }


@app.get("/", response_class=HTMLResponse, tags=["ops"])
def dashboard() -> str:
    return DASHBOARD_HTML


@app.get("/real", response_class=HTMLResponse, tags=["ops"])
def real_dashboard() -> str:
    return REAL_DASHBOARD_HTML


@app.get("/real/{native_id}", response_class=HTMLResponse, tags=["ops"])
def real_detail(native_id: str) -> str:
    return REAL_DETAIL_HTML.replace("__NATIVE_ID__", native_id)


DASHBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>TREI - demo dashboard</title>
<style>body{font-family:system-ui,sans-serif;margin:2rem;max-width:900px}
.banner{background:#fff3cd;border:1px solid #ffca2c;padding:.6rem 1rem;border-radius:6px}
code{background:#f4f4f4;padding:.1rem .3rem;border-radius:4px}</style></head>
<body>
<h1>Trentino Real Estate Intelligence</h1>
<p class="banner"><b>Demo:</b> the API is seeded with synthetic data for illustration
only. It is <b>not</b> real market data.</p>
<h2>Explore the API</h2>
<ul>
<li><a href="/health">/health</a></li>
<li><a href="/api/v1/meta">/api/v1/meta</a></li>
<li><a href="/api/v1/sources">/api/v1/sources</a> &mdash; source registry &amp; legal status</li>
<li><a href="/api/v1/agent/sources">/api/v1/agent/sources</a> &mdash; what the Agent Import is allowed to touch</li>
<li><a href="/api/v1/coverage">/api/v1/coverage</a> &mdash; what TREI observes, through which sources</li>
<li><a href="/api/v1/review-queue">/api/v1/review-queue</a> &mdash; low-confidence extractions awaiting a human</li>
<li><a href="/api/v1/crawl/metrics">/api/v1/crawl/metrics</a> &mdash; crawler health per source</li>
<li><a href="/api/v1/market/summary">/api/v1/market/summary</a> &mdash; asking &euro;/m&sup2; by comune</li>
<li><a href="/api/v1/listings?limit=5">/api/v1/listings</a></li>
<li><a href="/api/v1/auctions">/api/v1/auctions</a></li>
<li><a href="/api/v1/regime/demo">/api/v1/regime/demo</a></li>
<li><a href="/docs">OpenAPI docs</a></li>
</ul>
<p>Interactive valuation: <code>POST /api/v1/valuation</code></p>
</body></html>"""
