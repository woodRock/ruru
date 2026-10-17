import httpx
import pytest

from httpfix import Router
from ruru import http
from ruru.http import Client, HttpError, NotFound


def client(router, **kw):
    return Client(transport=router, **kw)


def test_polite_params_and_user_agent():
    router = Router().add("openalex", body="{}").add("crossref", body="{}").add("arxiv", body="")
    c = client(router, email="me@example.org", openalex_key="KEY")
    c.get_json("https://api.openalex.org/works/W1")
    c.get_json("https://api.crossref.org/works/10.1/x")
    c.get_text("https://export.arxiv.org/api/query", {"id_list": "1"})
    oa, cr, ax = router.requests
    assert oa.url.params["mailto"] == "me@example.org" and oa.url.params["api_key"] == "KEY"
    assert cr.url.params["mailto"] == "me@example.org" and "api_key" not in cr.url.params
    assert "mailto" not in ax.url.params
    assert "mailto:me@example.org" in oa.headers["user-agent"]


def test_arxiv_spacing(sleeps):
    router = Router().add("arxiv", body="")
    c = client(router)
    c.get("https://export.arxiv.org/api/query")
    c.get("https://export.arxiv.org/api/query")
    assert len(sleeps) == 1 and 2.5 < sleeps[0] <= 3.0


def test_retry_429_honours_retry_after(sleeps):
    router = (Router().add("x", status=429, headers={"Retry-After": "7"}, times=1)
              .add("x", status=503, times=1).add("x", body='{"ok": 1}'))
    assert client(router).get_json("https://example.org/x") == {"ok": 1}
    assert sleeps == [7.0, 2.0]  # Retry-After, then exponential backoff (attempt 1)


def test_retry_after_is_capped_and_http_dates_ignored(sleeps):
    router = (Router().add("x", status=429, headers={"Retry-After": "3600"}, times=1)
              .add("x", status=429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, times=1)
              .add("x", body="{}"))
    client(router).get_json("https://example.org/x")
    assert sleeps == [60.0, 2.0]


def test_gives_up_after_retries(sleeps):
    router = Router().add("x", status=503)
    with pytest.raises(HttpError, match="HTTP 503 after 3 attempts"):
        client(router, retries=2).get("https://example.org/x")
    assert len(router.requests) == 3 and sleeps == [1.0, 2.0]


def test_transport_errors_retry_then_fail(sleeps):
    router = Router().add("x", exc=httpx.ConnectError("refused"), times=1).add("x", body="{}")
    assert client(router).get_json("https://example.org/x") == {}
    router = Router().add("x", exc=httpx.ConnectError("refused"))
    with pytest.raises(HttpError, match="ConnectError"):
        client(router, retries=1).get("https://example.org/x")


def test_status_errors_and_malformed_json():
    router = Router().add("missing", status=404).add("teapot", status=418).add("html", body="<html>")
    c = client(router)
    with pytest.raises(NotFound):
        c.get_json("https://example.org/missing")
    with pytest.raises(HttpError, match="HTTP 418"):
        c.get_json("https://example.org/teapot")
    with pytest.raises(HttpError, match="malformed JSON"):
        c.get_json("https://example.org/html")
    with pytest.raises(NotFound):
        c.get_text("https://example.org/missing")
    with pytest.raises(HttpError):
        c.get_text("https://example.org/teapot")


def test_conditional_cache(tmp_path):
    validators = {"ETag": '"v1"', "Last-Modified": "Sat, 17 Oct 2026 00:00:00 GMT"}
    router = (Router().add("x", body="first", headers=validators, times=1)
              .add("x", status=304, times=1).add("x", body="second", headers={"ETag": '"v2"'}))
    c = client(router, cache_dir=tmp_path)
    assert c.get("https://example.org/x").text == "first"
    assert c.get("https://example.org/x").text == "first"  # 304 → from cache
    assert router.requests[1].headers["if-none-match"] == '"v1"'
    assert "if-modified-since" in router.requests[1].headers
    assert c.get("https://example.org/x").text == "second"
    assert "first" not in {p.read_text() for p in tmp_path.glob("*.body")}


def test_cache_ignores_corrupt_meta_and_unvalidated_responses(tmp_path):
    router = Router().add("x", body="plain")
    c = client(router, cache_dir=tmp_path)
    c.get("https://example.org/x")
    assert list(tmp_path.iterdir()) == []  # no validators, nothing cached
    router = Router().add("y", body="v", headers={"ETag": "e"})
    c = client(router, cache_dir=tmp_path)
    c.get("https://example.org/y")
    next(tmp_path.glob("*.json")).write_text("{broken")
    c.get("https://example.org/y")
    assert "if-none-match" not in router.requests[1].headers


def test_context_manager_and_default_transport(monkeypatch):
    router = Router().add("x", body="{}")
    monkeypatch.setattr(http, "default_transport", router)
    with Client() as c:
        c.get("https://example.org/x")
    assert router.urls() == ["https://example.org/x"]
