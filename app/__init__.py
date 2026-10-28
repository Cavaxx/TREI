"""Trentino Real Estate Intelligence (TREI).

A market-intelligence platform for the real-estate market of the Autonomous
Province of Trento. This package contains the domain logic: fetchers,
processors, market engine, auction engine, valuation models and the HTTP API.

The core domain modules are intentionally written to depend only on the Python
standard library where possible, so that they can be unit-tested and reasoned
about without a running Postgres/Redis/MinIO stack. Optional heavier
dependencies (numpy, pandas, scikit-learn) are isolated behind explicit
imports.
"""

from app.core.versions import (
    MODEL_VERSION,
    NORMALIZATION_VERSION,
    PARSER_VERSION,
    SCHEMA_VERSION,
)

__all__ = [
    "SCHEMA_VERSION",
    "PARSER_VERSION",
    "NORMALIZATION_VERSION",
    "MODEL_VERSION",
]
