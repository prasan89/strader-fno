from __future__ import annotations

import json
import logging
import time
from datetime import datetime, time as day_time, timedelta
from pathlib import Path

from .brokers import Upstox
from .http import ApiError
from .models import IST
from .scanner import Scanner, market_window, next_scan, scanner_lease, write_json
from .settings import load_settings
from .telegram import Telegram

LOG = logging.getLogger("strader-fno")


def next_wakeup(now, target, calendar):
    """Wake before opening to prepare history, and at midnight to refresh calendar."""
    midnight = datetime.combine(now.date() + timedelta(days=1), day_time(0), IST)
    result = min(target, midnight)
    if calendar:
        window = calendar.window(target.date())
        if window:
            preparation = window[0] - timedelta(minutes=25)
            if now < preparation:
                result = min(result, preparation)
    return result


class ConsoleSender:
    def send(self, message: str) -> None:
        LOG.info("DRY RUN — %s", message)


def run_scanner(args, *, prepare_only=False):
    directory = Path(args.state_dir)
    try:
        return _run_scanner(args, prepare_only=prepare_only)
    except (ApiError, ValueError, OSError) as exc:
        # Preserve the incumbent scanner's status if this is a duplicate launch.
        if str(exc) != "A scanner is already running for this state directory":
            write_json(directory / "status.json", {"status": "error", "error": str(exc),
                                                   "updated_at": datetime.now(IST).isoformat()})
        raise


def _run_scanner(args, *, prepare_only=False):
    settings = load_settings()
    broker = Upstox(settings.get("UPSTOX_ACCESS_TOKEN", ""))
    broker.verify()
    sender = ConsoleSender() if args.dry_run or prepare_only else Telegram(
        settings.get("TELEGRAM_BOT_TOKEN", ""), settings.get("TELEGRAM_CHAT_ID", ""))
    if not args.dry_run and not prepare_only:
        sender.verify()
    directory = Path(args.state_dir)
    with scanner_lease(directory):
        scanner = Scanner(broker, sender, directory, count=args.count, window=args.ranking_window,
                          mode=args.mode, dry_run=args.dry_run or prepare_only)
        try:
            today = datetime.now(IST).date()
            if (directory / f"levels-{today}.json").exists():
                scanner.prepare(today)
            if prepare_only:
                result = scanner.prepare(datetime.now(IST).date())
                print(json.dumps({key: value for key, value in result.items() if key != "instruments"}, indent=2))
                return
            if args.once:
                print(json.dumps(scanner.scan_once(), indent=2))
                return
            LOG.info("Scanner active: top %d F&O stocks, %s, every five minutes", args.count, args.mode)
            while True:
                now = datetime.now(IST)
                succeeded = True
                try:
                    calendar = scanner.calendar_for(now.date())
                    window = calendar.window(now.date())
                    closing_bar = args.mode == "candle_close"
                    if market_window(now, calendar, closing_bar=closing_bar):
                        scanner.scan_once(now)
                    elif (window and window[0] - timedelta(minutes=25) <= now < window[0]
                          and (not scanner.snapshot or scanner.snapshot["session"] != str(now.date()))):
                        scanner.prepare(now.date())
                except ApiError as exc:
                    succeeded = False
                    write_json(directory / "status.json", {"status": "error", "error": str(exc),
                                                           "updated_at": datetime.now(IST).isoformat()})
                    if exc.status in (400, 401, 403, 404):
                        raise
                    LOG.warning("%s; retry at the next scheduled scan", exc)
                target = next_scan(datetime.now(IST), calendar=scanner.calendar, closing_bar=args.mode == "candle_close")
                if succeeded and not market_window(datetime.now(IST), scanner.calendar, closing_bar=args.mode == "candle_close"):
                    write_json(directory / "status.json", {"status": "waiting_for_market", "next_scan": target.isoformat(),
                                                           "updated_at": datetime.now(IST).isoformat(),
                                                           "selected_count": scanner.snapshot.get("selected_count") if scanner.snapshot else None})
                wakeup = next_wakeup(datetime.now(IST), target, scanner.calendar)
                if not succeeded:
                    wakeup = min(wakeup, datetime.now(IST) + timedelta(minutes=5))
                while datetime.now(IST) < wakeup:
                    time.sleep(min(60, max(0.1, (wakeup - datetime.now(IST)).total_seconds())))
        finally:
            scanner.close()
