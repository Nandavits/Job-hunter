"""Shared HTTP session and helpers for collectors."""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger("jobhunter")

USER_AGENT = "job-hunter/0.1 (personal job search; reads public job-board APIs)"


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


def get(session, url: str, *, params=None, headers=None, retries: int = 2, timeout: int = 20):
    """GET with small retry/backoff. Returns the response or None on 404 / repeated failure."""
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            log.warning("Request failed (%s): %s", url, exc)
        else:
            if resp.status_code == 404:
                return None
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                log.warning("HTTP %s from %s: %s", resp.status_code, url, resp.text[:200])
                return None
            if resp.status_code == 429 or resp.status_code >= 500:
                log.warning("HTTP %s from %s, retrying", resp.status_code, url)
            else:
                resp.raise_for_status()
                return resp
        time.sleep(1.5 * (attempt + 1))
    return None
