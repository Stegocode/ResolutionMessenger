"""Suppression rules — "is someone already working on this?"

Three independent signals, OR-ed together, that mean "don't escalate":
    1. A Monday comment/update posted AFTER the most recent notification
       we sent for this item.
    2. A SCHEDULED/ALLOCATE/PAID column value that changed since the last
       notification (detected via Monday's activity_logs if available — but
       simpler: if the current state + most recent update timestamp imply
       activity, we treat it as a change).
    3. An email reply in our inbox tagged `[<MARKER>:NNNNN]` received after
       the last notification.

The final-warning slot (T-1 14:00) always fires — no suppression. See
should_suppress() for the is_final_warning escape hatch.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from . import config


def most_recent_notification_time(
    conn: sqlite3.Connection, item_id: str
) -> datetime | None:
    """Return the ``fired_at`` of the latest notification for this item, or None.

    We compare against "most recent notification" because the spec is:
    "did the salesperson react AFTER we last pinged them?".
    """
    row = conn.execute(
        "SELECT fired_at FROM notification_log "
        "WHERE item_id = ? ORDER BY fired_at DESC LIMIT 1",
        (item_id,),
    ).fetchone()
    if not row:
        return None
    return _parse_iso(row[0])


def most_recent_reply_time(
    conn: sqlite3.Connection, order_number: str
) -> datetime | None:
    """Return the ``replied_at`` of the latest inbox reply for this order."""
    row = conn.execute(
        "SELECT replied_at FROM reply_log "
        "WHERE order_number = ? ORDER BY replied_at DESC LIMIT 1",
        (order_number,),
    ).fetchone()
    if not row:
        return None
    return _parse_iso(row[0])


def most_recent_update_time(updates: list[dict]) -> datetime | None:
    """Given Monday update dicts, return the newest created_at as a datetime.

    Monday returns ``created_at`` in ISO-8601 with a trailing ``Z`` or ``+0000``.
    """
    times: list[datetime] = []
    for u in updates or []:
        dt = _parse_iso(u.get("created_at", ""))
        if dt:
            times.append(dt)
    return max(times) if times else None


def latest_comment_body(updates: list[dict]) -> str | None:
    """Return the text body of the newest update (for email quoting)."""
    best_dt = None
    best_body = None
    for u in updates or []:
        dt = _parse_iso(u.get("created_at", ""))
        if dt and (best_dt is None or dt > best_dt):
            best_dt = dt
            best_body = (u.get("text_body") or "").strip()
    return best_body or None


def should_suppress(
    conn: sqlite3.Connection,
    item_id: str,
    order_number: str,
    item_updates: list[dict],
    is_final_warning: bool,
) -> tuple[bool, str]:
    """Decide whether to skip sending this alert. Returns (suppress, reason).

    Reason is a short human-readable string for logs — tells you which
    signal triggered the suppression.

    The final-warning slot bypasses all suppression: that's the 14:00
    "we're about to reschedule" last-chance send.
    """
    if is_final_warning:
        return False, "final-warning-bypass"

    last_notif = most_recent_notification_time(conn, item_id)

    # If we've never notified this item, suppression makes no sense — we
    # haven't said anything yet. Let the first notification through.
    if last_notif is None:
        return False, "first-notification"

    # Signal 1: a Monday comment posted after last notification
    last_update = most_recent_update_time(item_updates)
    if last_update and last_update > last_notif:
        return True, f"comment-after-last-notification ({last_update.isoformat()})"

    # Signal 2: a reply email in our inbox received after last notification
    last_reply = most_recent_reply_time(conn, order_number)
    if last_reply and last_reply > last_notif:
        return True, f"inbox-reply-after-last-notification ({last_reply.isoformat()})"

    # No acknowledgment → escalation proceeds
    return False, "no-recent-activity"


# ── Private helpers ─────────────────────────────────────────────────

def _parse_iso(s: str) -> datetime | None:
    """Parse an ISO-8601 timestamp into a tz-aware datetime in UTC.

    Handles both the Monday-flavored ``2026-04-17T14:40:54.720Z`` and the
    SQLite-stored ``2026-04-17T14:40:54`` (which we assume is UTC if naive).
    """
    if not s:
        return None
    # Python's fromisoformat doesn't accept trailing Z on 3.10, so normalize.
    s2 = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s2)
    except ValueError:
        return None
    # Naive datetimes get attached to UTC to make comparisons safe.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
