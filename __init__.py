"""ResolutionMessenger — escalation-based delivery alert notifier.

This package watches a project-management board (Monday.com in our current
deployment) for items flagged as needing attention, then sends a calibrated
sequence of escalation emails to the assigned owner as the scheduled
delivery date approaches.

Public entry points:
    - notifier.run_tick()         — one pass of the scheduler's work
    - reports.write_stale_report() — snapshot of items past delivery

See DECISIONS.md for architectural notes and CHANGELOG.md for history.
"""

__version__ = "0.1.0"
