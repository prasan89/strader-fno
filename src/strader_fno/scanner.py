from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import time
from datetime import date, datetime, time as day_time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from .alerts import AlertStore
from .calendar import TradingCalendar, within_window
from .formulas import range_candidate
from .http import ApiError
from .models import Candle, IST
from .universe import closed_history, download_catalog, eligible_stocks, rank_liquidity

LOG = logging.getLogger("strader-fno")


def market_window(now: datetime, calendar: TradingCalendar | None = None, *, closing_bar=False) -> bool:
    return within_window(now, calendar or TradingCalendar(), closing_bar=closing_bar)


def next_scan(now: datetime, interval: int = 300, lag: int = 5,
              calendar: TradingCalendar | None = None, *, closing_bar=False) -> datetime:
    """Start five minutes after NSE open, allowing five seconds for bar delivery."""
    if interval <= 0:
        raise ValueError("Scan interval must be positive")
    now = now.astimezone(IST)
    calendar = calendar or TradingCalendar()
    day = now.date()
    while True:
        window = calendar.window(day)
        if window:
            opening, closing = window
            first = opening + timedelta(seconds=interval + lag)
            if now < first:
                return first
            elapsed = (now - opening).total_seconds() - lag
            slot = int(elapsed // interval) + 1
            target = opening + timedelta(seconds=slot * interval + lag)
            if target < closing or (closing_bar and target == closing + timedelta(seconds=lag)):
                return target
        day += timedelta(days=1)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


@contextlib.contextmanager
def scanner_lease(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / "scanner.lock", "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("A scanner is already running for this state directory") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Scanner:
    def __init__(self, broker, sender, directory: Path, *, count=200, window=20,
                 mode="ltp", dry_run=False, catalog_provider=download_catalog,
                 sleeper=time.sleep, clock=lambda: datetime.now(IST)):
        self.broker = broker
        self.sender = sender
        self.directory = Path(directory)
        self.count, self.window, self.mode = count, window, mode
        if not 1 <= count <= 500 or window < 1 or mode not in ("ltp", "candle_close"):
            raise ValueError("Invalid scanner count, ranking window, or signal mode")
        self.catalog_provider = catalog_provider
        self.sleeper, self.clock = sleeper, clock
        self.store = AlertStore(self.directory / ("dry-run-alerts.sqlite3" if dry_run else "alerts.sqlite3"))
        self.snapshot = None
        self.calendar = None
        self.calendar_date = None

    def calendar_for(self, session: date) -> TradingCalendar:
        if self.calendar_date == session:
            return self.calendar
        path = self.directory / f"calendar-{session}.json"
        if path.exists():
            entries = json.loads(path.read_text())["entries"]
        else:
            entries = self.broker.market_calendar()
            # The all-holidays endpoint publishes the current year. Resolve an
            # older-year boundary explicitly rather than assuming it was open.
            prior = session - timedelta(days=1)
            for _ in range(14):
                if prior.year == session.year:
                    break
                if prior.weekday() < 5:
                    entries.extend(self.broker.market_calendar(prior))
                    if TradingCalendar(entries).window(prior):
                        break
                prior -= timedelta(days=1)
            write_json(path, {"entries": entries})
        self.calendar, self.calendar_date = TradingCalendar(entries), session
        return self.calendar

    def close(self):
        self.store.close()

    def _history(self, stock, session, cache, expected_session):
        if stock.instrument in cache:
            cached = [Candle(date.fromisoformat(row["date"]),
                           *(Decimal(row[key]) for key in ("open", "high", "low", "close")),
                           volume=row["volume"]) for row in cache[stock.instrument]]
            if cached and max(row.session for row in cached) == expected_session:
                return cached
        for attempt in range(3):
            try:
                rows = closed_history(self.broker.daily_candles(stock.instrument,
                                      session - timedelta(days=90), session - timedelta(days=1)), session)
                break
            except ApiError as exc:
                if exc.status in (400, 401, 403, 404) or attempt == 2:
                    raise
                self.sleeper(min(max(float(exc.retry_after or 2 ** attempt), 1), 300))
        cache[stock.instrument] = [{"date": str(row.session), "volume": row.volume,
                                   **{key: str(getattr(row, key)) for key in ("open", "high", "low", "close")}}
                                  for row in rows]
        self.sleeper(0.25)  # Four historical requests/second, only during daily preparation.
        return rows

    def prepare(self, session: date) -> dict:
        calendar = self.calendar_for(session)
        if not calendar.window(session):
            raise ValueError("NSE is closed for the requested session")
        expected_session = calendar.previous_session(session)
        snapshot_path = self.directory / f"levels-{session}.json"
        if snapshot_path.exists():
            result = json.loads(snapshot_path.read_text())
            if (result.get("session") == str(session) and result.get("strategy") == "midpoint-range-v1"
                    and result.get("previous_session") == str(expected_session)
                    and result.get("requested_count") == self.count and result.get("ranking_window") == self.window):
                self.snapshot = result
                return result
        catalog_path = self.directory / f"catalog-{session}.json"
        if catalog_path.exists():
            catalog = json.loads(catalog_path.read_text())["instruments"]
        else:
            catalog = self.catalog_provider()
            write_json(catalog_path, {"instruments": catalog})
        stocks = eligible_stocks(catalog, session)
        if not stocks:
            raise ValueError("Catalogue contains no current NSE equity F&O underlyings")
        LOG.info("Preparing closed-session history for %d F&O stocks", len(stocks))
        cache_path = self.directory / f"history-{session}.json"
        cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
        histories, exclusions = {}, []
        for index, stock in enumerate(stocks, 1):
            try:
                histories[stock.instrument] = self._history(stock, session, cache, expected_session)
            except ApiError as exc:
                if exc.status in (401, 403):
                    write_json(cache_path, cache)
                    raise
                LOG.warning("History unavailable for %s: %s", stock.symbol, exc)
                exclusions.append(stock.symbol)
            if index % 25 == 0:
                write_json(cache_path, cache)
                LOG.info("Prepared %d/%d histories", index, len(stocks))
        write_json(cache_path, cache)
        available = {key: closed_history(rows, session) for key, rows in histories.items()}
        available = {key: rows for key, rows in available.items() if rows}
        if not available:
            raise ValueError("No previous completed daily candles returned")
        latest_session = expected_session
        fresh_stocks = []
        for stock in stocks:
            rows = available.get(stock.instrument, [])
            if not rows or rows[-1].session != latest_session or rows[-1].volume <= 0:
                exclusions.append(stock.symbol)
                continue
            zones = range_candidate(rows[-1])
            if zones.lower_outer <= 0 or zones.lower_outer >= zones.upper_outer:
                exclusions.append(stock.symbol)
                continue
            fresh_stocks.append(stock)
        ranked = rank_liquidity(fresh_stocks, histories, session, self.count, self.window)
        if not ranked:
            raise ValueError("No stocks have adequate closed daily candles and volume for ranking")
        entries = []
        for stock, liquidity in ranked:
            previous = closed_history(histories[stock.instrument], session)[-1]
            if previous.session != latest_session:
                exclusions.append(stock.symbol)
                continue
            zones = range_candidate(previous)
            values = {key: str(getattr(zones, key).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                      for key in ("upper_outer", "lower_outer", "upper_inner", "lower_inner",
                                  "upper_reference", "lower_reference")}
            if Decimal(values["lower_outer"]) <= 0 or Decimal(values["lower_outer"]) >= Decimal(values["upper_outer"]):
                exclusions.append(stock.symbol)
                continue
            entries.append({"symbol": stock.symbol, "instrument_key": stock.instrument,
                            "previous_session": str(previous.session), "previous_close": str(previous.close),
                            "previous_high": str(previous.high), "previous_low": str(previous.low),
                            "liquidity_score": str(liquidity), **values})
        if not entries:
            raise ValueError("No stocks passed level and prior-session freshness validation")
        result = {"session": str(session), "previous_session": str(expected_session),
                  "strategy": "midpoint-range-v1", "requested_count": self.count,
                  "selected_count": len(entries), "eligible_count": len(stocks), "ranking_window": self.window,
                  "ranking": "mean(previous closed-session close * volume)",
                  "exclusions": exclusions, "instruments": entries}
        write_json(snapshot_path, result)
        self.snapshot = result
        if len(entries) < self.count:
            LOG.warning("Selected %d/%d stocks; insufficient eligible fresh history", len(entries), self.count)
        LOG.info("Daily levels ready for %d stocks, based on session %s", len(entries), latest_session)
        return result

    def scan_once(self, now: datetime | None = None) -> dict:
        now = now or self.clock()
        session = now.astimezone(IST).date()
        calendar = self.calendar_for(session)
        closing_bar = self.mode == "candle_close"
        if not market_window(now, calendar, closing_bar=closing_bar):
            return {"status": "market_closed", "next_scan": next_scan(now, calendar=calendar, closing_bar=closing_bar).isoformat()}
        if now < calendar.window(session)[0] + timedelta(minutes=5, seconds=5):
            return {"status": "waiting_for_first_scan", "next_scan": next_scan(now, calendar=calendar, closing_bar=closing_bar).isoformat()}
        if not self.snapshot or self.snapshot["session"] != str(session):
            self.prepare(session)
        levels = {row["instrument_key"]: row for row in self.snapshot["instruments"]}
        queued = received = fresh = 0
        keys = list(levels)
        failures = []
        def quote_batches():
            groups = [keys[i:i + 100] for i in range(0, len(keys), 100)] if self.mode == "ltp" else [[key] for key in keys]
            for group in groups:
                try:
                    if self.mode == "ltp":
                        yield self.broker.quotes(group)
                    else:
                        bar = self.broker.completed_five_minute_close(group[0], self.clock())
                        yield [bar] if bar else []
                        self.sleeper(0.25)
                except ApiError as exc:
                    if exc.status in (401, 403):
                        raise
                    failures.extend(group)
                    LOG.warning("Quote request failed for %d instruments: %s", len(group), exc)
        for batch in quote_batches():
            for quote in batch:
                if quote.instrument not in levels:
                    raise ValueError("Broker returned an instrument outside the selected universe")
                received += 1
                observed_now = self.clock()
                max_age = 60 if self.mode == "ltp" else 120
                age = (observed_now - quote.timestamp).total_seconds()
                if quote.timestamp.astimezone(IST).date() != session or not -5 <= age <= max_age:
                    continue
                fresh += 1
                row = levels[quote.instrument]
                prior = datetime.combine(date.fromisoformat(row["previous_session"]), day_time(15, 30), IST)
                for direction, key in (("up", "upper_outer"), ("down", "lower_outer")):
                    threshold = Decimal(row[key])
                    self.store.seed(quote.instrument, threshold, session, Decimal(row["previous_close"]), prior)
                    queued += self.store.observe(row["symbol"], quote, threshold, session,
                                                now=observed_now, max_age=max_age,
                                                direction=direction, delivery_ttl=600,
                                                market_hours=calendar.window(session), closing_bar=closing_bar,
                                                source=f"Midpoint Range | {self.mode} | prior session {row['previous_session']}")
        delivered = self.store.deliver(self.sender, session, now=self.clock())
        result = {"status": "partial" if failures or fresh != len(keys) else "scanned",
                  "session": str(session), "selected": len(keys), "failed_instruments": failures,
                  "quotes_received": received, "fresh_quotes": fresh, "alerts_queued": queued, "alerts_delivered": delivered}
        write_json(self.directory / "status.json", {**result, "last_scan": self.clock().isoformat()})
        LOG.info("Scan complete: %d/%d quotes; %d queued; %d delivered", received, len(keys), queued, delivered)
        return result
