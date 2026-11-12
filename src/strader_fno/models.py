from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def price(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid price") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError("Price must be finite and positive")
    return result


@dataclass(frozen=True)
class Candle:
    session: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int = 0

    def __post_init__(self) -> None:
        for value in (self.open, self.high, self.low, self.close):
            price(value)
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("Inconsistent candle OHLC")
        if not isinstance(self.volume, int) or self.volume < 0:
            raise ValueError("Candle volume must be a nonnegative integer")


@dataclass(frozen=True)
class Quote:
    instrument: str
    last_price: Decimal
    timestamp: datetime

    def __post_init__(self) -> None:
        price(self.last_price)
        if not self.instrument or self.timestamp.tzinfo is None:
            raise ValueError("Quote needs an instrument and an aware timestamp")


@dataclass(frozen=True)
class Zones:
    upper_outer: Decimal
    lower_outer: Decimal
    upper_inner: Decimal
    lower_inner: Decimal
    lower_reference: Decimal
    upper_reference: Decimal

    def as_plot_values(self) -> list[Decimal]:
        return [self.upper_outer, self.lower_outer, self.upper_inner,
                self.lower_inner, self.lower_reference, self.upper_reference,
                self.lower_inner]
