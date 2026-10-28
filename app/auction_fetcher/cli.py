"""Command-line entry point for the auction channel (§33-35).

Usage::

    python -m app.auction_fetcher.cli feeds astalegale_net --url <rss-url>
    python -m app.auction_fetcher.cli pvp-export /path/to/pvp-drop
    python -m app.auction_fetcher.cli sources

``feeds`` refuses to make a request unless the registry policy allows it; the
refusal is written to the access evidence log.
"""

from __future__ import annotations

import argparse
import json
import sys

from app.agent_import.policy import PolicyResolver
from app.auction_fetcher.pipeline import AuctionPipeline
from app.auction_fetcher.rss import RssAuctionFetcher
from app.core.config import get_settings
from app.fetchers.licence import LicenceRegistry
from app.fetchers.registry import SourceRegistry
from app.storage.datalake import DataLake


def _load_registries():
    registry = SourceRegistry.load()
    try:
        licences = LicenceRegistry.load()
    except FileNotFoundError:
        licences = LicenceRegistry([])
    return registry, licences


def _cmd_sources(args: argparse.Namespace) -> int:
    registry, licences = _load_registries()
    resolver = PolicyResolver(registry, licences=licences)
    rows = [
        {
            "source": source.name,
            "tier": source.tier,
            "status": source.status,
            "feed": source.feed,
            "rss": source.rss,
            "allowed": resolver.decide(source.name).allowed,
        }
        for source in registry.all()
        if source.kind == "auction"
    ]
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def _cmd_feeds(args: argparse.Namespace) -> int:
    registry, licences = _load_registries()
    config = registry.get(args.source)
    if config is None:
        print(f"unknown source: {args.source}", file=sys.stderr)
        return 2
    resolver = PolicyResolver(registry, licences=licences)
    settings = get_settings()
    fetcher = RssAuctionFetcher(
        config, resolver=resolver, datalake=DataLake(settings.datalake_root)
    )
    items, decision = fetcher.fetch(args.url)
    if not decision.allowed:
        print(f"REFUSED: {decision.reason}", file=sys.stderr)
        return 4

    report = AuctionPipeline(datalake=DataLake(settings.datalake_root)).ingest_feed_items(
        items, source=config.name
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _cmd_pvp_export(args: argparse.Namespace) -> int:
    report = AuctionPipeline().read_pvp_export(args.directory, source=args.source)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trei-auction", description="TREI auction feeds")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sources", help="auction sources and their policy verdict").set_defaults(
        func=_cmd_sources
    )

    feeds = sub.add_parser("feeds", help="read an authorized auction feed")
    feeds.add_argument("source")
    feeds.add_argument("--url", action="append", required=True, help="feed URL (repeatable)")
    feeds.set_defaults(func=_cmd_feeds)

    export = sub.add_parser("pvp-export", help="read a licensed PVP export drop")
    export.add_argument("directory")
    export.add_argument("--source", default="pvp_giustizia")
    export.set_defaults(func=_cmd_pvp_export)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
