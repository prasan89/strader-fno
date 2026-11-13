import unittest
from datetime import date, datetime
from decimal import Decimal

from strader_fno.brokers import Upstox, parse_timestamp
from strader_fno.http import ApiError
from strader_fno.models import IST, price
from strader_fno.telegram import Telegram


class BrokerTests(unittest.TestCase):
    def test_token_verification_uses_market_api_supported_by_analytics_tokens_without_static_ip(self):
        def transport(url, **kwargs):
            self.assertEqual("https://api.upstox.com/v2/market/status/NSE", url)
            self.assertEqual("Bearer analytics-test-token", kwargs["headers"]["Authorization"])
            return {"status": "success", "data": {"exchange": "NSE", "status": "normal"}}
        Upstox("analytics-test-token", transport).verify()

    def test_full_quote_uses_broker_trade_time_and_requested_instrument(self):
        def transport(url, **kwargs):
            self.assertIn("/v2/market-quote/quotes?", url)
            self.assertIn("NSE_EQ%7CTEST", url)
            return {"status": "success", "data": {"NSE_EQ:SBILIFE": {
                "instrument_token": "NSE_EQ|TEST", "last_price": 1825.1,
                "last_trade_time": "2026-11-12T10:00:00+05:30",
            }}}
        quote = Upstox("test-token", transport).quotes(["NSE_EQ|TEST"])[0]
        self.assertEqual(Decimal("1825.1"), quote.last_price)
        self.assertEqual(datetime(2026, 11, 12, 10, 0, tzinfo=IST), quote.timestamp)

    def test_quote_without_trade_timestamp_is_not_presented_as_live(self):
        def transport(url, **kwargs):
            return {"status": "success", "data": {"NSE_EQ:SBILIFE": {
                "instrument_token": "NSE_EQ|TEST", "last_price": 1825.1,
            }}}
        self.assertEqual([], Upstox("test-token", transport).quotes(["NSE_EQ|TEST"]))

    def test_unrequested_instrument_and_unsuccessful_response_fail(self):
        def transport(url, **kwargs):
            return {"status": "success", "data": {"wrong": {"instrument_token": "NSE_EQ|WRONG"}}}
        with self.assertRaises(ApiError):
            Upstox("test-token", transport).quotes(["NSE_EQ|TEST"])

    def test_daily_history_uses_day_bars_and_preserves_order(self):
        def transport(url, **kwargs):
            self.assertIn("/v3/historical-candle/NSE_EQ%7CTEST/days/1/", url)
            return {"status": "success", "data": {"candles": [
                ["2026-11-11T00:00:00+05:30", 1790, 1800, 1748.2, 1767, 100],
                ["2026-11-09T00:00:00+05:30", 1763, 1771.7, 1730, 1748, 100],
            ]}}
        candles = Upstox("test-token", transport).daily_candles("NSE_EQ|TEST", date(2026, 11, 1), date(2026, 11, 11))
        self.assertEqual([date(2026, 11, 9), date(2026, 11, 11)], [c.session for c in candles])
        self.assertEqual(Decimal("1748.2"), candles[-1].low)

    def test_epoch_milliseconds_and_naive_timestamp_rejection(self):
        instant = datetime(2026, 11, 12, 10, 0, tzinfo=IST)
        self.assertEqual(instant, parse_timestamp(str(int(instant.timestamp() * 1000))))
        with self.assertRaises(ValueError):
            parse_timestamp("2026-11-12T10:00:00")

    def test_candle_mode_ignores_unfinished_five_minute_bar(self):
        def transport(url, **kwargs):
            return {"status": "success", "data": {"candles": [
                ["2026-11-12T10:00:00+05:30", 100, 110, 99, 109],
                ["2026-11-12T09:55:00+05:30", 100, 103, 99, 102],
            ]}}
        now = datetime(2026, 11, 12, 10, 0, 5, tzinfo=IST)
        result = Upstox("test", transport).completed_five_minute_close("NSE_EQ|TEST", now)
        self.assertEqual(Decimal(102), result.last_price)
        self.assertEqual(now.replace(second=0), result.timestamp)

    def test_nonfinite_prices_are_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity", "0", "-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                price(value)

    def test_telegram_rate_limit_is_a_failed_delivery_and_error_has_no_token(self):
        def transport(url, **kwargs):
            self.assertEqual("chat", kwargs["payload"]["chat_id"])
            return {"ok": False, "error_code": 429, "parameters": {"retry_after": 12}}
        with self.assertRaises(ApiError) as failure:
            Telegram("123:test-secret", "chat", transport).send("example")
        self.assertEqual(12, failure.exception.retry_after)
        self.assertNotIn("test-secret", str(failure.exception))


if __name__ == "__main__":
    unittest.main()
