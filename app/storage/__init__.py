"""Storage layer: the raw-object data lake (§45).

Raw payloads (HTTP responses, HTML, JSON, screenshots) are kept untouched so a
structured record can always be traced back to the bytes it came from. The lake
is written through :class:`DataLake`; the local filesystem is always available,
S3/MinIO is used when configured.
"""

from app.storage.datalake import DataLake, RawObjectRef

__all__ = ["DataLake", "RawObjectRef"]
