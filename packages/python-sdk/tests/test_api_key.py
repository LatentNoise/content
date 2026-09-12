"""The credential the SDK presents, and where it is allowed to appear.

The SDK is the one official door to the API (ADR 0015), so it is also the one
place a credential has to be attached correctly — and the one place a mistake
would leak it into every consumer at once.
"""

import httpx
import pytest
from content_sdk import ContentClient
from content_sdk._transport import auth_headers, resolve_api_key
from content_sdk.aio import AsyncContentClient


def test_no_key_is_the_normal_state(monkeypatch):
    """A self-hosted engine asks for nothing, and that is not a degraded case."""
    monkeypatch.delenv("CONTENT_API_KEY", raising=False)
    assert resolve_api_key(None) == ""
    assert auth_headers("") == {}


def test_the_environment_supplies_the_key(monkeypatch):
    monkeypatch.setenv("CONTENT_API_KEY", "ck_live_from_env")
    assert resolve_api_key(None) == "ck_live_from_env"
    # An explicit argument still wins, and an explicit empty string means none.
    assert resolve_api_key("ck_live_explicit") == "ck_live_explicit"
    assert resolve_api_key("") == ""


def test_whitespace_around_a_key_is_forgiven(monkeypatch):
    monkeypatch.setenv("CONTENT_API_KEY", "  ck_live_padded \n")
    assert resolve_api_key(None) == "ck_live_padded"


def test_the_key_travels_in_the_authorization_header(monkeypatch):
    monkeypatch.delenv("CONTENT_API_KEY", raising=False)
    client = ContentClient("http://engine.test", api_key="ck_live_abc")
    assert client._t._client.headers["authorization"] == "Bearer ck_live_abc"


def test_the_async_client_behaves_identically(monkeypatch):
    monkeypatch.delenv("CONTENT_API_KEY", raising=False)
    client = AsyncContentClient("http://engine.test", api_key="ck_live_abc")
    assert client._t._client.headers["authorization"] == "Bearer ck_live_abc"


def test_the_key_never_reaches_the_url_or_the_body(monkeypatch):
    """A secret in a query string survives in history, proxy logs and Referer;
    a secret in a body is written to application logs. Only the header."""
    monkeypatch.delenv("CONTENT_API_KEY", raising=False)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"jobs": []})

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    client = ContentClient(
        "http://engine.test", http_client=http_client, api_key="ck_live_secret"
    )
    client._t.request("GET", "/jobs")

    request = seen[0]
    assert request.headers["authorization"] == "Bearer ck_live_secret"
    assert "ck_live_secret" not in str(request.url)
    assert b"ck_live_secret" not in request.content


def test_a_caller_supplied_client_is_not_mutated(monkeypatch):
    """Someone else's httpx.Client is theirs: the credential goes on our
    requests, never onto their object's default headers."""
    monkeypatch.delenv("CONTENT_API_KEY", raising=False)
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    )
    ContentClient("http://engine.test", http_client=http_client, api_key="ck_live_x")
    assert "authorization" not in http_client.headers


@pytest.mark.parametrize("key", ["", "   "])
def test_an_empty_key_adds_no_header(key):
    assert auth_headers(resolve_api_key(key)) == {}
