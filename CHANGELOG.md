# ResolutionMessenger — Changelog

## 0.1.0 — Initial release

### Added
- `config.py` — central constants, env loading, schedule definition, holiday list
- `board_client.py` — Monday.com GraphQL wrapper (items, updates, users)
- `schema.py` — SQLite DDL for `notification_log` and `reply_log` tables
- `escalation.py` — fire-slot computation (T-7 through T-1, weekend/holiday skip)
- `recipients.py` — template selection + email derivation
- `templates.py` — HTML alert rendering + mailto button builder
- `suppression.py` — three-signal acknowledgment detection (comment / reply / future column-change)
- `reply_scanner.py` — Outlook inbox scanner for tracking-marker reply tags
- `notifier.py` — `run_tick()` orchestrator
- `reports.py` — stale-order Markdown report generator
- `__main__.py` — CLI with `tick`, `scan-replies`, `stale-report`, `all` subcommands

### Tests (78 passing)
- `test_escalation.py` — schedule, slot timing, weekend/holiday skip, fire_key shape
- `test_recipients.py` — template rules, email derivation, fallback chain
- `test_templates.py` — HTML render, mailto URL, final-warning styling, HTML escape
- `test_suppression.py` — all three signals + final-warning bypass + ISO parsing
- `test_schema.py` — DDL idempotency, PK dedup behavior
- `test_notifier.py` — end-to-end integration (dry-run, live, suppression, force-recipient)
- `test_reports.py` — stale-order formatting, window filtering, pipe escape

### Configuration is fully `.env`-driven
- All board IDs, column IDs, recipient lists, status labels, and the marker
  prefix are read from environment variables with placeholder defaults.
- See `.env.example` for the complete list of supported variables.

### Deferred
- Missing-board-item auto-create
- Column-change signal in suppression (third arm of the OR)
