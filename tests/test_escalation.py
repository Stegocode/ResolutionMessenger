"""Tests for the escalation scheduling engine.

Coverage:
    - Each T-N day produces the right number and timing of fire slots.
    - Weekends and holidays skip entirely (no catchup).
    - Delivery in the past or more than 7 days out produces zero slots.
    - fire_key is stable and unique per (date, hour, threshold).
    - The T-1 14:00 slot is marked as the final warning.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from ResolutionMessenger import config, escalation


# ── Helpers ─────────────────────────────────────────────────────────

def _now_pst(y: int, m: int, d: int, h: int = 10, minute: int = 0) -> datetime:
    """Build a tz-aware datetime in OPS_TZ for test consistency."""
    return datetime(y, m, d, h, minute, tzinfo=config.OPS_TZ)


# ── Slot counts per threshold ──────────────────────────────────────

@pytest.mark.parametrize("days_out,hours,expected_count", [
    (7, (9,),                                           1),
    (6, (9,),                                           1),
    (5, (9,),                                           1),
    (4, (9,),                                           1),
    (3, (9, 14),                                        2),
    (2, (8, 11, 13, 15),                                4),
    (1, (8, 9, 10, 11, 12, 13, 14),                     7),
])
def test_schedule_matches_spec(days_out, hours, expected_count):
    """Config SCHEDULE should match the operational spec exactly."""
    assert config.SCHEDULE[days_out] == hours
    assert len(hours) == expected_count


def test_t0_deliberately_absent():
    """No T-0 slot: delivery-day is explicitly not a notification day."""
    assert 0 not in config.SCHEDULE


# ── Business-day filtering ─────────────────────────────────────────

def test_weekend_produces_no_slots():
    """Saturday and Sunday never produce fire slots."""
    # Saturday 2026-04-18
    now = _now_pst(2026, 4, 18, 9, 30)
    # Delivery Monday 4/20 => T-2 on Saturday => should be 0 slots.
    assert escalation.slots_due_for_item("i1", date(2026, 4, 20), now) == []


def test_holiday_in_env_is_respected(monkeypatch):
    """If today's ISO date is in config.HOLIDAYS, no slots fire."""
    monkeypatch.setattr(config, "HOLIDAYS", frozenset({"2026-04-20"}))
    now = _now_pst(2026, 4, 20, 10, 0)
    # Delivery Thursday 4/23 -> would be T-3 (2 slots), but it's a holiday.
    assert escalation.slots_due_for_item("i1", date(2026, 4, 23), now) == []


# ── Out-of-window deliveries ────────────────────────────────────────

def test_delivery_more_than_7_days_out_produces_nothing():
    now = _now_pst(2026, 4, 20, 10, 0)
    assert escalation.slots_due_for_item("i1", date(2026, 5, 1), now) == []


def test_delivery_today_or_past_produces_nothing():
    """T-0 is not a fire day, neither is past-delivery."""
    now = _now_pst(2026, 4, 20, 10, 0)
    assert escalation.slots_due_for_item("i1", date(2026, 4, 20), now) == []
    assert escalation.slots_due_for_item("i1", date(2026, 4, 19), now) == []


# ── "Only slots that have already opened" ─────────────────────────

def test_only_opened_slots_are_returned():
    """A 09:30 tick on T-3 returns the 09:00 slot but NOT 14:00 (not yet open)."""
    now = _now_pst(2026, 4, 20, 9, 30)    # Monday
    # Delivery 4/23 (Thursday) -> T-3
    slots = escalation.slots_due_for_item("i1", date(2026, 4, 23), now)
    assert len(slots) == 1
    assert slots[0].days_before == 3
    assert slots[0].threshold == "T-3"


def test_all_slots_returned_when_after_last_hour():
    """A 15:00 tick on T-3 has both 09:00 and 14:00 slots opened."""
    now = _now_pst(2026, 4, 20, 15, 0)
    slots = escalation.slots_due_for_item("i1", date(2026, 4, 23), now)
    assert len(slots) == 2
    assert [s.days_before for s in slots] == [3, 3]


# ── fire_key shape + uniqueness ────────────────────────────────────

def test_fire_key_includes_date_hour_threshold():
    """Stable fire_key format: 'YYYY-MM-DD_HHMM_T-N'."""
    now = _now_pst(2026, 4, 20, 10, 0)
    slots = escalation.slots_due_for_item("i1", date(2026, 4, 23), now)
    assert slots[0].fire_key == "2026-04-20_0900_T-3"


def test_fire_keys_are_unique_across_slots():
    now = _now_pst(2026, 4, 20, 16, 0)     # after all T-2 hours
    # Delivery 4/22 (Wednesday) -> T-2 (4 slots)
    slots = escalation.slots_due_for_item("i1", date(2026, 4, 22), now)
    keys = [s.fire_key for s in slots]
    assert len(keys) == len(set(keys))


# ── Final warning flag ─────────────────────────────────────────────

def test_t1_14_00_is_marked_final_warning():
    """The T-1 14:00 slot must have is_final=True."""
    now = _now_pst(2026, 4, 20, 14, 30)   # Monday 2:30pm
    # Delivery Tuesday 4/21 -> T-1
    slots = escalation.slots_due_for_item("i1", date(2026, 4, 21), now)
    assert slots, "expected at least one T-1 slot"
    final = [s for s in slots if s.is_final]
    assert len(final) == 1
    assert final[0].fire_key == "2026-04-20_1400_T-1"


def test_t1_earlier_slots_are_not_final():
    now = _now_pst(2026, 4, 20, 13, 59)  # just before 14:00
    slots = escalation.slots_due_for_item("i1", date(2026, 4, 21), now)
    assert all(not s.is_final for s in slots)


# ── parse_delivery_date ────────────────────────────────────────────

def test_parse_delivery_date_accepts_iso():
    assert escalation.parse_delivery_date("2026-04-20") == date(2026, 4, 20)


def test_parse_delivery_date_empty_returns_none():
    assert escalation.parse_delivery_date("") is None
    assert escalation.parse_delivery_date(None) is None


def test_parse_delivery_date_invalid_returns_none():
    assert escalation.parse_delivery_date("not-a-date") is None
    assert escalation.parse_delivery_date("2026-13-40") is None
