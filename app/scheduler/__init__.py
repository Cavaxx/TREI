"""Scheduling (§47).

``app.scheduler.plan`` builds the timetable from the registry and the policy
resolver: incremental crawls, less frequent full re-crawls, email polling, at
least a daily auction check, and macro refreshes at each source's own cadence.

A source the policy refuses is simply never scheduled, so a wrong cadence can
never turn into an unauthorized request.

Run it with ``python -m app.scheduler.plan plan`` (or ``due``).
"""
