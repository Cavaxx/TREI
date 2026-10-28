"""Raw-object data lake (§45).

Layout::

    raw/
      <source>/
        <YYYY-MM-DD>/
          <crawl_id|crawl>/
            response        # verbatim bytes as received
            html            # decoded HTML when the payload is a document
            json            # structured data extracted from the page
            screenshot      # PNG bytes, only when vision extraction was used

Design rules:

* Raw payloads are **immutable**. Storing the same bytes twice is a no-op: the
  object is content-addressed by its SHA-256, so re-crawling a page that has not
  changed does not grow the lake.
* A screenshot is stored as a *data source*, never as a means of circumventing
  an access control (§13). Its hash and capture timestamp travel with the record.
* Nothing here knows about HTTP. The crawler decides what to store; the lake
  only names things deterministically and gives back a reference.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.core.versions import PARSER_VERSION, SCHEMA_VERSION

#: Kinds of raw object the platform stores.
RAW_KINDS = ("response", "html", "json", "screenshot")

_EXTENSIONS = {
    "response": ".bin",
    "html": ".html",
    "json": ".json",
    "screenshot": ".png",
}


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


@dataclass(frozen=True)
class RawObjectRef:
    """A pointer to one immutable object in the lake."""

    source: str
    kind: str
    relative_path: str
    content_hash: str
    byte_size: int
    captured_at: str
    source_url: str = ""
    crawl_id: str = ""
    parser_version: str = PARSER_VERSION
    schema_version: str = SCHEMA_VERSION

    @property
    def raw_object_id(self) -> str:
        """Stable id used by structured records to link back to the raw bytes."""
        return f"{self.source}:{self.content_hash[:16]}"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["raw_object_id"] = self.raw_object_id
        return data


class DataLake:
    """Filesystem-backed raw store with optional object-storage mirroring."""

    def __init__(self, root: Path | str | None = None, *, s3_client=None, bucket: str | None = None) -> None:
        self.root = Path(root) if root is not None else get_settings().datalake_root
        self._s3 = s3_client
        self.bucket = bucket

    # --- paths --------------------------------------------------------------
    def directory(self, source: str, *, day: datetime | None = None, crawl_id: str | None = None) -> Path:
        """Return (and create) the directory for one source/day/crawl."""
        d = (day or _now()).strftime("%Y-%m-%d")
        return self.root / _safe(source) / d / (crawl_id or "crawl")

    @staticmethod
    def _filename(kind: str, content_hash: str) -> str:
        ext = _EXTENSIONS.get(kind, ".bin")
        return f"{kind}-{content_hash[:16]}{ext}"

    # --- writes -------------------------------------------------------------
    def store(
        self,
        source: str,
        kind: str,
        payload: bytes | str,
        *,
        url: str = "",
        crawl_id: str = "",
        captured_at: datetime | None = None,
        s3_key_prefix: str | None = None,
    ) -> RawObjectRef:
        """Persist ``payload`` and return its immutable reference.

        Storing the same bytes again is idempotent (content-addressed).
        """
        if kind not in RAW_KINDS:
            raise ValueError(f"unknown raw kind {kind!r}; expected one of {RAW_KINDS}")
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
        digest = hashlib.sha256(data).hexdigest()
        stamp = captured_at or _now()
        directory = self.directory(source, day=stamp, crawl_id=crawl_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / self._filename(kind, digest)
        if not path.exists():
            path.write_bytes(data)
        relative = str(path.relative_to(self.root))

        ref = RawObjectRef(
            source=source,
            kind=kind,
            relative_path=relative,
            content_hash=digest,
            byte_size=len(data),
            captured_at=stamp.isoformat(),
            source_url=url,
            crawl_id=crawl_id,
        )

        if self._s3 is not None and self.bucket:
            key = s3_key_prefix or relative
            try:  # pragma: no cover - requires MinIO/S3
                self._s3.put_object(Bucket=self.bucket, Key=key, Body=data)
            except Exception:  # noqa: BLE001 - the lake can be degraded without failing the crawl
                pass
        return ref

    # --- reads --------------------------------------------------------------
    def read(self, ref: RawObjectRef) -> bytes:
        return (self.root / ref.relative_path).read_bytes()

    def path_for(self, ref: RawObjectRef) -> Path:
        return self.root / ref.relative_path

    def exists(self, ref: RawObjectRef) -> bool:
        return (self.root / ref.relative_path).exists()


def _safe(name: str) -> str:
    """Make a source name safe to use as a single path segment."""
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in name) or "unknown"
