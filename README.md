# strader-fno

JFT reference validation and Upstox-to-Telegram breakout notifications.

The complete JFT formula is still unconfirmed. Five user-supplied screenshots
match the previous session's high/low midpoint. A simple range formula matches
all seven SBI Life plot values, but fails the four other stocks. The live watcher
therefore uses supplied, session-dated chart levels, not experimental formulas.

## Cross-check the reference screenshots

Requires Python 3.11 or newer. The project uses the Python standard library.

    PYTHONPATH=src python3 -m strader_fno research

The five fixtures preserve the seven plotted values and the preceding daily
candles, with source URLs. The saved report is in
docs/cross-check-2026-11-12.json. See docs/formula-investigation.md for the findings
and remaining validation work. Decimal arithmetic and each value's displayed
precision are used when comparing predictions.

## Configure live alerts

    cp .env.example .env

Set UPSTOX_ACCESS_TOKEN, TELEGRAM_BOT_TOKEN, and TELEGRAM_CHAT_ID in this file.
Create a Telegram bot with BotFather and use a chat that has started the bot.
Credentials, local state, and environment files are ignored by Git.

Upstox requires a valid API access token; being signed in to its website does
not supply that token to this application. Renew the token when it expires.

The example levels file contains the five screenshots' uppermost red boundaries:

| Symbol | Uppermost level, INR |
| --- | ---: |
| SBILIFE | 1824.9 |
| UPL | 570.73 |
| GODREJIND | 1029.5 |
| DLF | 771.61 |
| NHPC | 81.59 |

Verify the instrument keys against the broker's instrument catalogue. Update
the session date and chart levels every trading day; an old levels file is
rejected. These fixtures are snapshots, not daily automatic calculations.

    PYTHONPATH=src python3 -m strader_fno telegram-test
    PYTHONPATH=src python3 -m strader_fno watch --levels examples/levels-2026-11-12.json

The explicit telegram-test command sends one setup message. The watcher polls
Upstox full quotes every five seconds and checks the broker's last-trade time.
It operates during NSE's regular weekday session, 09:15–15:30 IST. Previous-day
quotes returned on a market holiday are rejected by their timestamps.

A crossing means previous observed price <= level and new price > level.
An initial quote already above the level establishes a baseline. To notify on
that initial condition as well, pass --alert-if-already-above. These are live
quote observations, not five-minute candle-close signals, and polling can miss
a brief crossing between observations.

Alerts are queued durably in SQLite and sent once per instrument/level/session.
Failed deliveries remain pending across restarts. Delivery is at-least-once:
a network timeout after Telegram accepts a message can cause a retry duplicate.
Run one watcher per state file. Pending alerts from an earlier date are not
sent the next day. Authentication failures stop the watcher with a nonzero exit;
transient failures retry, including Telegram rate-limit delays.

## Fetch broker candles for further formula validation

    PYTHONPATH=src python3 -m strader_fno history --instrument 'NSE_EQ|INE123W01016' --session 2026-11-12

The history command fetches closed daily candles before the requested session,
including the preceding trading day across holidays. It does not use the current
unfinished daily candle. Compare these broker prices with the daily inputs of
the TradingView chart before promoting a candidate formula.

## Tests

    PYTHONPATH=src python3 -m unittest discover -s tests -v

Tests cover reference mismatches, timestamp freshness, strict crossings,
duplicate suppression, restart recovery, failed Telegram delivery, stale levels,
market-session boundaries, and broker response parsing. No broker credentials
or external requests are used by tests. Live authenticated broker access and
Telegram delivery need a separate setup check.
