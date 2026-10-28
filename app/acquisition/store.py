"""Append-only observation store (idempotent, revision-preserving).

Real observations are kept apart from the synthetic demo data used by the API. The
store is a JSONL ledger plus an in-memory index: re-ingesting the same natural key
with the same content is a no-op, while a changed value appends a new revision
instead of overwriting history. A failed live run never writes a synthetic row.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.acquisition.contracts import Observation, observation_key
from app.core.config import PROJECT_ROOT


class ObservationStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else PROJECT_ROOT / "data" / "observations.jsonl"
        self._records: list[dict] = []
        self._by_key: dict[str, list[dict]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            self._records.append(rec)
            self._by_key.setdefault(rec.get("key", ""), []).append(rec)

    def upsert(self, observation: Observation | dict) -> dict:
        """Insert or revise. Returns accounting info (never stores synthetic data)."""
        rec = observation.to_dict() if isinstance(observation, Observation) else dict(observation)
        if rec.get("synthetic_demo") or rec.get("source") == "synthetic_demo":
            return {"stored": False, "new": False, "revised": False, "rejected": True,
                    "reason": "synthetic_demo is never stored as a real observation"}
        key = observation_key(rec)
        rec["key"] = key
        prior = self._by_key.get(key, [])
        if prior:
            last = prior[-1]
            if last.get("content_hash") == rec.get("content_hash") and last.get("value") == rec.get("value"):
                return {"stored": False, "new": False, "revised": False, "revision": len(prior), "key": key}
        rec["revision"] = len(prior) + 1
        self._records.append(rec)
        self._by_key.setdefault(key, []).append(rec)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        return {"stored": True, "new": not prior, "revised": bool(prior), "revision": rec["revision"], "key": key}

    def all(self) -> list[dict]:
        return list(self._records)

    def coverage(self) -> dict:
        """Effective coverage: indicator -> territory -> sorted periods."""
        out: dict = {}
        for rec in self._records:
            ind = rec.get("indicator", "?")
            terr = rec.get("territory_code", "?")
            period = rec.get("period", "?")
            out.setdefault(ind, {}).setdefault(terr, []).append(period)
        for ind in out:
            for terr in out[ind]:
                out[ind][terr] = sorted(set(out[ind][terr]))
        return out
