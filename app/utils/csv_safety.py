"""Excel-safe CSV writing helpers.

A CSV cell that starts with =, +, - or @ is executed by Excel as a formula, so
every free-text field written from user input (column names, file names, table
names, evidence strings) is neutralised before it reaches a report.
"""
from __future__ import annotations

FORMULA_PREFIXES = ("=", "+", "-", "@")
# Characters Excel skips before deciding whether a cell is a formula. Leading
# whitespace must be checked too, otherwise " =1+1" still executes.
LEADING_SKIP = " \t\r\n"


def looks_like_formula(value) -> bool:
    """True when Excel would evaluate the value, ignoring leading whitespace."""
    text = "" if value is None else str(value)
    stripped = text.lstrip(LEADING_SKIP)
    return bool(stripped) and stripped[0] in FORMULA_PREFIXES


def formula_safe(value) -> str:
    """Return a value Excel renders as literal text.

    The original text is never trimmed or otherwise altered: an apostrophe is
    merely prepended, which is what spreadsheet importers use to force a literal.
    """
    text = "" if value is None else str(value)
    if looks_like_formula(text):
        return "'" + text
    return text
