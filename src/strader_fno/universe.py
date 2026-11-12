from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .http import ApiError
from .models import Candle, IST, price

CATALOG_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"


@dataclass(frozen=True)
class Stock:
    symbol: str
    instrument: str
    tick_size: Decimal


def download_catalog() -> list[dict]:
    request = Request(CATALOG_URL, headers={"User-Agent": "strader-fno/0.2"})
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read(50 * 1024 * 1024 + 1)
        if len(raw) > 50 * 1024 * 1024:
            raise ApiError("Upstox catalogue")
        if raw.startswith(b"\x1f\x8b"):
            import io
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
                raw = compressed.read(200 * 1024 * 1024 + 1)
        if len(raw) > 200 * 1024 * 1024:
            raise ApiError("Upstox catalogue")
        result = json.loads(raw)
        if not isinstance(result, list):
            raise ApiError("Upstox catalogue")
        return result
    except HTTPError as exc:
        raise ApiError("Upstox catalogue", exc.code) from None
    except (URLError, OSError, ValueError):
        raise ApiError("Upstox catalogue") from None


def eligible_stocks(catalog: list[dict], session: date) -> list[Stock]:
    cash = {row["instrument_key"]: row for row in catalog
            if row.get("segment") == "NSE_EQ" and row.get("instrument_type") == "EQ"}
    underlying = set()
    for row in catalog:
        if (row.get("segment") != "NSE_FO" or row.get("instrument_type") != "FUT"
                or row.get("underlying_type") != "EQUITY"):
            continue
        expiry = datetime.fromtimestamp(int(row["expiry"]) / 1000, IST).date()
        if expiry >= session and row.get("underlying_key") in cash:
            underlying.add(row["underlying_key"])
    stocks = [Stock(cash[key]["trading_symbol"], key,
                    price(cash[key].get("tick_size", 5)) / 100) for key in underlying]
    return sorted(stocks, key=lambda stock: stock.symbol)


def closed_history(candles: list[Candle], session: date) -> list[Candle]:
    by_day = {}
    for candle in candles:
        if candle.session < session:
            if candle.session in by_day and by_day[candle.session] != candle:
                raise ValueError("Conflicting daily candles for one session")
            by_day[candle.session] = candle
    return sorted(by_day.values(), key=lambda candle: candle.session)


def rank_liquidity(stocks: list[Stock], histories: dict[str, list[Candle]],
                   session: date, count: int = 200, window: int = 20) -> list[tuple[Stock, Decimal]]:
    if not 1 <= count <= 500 or window < 1:
        raise ValueError("Count must be 1–500 and ranking window must be positive")
    ranked = []
    for stock in stocks:
        history = closed_history(histories.get(stock.instrument, []), session)
        if not history or history[-1].high <= history[-1].low:
            continue
        traded = [row for row in history if row.volume > 0][-window:]
        if len(traded) < window:
            continue
        # Daily candles contain volume, not actual trade-by-trade turnover.
        # Explicit liquidity proxy: mean(closing price * volume) over 20 sessions.
        liquidity = sum((row.close * row.volume for row in traded), Decimal(0)) / window
        ranked.append((stock, liquidity))
    ranked.sort(key=lambda item: (-item[1], item[0].symbol))
    return ranked[:count]
