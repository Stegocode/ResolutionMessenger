"""CLI entry point: `python -m ResolutionMessenger ...`.

Subcommands:
    tick              — run one notifier tick (sends emails unless --dry-run)
    scan-replies      — scan inbox for tracking-marker reply tags
    stale-report      — write the stale-order Markdown report
    all               — scan → tick → stale-report (the full cycle)
"""

from __future__ import annotations

import argparse
import sys

from . import notifier, reply_scanner, reports


def main(argv: list[str] | None = None) -> int:
    """Parse argv and dispatch to the right subcommand."""
    parser = argparse.ArgumentParser(prog="ResolutionMessenger")
    sub = parser.add_subparsers(dest="command", required=True)

    # `tick`
    p_tick = sub.add_parser("tick", help="run one notifier tick")
    p_tick.add_argument("--dry-run", action="store_true",
                        help="compute and log but do not send or record")
    p_tick.add_argument("--force-recipient", default=None,
                        help="override the TO address (debug/test only)")

    # `scan-replies`
    p_scan = sub.add_parser("scan-replies", help="scan inbox for reply markers")
    p_scan.add_argument("--lookback-days", type=int, default=14,
                        help="how far back to scan (default: 14)")

    # `stale-report`
    sub.add_parser("stale-report", help="write the stale-order Markdown report")

    # `all` — runs the whole cycle in order
    p_all = sub.add_parser("all", help="scan-replies, tick, stale-report")
    p_all.add_argument("--dry-run", action="store_true")
    p_all.add_argument("--force-recipient", default=None)

    args = parser.parse_args(argv)

    if args.command == "tick":
        notifier.run_tick(dry_run=args.dry_run, force_recipient=args.force_recipient)

    elif args.command == "scan-replies":
        inserted = reply_scanner.scan_inbox(lookback_days=args.lookback_days)
        print(f"reply_scanner: {inserted} new reply(ies) recorded")

    elif args.command == "stale-report":
        path = reports.write_stale_report()
        print(f"stale_report: wrote {path}")

    elif args.command == "all":
        inserted = reply_scanner.scan_inbox()
        print(f"reply_scanner: {inserted} new reply(ies) recorded")
        notifier.run_tick(dry_run=args.dry_run, force_recipient=args.force_recipient)
        path = reports.write_stale_report()
        print(f"stale_report: wrote {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
