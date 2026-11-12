import unittest
from datetime import date, datetime, timedelta

from strader_fno.calendar import TradingCalendar, within_window
from strader_fno.models import IST
from strader_fno.runner import next_wakeup
from strader_fno.scanner import next_scan


class CalendarTests(unittest.TestCase):
    def test_weekday_holiday_skips_scan_and_previous_session(self):
        calendar = TradingCalendar([{"date": "2026-11-10", "closed_exchanges": ["NSE"]}])
        self.assertIsNone(calendar.window(date(2026, 11, 10)))
        self.assertEqual(date(2026, 11, 9), calendar.previous_session(date(2026, 11, 11)))
        now = datetime(2026, 11, 9, 16, tzinfo=IST)
        self.assertEqual(datetime(2026, 11, 11, 9, 20, 5, tzinfo=IST), next_scan(now, calendar=calendar))

    def test_weekend_special_session_uses_published_hours(self):
        opening = datetime(2026, 11, 8, 18, tzinfo=IST)
        closing = opening + timedelta(hours=1)
        calendar = TradingCalendar([{"date": "2026-11-08", "open_exchanges": [{
            "exchange": "NSE", "start_time": int(opening.timestamp() * 1000),
            "end_time": int(closing.timestamp() * 1000)}]}])
        self.assertTrue(within_window(opening, calendar))
        self.assertFalse(within_window(closing, calendar))
        self.assertEqual(date(2026, 11, 8), calendar.previous_session(date(2026, 11, 9)))
        self.assertEqual(opening + timedelta(minutes=5, seconds=5), next_scan(opening, calendar=calendar))

    def test_last_completed_bar_has_one_minute_grace_and_scheduler_includes_it(self):
        calendar = TradingCalendar()
        close = datetime(2026, 11, 12, 15, 30, 5, tzinfo=IST)
        self.assertFalse(within_window(close, calendar))
        self.assertTrue(within_window(close, calendar, closing_bar=True))
        self.assertEqual(close, next_scan(close - timedelta(minutes=4), calendar=calendar, closing_bar=True))
        self.assertFalse(within_window(close.replace(minute=31), calendar, closing_bar=True))

    def test_sleep_wakes_before_opening_to_prepare_and_at_midnight_to_refresh(self):
        calendar = TradingCalendar()
        now = datetime(2026, 11, 12, 8, tzinfo=IST)
        target = next_scan(now, calendar=calendar)
        self.assertEqual(now.replace(hour=8, minute=50), next_wakeup(now, target, calendar))
        late = now.replace(hour=20)
        self.assertEqual(datetime(2026, 11, 13, tzinfo=IST), next_wakeup(late, next_scan(late), calendar))
