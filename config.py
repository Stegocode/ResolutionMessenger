"""Central configuration for ResolutionMessenger.

All tunable values live here so the rest of the package stays free of magic
numbers, hard-coded strings, and inline env lookups. Secrets and deployment-
specific identifiers (API tokens, recipient lists, the board ID, column IDs)
are read from `.env`; everything else is expressed as module-level constants
you can edit in one place.

Why this structure?
-------------------
Python idiom: keep I/O (reading .env) at the edge, and let the core modules
import pure-data constants. This makes the code easy to test without a real
.env on disk, and it keeps one searchable place for every tunable.

Portfolio note
--------------
Every default in this file is a placeholder (`example.com`, zeroed IDs,
empty CC lists). A real deployment supplies the real values through `.env`,
so this file can be read end-to-end without leaking any deployment's
specifics. See `.env.example` in the repo root for the full set of variables.
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# ── Load env early so everything below can read it ──────────────────
# Try an explicit RM_ENV_PATH override first (set it if you keep your .env
# somewhere outside the package folder), else fall back to the package's
# own .env. This keeps the package usable both in development (local .env
# beside the code) and in a production deployment (env file held elsewhere).
ENV_PATH = Path(os.getenv("RM_ENV_PATH", Path(__file__).resolve().parent / ".env"))
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)


# ── Paths ───────────────────────────────────────────────────────────
# The package folder is the anchor for everything relative. Using
# Path(__file__).parent keeps this robust to being called from any working
# directory.
PACKAGE_DIR = Path(__file__).resolve().parent
REPORTS_DIR = PACKAGE_DIR / "reports"

# SQLite DB path. Defaults to a file inside the package so the CLI works
# out-of-the-box for a first-time evaluator; a real deployment overrides
# this via ISSUES_DB_PATH in .env.
ISSUES_DB_PATH = Path(os.getenv("ISSUES_DB_PATH",
                                str(PACKAGE_DIR / "resolution_messenger.db")))


# ── Timezone / schedule ─────────────────────────────────────────────
# All business-hour logic is expressed in a single timezone so we can be
# sensible about "emails fire between 0800 and 1400". Server clock may be
# UTC or local, but decisions happen in OPS_TZ.
OPS_TZ = ZoneInfo(os.getenv("OPS_TZ", "America/Los_Angeles"))

# Threshold schedule: (days_before_delivery) -> list of hour-of-day slots
# when an email fires.
#
# Design notes / decisions:
#   - T-7..T-4 each fire once per day at 09:00.
#   - T-3 fires twice (mid-morning and mid-afternoon) — reminder frequency
#     starts ramping up.
#   - T-2 fires four times at evenly-spaced business-day intervals.
#   - T-1 fires hourly from 08:00 through 14:00 inclusive — 7 fires.
#     The 14:00 fire is the "last warning" before dispatch reschedules at 15:00.
#   - T-0 is deliberately omitted. If by 14:00 on T-1 the issue isn't closed,
#     operations reschedules; there is no delivery-day nag.
#
# Hours are integers (0-23) in OPS_TZ.
SCHEDULE: dict[int, tuple[int, ...]] = {
    7: (9,),
    6: (9,),
    5: (9,),
    4: (9,),
    3: (9, 14),
    2: (8, 11, 13, 15),
    1: (8, 9, 10, 11, 12, 13, 14),
}

# The "last warning" slot — when a comment/reply/column-change MUST NOT
# suppress the email. Key is days_before_delivery, value is the hour.
FINAL_WARNING_SLOT: tuple[int, int] = (1, 14)  # T-1 at 14:00


# ── Secrets / credentials (pulled from .env) ────────────────────────
# Accepts either BOARD_API_TOKEN (portfolio-friendly generic name) or
# the legacy MONDAY_API_TOKEN. Any missing ones are checked at use-site.
BOARD_API_TOKEN = os.getenv("BOARD_API_TOKEN") or os.getenv("MONDAY_API_TOKEN")
EMAIL_FROM      = os.getenv("EMAIL_FROM")
EMAIL_TO        = os.getenv("EMAIL_TO")          # last-resort fallback
NOTIFY_FALLBACK = os.getenv("NOTIFY_FALLBACK_EMAIL")
NOTIFY_BCC      = os.getenv("NOTIFY_BCC")         # optional, comma-separated


# ── Board (Monday.com) structure — layout, not secrets ──────────────
# Board ID and column IDs identify WHERE on the board we read and write.
# They're not secrets, but they ARE deployment-specific — a different Monday
# board would have entirely different IDs. Read from .env so a fork of this
# project points at its own board without code edits.
BOARD_API_URL = os.getenv("BOARD_API_URL", "https://api.monday.com/v2")
BOARD_ID      = os.getenv("BOARD_ID", "0000000000")

# Column IDs on the board. Defaults mirror Monday.com's auto-generated
# naming scheme; adjust to the IDs of your own board.
COL_STATUS       = os.getenv("COL_STATUS",       "status__1")
COL_ALLOCATE     = os.getenv("COL_ALLOCATE",     "status9__1")
COL_PAID_IN_FULL = os.getenv("COL_PAID_IN_FULL", "single_select62__1")
COL_SALESPERSON  = os.getenv("COL_SALESPERSON",  "dup__of_people__1")
COL_DELIVERY     = os.getenv("COL_DELIVERY",     "date7__1")

# Human-readable labels for display / template output.
COL_DISPLAY: dict[str, str] = {
    COL_STATUS:       "SCHEDULED",
    COL_ALLOCATE:     "ALLOCATE",
    COL_PAID_IN_FULL: "PAID IN FULL?",
    COL_DELIVERY:     "REQUESTED DELIVERY DATE",
}

# Column VALUES we care about (must match the exact label text on the board).
# Override in .env if your deployment labels the statuses differently.
STATUS_NEEDS_ATTENTION  = os.getenv("STATUS_NEEDS_ATTENTION",  "NEEDS ATTENTION")
STATUS_SCHEDULED        = os.getenv("STATUS_SCHEDULED",        "SCHEDULED")
ALLOCATE_PIECE_ON_PO    = os.getenv("ALLOCATE_PIECE_ON_PO",    "PIECE(S) ON PO")
ALLOCATE_LOW_STOCK      = os.getenv("ALLOCATE_LOW_STOCK",      "PIECE(S) LOW STOCK")
PAID_IN_FULL_NO         = os.getenv("PAID_IN_FULL_NO",         "NO")
PAID_IN_FULL_AR_ACCOUNT = os.getenv("PAID_IN_FULL_AR_ACCOUNT", "AR ACCOUNT")


# ── Reply-button routing (the mailto link) ──────────────────────────
# When someone clicks REPLY TO OPERATIONS in the alert, the mailto URL
# pre-fills these addresses. This creates the accountability loop:
# operations gets the reply, the rest get a copy.
REPLY_TO_ADDRESS = os.getenv("REPLY_TO_ADDRESS", "operations@example.com")


def _csv_env(key: str, default: str = "") -> tuple[str, ...]:
    """Parse a comma-separated .env value into a clean tuple of strings.

    Empty or whitespace-only entries are dropped. An empty env value yields
    an empty tuple, which downstream code handles gracefully.
    """
    raw = os.getenv(key, default)
    return tuple(a.strip() for a in raw.split(",") if a.strip())


REPLY_CC_ADDRESSES: tuple[str, ...] = _csv_env("REPLY_CC_ADDRESSES")


# ── Template-specific CC lists ──────────────────────────────────────
# Different alert templates loop in different parts of the business.
# Payment issues reach accounting; supply-chain issues reach purchasing.
UNPAID_TEMPLATE_CC: tuple[str, ...] = _csv_env("UNPAID_TEMPLATE_CC")
SUPPLY_TEMPLATE_CC: tuple[str, ...] = _csv_env("SUPPLY_TEMPLATE_CC")

# Domain used when deriving an email from a person's name
# (e.g., "Rob Robertson" -> "robr@example.com").
RECIPIENT_DOMAIN = os.getenv("RECIPIENT_DOMAIN", "example.com")


# ── Tracking marker embedded in alert + reply subjects ──────────────
# A short, unique tag that the inbox scanner looks for to detect a reply.
# Appears in: mailto reply subject, mailto reply body, hidden marker in
# the alert HTML. Keep short and unique-ish to any other traffic.
TRACKING_MARKER_PREFIX = os.getenv("TRACKING_MARKER_PREFIX", "RM")


# ── Holiday list (from .env, ISO dates) ─────────────────────────────
# OPS_HOLIDAYS="2026-01-01,2026-07-04,2026-12-25" — comma-separated ISO dates.
# We skip email sends on these days. Weekends are always skipped regardless.
_raw_holidays = os.getenv("OPS_HOLIDAYS", "")
HOLIDAYS: frozenset[str] = frozenset(
    d.strip() for d in _raw_holidays.split(",") if d.strip()
)


# ── Staleness thresholds ────────────────────────────────────────────
# How long after delivery_date we still consider an unresolved item
# "interesting enough" to surface in the stale-order report.
STALE_REPORT_WINDOW_DAYS = int(os.getenv("STALE_REPORT_WINDOW_DAYS", "30"))

# How long after the most recent human activity (comment, column change,
# reply email) we still consider the item "in active conversation" — i.e.,
# suppressed from the standard escalation ramp.
ACTIVITY_FRESHNESS_HOURS = int(os.getenv("ACTIVITY_FRESHNESS_HOURS", "48"))
