"""Tests for the stale-order report."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ResolutionMessenger import board_client, config, reports


def _item(order: str, delivery: str, allocate: str = "PIECE(S) ON PO",
          paid: str = "") -> dict:
    return {
        "id":   f"item-{order}",
        "name": f"{order} Cust",
        "column_values": [
            {"id": config.COL_STATUS,       "text": "NEEDS ATTENTION", "value": ""},
            {"id": config.COL_ALLOCATE,     "text": allocate,          "value": ""},
            {"id": config.COL_PAID_IN_FULL, "text": paid,              "value": ""},
            {"id": config.COL_DELIVERY,     "text": delivery,          "value": ""},
        ],
    }


def test_report_lists_past_delivery_items(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(board_client, "iter_open_items", lambda: iter([
        _item("27341", delivery="2026-04-10"),   # past — should appear
        _item("27342", delivery="2026-04-25"),   # future — should NOT appear
    ]))
    path = reports.write_stale_report(out_dir=tmp_path, today=date(2026, 4, 20))
    body = path.read_text(encoding="utf-8")
    assert "27341" in body
    assert "27342" not in body


def test_report_with_nothing_stuck_writes_clean_message(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(board_client, "iter_open_items", lambda: iter([]))
    path = reports.write_stale_report(out_dir=tmp_path, today=date(2026, 4, 20))
    body = path.read_text(encoding="utf-8")
    assert "No stale orders" in body


def test_report_ignores_items_outside_window(monkeypatch, tmp_path: Path):
    """Items more than STALE_REPORT_WINDOW_DAYS past aren't included."""
    very_old = _item("10000", delivery="2025-01-01")
    recent   = _item("27341", delivery="2026-04-10")
    monkeypatch.setattr(board_client, "iter_open_items",
                        lambda: iter([very_old, recent]))
    path = reports.write_stale_report(out_dir=tmp_path, today=date(2026, 4, 20))
    body = path.read_text(encoding="utf-8")
    assert "27341" in body
    assert "10000" not in body


def test_report_escapes_pipes_in_item_names(monkeypatch, tmp_path: Path):
    """A pipe in the item name would break the markdown table — must escape."""
    item = _item("27341", delivery="2026-04-10")
    item["name"] = "27341 Customer | With | Pipes"
    monkeypatch.setattr(board_client, "iter_open_items", lambda: iter([item]))
    path = reports.write_stale_report(out_dir=tmp_path, today=date(2026, 4, 20))
    body = path.read_text(encoding="utf-8")
    assert "\\|" in body
