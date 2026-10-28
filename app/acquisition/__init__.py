"""Acquisition layer: routing, observation contract, storage and CLI.

Separates *ingestion* (raw bytes + provenance) from *derived* metrics and keeps
real observations strictly apart from the ``synthetic_demo`` fixtures used by the
API. A live run never writes a synthetic row into this store.
"""

from app.acquisition.contracts import Observation, observation_key
from app.acquisition.router import RouteDecision, route_acquisition
from app.acquisition.store import ObservationStore

__all__ = [
    "Observation",
    "ObservationStore",
    "RouteDecision",
    "observation_key",
    "route_acquisition",
]
