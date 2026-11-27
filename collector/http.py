"""Small HTTP helpers shared by every source. All network access goes through here,
so tests can replace `session` with a fake."""
import time

import requests

USER_AGENT = "jobs-feed/1.0 (weekly job collector; +https://github.com/dmehta94)"
TIMEOUT = 25


class FetchError(Exception):
    pass


session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})


def _request(method, url, *, retries=1, **kwargs):
    kwargs.setdefault("timeout", TIMEOUT)
    last = None
    for attempt in range(retries + 1):
        limited = None
        try:
            resp = session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            last = FetchError(f"{type(exc).__name__}: {exc}")
        else:
            if resp.status_code == 404:
                raise FetchError("404 Not Found")
            if resp.status_code < 400:
                return resp
            last = FetchError(f"HTTP {resp.status_code}")
            if resp.status_code == 429:
                limited = resp
            elif resp.status_code < 500:
                break
        if attempt < retries:
            time.sleep(_backoff(limited, attempt))
    raise last


def _backoff(resp, attempt):
    """Seconds to wait before retrying: the server's Retry-After for a 429 (capped at 60), else
    2s for other errors and 5s, 15s, ... for a 429 with no Retry-After."""
    if resp is None:
        return 2
    try:
        return min(60, max(1, int(getattr(resp, "headers", {}).get("Retry-After", ""))))
    except ValueError:
        return 5 * 3 ** attempt


def get_json(url, **kwargs):
    resp = _request("GET", url, **kwargs)
    try:
        return resp.json()
    except ValueError as exc:
        raise FetchError("response was not JSON") from exc


def post_json(url, payload, **kwargs):
    resp = _request("POST", url, json=payload, **kwargs)
    try:
        return resp.json()
    except ValueError as exc:
        raise FetchError("response was not JSON") from exc


def get_text(url, **kwargs):
    return _request("GET", url, **kwargs).text
