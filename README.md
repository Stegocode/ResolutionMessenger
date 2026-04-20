# ResolutionMessenger

An escalation-based notification engine that watches a Monday.com board for
items flagged as needing attention and sends a calibrated sequence of email
alerts to the assigned owner as the scheduled delivery date approaches.

Built to replace a "one-ping-and-forget" workflow with something that keeps
pressure on a shrinking action window without spamming — and goes quiet as
soon as there's evidence a human is handling the issue.

---

## What it does

For every item currently in the **NEEDS ATTENTION** group on the board:

1. **Computes which notification slot is due.** The schedule ramps up as
   delivery approaches:

   | Days before delivery | Emails fired | When |
   |---|---|---|
   | T-7, T-6, T-5, T-4 | 1/day | 09:00 |
   | T-3                | 2/day | 09:00, 14:00 |
   | T-2                | 4/day | 08:00, 11:00, 13:00, 15:00 |
   | T-1                | 7/day | hourly 08:00–14:00 |
   | T-0 (delivery day) | — | not sent |

   The 14:00 T-1 slot is the "LAST WARNING" before ops reschedules at 15:00.

2. **Picks the right template.** Three variants, selected from the item's
   current column state:

   - `UNPAID_BALANCE` — triggered by the PAID IN FULL? column
   - `ITEM_NOT_ON_PO` — supply-chain issue without a purchase order
   - `PIECES_ON_PO` — supply-chain issue with an active PO

3. **Suppresses if a human has responded.** Three OR-ed signals mean
   "someone's on it, skip the next ping":

   - A Monday comment posted after the last notification
   - A reply email in the inbox tagged with the item's tracking marker
   - (Future) A column-value change since the last notification

   The **LAST WARNING** slot bypasses all suppression — it must fire.

4. **Sends via Outlook desktop (MAPI/COM).** No SMTP AUTH means no fighting
   tenant-level Microsoft 365 policies — whatever auth Outlook is signed in
   with is what the notifier uses.

5. **Embeds a `REPLY TO OPERATIONS` button** (a styled `mailto:` link) that
   pre-fills a reply to the operations lead with the accountability team
   CC'd. The tracking marker `[RM:NNNNN]` in the subject is what the
   inbox scanner detects in step 3.

---

## Architecture at a glance

```
board_client.py   ── Monday.com GraphQL wrapper
escalation.py     ── "what slot fires now?" math
recipients.py     ── template picker + email derivation
templates.py      ── HTML render + mailto: button
suppression.py    ── three-signal acknowledgment detection
schema.py         ── SQLite DDL (notification_log, reply_log)
reply_scanner.py  ── Outlook inbox scanner for reply markers
reports.py        ── stale-order Markdown report
notifier.py       ── orchestrator (the `run_tick()` entry point)
__main__.py       ── CLI
config.py         ── every tunable, in one place
```

**Storage:** SQLite. Two small tables — one for sends we've made, one for
replies we've detected. See `schema.py`.

**Timezone:** all business-hour math uses `America/Los_Angeles`. Server
clock doesn't matter.

**Holidays:** comma-separated ISO dates in `.env` (`OPS_HOLIDAYS=...`).
Weekends are always skipped. No catchup on missed slots — each business
day reorients to today's position on the curve.

See [`DECISIONS.md`](DECISIONS.md) for the full rationale on every
non-obvious design call.

---

## Usage

### Install

```bash
pip install -r requirements.txt
```

### One tick

```bash
python -m ResolutionMessenger tick --dry-run              # preview only
python -m ResolutionMessenger tick --force-recipient you@example.com  # test-send
python -m ResolutionMessenger tick                         # live
```

### Other commands

```bash
python -m ResolutionMessenger scan-replies   # walk inbox for reply markers
python -m ResolutionMessenger stale-report   # write the stuck-orders Markdown
python -m ResolutionMessenger all            # scan → tick → stale-report
```

### Environment

Required in `.env`:

```
MONDAY_API_TOKEN=...
EMAIL_FROM=you@yourcompany.com
```

Optional:

```
NOTIFY_FALLBACK_EMAIL=ops@yourcompany.com
OPS_HOLIDAYS=2026-01-01,2026-07-04,2026-12-25
ISSUES_DB_PATH=/custom/path/to/issues.db
```

---

## Tests

```bash
pytest tests/ -v
```

78 tests covering the scheduling engine, template selection, HTML rendering,
suppression logic, SQLite schema, and end-to-end orchestration. Monday and
Outlook are both monkeypatched in the integration tests so nothing hits
real APIs during CI.

---

## License

MIT — see [`LICENSE`](LICENSE).
