from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .brokers import Upstox
from .http import ApiError
from .models import IST
from .scanner import Scanner, scanner_lease, write_json
from .settings import load_settings
from .telegram import Telegram

LOG = logging.getLogger("strader-fno")


def analyse_closes(row, quotes, session, market_hours):
    """Replay completed candle closes; report the first crossing of each boundary."""
    previous = Decimal(row["previous_close"])
    upper, lower = Decimal(row["upper_outer"]), Decimal(row["lower_outer"])
    seen, signals = set(), []
    last_time = None
    opening, closing = market_hours
    for quote in sorted(quotes, key=lambda item: item.timestamp):
        if quote.instrument != row["instrument_key"]:
            raise ValueError("Replay received an unexpected instrument")
        instant = quote.timestamp.astimezone(IST)
        if instant.date() != session or not opening < instant <= closing:
            continue
        if last_time is not None and instant <= last_time:
            continue
        for direction, threshold, crossed in (
                ("BREAKOUT", upper, previous <= upper < quote.last_price),
                ("BREAKDOWN", lower, previous >= lower > quote.last_price)):
            if crossed and direction not in seen:
                seen.add(direction)
                signals.append({"symbol": row["symbol"], "direction": direction,
                                "price": str(quote.last_price), "outer_level": str(threshold),
                                "time": instant.isoformat()})
        previous, last_time = quote.last_price, instant
    return signals


def telegram_pages(report, limit=3500):
    title = (f"HISTORICAL CHECK | {report['session']}\n"
             "Completed 5-minute candle CLOSE crossings | IST\n"
             f"Midpoint Range | levels based on {report['previous_session']}\n"
             "Retrospective results for testing; signals occurred earlier today.\n")
    header = (f"Stocks analysed: {report['analysed']}/{report['selected']}\n"
              f"Breakouts: {report['breakouts']} | Breakdowns: {report['breakdowns']}\n"
              f"No candle data: {len(report['missing'])} | API failures: {len(report['failed'])}\n")
    pages, current = [], title + header
    for signal in report["signals"]:
        instant = datetime.fromisoformat(signal["time"])
        line = (f"\n{instant:%H:%M} {signal['symbol']} {signal['direction']}\n"
                f"Close INR {signal['price']} | Outer INR {signal['outer_level']}\n")
        if len(current) + len(line) > limit:
            pages.append(current)
            current = title
        current += line
    if not report["signals"]:
        current += "\nNo completed candle-close crossing detected."
    pages.append(current)
    return [f"{page}\nPage {i}/{len(pages)}" for i, page in enumerate(pages, 1)]


def run_replay(args):
    settings = load_settings()
    broker = Upstox(settings.get("UPSTOX_ACCESS_TOKEN", ""))
    broker.verify()
    sender = Telegram(settings.get("TELEGRAM_BOT_TOKEN", ""), settings.get("TELEGRAM_CHAT_ID", "")) if args.send else None
    if sender:
        sender.verify()
    directory = Path(args.state_dir)
    started = datetime.now(IST)
    session = started.date()
    with scanner_lease(directory):
        scanner = Scanner(broker, sender, directory, count=args.count, window=args.ranking_window, dry_run=True)
        try:
            snapshot = scanner.prepare(session)
            market_hours = scanner.calendar.window(session)
            signals, missing, failed, analysed = [], [], [], 0
            for index, row in enumerate(snapshot["instruments"], 1):
                bars = None
                for attempt in range(3):
                    try:
                        bars = broker.intraday_five_minute_closes(row["instrument_key"], started)
                        break
                    except ApiError as exc:
                        if exc.status in (401, 403):
                            raise
                        if exc.status in (400, 404) or attempt == 2:
                            failed.append(row["symbol"])
                            LOG.warning("Replay unavailable for %s: %s", row["symbol"], exc)
                            break
                        time.sleep(min(max(float(exc.retry_after or 2 ** attempt), 1), 60))
                if bars:
                    analysed += 1
                    signals.extend(analyse_closes(row, bars, session, market_hours))
                elif bars is not None:
                    missing.append(row["symbol"])
                time.sleep(0.25)
                if index % 25 == 0:
                    LOG.info("Replayed %d/%d stocks", index, snapshot["selected_count"])
            signals.sort(key=lambda item: (item["time"], item["symbol"], item["direction"]))
            report = {"session": str(session), "previous_session": snapshot["previous_session"],
                      "strategy": snapshot["strategy"], "mode": "completed-five-minute-candle-close",
                      "historical": True, "as_of": started.isoformat(), "selected": snapshot["selected_count"],
                      "analysed": analysed, "missing": missing, "failed": failed,
                      "breakouts": sum(s["direction"] == "BREAKOUT" for s in signals),
                      "breakdowns": sum(s["direction"] == "BREAKDOWN" for s in signals),
                      "signals": signals, "telegram_pages_sent": 0}
            path = directory / f"replay-{session}.json"
            write_json(path, report)
            if sender:
                for page in telegram_pages(report):
                    sender.send(page)
                    report["telegram_pages_sent"] += 1
                    write_json(path, report)
                    time.sleep(1.1)
            print(json.dumps({key: value for key, value in report.items() if key != "signals"}, indent=2))
            return report
        finally:
            scanner.close()
