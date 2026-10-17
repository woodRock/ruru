"""Serve recorded responses (tests/fixtures/http/) through an httpx transport; fail on anything else."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

FIXTURES = Path(__file__).parent / "fixtures" / "http"


@dataclass
class Route:
    pattern: str
    status: int
    body: bytes
    headers: dict[str, str]
    times: int | None  # None: unlimited
    exc: Exception | None = None


@dataclass
class Router(httpx.BaseTransport):
    routes: list[Route] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)

    def add(self, pattern: str, fixture: str | None = None, *, status: int = 200, body: bytes | str = b"",
            headers: dict[str, str] | None = None, times: int | None = None,
            exc: Exception | None = None) -> Router:
        """Answer requests whose URL matches `pattern` (re.search) with a fixture file or `body`."""
        if fixture:
            body = (FIXTURES / fixture).read_bytes()
        if isinstance(body, str):
            body = body.encode()
        self.routes.append(Route(pattern, status, body, headers or {}, times, exc))
        return self

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        for route in self.routes:
            if route.times == 0 or not re.search(route.pattern, url):
                continue
            if route.times is not None:
                route.times -= 1
            if route.exc:
                raise route.exc
            return httpx.Response(route.status, content=route.body, headers=route.headers, request=request)
        raise AssertionError(f"unexpected HTTP request in a test: {url}")

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


def standard(router: Router) -> Router:
    """Every recorded fixture under the URL it was recorded from."""
    return (
        router
        .add(r"export\.arxiv\.org/api/query\?id_list=2103\.13807", "arxiv_2103.13807.xml")
        .add(r"export\.arxiv\.org/api/query\?id_list=1706\.03762", "arxiv_1706.03762.xml")
        .add(r"export\.arxiv\.org/api/query\?id_list=2699\.99999", "arxiv_missing.xml")
        .add(r"api\.crossref\.org/works/10\.1186/s13321-021-00544-w", "crossref_s13321.json")
        .add(r"api\.crossref\.org/works/10\.1038/nature14539", "crossref_nature14539.json")
        .add(r"api\.crossref\.org/works/10\.9999/", "crossref_missing.json", status=404)
        .add(r"api\.openalex\.org/works/doi:10\.1186/s13321-021-00544-w", "openalex_doi_s13321.json")
        .add(r"api\.openalex\.org/works/doi:10\.1038/nature14539", "openalex_doi_nature14539.json")
        .add(r"api\.openalex\.org/works/doi:10\.9999/", "openalex_missing.json", status=404)
        .add(r"api\.openalex\.org/works\?filter=title\.search%3AAttention\+Is\+All\+You\+Need%2Ctype%3Aarticle",
             "openalex_title_attention_article.json")
        .add(r"api\.openalex\.org/works\?filter=title\.search%3AAttention\+Is\+All\+You\+Need",
             "openalex_title_attention.json")
        .add(r"api\.openalex\.org/works\?filter=title\.search%3AAutomated\+fragment\+identification",
             "openalex_title_fragment.json")
    )
