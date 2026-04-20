"""Escalation scheduling engine.

Given a delivery date, a "now" timestamp, and an item id, this module tells
us exactly which notification slot (if any) is due to fire right now.

Design: the schedule is expressed as a dict in config.SCHEDULE — a mapping
from "days-before-delivery" to a tuple of 24-hour slot hours.  We never
"catch up" on missed slots; each call returns *the slot that just became
due*, if any. The 30-min scheduler tick is what drives us forward in time.

Key concepts:
    - fire_key: "YYYY-MM-DD_HHMM_T-N" — stable primary key for a single
      (day, hour, threshold) send slot. Two ticks at different wall-times
      will generate the same fire_key for the same slot, which is what lets
      notification_log dedup cleanly.
    - business day: Mon-Fri, excluding entries in config.HOLIDAYS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from . import config


@dataclass(frozen=True)
class FireSlot:
    """A single notification instance that the engine says should fire.

    Attributes:
        threshold:    T-N label (e.g., "T-3")
        days_before:  integer days between today and delivery_date
        slot_dt:      the tz-aware datetime at which this slot "opens"
        fire_key:     unique identifier for (item, slot) dedup
        is_final:     True if this is the last-warning T-1 14:00 slot
    """
    threshold: str
    days_before: int
    slot_dt: datetime
    fire_key: str
    is_final: bool


# ── Business-day arithmetic ────────────────────────────────────────

def _is_business_day(d: date) -> bool:
    """Monday=0 through Friday=4 are business days, unless on holiday list.

    Note: ISO weekday() returns 0-6 where Monday=0; that's what we want.
    """
    if d.weekday() >= 5:          # Saturday / Sunday
        return False
    if d.isoformat() in config.HOLIDAYS:  # explicit holiday
        return False
    return True


# ── Public: what should fire now? ──────────────────────────────────

def slots_due_for_item(
    item_id: str,
    delivery_date: date,
    now: datetime,
) -> list[FireSlot]:
    """Return every slot for this item that has opened but not fired yet.

    The caller is expected to intersect this with notification_log to drop
    already-fired slots — this function does not consult the DB.

    Rationale for "every slot that has opened":
        If the scheduler ticks at 10:35 and slots opened at 09:00 and 10:00
        today, BOTH are due (if not yet fired). This lets us survive a
        temporarily-down scheduler and still send what was owed. However,
        the notification_log dedup means re-runs don't resend.
    """
    today = now.astimezone(config.OPS_TZ).date()
    days_until = (delivery_date - today).days

    # If delivery is more than 7 days out or already past, no slots fire.
    # (We don't send a "T-0" delivery-day email at all — per spec.)
    if days_until < 1 or days_until > 7:
        return []

    # If today isn't a business day, nothing fires. We explicitly do NOT
    # roll slots forward to the next business day — spec says "no catchup".
    if not _is_business_day(today):
        return []

    # Look up today's slot hours for this threshold. If there's no entry,
    # this T-N day isn't scheduled to fire (shouldn't happen for 1..7).
    hours = config.SCHEDULE.get(days_until)
    if not hours:
        return []

    slots: list[FireSlot] = []
    for hour in hours:
        slot_dt = datetime.combine(today, time(hour=hour), tzinfo=config.OPS_TZ)
        # Only include slots that have already opened (<= now).
        if slot_dt > now.astimezone(config.OPS_TZ):
            continue
        threshold = f"T-{days_until}"
        fire_key = f"{today.isoformat()}_{hour:02d}00_{threshold}"
        is_final = (days_until, hour) == config.FINAL_WARNING_SLOT
        slots.append(
            FireSlot(
                threshold=threshold,
                days_before=days_until,
                slot_dt=slot_dt,
                fire_key=fire_key,
                is_final=is_final,
            )
        )
    return slots


def parse_delivery_date(s: str) -> date | None:
    """Parse an ISO YYYY-MM-DD string to a date. Returns None on empty/invalid."""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def now_in_ops_tz() -> datetime:
    """Return the current moment as a timezone-aware datetime in OPS_TZ.

    We wrap this so tests can monkeypatch one function instead of datetime.now.
    """
    return datetime.now(tz=config.OPS_TZ)
