# ResolutionMessenger — Design Decisions

One-line-per-decision log of non-obvious choices, with the reasoning. Read
this before changing anything load-bearing.

---

## Schedule shape

**Decision:** `config.SCHEDULE` maps days-before-delivery → tuple of 24h slot hours.

| Day | Slots (OPS_TZ) | Rationale |
|---|---|---|
| T-7 | 09:00 | Morning touch; one gentle nudge |
| T-6 | 09:00 | Same |
| T-5 | 09:00 | Same |
| T-4 | 09:00 | Same |
| T-3 | 09:00, 14:00 | Cadence starts ramping |
| T-2 | 08:00, 11:00, 13:00, 15:00 | Evenly spaced across business day |
| T-1 | 08:00–14:00 hourly (7 fires) | Pressure peaks |

**No T-0:** operations decided the 14:00 T-1 "last warning" is the final
auto-send. If unresolved by 15:00, dispatch reschedules manually. There's
no delivery-day nag.

---

## Timezone is baked into OPS_TZ, not parsed at call sites

All business-hour math uses `config.OPS_TZ` (default
`America/Los_Angeles`, override via `.env`). Single source of truth — if
the deployment's timezone changes, one line moves. Test helpers use the
same TZ, so no drift between runtime and tests.

---

## "Already opened" slot semantics

`escalation.slots_due_for_item()` returns every slot whose `slot_dt <= now`.
Multiple returned means the scheduler has been down and we have backlog.
The notifier consumes only the **latest** slot per tick — older slots are
silently skipped forever (we don't flood a single inbox with catch-up mail).

**Trade-off:** if the scheduler is down from 09:00 to 13:00 on T-3, the
09:00 slot never sends. The 14:00 slot does. We accept that loss.

---

## `fire_key` format: `YYYY-MM-DD_HHMM_T-N`

Human-readable, sortable, stable. Makes `notification_log` easy to scan by
eye: `SELECT * FROM notification_log WHERE item_id='123' ORDER BY fire_key`.

Dedup is enforced by the `(item_id, fire_key)` primary key on
`notification_log`. A second tick on the same slot is a no-op via
`INSERT OR IGNORE`.

---

## Board state is the source of truth, not our SQLite

Items at `<status_col> = "NEEDS ATTENTION"` on the board drive
notifications. The SQLite DB stores only:
1. The log of what we've sent (`notification_log`)
2. The log of inbound replies we've detected (`reply_log`)

**Why:** the dispatcher is the ground truth for "is this issue alive?" —
we should observe their state, not second-guess it with our own mirror.

---

## Template selection rules (`recipients.pick_template_key`)

Precedence: payment > supply.

- `PAID IN FULL? ∈ {NO, AR ACCOUNT}` → UNPAID_BALANCE
- `ALLOCATE = PIECE(S) LOW STOCK`     → ITEM_NOT_ON_PO
- Everything else (including `PIECE(S) ON PO` and blanks) → PIECES_ON_PO

Why payment wins: unpaid delivery blocks loading outright; supply issues
affect *what* is loaded but not *whether* the order can ship.

---

## Recipient derivation: `first + lastinitial @ domain`

Email is derived from the assigned user's board display name rather than
the board's `email` field. Reason: a deployment's directory may store
emails in a different format than its user-facing convention (e.g.,
stored as `firstname.lastname@`). Derivation is the single predictable
rule.

Domain comes from `RECIPIENT_DOMAIN` in `.env` (default `example.com`).

Fallback chain if derivation fails: `NOTIFY_FALLBACK_EMAIL` → `EMAIL_TO`
→ first static CC address.

---

## Suppression: three OR-ed signals

Any **one** of these three, newer than the most recent notification, means
"someone's working on it — skip escalation":

1. A board comment/update on the item
2. A reply email in the inbox tagged `[<MARKER>:NNNNN]`
3. (Future — not yet implemented) Column value change on the board

**Escape hatch:** the T-1 14:00 "final warning" slot bypasses all three.
It must fire or dispatch can't make the 15:00 reschedule decision.

---

## Hidden marker in alert HTML (`---\n<MARKER>:NNNNN\n---`)

Inserted in white 1px text at the bottom of every HTML alert. When the
recipient clicks plain "Reply" (instead of our button), Outlook quotes
the whole body — including the marker — into the reply. That lets the
inbox scanner detect the reply even if the button wasn't used.

Cross-client behavior: Outlook respects `mso-hide:all` and the 1px size.
Gmail/Apple Mail ignore `mso-hide` but the 1px size + white color on
white background keeps it invisible. Good enough.

`<MARKER>` is the value of `TRACKING_MARKER_PREFIX` in `.env`
(default `RM`).

---

## Reply detection via Outlook COM, not IMAP/Graph

Reuses the MAPI/COM channel the host workstation already has signed in.
No new auth, no admin intervention. Trade-off: Outlook desktop must be
running on the host machine.

Lookback window: 14 days by default. Long enough that a week-long
escalation cycle can't miss a reply; short enough that each scan is
fast.

---

## Final-warning template visually differentiated

Black banner (not red) + 🚨 siren emoji + yellow inline warning box. The
standard templates are all-red — by T-1 14:00 the recipient has seen
several of those. Making the final one visually distinct keeps it from
blending in.

---

## Stale-order report is write-only

Reports are Markdown files in `reports/`, one per run. Never emailed.
Rationale: a human already ignored several escalation emails — spamming
them with a "final final final" email wouldn't help. A written log that
a human *chooses to read* is the right pattern.

---

## No missing-order auto-create on the board

Explicitly deferred. When our DB has an open issue with no matching
board item, we currently just skip it. A human creates the item on the
board manually. Adding auto-create is future work.

---

## Portfolio-friendly configuration

Module names avoid product- and person-name prefixes. All deployment
specifics (board ID, column IDs, recipient lists, marker prefix, domain)
are read from `.env` with placeholder defaults — `config.py` itself
contains no real-world identifiers. See `.env.example` for the full set
of variables a deployment supplies.

---

## Testing philosophy

- Pure logic modules (`escalation`, `recipients.email_from_name`,
  `templates`) have many small tests.
- I/O modules (`board_client`, `reply_scanner`) are monkeypatched by the
  `notifier` tests — we verify the orchestrator's glue, not that
  `requests.post()` works.
- Date/time tests use an explicit `OPS_TZ` datetime so they don't depend
  on when the test is run.
