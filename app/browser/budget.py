"""Run-wide browser budget.

The limits are run limits, not per-worker limits: every session open/close and
every wall-clock second is accounted against one ledger, so two workers on the
same host cannot jointly exceed the budget. When ``path`` is set the accounting
is checkpointed to a JSON file under an advisory lock; otherwise it is in-memory
(tests and single-process runs).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.browser.errors import BrowserFailure, BrowserProviderError


def _monotonic() -> float:
    return time.monotonic()


@dataclass
class RunBudget:
    max_sessions: int = 5
    max_session_wall_seconds: int = 180
    max_total_browser_seconds: int = 900
    #: Bookkeeping (persisted under a lock when ``path`` is set).
    sessions_created: int = 0
    total_browser_seconds: float = 0.0
    path: Path | None = field(default=None, repr=False)

    @classmethod
    def from_settings(cls, settings) -> RunBudget:
        return cls(
            max_sessions=settings.kernel_max_sessions_per_run,
            max_session_wall_seconds=settings.kernel_max_session_wall_seconds,
            max_total_browser_seconds=settings.kernel_max_total_browser_seconds,
        )

    # -- accounting ---------------------------------------------------------
    def remaining_total_seconds(self) -> float:
        return max(0.0, self.max_total_browser_seconds - self.total_browser_seconds)

    def effective_session_wall_seconds(self) -> float:
        """Per-session deadline = min(app limit, remaining run budget)."""
        return max(1.0, min(float(self.max_session_wall_seconds), self.remaining_total_seconds()))

    def check_can_open(self) -> None:
        if self.sessions_created >= self.max_sessions:
            raise BrowserProviderError(
                BrowserFailure.BUDGET_EXHAUSTED,
                f"session limit reached ({self.sessions_created}/{self.max_sessions})",
            )
        if self.remaining_total_seconds() <= 0:
            raise BrowserProviderError(
                BrowserFailure.BUDGET_EXHAUSTED,
                "cumulative browser budget exhausted",
            )

    def register_open(self) -> float:
        """Reserve a session and return its effective wall deadline (seconds)."""
        self.check_can_open()
        self.sessions_created += 1
        self._persist()
        return self.effective_session_wall_seconds()

    def record_wall(self, seconds: float) -> None:
        self.total_browser_seconds += max(0.0, seconds)
        self._persist()

    def to_dict(self) -> dict:
        return {
            "max_sessions": self.max_sessions,
            "max_session_wall_seconds": self.max_session_wall_seconds,
            "max_total_browser_seconds": self.max_total_browser_seconds,
            "sessions_created": self.sessions_created,
            "total_browser_seconds": round(self.total_browser_seconds, 3),
        }

    # -- persistence --------------------------------------------------------
    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(self.to_dict(), sort_keys=True)
        try:
            import fcntl

            with self.path.open("a+", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                handle.seek(0)
                handle.truncate()
                handle.write(data)
                handle.flush()
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            self.path.write_text(data, encoding="utf-8")

    def load(self) -> None:
        """Restore counters from a previous checkpoint (best-effort)."""
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.sessions_created = int(data.get("sessions_created", 0))
            self.total_browser_seconds = float(data.get("total_browser_seconds", 0.0))
        except (ValueError, OSError):
            pass
