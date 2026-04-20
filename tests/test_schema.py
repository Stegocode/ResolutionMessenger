"""Schema tests — verify DDL is idempotent and the tables exist."""

from __future__ import annotations

from pathlib import Path

from ResolutionMessenger import schema


def test_ensure_tables_creates_notification_log(tmp_path: Path):
    db = tmp_path / "s.db"
    conn = schema.connect(db)
    schema.ensure_tables(conn)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name IN ('notification_log','reply_log') ORDER BY name"
    ).fetchall()
    assert [r[0] for r in rows] == ["notification_log", "reply_log"]


def test_ensure_tables_is_idempotent(tmp_path: Path):
    """Calling ensure_tables() twice must not raise."""
    db = tmp_path / "s.db"
    conn = schema.connect(db)
    schema.ensure_tables(conn)
    schema.ensure_tables(conn)     # second call — must be a no-op
    # Insert, then call again — data must survive.
    conn.execute(
        "INSERT INTO notification_log (item_id, fire_key, fired_at, recipient, template) "
        "VALUES ('i1', 'k1', '2026-04-20T10:00:00', 'x@y.com', 'T')"
    )
    conn.commit()
    schema.ensure_tables(conn)
    n = conn.execute("SELECT COUNT(*) FROM notification_log").fetchone()[0]
    assert n == 1


def test_primary_key_prevents_duplicate_fire(tmp_path: Path):
    """The (item_id, fire_key) PK blocks duplicate sends."""
    db = tmp_path / "s.db"
    conn = schema.connect(db)
    schema.ensure_tables(conn)
    conn.execute(
        "INSERT INTO notification_log (item_id, fire_key, fired_at, recipient, template) "
        "VALUES ('i1', 'k1', '2026-04-20T10:00:00', 'x@y.com', 'T')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO notification_log (item_id, fire_key, fired_at, recipient, template) "
        "VALUES ('i1', 'k1', '2026-04-20T10:30:00', 'x@y.com', 'T')"
    )
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM notification_log").fetchone()[0]
    assert n == 1
