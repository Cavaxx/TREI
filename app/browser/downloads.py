"""Artifact validation for browser downloads.

A file on the remote browser is raw bytes, not a guarantee: we verify size,
compute the SHA-256, and refuse to treat an HTML error/login page as a dataset.
"""

from __future__ import annotations

import hashlib

from app.browser.errors import BrowserFailure, BrowserProviderError

#: Markers that indicate a fetched "CSV/PDF" is actually an HTML page.
_HTML_MARKERS = (b"<!doctype html", b"<html", b"<head", b"<body")


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def looks_like_html(payload: bytes) -> bool:
    head = payload[:2048].strip().lower()
    return any(marker in head for marker in _HTML_MARKERS)


def validate_artifact(
    payload: bytes,
    *,
    max_bytes: int,
    expected_kind: str = "table",
) -> dict:
    """Validate a downloaded artifact and return its metadata.

    Raises ``BrowserProviderError(VALIDATION_FAILED)`` when the bytes are empty,
    over the size limit, or an HTML page masquerading as a structured export.
    """
    if not payload:
        raise BrowserProviderError(
            BrowserFailure.VALIDATION_FAILED, "downloaded artifact is empty"
        )
    if len(payload) > max_bytes:
        raise BrowserProviderError(
            BrowserFailure.VALIDATION_FAILED,
            f"artifact is {len(payload)} bytes, over the {max_bytes} limit",
        )
    if looks_like_html(payload):
        raise BrowserProviderError(
            BrowserFailure.VALIDATION_FAILED,
            "artifact is an HTML page (error/login), not a structured dataset",
        )
    return {
        "byte_size": len(payload),
        "content_hash": sha256(payload),
        "expected_kind": expected_kind,
    }
