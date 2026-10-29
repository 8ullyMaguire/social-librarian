"""The smallest HTTP client that does the job: urllib, JSON in and out, one retry on 429/5xx."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

UA = "social-librarian/0.1 (+https://polarisocial.xyz)"


class HttpError(RuntimeError):
    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"{status} {url}: {body[:300]}")
        self.status = status
        self.body = body
        self.url = url


def request_json(
    method: str,
    url: str,
    data=None,
    headers: dict | None = None,
    form: bool = False,
    timeout: int = 30,
    retries: int = 2,
) -> Any:
    body = None
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)

    delay = 1.0
    last: HttpError | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            last = HttpError(e.code, raw, url)
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise last
        except urllib.error.URLError as e:
            last = HttpError(0, str(e), url)
            if attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise last
    raise last or HttpError(0, "no attempt", url)
