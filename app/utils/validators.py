"""Checksum and validity helpers used by the detectors."""
from __future__ import annotations

from datetime import date


def digits_only(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def luhn_valid(number: str) -> bool:
    total = 0
    for index, ch in enumerate(reversed(number)):
        d = ord(ch) - 48
        if index % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def resident_checksum(digits: str) -> bool:
    """Validate the Korean resident registration number checksum (13 digits)."""
    if len(digits) < 12:
        return False
    if len(digits) == 13:
        weights = (2, 4, 6, 8, 1, 2, 4, 6, 8, 1, 2, 4)
        total = sum(w * int(d) for w, d in zip(weights, digits[:12]))
        return (10 - total % 10) % 10 == int(digits[12])
    weights = (2, 4, 6, 8, 1, 2, 4, 6, 8, 1)
    total = sum(w * int(d) for w, d in zip(weights, digits[:10]))
    return (10 - total % 10) % 10 == int(digits[10])


def birth_part_valid(yymmdd: str) -> bool:
    if len(yymmdd) != 6:
        return False
    month = int(yymmdd[2:4])
    day = int(yymmdd[4:6])
    return 1 <= month <= 12 and 1 <= day <= 31


def plausible_date(year: int, month: int, day: int) -> bool:
    if not 1900 <= year <= date.today().year + 1:
        return False
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return False
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


KOREAN_MCC = {450, 451}  # Korea mobile country codes


def imsi_valid(digits: str) -> bool:
    if len(digits) != 15:
        return False
    mcc = int(digits[:3])
    return 200 <= mcc <= 799
