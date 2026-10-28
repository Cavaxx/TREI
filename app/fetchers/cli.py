"""Command-line entry point for the fetcher layer.

Usage:
    python -m app.fetchers.cli list
    python -m app.fetchers.cli restricted
    python -m app.fetchers.cli check-robots <url> [<url> ...]

The registry is the source of truth; ``list``/``restricted`` never touch the
network. ``check-robots`` performs a live, polite robots.txt check and is the
only command that requires network access.
"""

from __future__ import annotations

import argparse
import json
import sys

from app.core.config import get_settings
from app.fetchers.adapters.feed import AuthorizedFeedAdapter, FeedScopeError
from app.fetchers.credentials import MissingCredentials
from app.fetchers.licence import LicenceRegistry, NotLicensed
from app.fetchers.registry import SourceRegistry
from app.fetchers.robots import RobotsCache


def _cmd_list(_args: argparse.Namespace) -> int:
    reg = SourceRegistry.load()
    print(f"{'source':22} {'tier':18} {'status':16} {'auto':5} mode")
    print("-" * 90)
    for s in reg.all():
        print(
            f"{s.name:22} {s.tier:18} {s.status:16} "
            f"{'yes' if s.auto_acquisition_allowed else 'no ':5} {s.recommended_mode}"
        )
    print(f"\n{len(reg.all())} sources, {len(reg.auto_enabled())} auto-enabled.")
    return 0


def _cmd_restricted(_args: argparse.Namespace) -> int:
    reg = SourceRegistry.load()
    restricted = reg.restricted()
    print(f"{len(restricted)} restricted / license-required sources:")
    for s in restricted:
        print(f"  - {s.name} [{s.tier}/{s.status}] -> {s.recommended_mode}")
    return 0


def _cmd_licences(_args: argparse.Namespace) -> int:
    """Show every registered source against the licence register.

    This is the operator's due-diligence view: a source with no active licence
    simply cannot be acquired automatically, by construction.
    """
    reg = SourceRegistry.load()
    licences = LicenceRegistry.load()
    print(f"{'source':22} {'licence':14} {'grantor':26} endpoints")
    print("-" * 90)
    for source in reg.all():
        state = licences.status(source.name)
        record = licences.active_for(source.name)
        grantor = record.grantor if record else ""
        count = len(record.endpoints) if record else 0
        print(f"{source.name:22} {state:14} {grantor:26} {count}")
    print(f"\n{len(licences.all())} licence record(s). No licence => no automated access.")
    return 0


def _cmd_feed(args: argparse.Namespace) -> int:
    """Run one authorized feed. Refuses without a covering licence."""
    if not args.url and not args.directory:
        print("provide --url (licensed API) or --directory (licensed export drop)", file=sys.stderr)
        return 2
    config = SourceRegistry.load().get(args.source)
    if config is None:
        print(f"unknown source: {args.source}", file=sys.stderr)
        return 2

    adapter = AuthorizedFeedAdapter(
        config,
        urls=args.url,
        directory=args.directory,
        fmt=args.format,
        records_path=args.records_path,
        record_tag=args.record_tag,
        next_field=args.next_field,
        max_pages=args.max_pages,
        actor=args.actor,
    )
    try:
        result = adapter.run()
    except (NotLicensed, MissingCredentials, FeedScopeError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 4

    print(
        json.dumps(
            {
                "source": result.source,
                "status": result.status.value,
                "records": len(result.records),
                "content_hash": result.content_hash,
                "error": result.error,
                "channel": adapter.credential_summary(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_check_robots(args: argparse.Namespace) -> int:
    settings = get_settings()
    cache = RobotsCache(settings.user_agent)
    rc = 0
    for url in args.urls:
        decision = cache.check(url)
        flag = "ALLOW" if decision.allowed else "DENY "
        print(f"{flag} {url}  ({decision.reason})")
        rc = rc or (0 if decision.allowed else 3)
    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trei-fetch", description="TREI fetcher CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(func=_cmd_list)
    sub.add_parser("restricted").set_defaults(func=_cmd_restricted)
    sub.add_parser("licences").set_defaults(func=_cmd_licences)

    p_feed = sub.add_parser("feed", help="pull a source through its licensed channel")
    p_feed.add_argument("source", help="registry source name")
    p_feed.add_argument("--url", action="append", help="licensed endpoint (repeatable)")
    p_feed.add_argument("--directory", help="folder holding a licensed export drop")
    p_feed.add_argument("--format", default="json", choices=["json", "jsonl", "csv", "xml"])
    p_feed.add_argument("--records-path", dest="records_path", default=None)
    p_feed.add_argument("--record-tag", dest="record_tag", default=None)
    p_feed.add_argument("--next-field", dest="next_field", default=None)
    p_feed.add_argument("--max-pages", dest="max_pages", type=int, default=1)
    p_feed.add_argument("--actor", default="cli", help="who is running this (audit log)")
    p_feed.set_defaults(func=_cmd_feed)

    p_robots = sub.add_parser("check-robots")
    p_robots.add_argument("urls", nargs="+")
    p_robots.set_defaults(func=_cmd_check_robots)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
