"""Tests for template selection and recipient resolution.

Covers:
    - pick_template_key rules for every combination of paid/allocate state.
    - email_from_name handles hyphens, apostrophes, extra whitespace.
    - resolve_recipients chooses Monday-sourced derivation and falls back
      gracefully.
"""

from __future__ import annotations

import pytest

from ResolutionMessenger import board_client, config, recipients


# ── Helpers ─────────────────────────────────────────────────────────

def _make_item(paid: str = "", allocate: str = "", status: str = "NEEDS ATTENTION",
               salesperson_value: str = "") -> dict:
    """Build a minimal Monday item dict with the columns the code reads."""
    return {
        "id": "123",
        "name": "27341 Joe Customer",
        "column_values": [
            {"id": config.COL_PAID_IN_FULL, "text": paid,     "value": ""},
            {"id": config.COL_ALLOCATE,     "text": allocate, "value": ""},
            {"id": config.COL_STATUS,       "text": status,   "value": ""},
            {"id": config.COL_SALESPERSON,  "text": "",       "value": salesperson_value},
        ],
    }


# ── Template-picker truth table ────────────────────────────────────

class TestPickTemplate:
    def test_paid_no_picks_unpaid(self):
        item = _make_item(paid=config.PAID_IN_FULL_NO)
        assert recipients.pick_template_key(item) == recipients.TEMPLATE_UNPAID

    def test_ar_account_picks_unpaid(self):
        item = _make_item(paid=config.PAID_IN_FULL_AR_ACCOUNT)
        assert recipients.pick_template_key(item) == recipients.TEMPLATE_UNPAID

    def test_low_stock_picks_supply_no_po(self):
        item = _make_item(allocate=config.ALLOCATE_LOW_STOCK)
        assert recipients.pick_template_key(item) == recipients.TEMPLATE_SUPPLY_NO_PO

    def test_on_po_picks_supply_with_po(self):
        item = _make_item(allocate=config.ALLOCATE_PIECE_ON_PO)
        assert recipients.pick_template_key(item) == recipients.TEMPLATE_SUPPLY_WITH_PO

    def test_paid_beats_allocate(self):
        """When BOTH a payment issue and a supply issue exist, payment wins."""
        item = _make_item(paid=config.PAID_IN_FULL_NO,
                          allocate=config.ALLOCATE_PIECE_ON_PO)
        assert recipients.pick_template_key(item) == recipients.TEMPLATE_UNPAID

    def test_empty_columns_default_to_supply_with_po(self):
        item = _make_item()
        assert recipients.pick_template_key(item) == recipients.TEMPLATE_SUPPLY_WITH_PO


# ── CC list per template ───────────────────────────────────────────

class TestCcList:
    def test_unpaid_cc_list(self):
        cc = recipients.cc_list_for_template(recipients.TEMPLATE_UNPAID)
        assert cc == list(config.UNPAID_TEMPLATE_CC)

    def test_supply_cc_list(self):
        cc_on = recipients.cc_list_for_template(recipients.TEMPLATE_SUPPLY_WITH_PO)
        cc_no = recipients.cc_list_for_template(recipients.TEMPLATE_SUPPLY_NO_PO)
        assert cc_on == list(config.SUPPLY_TEMPLATE_CC)
        assert cc_no == list(config.SUPPLY_TEMPLATE_CC)


# ── email_from_name derivation ─────────────────────────────────────

class TestEmailFromName:
    """The derivation uses config.RECIPIENT_DOMAIN; default is 'example.com'.

    Tests build the expected email at runtime so changing the default in
    config doesn't require touching every test case.
    """

    @pytest.mark.parametrize("name,local_part", [
        ("Alex Carter",     "alexc"),
        ("Jordan Wells",    "jordanw"),
        ("Sam  Wilson",     "samw"),       # double space
        ("Mary-Jo Smith",   "maryjos"),    # hyphen stripped
        ("O'Brien Foley",   "obrienf"),    # apostrophe stripped
        ("Jean Van Damme",  "jeand"),      # multi-word last name -> initial of last token
    ])
    def test_various_names(self, name, local_part):
        expected = f"{local_part}@{config.RECIPIENT_DOMAIN}"
        assert recipients.email_from_name(name) == expected

    def test_single_name_returns_none(self):
        assert recipients.email_from_name("Madonna") is None

    def test_empty_returns_none(self):
        assert recipients.email_from_name("") is None
        assert recipients.email_from_name(None) is None


# ── resolve_recipients ─────────────────────────────────────────────

class TestResolveRecipients:
    def test_uses_board_user_name_for_to(self, monkeypatch):
        """The TO address is derived from the salesperson's board user name."""
        item = _make_item(
            allocate=config.ALLOCATE_PIECE_ON_PO,
            salesperson_value='{"personsAndTeams":[{"id":999,"kind":"person"}]}',
        )
        monkeypatch.setattr(board_client, "fetch_user_name", lambda pid: "Alex Carter")

        to, cc = recipients.resolve_recipients(item)
        assert to == f"alexc@{config.RECIPIENT_DOMAIN}"
        assert cc == list(config.SUPPLY_TEMPLATE_CC)

    def test_falls_back_when_no_salesperson(self, monkeypatch):
        item = _make_item(allocate=config.ALLOCATE_PIECE_ON_PO)  # no salesperson value
        monkeypatch.setattr(config, "NOTIFY_FALLBACK", "fallback@example.com")
        monkeypatch.setattr(board_client, "fetch_user_name",
                            lambda pid: pytest.fail("should not be called"))

        to, cc = recipients.resolve_recipients(item)
        assert to == "fallback@example.com"
        assert cc == list(config.SUPPLY_TEMPLATE_CC)

    def test_falls_back_to_first_cc_when_no_fallback(self, monkeypatch):
        """If nothing else and a CC list exists, TO becomes the first CC."""
        item = _make_item(allocate=config.ALLOCATE_PIECE_ON_PO)
        monkeypatch.setattr(config, "NOTIFY_FALLBACK", None)
        monkeypatch.setattr(config, "EMAIL_TO", None)
        monkeypatch.setattr(board_client, "fetch_user_name", lambda pid: None)
        # Inject a static CC for this test so the assertion is meaningful even
        # when the default config has empty CC tuples.
        monkeypatch.setattr(config, "SUPPLY_TEMPLATE_CC",
                            ("ops1@example.com", "ops2@example.com"))

        to, cc = recipients.resolve_recipients(item)
        assert to == "ops1@example.com"
        assert "ops1@example.com" not in cc
        assert cc == ["ops2@example.com"]
