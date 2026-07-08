"""Polite HTTP client.

Handles the two ways the archives push back on anonymous traffic:
  * HTTP 429 (Too Many Requests)
  * an HTML CAPTCHA / interstitial served with a 200 in place of JSON

Both trigger exponential backoff on top of a constant polite base delay. Every
request URL and every 429 is logged.
"""

from __future__ import annotations

import re
import time
from typing import Optional
from urllib.parse import urlsplit

import requests

import config
from .logging_setup import get_logger

log = get_logger()

# Never write credentials to the logfile: redact known secret query params.
_SECRET_PARAM_RE = re.compile(r"((?:api_key|apikey|key|token)=)[^&]+", re.IGNORECASE)


def _redact(url: str) -> str:
    return _SECRET_PARAM_RE.sub(r"\1***", url or "")


class RateLimitedError(Exception):
    """Raised when a host keeps throttling us past MAX_RETRIES."""


def _looks_like_captcha(resp: requests.Response) -> bool:
    """LOC (and others) sometimes answer with an HTML challenge instead of JSON.
    Detect it so we back off rather than crash on a parse error."""
    ctype = resp.headers.get("Content-Type", "").lower()
    if "text/html" in ctype:
        body = resp.text[:2000].lower()
        return any(k in body for k in ("captcha", "unusual traffic",
                                       "access to www.loc.gov", "request denied"))
    return False


class HttpClient:
    """Thin wrapper over requests.Session with backoff + logging.

    `sleep` is injectable so tests can run without real delays.
    """

    #: substrings identifying a DNS failure — the host is gone, not busy.
    _DNS_FAILURE_MARKERS = ("failed to resolve", "nameresolutionerror",
                            "nodename nor servname", "name or service not known")

    def __init__(self, sleep=time.sleep):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": config.USER_AGENT})
        self._sleep = sleep
        # Hosts that failed DNS this run. A domain that doesn't resolve won't
        # start resolving mid-run, and one dead DPLA contributor host can back
        # hundreds of items — skip it after the first failure.
        self._dead_hosts: set = set()

    def _check_dead_host(self, url: str) -> str:
        host = urlsplit(url).netloc.lower()
        if host in self._dead_hosts:
            raise RateLimitedError(f"host {host} failed DNS earlier this run — skipping {url}")
        return host

    def _is_dns_failure(self, exc: Exception) -> bool:
        text = str(exc).lower()
        return any(m in text for m in self._DNS_FAILURE_MARKERS)

    def _backoff(self, attempt: int) -> float:
        delay = config.BASE_DELAY_SECONDS * (config.BACKOFF_FACTOR ** attempt)
        return min(delay, config.BACKOFF_CAP_SECONDS)

    def _backoff_sleep(self, attempt: int) -> None:
        # Sleep only when another attempt will actually follow — a terminal
        # backoff right before raising is pure wasted wall-clock.
        if attempt + 1 < config.MAX_RETRIES:
            self._sleep(self._backoff(attempt))

    def get_json(self, url: str, params: Optional[dict] = None,
                 headers: Optional[dict] = None) -> dict:
        """GET a URL expecting JSON. Retries on 429 / CAPTCHA / transient error
        with exponential backoff. Raises RateLimitedError if it never succeeds.

        `headers` are per-request extras (e.g. a source-specific API key) — they
        are NOT installed on the shared session, so keys never leak to other
        hosts."""
        host = self._check_dead_host(url)
        last_exc: Optional[Exception] = None
        for attempt in range(config.MAX_RETRIES):
            # Polite constant delay before every request.
            self._sleep(config.BASE_DELAY_SECONDS)
            full = _redact(requests.Request("GET", url, params=params).prepare().url)
            log.info("GET %s", full)
            try:
                resp = self.session.get(url, params=params, headers=headers,
                                        timeout=60)
            except requests.RequestException as exc:
                if self._is_dns_failure(exc):
                    self._dead_hosts.add(host)
                    log.warning("DNS failure for %s — skipping this host for "
                                "the rest of the run", host)
                    raise RateLimitedError(f"host {host} does not resolve") from exc
                last_exc = exc
                log.warning("network error on %s: %s (attempt %d)", full, exc, attempt + 1)
                self._backoff_sleep(attempt)
                continue

            if resp.status_code == 429:
                log.warning("429 Too Many Requests on %s (attempt %d)", full, attempt + 1)
                self._backoff_sleep(attempt)
                continue

            if _looks_like_captcha(resp):
                log.warning("CAPTCHA/HTML interstitial on %s (attempt %d)", full, attempt + 1)
                self._backoff_sleep(attempt)
                continue

            if resp.status_code >= 500:
                log.warning("HTTP %d on %s (attempt %d)", resp.status_code, full, attempt + 1)
                self._backoff_sleep(attempt)
                continue

            resp.raise_for_status()
            try:
                return resp.json()
            except ValueError as exc:
                # Non-JSON with a 200 and no CAPTCHA marker: treat as transient.
                last_exc = exc
                log.warning("non-JSON body on %s (attempt %d)", full, attempt + 1)
                self._backoff_sleep(attempt)
                continue

        raise RateLimitedError(
            f"gave up on {url} after {config.MAX_RETRIES} attempts"
            + (f": {last_exc}" if last_exc else "")
        )

    def download(self, url: str, dest_path: str, max_bytes: Optional[int] = None) -> int:
        """Stream a URL to a file. If max_bytes is set and the content is larger
        (by Content-Length or by streamed size), abort and return -1 without
        keeping a partial file. Returns bytes written, or -1 if skipped."""
        import os

        host = self._check_dead_host(url)
        self._sleep(config.BASE_DELAY_SECONDS)
        log.info("GET (download) %s", _redact(url))
        for attempt in range(config.MAX_RETRIES):
            try:
                with self.session.get(url, stream=True, timeout=120) as resp:
                    if resp.status_code == 429:
                        log.warning("429 on download %s (attempt %d)", url, attempt + 1)
                        self._backoff_sleep(attempt)
                        continue
                    # Permanent client errors (dead link, gone, forbidden) will
                    # never succeed on retry — fail fast instead of burning
                    # MAX_RETRIES rounds of backoff (~45s) per dead URL. Dead
                    # links are routine across thousands of institutional hosts.
                    if 400 <= resp.status_code < 500:
                        raise RateLimitedError(
                            f"HTTP {resp.status_code} (permanent) on {url}")
                    resp.raise_for_status()

                    clen = resp.headers.get("Content-Length")
                    if max_bytes is not None and clen is not None and int(clen) > max_bytes:
                        log.info("skip %s: %s bytes > cap %d", url, clen, max_bytes)
                        return -1

                    written = 0
                    tmp = dest_path + ".part"
                    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
                    with open(tmp, "wb") as fh:
                        for chunk in resp.iter_content(chunk_size=1 << 16):
                            if not chunk:
                                continue
                            written += len(chunk)
                            if max_bytes is not None and written > max_bytes:
                                fh.close()
                                os.remove(tmp)
                                log.info("skip %s: streamed past cap %d", url, max_bytes)
                                return -1
                            fh.write(chunk)
                    os.replace(tmp, dest_path)
                    return written
            except requests.RequestException as exc:
                if self._is_dns_failure(exc):
                    self._dead_hosts.add(host)
                    log.warning("DNS failure for %s — skipping this host for "
                                "the rest of the run", host)
                    raise RateLimitedError(f"host {host} does not resolve") from exc
                log.warning("download error on %s: %s (attempt %d)", url, exc, attempt + 1)
                self._backoff_sleep(attempt)
                continue
        raise RateLimitedError(f"gave up downloading {url}")
