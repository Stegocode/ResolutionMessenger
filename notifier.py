"""Main orchestrator — one tick of the notifier.

`run_tick()` is the single public entry point. On each call:

    1. Pull every board item in the NEEDS ATTENTION group.
    2. For each item:
       - Read its delivery date and compute which fire slots are due.
       - Drop slots already in notification_log.
       - For each remaining slot, check suppression rules.
       - If not suppressed, render + send + record.

The function returns a dict of counts so the scheduler can log progress.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import (
    board_client,
    config,
    escalation,
    recipients,
    schema,
    suppression,
    templates,
)


# Regex for lifting the order number from a Monday item name like
# "27341 Joe Customer".
_ORDER_PREFIX_RE = re.compile(r"^\s*(\d{4,6})\b")


# ── Email send (Outlook COM) ────────────────────────────────────────
# Kept local to this module so it can be monkeypatched by tests.

def _send_via_outlook(
    to: str,
    subject: str,
    body_html: str,
    cc: list[str] | None = None,
) -> None:
    """Send an HTML email through local Outlook desktop.

    Uses the signed-in profile's auth (MAPI/COM) — bypasses the Microsoft
    365 tenant's SMTP-AUTH disable. Requires Outlook to be running.
    """
    import win32com.client  # lazy: Windows-only

    outlook = win32com.client.Dispatch("Outlook.Application")
    mail = outlook.CreateItem(0)  # olMailItem
    mail.To      = to
    mail.Subject = subject
    mail.HTMLBody = body_html
    if cc:
        mail.CC = "; ".join(cc)
    if config.NOTIFY_BCC:
        mail.BCC = config.NOTIFY_BCC.replace(",", ";")
    mail.Send()


# ── Utility: lift order number + customer out of a Monday item name ─

def _split_item_name(item_name: str) -> tuple[str | None, str]:
    """Return (order_number, customer_name) from 'NNNNN Customer'."""
    m = _ORDER_PREFIX_RE.match(item_name or "")
    if not m:
        return None, item_name.strip()
    order = m.group(1)
    customer = (item_name[m.end():] or "").strip()
    return order, customer


# ── DB helpers for the main loop ────────────────────────────────────

def _already_fired(conn: sqlite3.Connection, item_id: str, fire_key: str) -> bool:
    """True if this exact (item_id, fire_key) has been recorded."""
    row = conn.execute(
        "SELECT 1 FROM notification_log WHERE item_id = ? AND fire_key = ?",
        (item_id, fire_key),
    ).fetchone()
    return row is not None


def _record_send(
    conn: sqlite3.Connection,
    item_id: str,
    fire_key: str,
    recipient: str,
    template_key: str,
) -> None:
    """Insert the notification_log row for a send."""
    conn.execute(
        "INSERT OR IGNORE INTO notification_log "
        "(item_id, fire_key, fired_at, recipient, template) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            item_id,
            fire_key,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            recipient,
            template_key,
        ),
    )


# ── Public orchestrator ─────────────────────────────────────────────

def run_tick(
    db_path: Path | None = None,
    dry_run: bool = False,
    force_recipient: str | None = None,
    now: datetime | None = None,
) -> dict:
    """One pass of the notifier.

    Args:
        db_path: override the SQLite DB (tests use a temp path).
        dry_run: if True, compute everything but don't send or record.
        force_recipient: override the computed TO address (debugging).
        now: override the current time (tests).

    Returns a counts dict. Side effects:
        - Sends emails (unless dry_run).
        - Writes to notification_log (unless dry_run).
    """
    current_time = now or escalation.now_in_ops_tz()
    counts = {
        "items_scanned": 0,
        "slots_computed": 0,
        "already_fired": 0,
        "suppressed": 0,
        "sent": 0,
        "errors": 0,
        "skipped_no_date": 0,
    }

    conn = schema.connect(db_path)
    schema.ensure_tables(conn)

    try:
        items = list(board_client.iter_open_items())
    except Exception as exc:
        print(f"! board fetch failed: {exc}", file=sys.stderr)
        conn.close()
        return counts

    try:
        for item in items:
            counts["items_scanned"] += 1
            item_id = str(item.get("id", ""))
            item_name = item.get("name", "")
            order_number, customer = _split_item_name(item_name)
            if not order_number:
                continue

            delivery_iso = board_client.column_value_text(item, config.COL_DELIVERY)
            delivery_date = escalation.parse_delivery_date(delivery_iso)
            if delivery_date is None:
                counts["skipped_no_date"] += 1
                continue

            slots = escalation.slots_due_for_item(item_id, delivery_date, current_time)
            counts["slots_computed"] += len(slots)
            if not slots:
                continue

            # Only sending the LATEST due slot per item per tick avoids firing
            # multiple stacked emails in one tick if several slots passed
            # (e.g., after a scheduler outage). The older ones will still be
            # recorded as "already fired" on the next tick because we don't
            # actually record them — they're effectively skipped forever.
            # This is a deliberate choice: one email at most per tick per item.
            slot = slots[-1]

            if _already_fired(conn, item_id, slot.fire_key):
                counts["already_fired"] += 1
                continue

            # Suppression gate — comment / reply / activity
            item_updates = _safe_fetch_updates(item_id)
            suppress, reason = suppression.should_suppress(
                conn, item_id, order_number, item_updates, slot.is_final
            )
            if suppress:
                counts["suppressed"] += 1
                print(f"  [SKIP] {order_number} slot={slot.fire_key} — {reason}")
                continue

            # Figure out template + recipients
            template_key = recipients.pick_template_key(item)
            if force_recipient:
                to_addr = force_recipient
                cc_list: list[str] = []
            else:
                to_addr, cc_list = recipients.resolve_recipients(item)

            if not to_addr:
                print(
                    f"  ! no recipient for order {order_number} "
                    f"({slot.fire_key}) — skipped",
                    file=sys.stderr,
                )
                counts["errors"] += 1
                continue

            # Body content
            po_numbers    = None  # could be sourced from an issues DB later
            model_numbers = None
            issue_summary = templates.TEMPLATES[template_key]["title"]
            last_comment  = suppression.latest_comment_body(item_updates)

            mailto_url = templates.build_mailto(
                order_number, customer, delivery_iso,
                template_key, issue_summary,
            )
            html_body = templates.render_html(
                template_key=template_key,
                order_number=order_number,
                customer=customer,
                delivery_date=delivery_iso,
                days_before=slot.days_before,
                threshold_label=slot.threshold,
                po_numbers=po_numbers,
                model_numbers=model_numbers,
                issue_summary=issue_summary,
                mailto_url=mailto_url,
                is_final_warning=slot.is_final,
                last_comment=last_comment,
            )
            subject = templates.subject_for(template_key, order_number, slot.is_final)

            if dry_run:
                print(
                    f"  [DRY-RUN] {slot.fire_key} -> {to_addr}  "
                    f"cc={len(cc_list)}  tmpl={template_key}  order={order_number}"
                )
                continue

            try:
                _send_via_outlook(to_addr, subject, html_body, cc=cc_list)
                with conn:
                    _record_send(conn, item_id, slot.fire_key, to_addr, template_key)
                counts["sent"] += 1
                print(
                    f"  [SENT] {slot.fire_key} -> {to_addr}  "
                    f"tmpl={template_key}  order={order_number}"
                )
            except Exception as exc:
                counts["errors"] += 1
                print(f"  ! send failed for order {order_number}: {exc}", file=sys.stderr)

    finally:
        conn.close()

    summary = ", ".join(f"{k}={v}" for k, v in counts.items())
    print(f"notifier tick done: {summary}")
    return counts


def _safe_fetch_updates(item_id: str) -> list[dict]:
    """Fetch updates with defensive error handling (one bad item shouldn't kill tick)."""
    try:
        return board_client.fetch_item_updates(item_id)
    except Exception as exc:
        print(f"  ! update fetch failed for item {item_id}: {exc}", file=sys.stderr)
        return []
