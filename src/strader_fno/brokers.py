from __future__ import annotations

from datetime import date, datetime
from urllib.parse import quote, urlencode

from .http import ApiError, request_json
from .models import Candle, IST, Quote, price


def parse_timestamp(value: object) -> datetime:
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        number = float(value)
        if number > 1e11:
            number /= 1000
        return datetime.fromtimestamp(number, IST)
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Broker timestamp has no timezone")
    return result.astimezone(IST)


class Upstox:
    def __init__(self, access_token: str, transport=request_json):
        if not access_token:
            raise ValueError("Set UPSTOX_ACCESS_TOKEN in your untracked .env file")
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._transport = transport

    def _get(self, path: str) -> dict:
        result = self._transport("https://api.upstox.com" + path,
                                 service="Upstox", headers=self._headers)
        if result.get("status") != "success" or not isinstance(result.get("data"), dict):
            raise ApiError("Upstox")
        return result["data"]

    def quotes(self, instruments: list[str]) -> list[Quote]:
        if not instruments or len(instruments) > 500:
            raise ValueError("Quote batches must contain between 1 and 500 instruments")
        data = self._get("/v2/market-quote/quotes?" + urlencode({"instrument_key": ",".join(instruments)}))
        requested = set(instruments)
        result = []
        for item in data.values():
            if not isinstance(item, dict):
                raise ApiError("Upstox")
            instrument = item.get("instrument_token")
            if instrument not in requested:
                raise ApiError("Upstox")
            # Full quotes expose last_trade_time; using receipt time could make stale quotes look live.
            if not item.get("last_trade_time"):
                continue
            result.append(Quote(instrument, price(item["last_price"]),
                                parse_timestamp(item["last_trade_time"])))
        return result

    def daily_candles(self, instrument: str, start: date, end: date) -> list[Candle]:
        data = self._get(f"/v3/historical-candle/{quote(instrument, safe='')}/days/1/{end}/{start}")
        candles = []
        for row in data.get("candles", []):
            if not isinstance(row, list) or len(row) < 5:
                raise ApiError("Upstox")
            candles.append(Candle(parse_timestamp(row[0]).date(),
                                  *(price(v) for v in row[1:5])))
        return sorted(candles, key=lambda candle: candle.session)
