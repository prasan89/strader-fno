import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal

from strader_fno.models import IST, Quote
from strader_fno.replay import analyse_closes, telegram_pages


class ReplayTests(unittest.TestCase):
    def test_strict_crossings_order_and_first_per_direction(self):
        opening = datetime(2026, 11, 12, 9, 15, tzinfo=IST)
        row = {"symbol": "TEST", "instrument_key": "NSE_EQ|TEST", "previous_close": "100",
               "upper_outer": "104", "lower_outer": "96"}
        values = (104, 105, 100, 106, 96, 95, 99, 94)
        bars = [Quote(row["instrument_key"], Decimal(value), opening + timedelta(minutes=5 * (i + 1)))
                for i, value in enumerate(values)]
        bars.append(Quote(row["instrument_key"], Decimal(200), opening - timedelta(days=1)))
        result = analyse_closes(row, list(reversed(bars)), opening.date(), (opening, opening.replace(hour=15, minute=30)))
        self.assertEqual(["BREAKOUT", "BREAKDOWN"], [s["direction"] for s in result])
        self.assertEqual(["105", "95"], [s["price"] for s in result])
        self.assertEqual(opening + timedelta(minutes=10), datetime.fromisoformat(result[0]["time"]))

    def test_telegram_pages_are_labelled_retrospective_and_preserve_every_signal(self):
        report = {"session": "2026-11-12", "previous_session": "2026-11-11", "selected": 200,
                  "analysed": 200, "breakouts": 100, "breakdowns": 100, "missing": [], "failed": [],
                  "signals": [{"symbol": f"TEST{i}", "direction": "BREAKOUT", "price": "105", "outer_level": "104",
                               "time": "2026-11-12T10:00:00+05:30"} for i in range(200)]}
        pages = telegram_pages(report)
        self.assertTrue(all(len(p) < 4096 and "HISTORICAL CHECK" in p for p in pages))
        self.assertEqual(200, sum(p.count("Close INR") for p in pages))

    def test_broker_instrument_mismatch_is_rejected(self):
        now = datetime(2026, 11, 12, 10, tzinfo=IST)
        row = {"symbol": "TEST", "instrument_key": "NSE_EQ|TEST", "previous_close": "100",
               "upper_outer": "104", "lower_outer": "96"}
        with self.assertRaises(ValueError):
            analyse_closes(row, [Quote("NSE_EQ|WRONG", Decimal(105), now)], now.date(), (now, now + timedelta(hours=1)))
