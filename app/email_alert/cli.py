"""Command-line entry point for the email channel (§4).

Usage::

    python -m app.email_alert.cli ingest --limit 50
    python -m app.email_alert.cli ingest --mailbox /path/to/inbox --json
    python -m app.email_alert.cli profiles

``ingest`` reads the configured mailbox (IMAP when TREI_IMAP_HOST is set, the
``.eml`` drop directory otherwise) and reports what it produced. Refusal-free by
construction: reading your own mailbox is not a policy question.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.core.config import get_settings
from app.email_alert.ingest import EmailIngestPipeline
from app.email_alert.mailbox import FileMailbox, from_settings
from app.email_alert.profiles import PROFILES
from app.property_master.master import PropertyMasterStore
from app.storage.datalake import DataLake


def _cmd_ingest(args: argparse.Namespace) -> int:
    settings = get_settings()
    mailbox = FileMailbox(args.mailbox) if args.mailbox else from_settings()
    store = PropertyMasterStore()
    pipeline = EmailIngestPipeline(
        store,
        mailbox=mailbox,
        datalake=DataLake(args.datalake or settings.datalake_root),
        review_threshold=settings.review_confidence_threshold,
    )
    report = pipeline.run(limit=args.limit)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"messages seen      : {report.messages_seen}")
        print(f"duplicates skipped : {report.messages_duplicate}")
        print(f"listings stored    : {report.stored}")
        print(f"review required    : {report.review_required}")
        print(f"out of territory   : {report.out_of_territory}")
        for warning in report.warnings:
            print(f"  ! {warning}")
        for error in report.errors:
            print(f"  x {error}", file=sys.stderr)
    return 0


def _cmd_profiles(_args: argparse.Namespace) -> int:
    print(f"{'profile':18} sender domains")
    print("-" * 70)
    for profile in PROFILES:
        print(f"{profile.name:18} {', '.join(profile.sender_domains) or '(generic)'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trei-email", description="TREI email alert ingest")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ingest = sub.add_parser("ingest", help="read the mailbox and store observations")
    ingest.add_argument("--limit", type=int, default=None)
    ingest.add_argument("--mailbox", help="directory of .eml files (overrides settings)")
    ingest.add_argument("--datalake", help="raw-object root (overrides settings)")
    ingest.add_argument("--json", action="store_true")
    ingest.set_defaults(func=_cmd_ingest)

    sub.add_parser("profiles").set_defaults(func=_cmd_profiles)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


def default_inbox() -> Path:
    """Where the operator drops legitimate .eml alerts when IMAP is not used."""
    return get_settings().email_drop_dir
