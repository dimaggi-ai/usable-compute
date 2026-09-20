"""Explicit integer units for infrastructure adapters; no ambiguous 'gbps'."""

from decimal import Decimal, InvalidOperation

UNITS = {
    "B": ("bytes", 1, 1),
    "KB": ("bytes", 1000, 1),
    "MB": ("bytes", 10**6, 1),
    "GB": ("bytes", 10**9, 1),
    "KiB": ("bytes", 2**10, 1),
    "MiB": ("bytes", 2**20, 1),
    "GiB": ("bytes", 2**30, 1),
    "B/s": ("bytes_per_second", 1, 1),
    "GB/s": ("bytes_per_second", 10**9, 1),
    "GiB/s": ("bytes_per_second", 2**30, 1),
    "Gb/s": ("bytes_per_second", 10**9, 8),
    "W": ("watts", 1, 1),
    "kW": ("watts", 1000, 1),
    "us": ("microseconds", 1, 1),
    "ms": ("microseconds", 1000, 1),
    "s": ("microseconds", 10**6, 1),
}


def normalize_quantity(value, unit, dimension):
    if type(value) not in (str, int) or type(unit) is not str or unit not in UNITS:
        raise ValueError(
            "explicit supported unit and decimal string or integer required"
        )
    # Bound Decimal work before parsing; floats and bools cannot silently round.
    raw = str(value)
    if len(raw) > 40 or "e" in raw.lower():
        raise ValueError("bounded non-exponent decimal required")
    try:
        number = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("invalid quantity") from exc
    expected, numerator, denominator = UNITS[unit]
    if dimension != expected or not number.is_finite() or number < 0:
        raise ValueError("invalid quantity dimension or value")
    converted = number * numerator / denominator
    if converted != converted.to_integral_value() or converted > 2**53 - 1:
        raise ValueError("quantity is fractional or exceeds exact integer boundary")
    return int(converted)
