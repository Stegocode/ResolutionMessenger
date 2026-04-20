"""Integration tests for run_tick() — the orchestrator.

We mock the Monday API surface and the Outlook send so nothing hits real
infrastructure. The rest of the pipeline runs end-to-end against a real
SQLite DB.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ResolutionMessenger import (
    board_client,
    config,
    notifier,
    schema,
)

# For the flake-prone suppression test we need these standard-library bits.
# (Already imported above, re-stated here so imports above can stay sorted.)


# ── Fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path: Path) -> Path:
    """A fresh DB path (tables created on first use inside run_tick)."""
    return tmp_path / "n.db"


def _make_item(order: str = "27341", customer: str = "Joe Customer",
               delivery: str = "2026-04-23",
               paid: str = "", allocate: str = "PIECE(S) ON PO") -> dict:
    """Build a Monday-shaped item dict."""
    return {
        "id":   f"item-{order}",
        "name": f"{order} {customer}",
        "column_values": [
            {"id": config.COL_STATUS,       "text": "NEEDS ATTENTION", "value": ""},
            {"id": config.COL_ALLOCATE,     "text": allocate,         "value": ""},
            {"id": config.COL_PAID_IN_FULL, "text": paid,             "value": ""},
            {"id": config.COL_SALESPERSON,  "text": "",
             "value": '{"personsAndTeams":[{"id":42,"kind":"person"}]}'},
            {"id": config.COL_DELIVERY,     "text": delivery,         "value": ""},
        ],
    }


def _ops_dt(y: int, m: int, d: int, h: int = 10, minute: int = 0) -> datetime:
    """Build a tz-aware datetime in OPS_TZ. Minute defaults to 0 for convenience."""
    return datetime(y, m, d, h, minute, tzinfo=config.OPS_TZ)


# ── Test cases ─────────────────────────────────────────────────────

class TestDryRun:
    def test_dry_run_sends_nothing_records_nothing(self, monkeypatch, db):
        """--dry-run: everything computed but no send + no DB insert."""
        monkeypatch.setattr(board_client, "iter_open_items",
                            lambda: iter([_make_item(delivery="2026-04-23")]))
        monkeypatch.setattr(board_client, "fetch_item_updates", lambda _: [])
        monkeypatch.setattr(board_client, "fetch_user_name", lambda _: "Alex Carter")
        monkeypatch.setattr(notifier, "_send_via_outlook",
                            lambda *a, **kw: pytest.fail("dry-run shouldn't send"))

        counts = notifier.run_tick(
            db_path=db, dry_run=True, now=_ops_dt(2026, 4, 20, 10),  # T-3 after 9am
        )
        assert counts["items_scanned"] == 1
        assert counts["slots_computed"] >= 1
        assert counts["sent"] == 0

        # No notification_log row inserted.
        conn = schema.connect(db)
        schema.ensure_tables(conn)
        n = conn.execute("SELECT COUNT(*) FROM notification_log").fetchone()[0]
        conn.close()
        assert n == 0


class TestLiveSend:
    def test_single_item_sends_once_and_records(self, monkeypatch, db):
        sent: list[dict] = []
        monkeypatch.setattr(board_client, "iter_open_items",
                            lambda: iter([_make_item(delivery="2026-04-23")]))
        monkeypatch.setattr(board_client, "fetch_item_updates", lambda _: [])
        monkeypatch.setattr(board_client, "fetch_user_name", lambda _: "Alex Carter")
        monkeypatch.setattr(
            notifier, "_send_via_outlook",
            lambda to, subject, body_html, cc=None:
                sent.append({"to": to, "subject": subject, "cc": cc}),
        )

        counts = notifier.run_tick(db_path=db, now=_ops_dt(2026, 4, 20, 10))
        assert counts["sent"] == 1
        assert len(sent) == 1
        assert sent[0]["to"] == f"alexc@{config.RECIPIENT_DOMAIN}"

        # Re-run: already_fired should kick in; no second send.
        counts2 = notifier.run_tick(db_path=db, now=_ops_dt(2026, 4, 20, 10, ))
        assert counts2["sent"] == 0
        assert counts2["already_fired"] == 1

    def test_force_recipient_overrides_lookup(self, monkeypatch, db):
        sent: list[dict] = []
        monkeypatch.setattr(board_client, "iter_open_items",
                            lambda: iter([_make_item()]))
        monkeypatch.setattr(board_client, "fetch_item_updates", lambda _: [])
        monkeypatch.setattr(board_client, "fetch_user_name",
                            lambda _: pytest.fail("shouldn't look up user"))
        monkeypatch.setattr(
            notifier, "_send_via_outlook",
            lambda to, subject, body_html, cc=None:
                sent.append({"to": to, "cc": cc}),
        )

        notifier.run_tick(
            db_path=db,
            force_recipient="test@example.com",
            now=_ops_dt(2026, 4, 20, 10),
        )
        assert sent[0]["to"] == "test@example.com"
        assert sent[0]["cc"] == []  # force-recipient suppresses CC


class TestSuppression:
    def test_recent_comment_suppresses_next_send(self, monkeypatch, db):
        """A comment newer than last notification blocks the next email."""
        monkeypatch.setattr(board_client, "iter_open_items",
                            lambda: iter([_make_item(delivery="2026-04-23")]))
        monkeypatch.setattr(board_client, "fetch_user_name", lambda _: "Alex Carter")

        sent: list = []
        monkeypatch.setattr(
            notifier, "_send_via_outlook",
            lambda to, subject, body_html, cc=None: sent.append(to),
        )

        # First: no updates, first notification goes out.
        monkeypatch.setattr(board_client, "fetch_item_updates", lambda _: [])
        notifier.run_tick(db_path=db, now=_ops_dt(2026, 4, 20, 10))
        assert len(sent) == 1

        # Now simulate a comment appearing strictly AFTER the notification
        # was sent. We read the actual fired_at out of the DB and set the
        # comment timestamp to fired_at + 60s to guarantee ordering — the
        # fired_at is stored at 1-second resolution, so a tight race can
        # leave both in the same second otherwise.
        conn = schema.connect(db)
        fired_at_str = conn.execute(
            "SELECT fired_at FROM notification_log LIMIT 1"
        ).fetchone()[0]
        conn.close()
        fired_at = datetime.fromisoformat(fired_at_str.replace("Z", "+00:00"))
        if fired_at.tzinfo is None:
            fired_at = fired_at.replace(tzinfo=timezone.utc)
        comment_time = fired_at + timedelta(seconds=60)
        monkeypatch.setattr(board_client, "fetch_item_updates", lambda _: [{
            "created_at": comment_time.isoformat(),
            "text_body": "Picking up Wed",
        }])
        counts = notifier.run_tick(db_path=db, now=_ops_dt(2026, 4, 20, 14, 1))
        assert counts["suppressed"] >= 1
        assert len(sent) == 1    # no new send

    def test_final_warning_bypasses_suppression(self, monkeypatch, db):
        """T-1 14:00 slot fires even if there's a fresh comment."""
        monkeypatch.setattr(board_client, "iter_open_items",
                            lambda: iter([_make_item(delivery="2026-04-21")]))  # T-1
        monkeypatch.setattr(board_client, "fetch_user_name", lambda _: "Alex Carter")
        # Lots of fresh comments — should not save this item.
        comment_time = datetime.now(timezone.utc).replace(microsecond=0)
        monkeypatch.setattr(board_client, "fetch_item_updates", lambda _: [{
            "created_at": comment_time.isoformat(),
            "text_body": "yes I know",
        }])

        # First fire the 08:00 slot (non-final) so there's prior notification.
        sent: list = []
        monkeypatch.setattr(
            notifier, "_send_via_outlook",
            lambda to, subject, body_html, cc=None: sent.append(subject),
        )
        notifier.run_tick(db_path=db, now=_ops_dt(2026, 4, 20, 8, 1))
        # After that, at 14:01 the final-warning slot should fire regardless.
        counts = notifier.run_tick(db_path=db, now=_ops_dt(2026, 4, 20, 14, 1))
        assert counts["sent"] >= 1
        # The subject of the final warning should show the louder framing.
        assert any("LAST WARNING" in s or "🚨" in s for s in sent)


class TestSkipCases:
    def test_item_without_delivery_date_is_skipped(self, monkeypatch, db):
        monkeypatch.setattr(board_client, "iter_open_items",
                            lambda: iter([_make_item(delivery="")]))
        monkeypatch.setattr(board_client, "fetch_item_updates", lambda _: [])
        monkeypatch.setattr(board_client, "fetch_user_name", lambda _: "Alex Carter")
        monkeypatch.setattr(notifier, "_send_via_outlook",
                            lambda *a, **kw: pytest.fail("shouldn't send"))

        counts = notifier.run_tick(db_path=db, now=_ops_dt(2026, 4, 20, 10))
        assert counts["skipped_no_date"] == 1
        assert counts["sent"] == 0

    def test_item_past_delivery_produces_no_slots(self, monkeypatch, db):
        monkeypatch.setattr(board_client, "iter_open_items",
                            lambda: iter([_make_item(delivery="2026-04-01")]))
        monkeypatch.setattr(board_client, "fetch_item_updates", lambda _: [])
        monkeypatch.setattr(board_client, "fetch_user_name", lambda _: "Alex Carter")
        monkeypatch.setattr(notifier, "_send_via_outlook",
                            lambda *a, **kw: pytest.fail("shouldn't send"))

        counts = notifier.run_tick(db_path=db, now=_ops_dt(2026, 4, 20, 10))
        assert counts["slots_computed"] == 0
        assert counts["sent"] == 0
