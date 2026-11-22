"""Excel-safe CSV writing helpers.

A CSV cell that starts with =, +, - or @ is executed by Excel as a formula, so
every free-text field written from user input (column names, file names, table
names, evidence strings) is neutralised before it reaches a report.
"""
from __future__ import annotations

FORMULA_PREFIXES = ("=", "+", "-", "@")


def formula_safe(value) -> str:
    """Return a value Excel renders as literal text."""
    text = "" if value is None else str(value)
    if text and text[0] in FORMULA_PREFIXES:
        return "'" + text
    return text
