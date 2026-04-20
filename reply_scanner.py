"""Inbox scanner — detects salesperson replies via the `[<MARKER>:NNNNN]` tag.

Runs each scheduler tick (or on demand). Walks recent inbox items in Outlook
desktop via win32com and records any whose subject contains the marker into
the reply_log table. The suppression module then uses those rows to decide
whether to keep escalating.

Why win32com instead of IMAP/EWS/Graph?
---------------------------------------
We already rely on Outlook desktop for *reading* the daily issues
emails; reusing the same auth path here means no extra credentials and no
admin intervention. The trade-off is that Outlook must be running on the
machine — acceptable for a single workstation deployment.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, schema


# The tag salespeople's replies will carry in the subject (or body, via
# quoted white-text marker). Captures the trailing order number. Built
# dynamically from config so the prefix can be customized per deployment.
MARKER_RE = re.compile(
    rf"\[{re.escape(config.TRACKING_MARKER_PREFIX)}:(\d{{4,6}})\]",
    re.IGNORECASE,
)


def _pywin_to_utc_iso(pywin_dt) -> str | None:
    """Convert a pywin32 datetime (from Outlook) to an ISO-8601 UTC string."""
    if pywin_dt is None:
        return None
    try:
        # pywin32 datetimes are pseudo-aware; extracting naive components
        # and reattaching UTC keeps things simple.
        naive = datetime(
            pywin_dt.year, pywin_dt.month, pywin_dt.day,
            pywin_dt.hour, pywin_dt.minute, pywin_dt.second,
        )
        return naive.replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return None


def scan_inbox(
    db_path: Path | None = None,
    lookback_days: int = 14,
) -> int:
    """Find reply-tagged messages in the Inbox and record them. Returns rows inserted.

    We look back ``lookback_days`` by default — a long enough window that a
    week-long escalation cycle can't miss a reply, but short enough to be
    fast. Idempotent via PRIMARY KEY (order_number, replied_at) — re-runs
    of the same scan don't duplicate.
    """
    import win32com.client   # lazy: Windows-only

    outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
    inbox = outlook.GetDefaultFolder(6)  # olFolderInbox

    items = inbox.Items
    try:
        items.Sort("[ReceivedTime]", True)   # newest first
    except Exception:
        pass

    cutoff = datetime.utcnow() - timedelta(days=lookback_days)

    conn = schema.connect(db_path)
    schema.ensure_tables(conn)
    inserted = 0

    try:
        with conn:  # transaction wrapper
            for item in items:
                try:
                    subject = (getattr(item, "Subject", "") or "").strip()
                    m = MARKER_RE.search(subject)
                    if not m:
                        continue
                    order_number = m.group(1)

                    received = getattr(item, "ReceivedTime", None)
                    iso = _pywin_to_utc_iso(received)
                    if iso is None:
                        continue
                    # Parse back for cutoff comparison (strip tz).
                    iso_naive = datetime.fromisoformat(iso).replace(tzinfo=None)
                    if iso_naive < cutoff:
                        # Items are sorted newest-first; once we pass the
                        # cutoff we can stop scanning.
                        break

                    sender = (getattr(item, "SenderName", "") or "").strip()

                    cur = conn.execute(
                        "INSERT OR IGNORE INTO reply_log "
                        "(order_number, replied_at, sender, subject) "
                        "VALUES (?, ?, ?, ?)",
                        (order_number, iso, sender, subject),
                    )
                    if cur.rowcount:
                        inserted += 1
                except Exception as exc:
                    # Defensive: a single malformed item shouldn't kill the scan.
                    print(f"  ! reply scan skipped item: {exc}")
                    continue
    finally:
        conn.close()

    return inserted
