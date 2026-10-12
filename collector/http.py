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
            if resp.status_code < 500 and resp.status_code != 429:
                break
        if attempt < retries:
            time.sleep(2)
    raise last


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
