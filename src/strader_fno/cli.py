from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from .alerts import AlertStore, load_levels
from .brokers import Upstox
from .http import ApiError
from .models import IST
from .research import compare_references
from .telegram import Telegram

LOG = logging.getLogger("strader-fno")


def load_env(path: Path = Path(".env")) -> None:
    """Load simple KEY=value settings without overriding the process environment.

    No shell expansion, command substitution, or printing secret values.
    """
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key.strip() not in {
            "UPSTOX_ACCESS_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"
        }:
            raise ValueError("Only broker/Telegram KEY=value settings are supported in .env")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def watch(args) -> None:
    broker = Upstox(os.environ.get("UPSTOX_ACCESS_TOKEN", ""))
    telegram = Telegram(os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", ""))
    if args.poll_seconds < 2 or args.max_quote_age <= 0:
        raise ValueError("Poll interval must be at least 2 seconds; quote age must be positive")
    store = AlertStore(args.state)
    LOG.info("Watching supplied uppermost chart levels; JFT formula reconstruction is unconfirmed")
    try:
        while True:
            now = datetime.now(IST)
            session = now.date()
            try:
                levels = load_levels(args.levels, session)
            except ValueError as exc:
                if args.once:
                    raise
                LOG.warning("%s", exc)
                time.sleep(30)
                continue
            if now.weekday() > 4 or not (9 * 60 + 15 <= now.hour * 60 + now.minute < 15 * 60 + 30):
                if args.once:
                    raise ValueError("NSE regular session is closed; no quote or alert request sent")
                time.sleep(30)
                continue
            try:
                quotes = broker.quotes(list(levels))
                if len(quotes) != len(levels):
                    LOG.warning("Only %d/%d instruments supplied a timestamped quote", len(quotes), len(levels))
                for quote in quotes:
                    item = levels[quote.instrument]
                    if store.observe(item["symbol"], quote, item["threshold"], session,
                                     now=datetime.now(IST), max_age=args.max_quote_age,
                                     alert_if_already_above=args.alert_if_already_above):
                        LOG.info("Breakout queued for %s", item["symbol"])
                delivered = store.deliver(telegram, session)
                if delivered:
                    LOG.info("Delivered %d Telegram alert(s)", delivered)
            except ApiError as exc:
                if exc.status in (400, 401, 403, 404) or args.once:
                    raise
                LOG.warning("%s; pending alerts remain durable", exc)
                time.sleep(min(max(float(exc.retry_after or 5), 2), 300))
            if args.once:
                return
            time.sleep(args.poll_seconds)
    finally:
        store.close()


def history(args) -> None:
    broker = Upstox(os.environ.get("UPSTOX_ACCESS_TOKEN", ""))
    session = date.fromisoformat(args.session)
    # Never include the current session's unfinished daily candle.
    candles = broker.daily_candles(args.instrument, session - timedelta(days=45), session - timedelta(days=1))
    if not candles:
        raise ValueError("No previous trading-session candles returned")
    result = [{"date": str(c.session), **{key: str(getattr(c, key)) for key in ("open", "high", "low", "close")}}
              for c in candles]
    print(json.dumps(result, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="JFT formula cross-checking and Upstox breakout notifications")
    sub = parser.add_subparsers(dest="command", required=True)
    research = sub.add_parser("research", help="Compare candidate formulas with all seven screenshot values")
    research.add_argument("--references", default="examples/references-2026-11-12.json")
    live = sub.add_parser("watch", help="Watch supplied, session-dated levels and send uppermost-level Telegram alerts")
    live.add_argument("--levels", required=True)
    live.add_argument("--state", default="state/alerts.sqlite3")
    live.add_argument("--poll-seconds", type=float, default=5)
    live.add_argument("--max-quote-age", type=float, default=30)
    live.add_argument("--alert-if-already-above", action="store_true")
    live.add_argument("--once", action="store_true")
    hist = sub.add_parser("history", help="Fetch prior closed daily candles from Upstox for formula validation")
    hist.add_argument("--instrument", required=True)
    hist.add_argument("--session", default=datetime.now(IST).date().isoformat())
    sub.add_parser("telegram-test", help="Explicitly send one setup-test notification to the configured chat")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        load_env()
        if args.command == "research":
            references = json.loads(Path(args.references).read_text())
            print(json.dumps(compare_references(references), indent=2))
        elif args.command == "watch":
            watch(args)
        elif args.command == "history":
            history(args)
        elif args.command == "telegram-test":
            Telegram(os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")).send(
                "strader-fno: Telegram setup test. Live alerts require current-session chart levels and an active watcher."
            )
        return 0
    except KeyboardInterrupt:
        LOG.info("Watcher stopped")
        return 0
    except (ValueError, OSError, ApiError) as exc:
        LOG.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
