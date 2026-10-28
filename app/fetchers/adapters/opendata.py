"""Generic adapter for Tier-3 open datasets (ISTAT, ISPAT, OMI exports, ...).

Open datasets are typically static CSV/JSON/Excel files rather than HTML pages,
so this adapter fetches a list of file URLs and hands each payload to a
user-supplied row-mapper. No HTML scraping, no robots ambiguity.
"""

from __future__ import annotations

import csv

from app.fetchers.adapters.parsing import (
    parse_csv_rows,
    parse_json_rows,
    parse_payload,
)
from app.fetchers.base import SourceAdapter, SourceDegraded


class OpenDataAdapter(SourceAdapter):
    """Fetch and parse JSON, JSONL, CSV or XML open-data files.

    ``record_mapper`` receives each parsed record (dict) and returns a canonical
    record dict (or None to skip). ``fmt`` is json | jsonl | csv | xml.
    """

    def __init__(
        self,
        urls: list[str],
        fmt: str = "json",
        record_mapper=None,
        records_path: str | None = None,
        record_tag: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._urls = urls
        self.fmt = fmt
        self.records_path = records_path
        self.record_tag = record_tag
        self.record_mapper = record_mapper or (lambda r: r)

    def fetch_urls(self) -> list[str]:
        return list(self._urls)

    def parse(self, url: str, payload: bytes) -> list[dict]:
        text = payload.decode("utf-8-sig", errors="replace")
        try:
            rows = parse_payload(self.fmt, text, self.records_path, self.record_tag)
        except (ValueError, csv.Error, SyntaxError, UnicodeDecodeError) as exc:
            raise SourceDegraded(f"cannot parse {url}: {exc}") from exc
        out: list[dict] = []
        for row in rows:
            mapped = self.record_mapper(row) if isinstance(row, dict) else None
            if mapped:
                out.append(mapped)
        return out

    # Kept for backwards compatibility with earlier callers and tests.
    @staticmethod
    def _parse_json(text: str) -> list[dict]:
        return parse_json_rows(text)

    @staticmethod
    def _parse_csv(text: str) -> list[dict]:
        return parse_csv_rows(text)
