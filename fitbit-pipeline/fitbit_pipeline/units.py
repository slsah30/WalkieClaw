"""Unit conversion.

Canonical units from the API are stored untouched: millimeters, grams, Celsius,
kilocalories, minutes. Imperial conversion happens here, at render time only.
"""

from __future__ import annotations

MM_PER_MILE = 1_609_344.0
MM_PER_FOOT = 304.8
MM_PER_INCH = 25.4
GRAMS_PER_POUND = 453.59237


def mm_to_miles(millimeters: float | None) -> float | None:
    return None if millimeters is None else millimeters / MM_PER_MILE


def mm_to_feet(millimeters: float | None) -> float | None:
    return None if millimeters is None else millimeters / MM_PER_FOOT


def mm_to_inches(millimeters: float | None) -> float | None:
    return None if millimeters is None else millimeters / MM_PER_INCH


def grams_to_pounds(grams: float | None) -> float | None:
    return None if grams is None else grams / GRAMS_PER_POUND


def celsius_to_fahrenheit(celsius: float | None) -> float | None:
    """Absolute temperature, for example a nightly skin temperature reading."""
    return None if celsius is None else celsius * 9.0 / 5.0 + 32.0


def celsius_delta_to_fahrenheit(celsius: float | None) -> float | None:
    """A temperature *difference*, which carries no 32 degree offset."""
    return None if celsius is None else celsius * 9.0 / 5.0


def minutes_to_hhmm(minutes: float | None) -> str:
    if minutes is None:
        return ""
    total = int(round(minutes))
    return f"{total // 60}h {total % 60:02d}m"


def seconds_to_hhmmss(seconds: float | None) -> str:
    if seconds is None:
        return ""
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
