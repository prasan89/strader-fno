from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .models import IST


class TradingCalendar:
    """NSE holiday closures and published special-session timings."""

    def __init__(self, entries: list[dict] = ()):
        self.entries = {date.fromisoformat(row["date"]): row for row in entries}

    def window(self, day: date) -> tuple[datetime, datetime] | None:
        row = self.entries.get(day, {})
        for session in row.get("open_exchanges", []):
            if session.get("exchange") == "NSE":
                opening = datetime.fromtimestamp(int(session["start_time"]) / 1000, IST)
                closing = datetime.fromtimestamp(int(session["end_time"]) / 1000, IST)
                if opening.date() != day or closing <= opening:
                    raise ValueError("Invalid NSE special-session timing")
                return opening, closing
        if "NSE" in row.get("closed_exchanges", []) or day.weekday() >= 5:
            return None
        return datetime.combine(day, time(9, 15), IST), datetime.combine(day, time(15, 30), IST)

    def previous_session(self, day: date) -> date:
        for offset in range(1, 367):
            previous = day - timedelta(days=offset)
            if self.window(previous):
                return previous
        raise ValueError("No preceding NSE trading session found")


def within_window(now: datetime, calendar: TradingCalendar, *, closing_bar=False) -> bool:
    now = now.astimezone(IST)
    window = calendar.window(now.date())
    if not window:
        return False
    opening, closing = window
    return opening <= now < closing + (timedelta(minutes=1) if closing_bar else timedelta(0))
