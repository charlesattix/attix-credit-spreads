"""
sentinel/v2 — Sentinel V2 watchdog architecture.

Architectural inversion: Sentinel owns the schedule, scanners are subprocesses.

  v1 (broken): scanner starts → calls sentinel
  v2 (fixed):  watchdog starts → calls scanner as subprocess

Entry point: python -m sentinel.v2.watchdog
"""
