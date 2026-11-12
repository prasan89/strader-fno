from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

from .models import IST, Quote, price


class AlertStore:
    """Single-watcher state and durable, once-per-session breakout outbox.

    Telegram delivery is at-least-once: a timeout after Telegram accepts a
    message can produce a retry. State and pending events survive restarts.
    """

    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS quotes (
                session TEXT NOT NULL, instrument TEXT NOT NULL, threshold TEXT NOT NULL,
                price TEXT NOT NULL, timestamp TEXT NOT NULL,
                PRIMARY KEY (session, instrument, threshold)
            );
            CREATE TABLE IF NOT EXISTS outbox (
                session TEXT NOT NULL, instrument TEXT NOT NULL, threshold TEXT NOT NULL,
                message TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (session, instrument, threshold)
            );
        """)

    def close(self) -> None:
        self.db.close()

    def observe(self, symbol: str, quote: Quote, threshold: Decimal,
                session: date, *, now: datetime, max_age: float = 30,
                alert_if_already_above: bool = False) -> bool:
        threshold = price(threshold)
        if now.tzinfo is None:
            raise ValueError("Watcher clock must have a timezone")
        local_now = now.astimezone(IST)
        observed = quote.timestamp.astimezone(IST)
        if (session != local_now.date() or observed.date() != session
                or local_now.weekday() > 4
                or not time(9, 15) <= local_now.time().replace(tzinfo=None) < time(15, 30)
                or (now - quote.timestamp).total_seconds() > max_age
                or (now - quote.timestamp).total_seconds() < -5):
            return False
        key = (session.isoformat(), quote.instrument, str(threshold.normalize()))
        with self.db:
            previous = self.db.execute(
                "SELECT price,timestamp FROM quotes WHERE session=? AND instrument=? AND threshold=?", key
            ).fetchone()
            if previous and observed <= datetime.fromisoformat(previous[1]):
                return False
            crossed = (previous is not None and Decimal(previous[0]) <= threshold < quote.last_price)
            if previous is None and alert_if_already_above:
                crossed = quote.last_price > threshold
            self.db.execute("INSERT INTO quotes VALUES (?,?,?,?,?) ON CONFLICT(session,instrument,threshold) "
                            "DO UPDATE SET price=excluded.price,timestamp=excluded.timestamp",
                            (*key, str(quote.last_price), observed.isoformat()))
            if not crossed:
                return False
            message = (f"{symbol}: upper JFT reference crossed\n"
                       f"Price: INR {quote.last_price}\nLevel: INR {threshold}\n"
                       f"Time: {observed:%Y-%m-%d %H:%M:%S} IST\n"
                       "Source: supplied daily chart level; formula reconstruction remains unconfirmed.")
            result = self.db.execute("INSERT OR IGNORE INTO outbox(session,instrument,threshold,message) "
                                     "VALUES (?,?,?,?)", (*key, message))
            return result.rowcount == 1

    def deliver(self, sender, session: date) -> int:
        # Never send old-session alerts after a restart the next day.
        rows = self.db.execute("SELECT instrument,threshold,message FROM outbox "
                               "WHERE session=? AND sent=0 ORDER BY rowid", (session.isoformat(),)).fetchall()
        sent = 0
        for instrument, threshold, message in rows:
            sender.send(message)
            with self.db:
                self.db.execute("UPDATE outbox SET sent=1 WHERE session=? AND instrument=? AND threshold=?",
                                (session.isoformat(), instrument, threshold))
            sent += 1
        return sent


def load_levels(path: str | Path, session: date) -> dict[str, dict]:
    raw = json.loads(Path(path).read_text())
    if raw.get("session") != session.isoformat():
        raise ValueError("Levels file is not dated for the current IST session; refresh the chart levels")
    entries = raw.get("instruments")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Levels file must contain a nonempty instruments list")
    result = {}
    for entry in entries:
        key = entry["instrument_key"]
        if key in result or not key.startswith("NSE_EQ|") or not entry.get("symbol"):
            raise ValueError("Invalid or duplicate NSE instrument")
        result[key] = {"symbol": entry["symbol"], "threshold": price(entry["upper_outer"])}
    return result
