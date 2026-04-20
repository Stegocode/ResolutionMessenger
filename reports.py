"""Stale-order report — the "what fell through the cracks" snapshot.

Produces a Markdown file listing every item currently at NEEDS ATTENTION
on the board whose REQUESTED DELIVERY DATE is in the past. These are
orders that *should have delivered but didn't* — usually because the
issue wasn't resolved in time and operations had to reschedule, but
sometimes because the item never got closed out properly.

Design note
-----------
This report does NOT send email. It writes a file to the configured
reports folder, which a human (ops/dispatcher) glances at weekly. That's
intentional: a stuck item already ignored several days of escalation
emails — sending a "final final" email wouldn't help. A written log
that a human scans is more useful than another nag.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from . import board_client, config, escalation


def write_stale_report(out_dir: Path | None = None, today: date | None = None) -> Path:
    """Render the stale-order report to a timestamped Markdown file.

    Returns the full path of the written file so the caller can log/open it.
    """
    today = today or escalation.now_in_ops_tz().date()
    out_dir = out_dir or config.REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"stale_orders_{today.isoformat()}.md"

    items = board_client.iter_open_items()
    rows: list[dict] = []
    for item in items:
        delivery_iso = board_client.column_value_text(item, config.COL_DELIVERY)
        delivery_date = escalation.parse_delivery_date(delivery_iso)
        if delivery_date is None or delivery_date >= today:
            continue
        days_past = (today - delivery_date).days
        if days_past > config.STALE_REPORT_WINDOW_DAYS:
            # Items past the visibility window are ignored — they're either
            # truly abandoned or need a different workflow entirely.
            continue

        allocate = board_client.column_value_text(item, config.COL_ALLOCATE)
        paid     = board_client.column_value_text(item, config.COL_PAID_IN_FULL)
        rows.append({
            "name":     item.get("name", ""),
            "days":     days_past,
            "delivery": delivery_iso,
            "allocate": allocate or "—",
            "paid":     paid or "—",
        })

    # Sort: oldest stuck items first (highest days_past). Everyone finds
    # those more interesting than the items that just missed yesterday.
    rows.sort(key=lambda r: (-r["days"], r["delivery"]))

    # Markdown body
    lines: list[str] = []
    lines.append("# Stale Orders — Still at NEEDS ATTENTION past delivery date")
    lines.append(f"_Generated {today.isoformat()} from live board state._")
    lines.append("")
    if not rows:
        lines.append("_No stale orders. All past-delivery items have been cleared._")
    else:
        lines.append(f"**{len(rows)} stuck order(s):**")
        lines.append("")
        lines.append("| Days Past | Delivery Date | Item | ALLOCATE | PAID? |")
        lines.append("|---|---|---|---|---|")
        for r in rows:
            # Escape pipes in the name so they don't break the table.
            safe_name = (r["name"] or "").replace("|", "\\|")
            lines.append(
                f"| {r['days']} | {r['delivery']} | {safe_name} "
                f"| {r['allocate']} | {r['paid']} |"
            )

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
