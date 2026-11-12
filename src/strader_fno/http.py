from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ApiError(RuntimeError):
    def __init__(self, service: str, status: int | None = None,
                 retry_after: float | None = None) -> None:
        self.status = status
        self.retry_after = retry_after
        # Never include a URL, request headers, response body, or token in errors.
        super().__init__(f"{service} request failed" + (f" (HTTP {status})" if status else ""))


def request_json(url: str, *, service: str, headers: dict | None = None,
                 payload: dict | None = None, timeout: float = 15) -> dict:
    request_headers = {"Accept": "application/json", "User-Agent": "strader-fno/0.1"}
    request_headers.update(headers or {})
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=request_headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ApiError(service)
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ApiError(service)
            return result
    except HTTPError as exc:
        retry_after = None
        try:
            retry_after = float(exc.headers.get("Retry-After", ""))
        except (ValueError, TypeError):
            pass
        raise ApiError(service, exc.code, retry_after) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise ApiError(service) from None
