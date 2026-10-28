"""Central version registry.

Every persisted record carries the parser/schema/normalization version that
produced it, and every estimate carries the model version. Bumping a constant
here is what makes historical data lineage auditable (see docs/data_dictionary.md
and section 34 of the master specification).
"""

from __future__ import annotations

import os

# --- Schema / storage -------------------------------------------------------
SCHEMA_VERSION: str = os.environ.get("TREI_SCHEMA_VERSION", "1.0.0")

# --- Ingestion --------------------------------------------------------------
# Parser versions are per-adapter; this is the global default and the value
# stored when an adapter does not override it.
PARSER_VERSION: str = os.environ.get("TREI_PARSER_VERSION", "0.1.0")

# Bumped whenever normalization rules change (unit handling, comune aliases,
# energy-class mapping, ...). A change here invalidates cached features.
NORMALIZATION_VERSION: str = os.environ.get("TREI_NORMALIZATION_VERSION", "0.1.0")

# --- Models -----------------------------------------------------------------
MODEL_VERSION: str = os.environ.get("TREI_MODEL_VERSION", "0.8.0")
FEATURES_VERSION: str = os.environ.get("TREI_FEATURES_VERSION", "0.1.0")

# Data cutoff used when a run does not provide its own. Kept as an explicit
# constant so that model artefacts are never confused across cutoffs.
DEFAULT_DATA_CUTOFF: str = os.environ.get("TREI_DATA_CUTOFF", "2026-09-30")


def model_metadata(data_cutoff: str | None = None, training_period: str | None = None) -> dict:
    """Return a JSON-serializable model provenance block.

    This is attached to every valuation/forecast response so that the question
    "which model produced this number, trained on what?" always has an answer.
    """
    meta = {
        "model_version": MODEL_VERSION,
        "features_version": FEATURES_VERSION,
        "data_cutoff": data_cutoff or DEFAULT_DATA_CUTOFF,
    }
    if training_period is not None:
        meta["training_period"] = training_period
    return meta
