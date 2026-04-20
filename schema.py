"""SQLite schema + migration helpers for ResolutionMessenger.

Two tables this package owns:

    notification_log
        One row per email the notifier has sent. The primary key combines
        (item_id, fire_key) so re-running the same schedule slot is a
        no-op — classic idempotency pattern.

    reply_log
        One row per "REPLY TO OPERATIONS" email we've seen in the inbox.
        Populated by the reply scanner. Keyed by (order_number, replied_at)
        so we can record multiple replies for the same order.

Why a separate schema module?
----------------------------
Keeping table DDL in one file means you can read the whole data model in 30
seconds — useful for anyone stepping into the codebase. Other modules import
the helpers here instead of scattering `CREATE TABLE IF NOT EXISTS` strings
all over the place.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config


# SQL DDL as module-level strings so tests can read them independently.
# The `IF NOT EXISTS` guards make every ensure_*() call idempotent.
DDL_NOTIFICATION_LOG = """
CREATE TABLE IF NOT EXISTS notification_log (
    item_id     TEXT    NOT NULL,   -- Monday item id (stable across time)
    fire_key    TEXT    NOT NULL,   -- "YYYY-MM-DD_HHMM_T-N" slot identifier
    fired_at    TEXT    NOT NULL,   -- ISO timestamp of actual send
    recipient   TEXT,               -- primary TO address
    template    TEXT,               -- which template was used
    PRIMARY KEY (item_id, fire_key)
);
"""

# Helpful index: fast lookup of "what's the most recent notification for
# this item?" — used by the suppression logic.
DDL_NOTIFICATION_IDX = """
CREATE INDEX IF NOT EXISTS idx_notification_log_item_time
    ON notification_log(item_id, fired_at DESC);
"""

DDL_REPLY_LOG = """
CREATE TABLE IF NOT EXISTS reply_log (
    order_number TEXT NOT NULL,
    replied_at   TEXT NOT NULL,    -- ISO timestamp from email ReceivedTime
    sender       TEXT,             -- who sent the reply (from Outlook SenderName)
    subject      TEXT,
    PRIMARY KEY (order_number, replied_at)
);
"""

DDL_REPLY_IDX = """
CREATE INDEX IF NOT EXISTS idx_reply_log_order
    ON reply_log(order_number, replied_at DESC);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with sensible defaults.

    - ``row_factory = sqlite3.Row`` lets callers use ``row["col"]`` syntax,
      which reads better than positional indexing.
    - ``isolation_level=None`` would turn on autocommit; we leave it at the
      default (deferred transactions) and commit explicitly, because we
      want a single logical unit of work per tick.
    """
    path = db_path or config.ISSUES_DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables(conn: sqlite3.Connection) -> None:
    """Create our tables + indexes if they don't exist. Safe to call often."""
    with conn:  # context-manager commits on exit, rolls back on exception
        conn.executescript(
            DDL_NOTIFICATION_LOG
            + DDL_NOTIFICATION_IDX
            + DDL_REPLY_LOG
            + DDL_REPLY_IDX
        )
