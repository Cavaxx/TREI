"""Command-line entry point for the Agent Import channel (§8-28).

Usage::

    python -m app.agent_import.cli status
    python -m app.agent_import.cli plan <source>
    python -m app.agent_import.cli crawl <source> --url <search-url> [--mode full]

``status`` and ``plan`` never touch the network: they print the policy verdict the
crawler would obey. ``crawl`` refuses to make a request unless that verdict is
``allowed``.
"""

from __future__ import annotations

import argparse
import json
import sys

from app.agent_import.crawler import CrawlerManager, CrawlRequest
from app.agent_import.policy import PolicyResolver
from app.core.config import get_settings
from app.fetchers.licence import LicenceRegistry
from app.fetchers.registry import SourceConfig, SourceRegistry
from app.property_master.master import PropertyMasterStore


def _load_registries():
    registry = SourceRegistry.load()
    try:
        licences = LicenceRegistry.load()
    except FileNotFoundError:
        licences = LicenceRegistry([])
    return registry, licences


def _extra(config: SourceConfig) -> dict:
    return config.extra if isinstance(config.extra, dict) else {}


def _configured_urls(config: SourceConfig) -> list[str]:
    """Search URLs declared in the registry record (data, not code)."""
    extra = _extra(config)
    crawl = extra.get("crawl") if isinstance(extra.get("crawl"), dict) else {}
    if crawl.get("search_urls"):
        return [str(u) for u in crawl["search_urls"]]
    if extra.get("search_urls"):
        return [str(u) for u in extra["search_urls"]]
    return []


def _cmd_status(args: argparse.Namespace) -> int:
    registry, licences = _load_registries()
    resolver = PolicyResolver(registry, licences=licences)
    rows = []
    for source in registry.all():
        urls = _configured_urls(source)
        decision = resolver.decide(source.name, urls=urls or None)
        rows.append(
            {
                "source": source.name,
                "tier": source.tier,
                "status": source.status,
                "mode": decision.mode,
                "allowed": decision.allowed,
                "reason": decision.reason,
                "licence_id": decision.licence_id,
                "requests_per_minute": decision.requests_per_minute,
                "search_urls": len(urls),
            }
        )
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(f"{'source':22} {'tier':18} {'mode':16} {'allowed':8} reason")
        print("-" * 110)
        for row in rows:
            print(
                f"{row['source']:22} {row['tier']:18} {row['mode']:16} "
                f"{str(row['allowed']):8} {row['reason']}"
            )
        allowed = sum(1 for r in rows if r["allowed"])
        print(f"\n{allowed}/{len(rows)} sources are allowed to be crawled automatically.")
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    registry, licences = _load_registries()
    source = registry.get(args.source)
    if source is None:
        print(f"unknown source: {args.source}", file=sys.stderr)
        return 2
    resolver = PolicyResolver(registry, licences=licences)
    urls = args.url or _configured_urls(source)
    decision = resolver.decide(source.name, urls=urls or None)
    print(
        json.dumps(
            {"source": source.name, "search_urls": urls, "decision": decision.to_dict()},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if decision.allowed else 3


def _cmd_crawl(args: argparse.Namespace) -> int:
    registry, licences = _load_registries()
    config = registry.get(args.source)
    if config is None:
        print(f"unknown source: {args.source}", file=sys.stderr)
        return 2

    settings = get_settings()
    resolver = PolicyResolver(registry, licences=licences)
    store = PropertyMasterStore()
    manager = CrawlerManager(resolver=resolver, store=store, settings=settings)
    extra = _extra(config)
    request = CrawlRequest(
        source=config.name,
        search_urls=args.url or _configured_urls(config),
        crawl_mode=args.mode,
        max_requests=args.max_requests,
        max_listings=args.max_listings,
        id_patterns=tuple(extra.get("id_patterns") or ()),
        url_patterns=tuple(extra.get("url_patterns") or ()),
    )
    if not request.search_urls:
        print("no search URLs configured for this source", file=sys.stderr)
        return 2

    job = manager.run(request)
    if args.json:
        print(json.dumps(job.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"job        : {job.job_id}")
        print(f"status     : {job.status.value}")
        print(f"pages      : {len(job.pages)}")
        print(f"discovered : {job.listings_discovered}")
        print(f"processed  : {job.listings_processed}")
        print(f"new/updated: {job.new_listings}/{job.updated_listings}")
        print(f"removed    : {job.removed_listings}")
        print(f"failed     : {job.listings_failed}")
        print(f"blocked    : {job.blocked_requests}")
        for note in job.notes:
            print(f"  - {note}")
    return 4 if job.status.value == "blocked" else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trei-agent", description="TREI agent import")
    sub = parser.add_subparsers(dest="cmd", required=True)

    status = sub.add_parser("status", help="policy verdict for every registered source")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=_cmd_status)

    plan = sub.add_parser("plan", help="show what a crawl of one source would do")
    plan.add_argument("source")
    plan.add_argument("--url", action="append", help="search URL (repeatable)")
    plan.set_defaults(func=_cmd_plan)

    crawl = sub.add_parser("crawl", help="crawl one source (refuses if not allowed)")
    crawl.add_argument("source")
    crawl.add_argument("--url", action="append", help="search URL (repeatable)")
    crawl.add_argument("--mode", default="incremental", choices=["incremental", "full"])
    crawl.add_argument("--max-requests", dest="max_requests", type=int, default=200)
    crawl.add_argument("--max-listings", dest="max_listings", type=int, default=None)
    crawl.add_argument("--json", action="store_true")
    crawl.set_defaults(func=_cmd_crawl)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
