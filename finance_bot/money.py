from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


CENTS = Decimal("100")


def to_cents(value: float | int | str | Decimal) -> int:
    """Convert a money value to integer cents using financial rounding."""
    decimal_value = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(decimal_value * CENTS)


def from_cents(value: int | None, fallback_amount: float | int | str | Decimal | None = None) -> float:
    """Return a float amount for presentation/query compatibility."""
    if value is not None:
        return float(Decimal(int(value)) / CENTS)
    if fallback_amount is None:
        return 0.0
    return float(Decimal(str(fallback_amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
