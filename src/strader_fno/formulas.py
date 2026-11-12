from __future__ import annotations

from decimal import Decimal

from .models import Candle, Zones


def symmetric_zones(center: Decimal, outer: Decimal, inner: Decimal,
                    reference: Decimal) -> Zones:
    return Zones(center + outer, center - outer, center + inner,
                 center - inner, center - reference, center + reference)


def range_candidate(previous: Candle) -> Zones:
    """User-adopted Midpoint Range strategy; JFT reconstruction is unconfirmed.

    Five screenshots establish the previous H/L midpoint. The offsets are
    approximate relative to the references. The scanner uses these fixed
    coefficients as its own strategy, without labelling them verified JFT.
    """
    center = (previous.high + previous.low) / 2
    width = previous.high - previous.low
    return symmetric_zones(center, width * Decimal("0.98"),
                           width * Decimal("0.76"), width * Decimal("0.29"))


def fibonacci_candidate(previous: Candle) -> Zones:
    center = (previous.high + previous.low) / 2
    width = previous.high - previous.low
    return symmetric_zones(center, width * Decimal("0.786"),
                           width * Decimal("0.618"), width * Decimal("0.236"))


def gann_candidate(previous: Candle) -> Zones:
    root = previous.close.sqrt()
    offsets = [Decimal("0.625"), Decimal("0.5"), Decimal("0.1875")]
    upper = [(root + offset) ** 2 for offset in offsets]
    lower = [(root - offset) ** 2 for offset in offsets]
    return Zones(upper[0], lower[0], upper[1], lower[1], lower[2], upper[2])


CANDIDATES = {
    "sbi_range_hypothesis": range_candidate,
    "fibonacci_midpoint": fibonacci_candidate,
    "gann_square_root": gann_candidate,
}
