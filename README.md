# strader-fno

Scan the 200 most liquid NSE cash stocks with active equity futures every five
minutes and send Telegram alerts above the upper outer level or below the lower
outer level. Upstox supplies instruments, daily candles and quotes.

## Strategy and selection

The user adopted Midpoint Range after reviewing reference levels. The exact
proprietary JFT formula remains unconfirmed; see docs/formula-investigation.md.

For the previous completed NSE session's high H and low L, let P = (H + L) / 2
and R = H - L:

| Zone | Upper | Lower |
| --- | --- | --- |
| Outer, triggers alerts | P + 0.98R | P - 0.98R |
| Inner | P + 0.76R | P - 0.76R |
| Reference | P + 0.29R | P - 0.29R |

Levels use Decimal arithmetic, are rounded to two decimals and frozen for the
trading day. Today's unfinished daily candle is never an input. Published NSE
holidays and special sessions, including weekend trading, come from Upstox's
calendar. History must include the actual previous trading session.

Select NSE cash EQ underlyings with unexpired equity futures, excluding indices
and duplicate expiries. Rank by mean(closing price * volume) across the last 20
traded sessions and select 200. This is a liquidity proxy, not exact turnover or
market-cap ranking. Stocks with inadequate or stale history are excluded and
any selection shortfall is reported. History, catalogue and levels cache daily.

## Setup

Requires Python 3.11+ with no third-party runtime dependencies. From the repo:

    PYTHONPATH=src python3 -B -m strader_fno setup

Open http://127.0.0.1:8765. Enter an Upstox access token or the API key and secret
of an app with the exact registered redirect http://127.0.0.1:8765/callback.
Enter the Telegram bot token and chat ID; start the bot in Telegram first.
The save button verifies Upstox and sends a labelled Telegram setup test before
.env is saved with permissions
600. Credentials and state are ignored by Git. Setup binds only to localhost
and closes after successful saving. Renew the Upstox token here when it expires.

## Prepare and scan

    PYTHONPATH=src python3 -B -m strader_fno prepare
    PYTHONPATH=src python3 -B -m strader_fno scan

Default mode observes latest traded prices at 09:20:05, 09:25:05, etc. IST, in
batches of 100. BREAKOUT means previous observed price <= upper outer and new
price > upper outer. BREAKDOWN means previous observed price >= lower outer
and new price < lower outer. Equality alone is not a signal. Previous close
seeds each new session, capturing opening gaps and first observed crossings.
Brief moves between observations may be missed.

Optional completed five-minute candle-close mode:

    PYTHONPATH=src python3 -B -m strader_fno scan --mode candle_close

This mode fetches each instrument's intraday bars, rejects unfinished bars,
and includes the final 15:30 close with a one-minute grace period. Latest-price
quotes older than 60 seconds and candle closes older than 120 seconds are
rejected. Status reports fresh quotes, missing instruments and request failures.

Send once per direction per stock per session. SQLite persists observations
and pending delivery across restarts. Scanner events expire after ten minutes;
old-session events are never sent the next day. Telegram delivery is at-least-once:
an ambiguous timeout can cause a retry duplicate. Auth failures stop scanning;
transient failures retry. A state directory permits only one active scanner.

## Today's historical analysis

    PYTHONPATH=src python3 -B -m strader_fno replay-today
    PYTHONPATH=src python3 -B -m strader_fno replay-today --send

Analyse today's completed five-minute candle closes and report the first
crossing of each outer boundary per stock. --send delivers chronological pages
to Telegram labelled HISTORICAL CHECK. These results cannot reconstruct every
latest-price observation or intrabar crossing. Replay does not change live
alert state. Reports record coverage, API failures and sent page counts in
state/replay-YYYY-MM-DD.json. Repeating --send sends the report again. The
intraday replay endpoint supplies the current day's data only.

## Automatic operation on this Mac

    PYTHONPATH=src python3 -B -m strader_fno install-service
    PYTHONPATH=src python3 -B -m strader_fno status
    PYTHONPATH=src python3 -B -m strader_fno stop-service

Installation verifies both connections, then starts the macOS LaunchAgent at
~/Library/LaunchAgents/com.strader-fno.scanner.plist. It starts on login, waits
outside trading hours and prepares data 25 minutes before opening. The first
quote scan is five minutes after opening, plus five seconds for data delivery. The Mac must
remain awake and online with a valid Upstox token. Authentication must be renewed
locally when it expires. Credentials are not stored in the plist. Pass
--mode candle_close to install the alternative observation mode.

Logs: state/scanner.log and state/scanner-error.log. Status: state/status.json.
Snapshots: state/levels-YYYY-MM-DD.json, including selected symbols, instrument
keys, prior-session OHLC, liquidity scores and six levels.

## Research and manual levels

    PYTHONPATH=src python3 -B -m strader_fno research
    PYTHONPATH=src python3 -B -m strader_fno history --instrument 'NSE_EQ|INE123W01016' --session 2026-11-12

Saved reference comparisons: docs/cross-check-2026-11-12.json. Original watch
command supports supplied, dated upper boundaries and polls every five seconds;
it is separate from the automatic scan command.

## Tests

    PYTHONPATH=src python3 -B -m unittest discover -s tests -v

Deterministic tests cover eligibility, ranking, cache recovery, holidays, special
sessions, stale data, strict crossings, both directions, duplicates, expiry,
replay, OAuth state, verified setup and private credential storage. Unit tests
make no authenticated external calls.
