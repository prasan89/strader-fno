import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from strader_fno.alerts import AlertStore, load_levels
from strader_fno.models import IST, Quote

SESSION = date(2026, 11, 12)
START = datetime(2026, 11, 12, 10, 0, tzinfo=IST)


class Sender:
    def __init__(self, fail=False):
        self.messages = []
        self.fail = fail

    def send(self, text):
        if self.fail:
            raise RuntimeError("delivery failed")
        self.messages.append(text)


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "alerts.sqlite"
        self.store = AlertStore(self.path)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def observe(self, value, seconds=0, **options):
        now = START + timedelta(seconds=seconds)
        quote = Quote("NSE_EQ|TEST", Decimal(value), now)
        return self.store.observe("SBILIFE", quote, Decimal("1824.9"), SESSION, now=now, **options)

    def test_crossing_is_strict_and_sent_once_per_session(self):
        self.assertFalse(self.observe("1824.8"))
        self.assertFalse(self.observe("1824.9", 1))
        self.assertTrue(self.observe("1825.0", 2))
        sender = Sender()
        self.assertEqual(1, self.store.deliver(sender, SESSION))
        self.assertIn("1824.9", sender.messages[0])
        self.assertFalse(self.observe("1824.7", 3))
        self.assertFalse(self.observe("1825.1", 4))
        self.assertEqual(0, self.store.deliver(sender, SESSION))

    def test_initial_above_threshold_does_not_invent_a_crossing(self):
        self.assertFalse(self.observe("1830"))
        self.assertFalse(self.observe("1831", 1))
        self.assertFalse(self.observe("1820", 2))
        self.assertTrue(self.observe("1825", 3))

    def test_startup_above_alert_is_an_explicit_option(self):
        self.assertTrue(self.observe("1830", alert_if_already_above=True))

    def test_failed_delivery_remains_pending_after_restart(self):
        self.observe("1824.8")
        self.observe("1825", 1)
        with self.assertRaises(RuntimeError):
            self.store.deliver(Sender(fail=True), SESSION)
        self.store.close()
        self.store = AlertStore(self.path)
        sender = Sender()
        self.assertEqual(1, self.store.deliver(sender, SESSION))
        self.assertEqual(0, self.store.deliver(sender, SESSION))

    def test_older_duplicate_and_stale_quotes_do_not_change_the_baseline(self):
        self.observe("1824.8", 2)
        self.assertFalse(self.observe("1830", 1))
        self.assertFalse(self.observe("1830", 2))
        old = Quote("NSE_EQ|TEST", Decimal("1830"), START)
        self.assertFalse(self.store.observe("SBILIFE", old, Decimal("1824.9"), SESSION,
                                            now=START + timedelta(seconds=60)))
        self.assertTrue(self.observe("1830", 3))

    def test_future_quotes_and_market_close_are_rejected(self):
        quote = Quote("NSE_EQ|TEST", Decimal("1830"), START + timedelta(seconds=10))
        self.assertFalse(self.store.observe("SBILIFE", quote, Decimal("1824.9"), SESSION,
                                            now=START, alert_if_already_above=True))
        close = START.replace(hour=15, minute=30)
        quote = Quote("NSE_EQ|TEST", Decimal("1830"), close)
        self.assertFalse(self.store.observe("SBILIFE", quote, Decimal("1824.9"), SESSION,
                                            now=close, alert_if_already_above=True))

    def test_old_session_outbox_is_not_delivered_the_next_day(self):
        self.observe("1820")
        self.observe("1825", 1)
        self.assertEqual(0, self.store.deliver(Sender(), SESSION + timedelta(days=1)))

    def test_decimal_threshold_spellings_share_a_deduplication_key(self):
        self.observe("1820")
        self.observe("1825", 1)
        later = START + timedelta(seconds=2)
        quote = Quote("NSE_EQ|TEST", Decimal("1830"), later)
        self.assertFalse(self.store.observe("SBILIFE", quote, Decimal("1824.90"), SESSION,
                                            now=later, alert_if_already_above=True))

    def test_levels_reject_stale_dates_and_duplicate_instruments(self):
        path = Path(self.directory.name) / "levels.json"
        entry = {"symbol": "SBILIFE", "instrument_key": "NSE_EQ|TEST", "upper_outer": "1824.9"}
        path.write_text(json.dumps({"session": str(SESSION), "instruments": [entry]}))
        self.assertEqual(Decimal("1824.9"), load_levels(path, SESSION)["NSE_EQ|TEST"]["threshold"])
        with self.assertRaises(ValueError):
            load_levels(path, SESSION + timedelta(days=1))
        path.write_text(json.dumps({"session": str(SESSION), "instruments": [entry, entry]}))
        with self.assertRaises(ValueError):
            load_levels(path, SESSION)


if __name__ == "__main__":
    unittest.main()
