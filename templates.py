"""HTML email templates and the REPLY TO OPERATIONS mailto builder.

Three visually-distinct alert bodies:
    - UNPAID_BALANCE      — payment issue, red banner
    - ITEM_NOT_ON_PO      — stock issue without a PO, red banner
    - PIECES_ON_PO        — supply chain issue with a PO, red banner
    - FINAL_WARNING        — T-1 14:00 "last warning", BIGGER/scarier styling

All HTML uses inline styles because Outlook desktop (the recipient env) is
famously hostile to <style> blocks. Tables-in-tables is the canonical
email-safe layout pattern.
"""

from __future__ import annotations

import html as _html
from datetime import date
from urllib.parse import quote

from . import config, recipients


# Button label shown on the big red call-to-action.
BUTTON_LABEL = "REPLY TO OPERATIONS"


# ── Template copy ────────────────────────────────────────────────────
# Each template has a title (banner line) and an intro paragraph (body
# text). Kept as dicts so we can add fields later (e.g., a severity color)
# without changing call sites.

TEMPLATES: dict[str, dict[str, str]] = {
    recipients.TEMPLATE_UNPAID: {
        "title":   "Delivery On Hold — Unpaid Balance",
        "subject": "ALERT — Delivery On Hold — Unpaid Balance",
        "intro": (
            "Operations has placed this delivery on hold due to an outstanding "
            "balance. Please be advised that we will not pick or load this order "
            "until payment has been received in full. Payments received after "
            "2:00 PM may result in rescheduling. Please coordinate with your "
            "customer immediately and notify Operations once payment has been "
            "confirmed so we can assess scheduling availability."
        ),
    },
    recipients.TEMPLATE_SUPPLY_NO_PO: {
        "title":   "Immediate Attention Needed — Item Not on PO",
        "subject": "ALERT — Immediate Attention Needed — Item Not on PO",
        "intro": (
            "Operations has a delivery scheduled that requires your immediate "
            "attention. One or more items on this order have not been placed on "
            "a PO and have not been received. Please reference the daily issues "
            "email for order details and confirm a PO has been created and "
            "provide an ETA at your earliest convenience. Timely response is "
            "critical to keeping this delivery on schedule."
        ),
    },
    recipients.TEMPLATE_SUPPLY_WITH_PO: {
        "title":   "Immediate Attention Needed",
        "subject": "ALERT — Immediate Attention Needed",
        "intro": (
            "Operations has a delivery scheduled that requires your immediate "
            "attention. One or more items on this order show pieces on PO that "
            "have not yet been received. Please reference the daily issues "
            "email for order details and provide an ETA at your earliest "
            "convenience. Timely response is critical to keeping this delivery "
            "on schedule."
        ),
    },
}

# Final-warning overlay — reuses whichever template applies but with louder
# framing. The 14:00 T-1 slot is the last auto-send before dispatch
# reschedules the delivery at 15:00.
FINAL_WARNING_TITLE = "LAST WARNING — Reschedule at 15:00"
FINAL_WARNING_INTRO = (
    "This is the FINAL auto-notification for this delivery. If no resolution "
    "is confirmed by 3:00 PM today, Operations will reschedule this delivery. "
    "Please reply immediately or update the Monday board."
)


# ── Mailto builder ──────────────────────────────────────────────────

def build_mailto(order_number: str, customer: str, delivery_date: str,
                 template_key: str, issue_summary: str) -> str:
    """Build the mailto: URL used by the REPLY TO OPERATIONS button.

    The URL pre-fills:
        - To:      operations inbox
        - CC:      the accountability-team static list
        - Subject: contains the [<MARKER>:NNNNN] tag for inbox detection
        - Body:    context block + marker repeated (belt-and-suspenders)

    Returns a fully URL-encoded mailto: string safe to drop in an href.
    """
    marker = f"{config.TRACKING_MARKER_PREFIX}:{order_number}"
    template_title = TEMPLATES.get(template_key, {}).get("title", "Reply")
    subject = f"[{marker}] Re: Order {order_number} — {template_title}"

    # \r\n line endings are required for mail clients to render newlines
    # correctly when the mailto body is encoded.
    body = (
        f"Operations,\r\n\r\n"
        f"[Type your resolution details below this line]\r\n\r\n\r\n"
        f"---\r\n"
        f"Order: {order_number} — {customer}\r\n"
        f"Delivery: {delivery_date}\r\n"
        f"Issue: {issue_summary}\r\n\r\n"
        f"---\r\n{marker}\r\n---\r\n"
    )
    return (
        f"mailto:{config.REPLY_TO_ADDRESS}"
        f"?cc={','.join(config.REPLY_CC_ADDRESSES)}"
        f"&subject={quote(subject)}"
        f"&body={quote(body)}"
    )


# ── HTML rendering ──────────────────────────────────────────────────

def _detail_row(label: str, value: str) -> str:
    """Render a single two-cell <tr> for the order-details mini-table.

    Every value goes through html.escape() to prevent template injection if
    any field ever contains user-controlled content. We want invisible-on-
    rendering behavior for the hidden marker, but visible content must be
    safely escaped.
    """
    h = _html.escape
    return (
        f'<tr><td style="padding:6px 12px 6px 0;color:#6c757d;font-size:13px;'
        f'white-space:nowrap;vertical-align:top;">{h(label)}</td>'
        f'<td style="padding:6px 0;color:#212529;font-size:14px;">{h(value)}</td></tr>'
    )


def render_html(
    template_key: str,
    order_number: str,
    customer: str,
    delivery_date: str,
    days_before: int,
    threshold_label: str,
    po_numbers: str | None,
    model_numbers: str | None,
    issue_summary: str,
    mailto_url: str,
    is_final_warning: bool,
    last_comment: str | None = None,
) -> str:
    """Render the alert email body as HTML.

    Inline styles only. No web fonts. All widths fixed because Outlook
    desktop's rendering engine is based on Word and it doesn't do
    responsive layout.

    Args:
        template_key: one of the keys in TEMPLATES.
        is_final_warning: if True, use the louder "LAST WARNING" framing.
        last_comment: if provided, quotes the most recent Monday comment
            inline so the salesperson sees their own prior commitment.
    """
    h = _html.escape

    template = TEMPLATES.get(template_key, TEMPLATES[recipients.TEMPLATE_SUPPLY_WITH_PO])

    # Final-warning overrides headline; keep the body intro but prepend urgency.
    if is_final_warning:
        banner_title = FINAL_WARNING_TITLE
        banner_bg    = "#000000"   # black banner = scariest
        banner_icon  = "🚨"
        urgent_line  = (
            f'<div style="background-color:#fff3cd;border:1px solid #ffc107;'
            f'border-radius:4px;padding:12px 14px;margin-bottom:18px;'
            f'color:#856404;font-weight:bold;font-size:14px;">{h(FINAL_WARNING_INTRO)}</div>'
        )
    else:
        banner_title = template["title"]
        banner_bg    = "#dc3545"   # standard red banner
        banner_icon  = "⚠️"
        urgent_line  = ""

    detail_rows = [
        _detail_row("Order #",       order_number),
        _detail_row("Customer",      customer or "—"),
        _detail_row("Delivery Date", f"{delivery_date}  ({days_before} day(s) out)"),
        _detail_row("Issue",         issue_summary),
    ]
    if po_numbers:
        detail_rows.append(_detail_row("PO #",    po_numbers))
    if model_numbers:
        detail_rows.append(_detail_row("Model #", model_numbers))

    comment_block = ""
    if last_comment:
        # Clip very long comments so the email stays scannable.
        snippet = (last_comment[:400] + "…") if len(last_comment) > 400 else last_comment
        comment_block = (
            f'<div style="background-color:#e7f3ff;border-left:4px solid #0d6efd;'
            f'padding:12px 16px;margin-bottom:18px;color:#212529;font-size:13px;">'
            f'<div style="color:#6c757d;font-size:11px;text-transform:uppercase;'
            f'letter-spacing:0.8px;margin-bottom:6px;">Last update</div>'
            f'{h(snippet)}</div>'
        )

    today_iso  = date.today().isoformat()
    cc_summary = ", ".join(c.split("@")[0] for c in config.REPLY_CC_ADDRESSES)

    # The bottom hidden marker — white text at 1px is the classic trick.
    # It preserves the <MARKER>:NNNNN tag when a salesperson hits plain
    # "Reply" instead of our button (Outlook quotes it in the reply body).
    hidden_marker = (
        f'<div style="color:#ffffff;font-size:1px;line-height:1px;mso-hide:all;">'
        f'---<br>{config.TRACKING_MARKER_PREFIX}:{order_number}<br>---</div>'
    )

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background-color:#f4f4f4;font-family:Segoe UI,Arial,Helvetica,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f4f4f4;padding:24px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="background-color:#ffffff;border-radius:6px;overflow:hidden;border:1px solid #e9ecef;">
          <tr>
            <td style="background-color:{banner_bg};color:#ffffff;padding:18px 28px;">
              <div style="font-size:12px;font-weight:bold;letter-spacing:1.5px;opacity:0.85;">{banner_icon}  ALERT  ·  {h(threshold_label)}</div>
              <div style="font-size:{22 if is_final_warning else 20}px;font-weight:bold;margin-top:6px;line-height:1.3;">{h(banner_title)}</div>
            </td>
          </tr>
          <tr>
            <td style="padding:24px 28px 8px 28px;color:#212529;font-size:14px;line-height:1.55;">
              {urgent_line}
              <p style="margin:0 0 18px 0;">{h(template['intro'])}</p>
              <p style="margin:0 0 22px 0;">Click <strong>{h(BUTTON_LABEL)}</strong> below to reply with details for a resolution or to reschedule.</p>
            </td>
          </tr>
          <tr>
            <td style="padding:0 28px 24px 28px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f8f9fa;border:1px solid #e9ecef;border-radius:4px;padding:14px 18px;">
                {''.join(detail_rows)}
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:0 28px 8px 28px;">
              {comment_block}
            </td>
          </tr>
          <tr>
            <td align="center" style="padding:8px 28px 28px 28px;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td align="center" bgcolor="{banner_bg}" style="background-color:{banner_bg};border-radius:4px;">
                    <a href="{mailto_url}" style="display:inline-block;padding:14px 36px;color:#ffffff;text-decoration:none;font-weight:bold;font-size:14px;letter-spacing:0.6px;font-family:Segoe UI,Arial,Helvetica,sans-serif;">{h(BUTTON_LABEL)}</a>
                  </td>
                </tr>
              </table>
              <div style="margin-top:14px;font-size:12px;color:#6c757d;">Reply will go to Operations · CC: {h(cc_summary)}</div>
            </td>
          </tr>
          <tr>
            <td style="background-color:#f8f9fa;padding:12px 28px;color:#6c757d;font-size:11px;text-align:center;border-top:1px solid #e9ecef;">
              ResolutionMessenger automated alert · {today_iso}
            </td>
          </tr>
        </table>
        {hidden_marker}
      </td>
    </tr>
  </table>
</body>
</html>"""


def subject_for(template_key: str, order_number: str, is_final_warning: bool) -> str:
    """Build the outer email subject line (not the mailto reply subject)."""
    if is_final_warning:
        return f"🚨 LAST WARNING — Reschedule at 15:00 — Order {order_number}"
    base = TEMPLATES.get(template_key, {}).get("subject", "ALERT")
    return f"⚠️ {base} — Order {order_number}"
