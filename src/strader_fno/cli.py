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
    from .settings import load_settings
    for key, value in load_settings(path).items():
        os.environ.setdefault(key, value)


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
    setup = sub.add_parser("setup", help="Configure Upstox and Telegram in a private local browser form")
    setup.add_argument("--port", type=int, default=8765)
    for command in ("scan", "prepare"):
        scan = sub.add_parser(command, help="Automatically select F&O stocks and generate daily levels")
        scan.add_argument("--count", type=int, default=200)
        scan.add_argument("--ranking-window", type=int, default=20)
        scan.add_argument("--mode", choices=("ltp", "candle_close"), default="ltp")
        scan.add_argument("--state-dir", default="state")
        scan.add_argument("--dry-run", action="store_true")
        scan.add_argument("--once", action="store_true")
    install = sub.add_parser("install-service", help="Run the scanner automatically using a macOS launch agent")
    install.add_argument("--count", type=int, default=200)
    install.add_argument("--mode", choices=("ltp", "candle_close"), default="ltp")
    sub.add_parser("stop-service", help="Stop the macOS scanner launch agent")
    status = sub.add_parser("status", help="Show the latest scanner state without exposing credentials")
    status.add_argument("--state-dir", default="state")
    replay = sub.add_parser("replay-today", help="Analyse today's completed five-minute candles against daily outer levels")
    replay.add_argument("--send", action="store_true", help="Send the historical analysis to the configured Telegram chat")
    replay.add_argument("--count", type=int, default=200)
    replay.add_argument("--ranking-window", type=int, default=20)
    replay.add_argument("--state-dir", default="state")
    export = sub.add_parser("export-levels", help="Export cached daily stock levels to an Excel cross-check workbook")
    export.add_argument("--session", default=datetime.now(IST).date().isoformat())
    export.add_argument("--state-dir", default="state")
    export.add_argument("--output")
    export.add_argument("--overwrite", action="store_true", help="Replace an existing workbook, including any comparison edits")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.command in ("watch", "history", "telegram-test"):
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
                "strader-fno: Telegram setup test. The automatic scanner checks daily upper and lower outer levels every five minutes during NSE trading hours."
            )
        elif args.command == "setup":
            from .setup import run_setup
            run_setup(port=args.port)
        elif args.command in ("scan", "prepare"):
            from .runner import run_scanner
            run_scanner(args, prepare_only=args.command == "prepare")
        elif args.command == "install-service":
            from .service import install_service
            print("Scanner launch agent installed:", install_service(Path.cwd(), count=args.count, mode=args.mode))
        elif args.command == "stop-service":
            from .service import stop_service
            stop_service()
        elif args.command == "replay-today":
            from .replay import run_replay
            run_replay(args)
        elif args.command == "export-levels":
            from .excel import export_levels
            session = date.fromisoformat(args.session)
            directory = Path(args.state_dir)
            snapshot = json.loads((directory / f"levels-{session}.json").read_text())
            if snapshot.get("session") != str(session):
                raise ValueError("Cached levels do not match the requested trading session")
            target = Path(args.output) if args.output else directory / f"levels-{session}.xlsx"
            print("Excel cross-check workbook:", export_levels(snapshot, target, overwrite=args.overwrite))
        elif args.command == "status":
            path = Path(args.state_dir) / "status.json"
            print(path.read_text() if path.exists() else '{"status":"not_started"}')
        return 0
    except KeyboardInterrupt:
        LOG.info("Watcher stopped")
        return 0
    except (ValueError, OSError, ApiError) as exc:
        LOG.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
