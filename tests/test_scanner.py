import tempfile
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from strader_fno.models import Candle, IST, Quote
from strader_fno.scanner import Scanner, market_window, next_scan, scanner_lease
from strader_fno.universe import eligible_stocks, rank_liquidity

SESSION = date(2026, 11, 12)
NOW = datetime(2026, 11, 12, 10, 0, 5, tzinfo=IST)


def catalog(count=210):
    expiry = int(datetime(2026, 12, 29, tzinfo=IST).timestamp() * 1000)
    result = []
    for index in range(count):
        key = f"NSE_EQ|TEST{index:03}"
        result.append({"segment": "NSE_EQ", "instrument_type": "EQ", "instrument_key": key,
                       "trading_symbol": f"S{index:03}", "tick_size": 5})
        for month in (0, 1):
            result.append({"segment": "NSE_FO", "instrument_type": "FUT", "underlying_type": "EQUITY",
                           "underlying_key": key, "expiry": expiry + month * 86400000})
    return result


class Broker:
    def __init__(self):
        self.history_calls, self.quote_batches = [], []
        self.stale = False

    def market_calendar(self, day=None):
        return []

    def daily_candles(self, instrument, start, end):
        self.history_calls.append(instrument)
        index = int(instrument[-3:])
        return [Candle(end - timedelta(days=i), Decimal(100), Decimal(102), Decimal(98),
                       Decimal(100), volume=(index + 1) * 1000) for i in range(25)]

    def quotes(self, keys):
        self.quote_batches.append(keys)
        result = []
        for key in keys:
            value = Decimal(107) if key.endswith("209") else Decimal(93) if key.endswith("208") else Decimal(100)
            result.append(Quote(key, value, NOW - timedelta(days=1) if self.stale else NOW))
        return result


class Sender:
    def __init__(self):
        self.messages = []

    def send(self, text):
        self.messages.append(text)


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.broker, self.sender = Broker(), Sender()
        self.scanner = Scanner(self.broker, self.sender, Path(self.directory.name),
                               catalog_provider=lambda: catalog(), sleeper=lambda _: None, clock=lambda: NOW)

    def tearDown(self):
        self.scanner.close()
        self.directory.cleanup()

    def test_top_200_ranked_once_and_full_scan_batches_both_directions(self):
        snapshot = self.scanner.prepare(SESSION)
        self.assertEqual(210, snapshot["eligible_count"])
        self.assertEqual(200, snapshot["selected_count"])
        self.assertEqual("S209", snapshot["instruments"][0]["symbol"])
        self.assertEqual("S010", snapshot["instruments"][-1]["symbol"])
        self.assertEqual("103.92", snapshot["instruments"][0]["upper_outer"])
        self.assertEqual("96.08", snapshot["instruments"][0]["lower_outer"])
        first = self.scanner.scan_once(NOW)
        self.assertEqual([100, 100], [len(batch) for batch in self.broker.quote_batches])
        self.assertEqual(200, first["fresh_quotes"])
        self.assertEqual(2, first["alerts_delivered"])
        self.assertTrue(self.sender.messages[0].startswith("BREAKOUT | S209"))
        self.assertTrue(self.sender.messages[1].startswith("BREAKDOWN | S208"))
        self.assertEqual(210, len(self.broker.history_calls))
        second = self.scanner.scan_once(NOW)
        self.assertEqual(0, second["alerts_delivered"])
        self.assertEqual(210, len(self.broker.history_calls))

    def test_daily_snapshot_survives_restart_without_history_refetch(self):
        self.scanner.prepare(SESSION)
        self.scanner.close()
        self.scanner = Scanner(self.broker, self.sender, Path(self.directory.name),
                               catalog_provider=lambda: self.fail("should reuse daily snapshot"), clock=lambda: NOW)
        self.assertEqual(200, self.scanner.prepare(SESSION)["selected_count"])
        self.assertEqual(210, len(self.broker.history_calls))

    def test_new_session_fetches_and_rebuilds_daily_levels(self):
        self.scanner.prepare(SESSION)
        self.scanner.prepare(SESSION + timedelta(days=1))
        self.assertEqual(420, len(self.broker.history_calls))
        self.assertEqual("2026-11-12", self.scanner.snapshot["instruments"][0]["previous_session"])

    def test_market_closed_does_not_call_broker_or_send_alerts(self):
        result = self.scanner.scan_once(NOW.replace(hour=16))
        self.assertEqual("market_closed", result["status"])
        self.assertEqual([], self.broker.history_calls)
        self.assertEqual([], self.broker.quote_batches)

    def test_previous_day_quotes_are_visible_as_stale_and_never_alert(self):
        self.broker.stale = True
        result = self.scanner.scan_once(NOW)
        self.assertEqual("partial", result["status"])
        self.assertEqual(0, result["fresh_quotes"])
        self.assertEqual(0, result["alerts_queued"])

    def test_two_watchers_cannot_share_one_state_directory(self):
        with scanner_lease(Path(self.directory.name)):
            with self.assertRaises(ValueError):
                with scanner_lease(Path(self.directory.name)):
                    pass

    def test_all_stale_history_is_rejected_and_refetched_after_feed_recovers(self):
        original = self.broker.daily_candles
        self.broker.daily_candles = lambda key, start, end: original(key, start, end - timedelta(days=1))
        with self.assertRaises(ValueError):
            self.scanner.prepare(SESSION)
        self.assertFalse((Path(self.directory.name) / f"levels-{SESSION}.json").exists())
        self.broker.daily_candles = original
        self.assertEqual(200, self.scanner.prepare(SESSION)["selected_count"])
        self.assertEqual(420, len(self.broker.history_calls))


class UniverseTests(unittest.TestCase):
    def test_active_equity_futures_only_and_duplicate_expiries_are_deduplicated(self):
        rows = catalog(3)
        rows.extend([
            {"segment": "NSE_FO", "instrument_type": "FUT", "underlying_type": "INDEX",
             "underlying_key": "NSE_INDEX|Nifty 50", "expiry": 1999999999000},
            {"segment": "NSE_FO", "instrument_type": "FUT", "underlying_type": "EQUITY",
             "underlying_key": "NSE_EQ|MISSING", "expiry": 1999999999000},
        ])
        for row in rows:
            if row.get("underlying_key") == "NSE_EQ|TEST000":
                row["expiry"] = 0
        stocks = eligible_stocks(rows, SESSION)
        self.assertEqual(["S001", "S002"], [s.symbol for s in stocks])
        self.assertEqual(Decimal("0.05"), stocks[0].tick_size)

    def test_unfinished_current_day_candle_does_not_affect_liquidity_ranking(self):
        stocks = eligible_stocks(catalog(2), SESSION)
        history = {s.instrument: [Candle(SESSION - timedelta(days=i + 1), Decimal(100), Decimal(102),
                                       Decimal(98), Decimal(100), volume=index + 1) for i in range(20)]
                   for index, s in enumerate(stocks)}
        history[stocks[0].instrument].append(Candle(SESSION, Decimal(100), Decimal(102), Decimal(98),
                                                  Decimal(100), volume=999999999))
        ranked = rank_liquidity(stocks, history, SESSION, count=1)
        self.assertEqual("S001", ranked[0][0].symbol)

    def test_zero_volume_or_insufficient_history_is_excluded(self):
        stocks = eligible_stocks(catalog(2), SESSION)
        history = {s.instrument: [Candle(SESSION - timedelta(days=i + 1), Decimal(100), Decimal(102),
                                       Decimal(98), Decimal(100), volume=0) for i in range(20)] for s in stocks}
        self.assertEqual([], rank_liquidity(stocks, history, SESSION))


class ScheduleTests(unittest.TestCase):
    def test_five_minute_boundaries_and_friday_close(self):
        self.assertEqual(NOW.replace(minute=5), next_scan(NOW))
        before = NOW.replace(hour=9, minute=14, second=0)
        self.assertEqual(before.replace(minute=15, second=5), next_scan(before))
        friday = datetime(2026, 11, 13, 15, 26, tzinfo=IST)
        self.assertEqual(datetime(2026, 11, 16, 9, 15, 5, tzinfo=IST), next_scan(friday))

    def test_weekends_and_market_close_are_outside_the_window(self):
        self.assertFalse(market_window(NOW.replace(hour=15, minute=30)))
        self.assertFalse(market_window(datetime(2026, 11, 14, 10, 0, tzinfo=IST)))


if __name__ == "__main__":
    unittest.main()
