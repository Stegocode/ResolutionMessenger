"""Tests for the three-signal suppression rules.

Key behaviors under test:
    - First-ever notification is never suppressed.
    - Comment on Monday AFTER last notification -> suppress.
    - Comment BEFORE last notification -> do NOT suppress (we pinged since).
    - Reply email AFTER last notification -> suppress.
    - Final-warning slot bypasses all suppression.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ResolutionMessenger import schema, suppression


# ── Helpers ────────────────────────────────────────────────────────

def _iso_utc(dt: datetime) -> str:
    """ISO string, tz-aware in UTC."""
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


@pytest.fixture
def conn(tmp_path: Path):
    """A fresh SQLite DB with our tables already created, per test."""
    db = tmp_path / "sup.db"
    c = schema.connect(db)
    schema.ensure_tables(c)
    yield c
    c.close()


def _record_notification(conn: sqlite3.Connection, item_id: str, when: datetime) -> None:
    conn.execute(
        "INSERT INTO notification_log (item_id, fire_key, fired_at, recipient, template) "
        "VALUES (?, ?, ?, 'x@y.com', 'T')",
        (item_id, f"key-{when.isoformat()}", _iso_utc(when)),
    )
    conn.commit()


def _record_reply(conn: sqlite3.Connection, order_number: str, when: datetime) -> None:
    conn.execute(
        "INSERT INTO reply_log (order_number, replied_at, sender, subject) "
        "VALUES (?, ?, 'Bob', '[RM:27341] test')",
        (order_number, _iso_utc(when)),
    )
    conn.commit()


# ── Test cases ─────────────────────────────────────────────────────

class TestSuppress:
    def test_first_notification_is_not_suppressed(self, conn):
        """With no prior notifications, suppression is bypassed for the first send."""
        suppress, reason = suppression.should_suppress(
            conn, item_id="it1", order_number="27341",
            item_updates=[], is_final_warning=False,
        )
        assert suppress is False
        assert "first-notification" in reason

    def test_comment_after_last_notification_suppresses(self, conn):
        now = datetime.now(timezone.utc)
        _record_notification(conn, "it1", now - timedelta(hours=2))
        updates = [{"created_at": _iso_utc(now - timedelta(minutes=30)),
                    "text_body": "Picking up Wed"}]
        suppress, reason = suppression.should_suppress(
            conn, "it1", "27341", updates, is_final_warning=False,
        )
        assert suppress is True
        assert "comment-after-last-notification" in reason

    def test_comment_before_last_notification_does_not_suppress(self, conn):
        now = datetime.now(timezone.utc)
        _record_notification(conn, "it1", now - timedelta(minutes=30))
        # Comment is older than the notification.
        updates = [{"created_at": _iso_utc(now - timedelta(hours=6)),
                    "text_body": "stale comment"}]
        suppress, _ = suppression.should_suppress(
            conn, "it1", "27341", updates, is_final_warning=False,
        )
        assert suppress is False

    def test_reply_after_last_notification_suppresses(self, conn):
        now = datetime.now(timezone.utc)
        _record_notification(conn, "it1", now - timedelta(hours=3))
        _record_reply(conn, "27341", now - timedelta(minutes=10))
        suppress, reason = suppression.should_suppress(
            conn, "it1", "27341", item_updates=[], is_final_warning=False,
        )
        assert suppress is True
        assert "inbox-reply" in reason

    def test_final_warning_bypasses_everything(self, conn):
        """The T-1 14:00 slot ignores all signals — it always fires."""
        now = datetime.now(timezone.utc)
        _record_notification(conn, "it1", now - timedelta(hours=5))
        updates = [{"created_at": _iso_utc(now - timedelta(minutes=1)),
                    "text_body": "recent ack"}]
        _record_reply(conn, "27341", now)
        suppress, reason = suppression.should_suppress(
            conn, "it1", "27341", updates, is_final_warning=True,
        )
        assert suppress is False
        assert "final-warning-bypass" in reason


class TestLatestCommentBody:
    def test_returns_text_of_newest_update(self):
        older = {"created_at": "2026-04-10T08:00:00Z", "text_body": "old"}
        newer = {"created_at": "2026-04-17T15:00:00Z", "text_body": "new"}
        assert suppression.latest_comment_body([older, newer]) == "new"

    def test_returns_none_when_no_updates(self):
        assert suppression.latest_comment_body([]) is None


class TestIsoParsing:
    @pytest.mark.parametrize("s", [
        "2026-04-17T15:00:00Z",
        "2026-04-17T15:00:00+00:00",
        "2026-04-17T15:00:00.123Z",
    ])
    def test_parses_common_iso_variants(self, s):
        dt = suppression._parse_iso(s)
        assert dt is not None
        assert dt.tzinfo is not None

    def test_returns_none_on_empty(self):
        assert suppression._parse_iso("") is None
        assert suppression._parse_iso("garbage") is None
