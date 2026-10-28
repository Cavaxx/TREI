"""Minimal ``.env`` loader for the acquisition CLI.

The project reads configuration from ``os.environ`` (12-factor). This module adds
the *only* missing piece: loading ``PROJECT_ROOT/.env`` so an operator can put
``KERNEL_API_KEY`` (and the ``TREI_*`` overrides) in one local, git-ignored file.

Guarantees:

* Only allow-listed keys are imported: ``KERNEL_API_KEY`` and ``TREI_*``.
* Values already present in the environment always win (never overridden).
* Values are never logged or returned; they only reach ``os.environ``.
"""

from __future__ import annotations

import os
from pathlib import Path


def _allowed(key: str) -> bool:
    return key == "KERNEL_API_KEY" or key.startswith("TREI_")


def load_env_file(path: Path | str | None = None) -> None:
    """Import allow-listed variables from ``path`` (default ``PROJECT_ROOT/.env``)."""
    if path is None:
        from app.core.config import PROJECT_ROOT

        path = PROJECT_ROOT / ".env"
    path = Path(path)
    if not path.exists():
        return

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        if not _allowed(key):
            continue
        if key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")
