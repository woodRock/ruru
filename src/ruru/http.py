"""A polite HTTP client: per-host spacing, retries with backoff, conditional-request caching.

- Spacing: at least `SPACING[host]` seconds between requests to a host (arXiv asks for 3 s).
- Retries: 429, 5xx and transport errors are retried with exponential backoff (1, 2, 4, … s),
  honouring `Retry-After` up to a minute. After the last attempt: `HttpError` (exit 3).
- Caching: responses that carry an ETag or Last-Modified are kept under `.ruru/http/`; the next
  request for the same URL is conditional, and a 304 is served from the cache.
- Politeness: a User-Agent with the contact email, `mailto=` for OpenAlex and Crossref, and the
  optional OpenAlex API key from the user config.

Tests replace `default_transport` (conftest installs one that fails on any request), so nothing
in the test suite ever reaches the network.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from ruru import __version__
from ruru.errors import PARTIAL, USAGE, RuruError

SPACING = {"export.arxiv.org": 3.0, "api.openalex.org": 0.1, "api.crossref.org": 0.05}
MAILTO_HOSTS = ("api.openalex.org", "api.crossref.org")
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER = 60.0

# Replaced in tests. None means httpx's real network transport.
default_transport: httpx.BaseTransport | None = None
default_sleep: Callable[[float], None] = time.sleep


class HttpError(RuruError):
    """The network or an API failed after retries: a partial failure."""
    code = PARTIAL


class NotFound(RuruError):
    """The API answered, and the thing does not exist: an unknown ID."""
    code = USAGE


class Client:
    def __init__(self, *, email: str = "", openalex_key: str | None = None, cache_dir: Path | None = None,
                 transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] | None = None,
                 retries: int = 4, timeout: float = 30.0) -> None:
        self.email = email
        self.openalex_key = openalex_key
        self.cache_dir = cache_dir
        self.retries = retries
        self.sleep = sleep or default_sleep
        self._last: dict[str, float] = {}
        mailto = f"; mailto:{email}" if email else ""
        self._http = httpx.Client(
            transport=transport or default_transport, timeout=timeout, follow_redirects=True,
            headers={"User-Agent": f"ruru/{__version__} (+https://github.com/woodRock/ruru{mailto})"},
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- internals -------------------------------------------------------------------------------

    def _params(self, host: str, params: dict[str, Any] | None) -> dict[str, Any]:
        params = dict(params or {})
        if host in MAILTO_HOSTS and self.email:
            params.setdefault("mailto", self.email)
        if host == "api.openalex.org" and self.openalex_key:
            params.setdefault("api_key", self.openalex_key)
        return params

    def _space(self, host: str) -> None:
        gap = SPACING.get(host, 0.0)
        last = self._last.get(host)
        if gap and last is not None:
            wait = last + gap - time.monotonic()
            if wait > 0:
                self.sleep(wait)
        self._last[host] = time.monotonic()

    def _cache_paths(self, url: str) -> tuple[Path, Path] | None:
        if self.cache_dir is None:
            return None
        h = hashlib.sha256(url.encode()).hexdigest()[:32]
        return self.cache_dir / f"{h}.json", self.cache_dir / f"{h}.body"

    def _cached(self, url: str) -> tuple[dict[str, str], bytes] | None:
        paths = self._cache_paths(url)
        if not paths or not paths[0].exists() or not paths[1].exists():
            return None
        try:
            meta = json.loads(paths[0].read_text())
        except ValueError:
            return None
        return meta, paths[1].read_bytes()

    def _store(self, url: str, response: httpx.Response) -> None:
        paths = self._cache_paths(url)
        validators = {k: response.headers[k] for k in ("etag", "last-modified") if k in response.headers}
        if not paths or not validators:
            return
        paths[0].parent.mkdir(parents=True, exist_ok=True)
        paths[1].write_bytes(response.content)
        paths[0].write_text(json.dumps({"url": url, **validators}))

    @staticmethod
    def _retry_after(response: httpx.Response | None, attempt: int) -> float:
        delay = float(2 ** attempt)
        if response is not None and "retry-after" in response.headers:
            with contextlib.suppress(ValueError):  # an HTTP date: keep the backoff
                delay = float(response.headers["retry-after"])
        return min(delay, MAX_RETRY_AFTER)

    # --- API -------------------------------------------------------------------------------------

    def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        """GET with spacing, retries and caching. Returns any final non-retryable response (404 too)."""
        host = urlsplit(url).hostname or ""
        request = self._http.build_request("GET", url, params=self._params(host, params))
        full_url = str(request.url)
        cached = self._cached(full_url)
        if cached:
            meta, _ = cached
            if "etag" in meta:
                request.headers["If-None-Match"] = meta["etag"]
            if "last-modified" in meta:
                request.headers["If-Modified-Since"] = meta["last-modified"]
        response: httpx.Response | None = None
        for attempt in range(self.retries + 1):
            self._space(host)
            try:
                response = self._http.send(request)
            except httpx.TransportError as e:
                if attempt == self.retries:
                    raise HttpError(f"{host}: {type(e).__name__}: {e}") from None
                self.sleep(self._retry_after(None, attempt))
                continue
            if response.status_code == 304 and cached:
                return httpx.Response(200, content=cached[1], request=request, headers=response.headers)
            if response.status_code in RETRY_STATUS and attempt < self.retries:
                self.sleep(self._retry_after(response, attempt))
                continue
            break
        assert response is not None
        if response.status_code in RETRY_STATUS:
            raise HttpError(f"{host}: HTTP {response.status_code} after {self.retries + 1} attempts")
        if response.status_code == 200:
            self._store(full_url, response)
        return response

    def get_json(self, url: str, params: dict[str, Any] | None = None, *, what: str = "") -> Any:
        response = self.get(url, params)
        host = urlsplit(url).hostname
        if response.status_code == 404:
            raise NotFound(f"{host}: not found: {what or url}")
        if response.status_code != 200:
            raise HttpError(f"{host}: HTTP {response.status_code} for {what or url}")
        try:
            return response.json()
        except ValueError:
            raise HttpError(f"{host}: malformed JSON for {what or url}") from None

    def get_text(self, url: str, params: dict[str, Any] | None = None, *, what: str = "") -> str:
        response = self.get(url, params)
        if response.status_code != 200:
            host = urlsplit(url).hostname
            exc = NotFound if response.status_code == 404 else HttpError
            raise exc(f"{host}: HTTP {response.status_code} for {what or url}")
        return response.text
