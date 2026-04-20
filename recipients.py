"""Recipient resolution and template selection.

For a given board item at NEEDS ATTENTION, this module figures out:
    1. Which email TEMPLATE applies (UNPAID / SUPPLY_WITH_PO / SUPPLY_NO_PO).
    2. Who the email should go TO and who gets CC'd.

The selection rules are derived from the item's current column state:
    - PAID IN FULL? is "NO" or "AR ACCOUNT" → UNPAID template
    - ALLOCATE is "PIECE(S) LOW STOCK"     → SUPPLY_NO_PO template
    - Otherwise                              → SUPPLY_WITH_PO template

We lean on Monday's columns (not our DB) because they're the operational
source of truth — a dispatcher can re-categorize an order just by toggling
a column, and the next notification honors the change without any DB edit.
"""

from __future__ import annotations

import re

from . import board_client, config


# Template keys. We use a small enum-like set of string constants so tests
# can assert on them without importing the full templates module.
TEMPLATE_UNPAID        = "UNPAID_BALANCE"
TEMPLATE_SUPPLY_WITH_PO = "PIECES_ON_PO"
TEMPLATE_SUPPLY_NO_PO  = "ITEM_NOT_ON_PO"


def pick_template_key(item: dict) -> str:
    """Return the template key for this item based on its current columns.

    Precedence: payment issues first (they block loading), then supply-chain.
    """
    paid_value     = board_client.column_value_text(item, config.COL_PAID_IN_FULL)
    allocate_value = board_client.column_value_text(item, config.COL_ALLOCATE)

    if paid_value in (config.PAID_IN_FULL_NO, config.PAID_IN_FULL_AR_ACCOUNT):
        return TEMPLATE_UNPAID

    if allocate_value == config.ALLOCATE_LOW_STOCK:
        return TEMPLATE_SUPPLY_NO_PO

    # Default for items at NEEDS ATTENTION that don't hit a more specific rule.
    # Items showing PIECE(S) ON PO and anything else fall here.
    return TEMPLATE_SUPPLY_WITH_PO


def cc_list_for_template(template_key: str) -> list[str]:
    """Return the template-specific static CC list."""
    if template_key == TEMPLATE_UNPAID:
        return list(config.UNPAID_TEMPLATE_CC)
    return list(config.SUPPLY_TEMPLATE_CC)


# ── Name → email derivation ─────────────────────────────────────────
# Rule confirmed with ops: `first + last_initial @ domain`.
# Example: "Alex Carter" -> "alexc@{RECIPIENT_DOMAIN}"

def email_from_name(name: str) -> str | None:
    """Derive a user email from a 'First Last' display name.

    - Lowercases.
    - Strips non-alphabetic characters (handles "Mary-Jo" and "O'Brien").
    - First name is the first whitespace-separated token.
    - Last initial is the first letter of the last token.
    - Returns None if we can't find at least two tokens.

    The regex `[^A-Za-z]` cleans apostrophes, hyphens, and stray whitespace
    before lowering, which is the common pattern used by most corp email
    conventions.
    """
    if not name:
        return None
    tokens = [re.sub(r"[^A-Za-z]", "", t) for t in name.strip().split()]
    tokens = [t for t in tokens if t]
    if len(tokens) < 2:
        return None
    first = tokens[0].lower()
    last_initial = tokens[-1][0].lower()
    return f"{first}{last_initial}@{config.RECIPIENT_DOMAIN}"


# ── Recipient resolution for an item ────────────────────────────────

def resolve_recipients(item: dict) -> tuple[str, list[str]]:
    """Return (to_address, cc_list) for this item's escalation email.

    Resolution strategy:
        1. TO: the assigned salesperson's derived email. Pulled by looking
           up the Monday user in the SALESPERSON column, fetching their
           display name, then running email_from_name() on it.
        2. CC: template-specific static list.
        3. Fallback chain if name-derivation fails:
              NOTIFY_FALLBACK_EMAIL → EMAIL_TO → first CC address → "".
    """
    template_key = pick_template_key(item)
    cc_list = cc_list_for_template(template_key)

    to_address = ""
    for pid in board_client.person_ids_on_item(item):
        name = board_client.fetch_user_name(pid)
        if not name:
            continue
        derived = email_from_name(name)
        if derived:
            to_address = derived
            break

    if not to_address:
        to_address = (
            config.NOTIFY_FALLBACK
            or config.EMAIL_TO
            or (cc_list[0] if cc_list else "")
        )
        # If we pushed a CC into TO we don't also want the same address as CC.
        if cc_list and to_address == cc_list[0]:
            cc_list = cc_list[1:]

    return to_address, cc_list
