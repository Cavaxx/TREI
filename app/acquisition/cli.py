"""Acquisition CLI.

    python -m app.acquisition.cli config-check
    python -m app.acquisition.cli route <source>
    python -m app.acquisition.cli report [--store PATH]
    python -m app.acquisition.cli kernel-smoke [--url URL]

``config-check`` and ``route`` never touch the network. ``kernel-smoke`` requires
``KERNEL_API_KEY`` and fails clearly with ``configuration_missing`` otherwise; it
never opens a session without the key. ``report`` reads the observation store and
never mixes in synthetic demo data.
"""

from __future__ import annotations

import argparse
import json
import sys

from app.acquisition.router import route_acquisition
from app.acquisition.store import ObservationStore
from app.core.config import get_settings
from app.core.envfile import load_env_file
from app.fetchers.registry import SourceRegistry


def _cmd_config_check(_args: argparse.Namespace) -> int:
    settings = get_settings()
    print(
        json.dumps(
            {
                "kernel_api_key_configured": bool(settings.kernel_api_key),
                "browser_provider": settings.browser_provider,
                "acquisition_mode": settings.acquisition_mode,
                "target_start_year": settings.target_start_year,
                "target_end_year": settings.target_end_year,
                "kernel_max_concurrency": settings.kernel_max_concurrency,
                "kernel_max_sessions_per_run": settings.kernel_max_sessions_per_run,
                "kernel_max_session_wall_seconds": settings.kernel_max_session_wall_seconds,
                "kernel_max_total_browser_seconds": settings.kernel_max_total_browser_seconds,
                "kernel_standby_timeout_seconds": settings.kernel_standby_timeout_seconds,
                "browser_max_download_mb": settings.browser_max_download_mb,
                "browser_max_pages_per_source": settings.browser_max_pages_per_source,
                "browser_max_records_per_source": settings.browser_max_records_per_source,
                "browser_max_retries": settings.browser_max_retries,
                "browser_allowed_domains": list(settings.browser_allowed_domains),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def _cmd_route(args: argparse.Namespace) -> int:
    registry = SourceRegistry.load()
    config = registry.get(args.source)
    if config is None:
        print(f"unknown source: {args.source}", file=sys.stderr)
        return 2
    print(json.dumps(route_acquisition(config).to_dict(), ensure_ascii=False, indent=2))
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    store = ObservationStore(args.store)
    records = store.all()
    print(
        json.dumps(
            {"records": len(records), "coverage": store.coverage(), "observations": records},
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def _cmd_kernel_smoke(args: argparse.Namespace) -> int:
    from app.browser import BrowserProviderError, KernelBrowserProvider

    settings = get_settings()
    provider = KernelBrowserProvider(settings=settings)
    if not provider.configured:
        print(
            json.dumps(
                {
                    "ok": False,
                    "failure": "configuration_missing",
                    "message": "KERNEL_API_KEY is not set; no session was opened",
                }
            )
        )
        return 3

    url = args.url or "https://statweb.provincia.tn.it/annuario/"
    session = None
    try:
        session = provider.open_session(start_url=url, name="trei-kernel-smoke")
        result = provider.execute(session, "return await page.title();")
        print(
            json.dumps(
                {
                    "ok": result.success,
                    "session_id": session.session_id,
                    "title": result.result,
                    "error": result.error,
                },
                ensure_ascii=False,
            )
        )
        return 0 if result.success else 4
    except BrowserProviderError as exc:
        print(json.dumps({"ok": False, "failure": exc.failure.value, "message": str(exc)}, ensure_ascii=False))
        return 1
    finally:
        if session is not None:
            try:
                session.close()
            except BrowserProviderError as exc:
                print(json.dumps({"cleanup": exc.to_dict()}, ensure_ascii=False), file=sys.stderr)


def _cmd_pilot(args: argparse.Namespace) -> int:
    from app.acquisition.pilot import run_pilot

    report = run_pilot(store_path=args.store, budget_path=args.budget)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "completed" else (1 if report["status"] == "partial" else 2)


def main(argv: list[str] | None = None) -> int:
    load_env_file()
    parser = argparse.ArgumentParser(prog="trei-acquire", description="TREI acquisition CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("config-check").set_defaults(func=_cmd_config_check)

    p_route = sub.add_parser("route", help="show the acquisition method for one source")
    p_route.add_argument("source")
    p_route.set_defaults(func=_cmd_route)

    p_report = sub.add_parser("report", help="read the observation store (no network)")
    p_report.add_argument("--store", default=None)
    p_report.set_defaults(func=_cmd_report)

    p_smoke = sub.add_parser("kernel-smoke", help="open, read and release one Kernel session")
    p_smoke.add_argument("--url", default=None)
    p_smoke.set_defaults(func=_cmd_kernel_smoke)

    p_pilot = sub.add_parser("pilot", help="run the ISPAT pilot acquisition (requires KERNEL_API_KEY)")
    p_pilot.add_argument("--store", default=None)
    p_pilot.add_argument("--budget", default=None)
    p_pilot.set_defaults(func=_cmd_pilot)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
