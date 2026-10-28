"""Payload parsers shared by the feed and open-data adapters.

Kept separate from the adapters so the same, tested parsing behaviour is used
for every authorized channel (JSON API, JSONL export, CSV export, XML export).
No HTML parsing lives here: a licensed feed delivers structured data.
"""

from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
from collections import Counter

#: Keys commonly wrapping a list of records in JSON API responses.
LIST_KEYS = ("data", "rows", "results", "items", "records", "dataset", "listings")


def dig(obj, path: str | None):
    """Walk a dotted path (``"data.items.0"``) into nested dict/list data."""
    if not path:
        return obj
    current = obj
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if current is None:
            return None
    return current


def _autodetect_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in LIST_KEYS:
            if isinstance(data.get(key), list):
                return data[key]
        # e.g. {"dataset": {"data": [...]}}
        for value in data.values():
            if isinstance(value, dict):
                for key in LIST_KEYS:
                    if isinstance(value.get(key), list):
                        return value[key]
    return None


def parse_json_rows(text: str, records_path: str | None = None) -> list[dict]:
    """Parse a JSON payload into a list of record dicts.

    ``records_path`` names the container explicitly (recommended for partner
    APIs, which nest their payload); otherwise common shapes are auto-detected.
    """
    data = json.loads(text)
    found = dig(data, records_path) if records_path else _autodetect_list(data)
    if found is None:
        return []
    if isinstance(found, list):
        return [row for row in found if isinstance(row, dict)]
    if isinstance(found, dict):
        return [found]
    return []


def parse_jsonl_rows(text: str) -> list[dict]:
    """Parse newline-delimited JSON (one record per line)."""
    rows: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def parse_csv_rows(text: str) -> list[dict]:
    return [row for row in csv.DictReader(io.StringIO(text)) if any(v for v in row.values())]


def parse_xml_rows(text: str, record_tag: str | None = None) -> list[dict]:
    """Parse an XML export.

    ``record_tag`` names the repeated element (namespace-insensitive). When it is
    omitted the most frequent child tag of the root is used, which is the usual
    convention for portal exports (``<annunci><annuncio>...</annuncio></annunci>``).
    """
    root = ET.fromstring(text)
    if record_tag:
        matches = [el for el in root.iter() if _localname(el.tag) == _localname(record_tag)]
    else:
        counts = Counter(_localname(child.tag) for child in root)
        if not counts:
            return []
        tag = counts.most_common(1)[0][0]
        matches = [el for el in root.iter() if _localname(el.tag) == tag and el is not root]
    return [_element_to_dict(el) for el in matches]


def _localname(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _element_to_dict(element: ET.Element) -> dict:
    out: dict = dict(element.attrib)
    children = list(element)
    if not children:
        text = (element.text or "").strip()
        if text:
            out["value"] = text
        return out
    for child in children:
        key = _localname(child.tag)
        value = _element_to_dict(child) if (len(child) or child.attrib) else (child.text or "").strip()
        if key in out:
            existing = out[key]
            out[key] = [*existing, value] if isinstance(existing, list) else [existing, value]
        else:
            out[key] = value
    return out


def parse_payload(fmt: str, text: str, records_path: str | None = None,
                  record_tag: str | None = None) -> list[dict]:
    """Dispatch to the parser for ``fmt`` (json | jsonl | csv | xml)."""
    normalized = (fmt or "json").strip().lower()
    if normalized == "json":
        return parse_json_rows(text, records_path)
    if normalized == "jsonl":
        return parse_jsonl_rows(text)
    if normalized == "csv":
        return parse_csv_rows(text)
    if normalized == "xml":
        return parse_xml_rows(text, record_tag)
    raise ValueError(f"unsupported feed format: {fmt!r}")
