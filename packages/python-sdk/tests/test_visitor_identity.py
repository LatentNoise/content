"""One client, many visitors: identity belongs to the request.

The three UIs are Streamlit applications. They run on a server, cache one SDK
client per process, and serve every visitor from it. The browser's session
cookie reaches the *UI*, never the engine — so the UI forwards it, and the
engine derives the identity as it always does (ADR 0030).

The failure this guards against is the dangerous one: a credential stored on
the shared client would be inherited by whoever asks next.
"""

import httpx
import pytest
from content_sdk import ContentClient
from content_sdk._transport import session_cookie_header
from content_sdk.compat import (
    ContentClient as CompatClient,
)
from content_sdk.compat import (
    is_unauthenticated,
    sign_in_url,
    streamlit_visitor_headers,
)
from content_sdk.errors import APIError


class Context:
    """Stands in for `st.context`."""

    def __init__(self, cookies):
        self.cookies = cookies


class NoContext:
    @property
    def cookies(self):
        raise RuntimeError("no script run context")


def _recorder():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    return seen, httpx.Client(transport=httpx.MockTransport(handler))


# --- reading the visitor --------------------------------------------------------


def test_the_visitors_cookie_becomes_a_header():
    ctx = Context({"content_session": "abc", "other": "ignored"})
    assert streamlit_visitor_headers(ctx) == {"Cookie": "content_session=abc"}


def test_no_cookie_means_no_identity():
    """Which is exactly right self-hosted: the engine asks for none."""
    assert streamlit_visitor_headers(Context({})) == {}
    assert streamlit_visitor_headers(Context(None)) == {}


def test_an_unreadable_context_is_not_an_error():
    assert streamlit_visitor_headers(NoContext()) == {}


def test_an_empty_secret_adds_no_header():
    assert session_cookie_header("") == {}


# --- the property that matters --------------------------------------------------


def test_one_shared_client_carries_each_visitors_identity():
    """The regression this file exists for.

    A credential parked on the cached client would make one person's session
    everybody's. Resolving it per request is what prevents that.
    """
    seen, http = _recorder()
    visitor = Context({"content_session": "alice"})
    client = ContentClient(
        "http://engine.test",
        http_client=http,
        headers_provider=lambda: streamlit_visitor_headers(visitor),
    )

    client._t.request("GET", "/jobs")
    visitor.cookies = {"content_session": "bob"}
    client._t.request("GET", "/jobs")
    visitor.cookies = {}
    client._t.request("GET", "/jobs")

    assert [r.headers.get("cookie") for r in seen] == [
        "content_session=alice",
        "content_session=bob",
        None,
    ]


def test_the_compat_client_does_it_too():
    """The UIs use the dict-returning client, so it needs the same seam."""
    seen, http = _recorder()
    visitor = Context({"content_session": "alice"})
    client = CompatClient(
        "http://engine.test",
        headers_provider=lambda: streamlit_visitor_headers(visitor),
    )
    client._t._client = http
    client._t._owns_client = False
    client._t._static_headers = {}
    client._t.get("/jobs")
    assert seen[0].headers.get("cookie") == "content_session=alice"


def test_a_key_and_a_cookie_can_coexist():
    """A program's key on the client, a visitor's cookie on the request."""
    seen, http = _recorder()
    client = ContentClient(
        "http://engine.test",
        http_client=http,
        api_key="ck_live_abc",
        headers_provider=lambda: session_cookie_header("alice"),
    )
    client._t.request("GET", "/jobs")
    assert seen[0].headers["authorization"] == "Bearer ck_live_abc"
    assert seen[0].headers["cookie"] == "content_session=alice"


def test_a_failing_provider_never_breaks_the_call():
    def boom():
        raise RuntimeError("no context")

    seen, http = _recorder()
    client = ContentClient(
        "http://engine.test", http_client=http, headers_provider=boom
    )
    client._t.request("GET", "/jobs")
    assert seen[0].headers.get("cookie") is None


# --- sending a refused visitor to the door --------------------------------------


def test_a_401_is_told_apart_from_a_failure():
    assert is_unauthenticated(APIError(401, {}))
    # 403 means signed in but not allowed: a different message, and sending
    # someone back to a form they already filled would be wrong.
    assert not is_unauthenticated(APIError(403, {}))
    assert not is_unauthenticated(APIError(500, {}))
    assert not is_unauthenticated(RuntimeError("network down"))


def test_the_sign_in_url_points_at_the_engine():
    assert (
        sign_in_url("https://api.example.test/")
        == "https://api.example.test/auth/sign-in"
    )


def test_the_return_address_is_encoded():
    url = sign_in_url(
        "https://api.example.test", "https://studio.example.test/jobs?a=1"
    )
    assert url.endswith("next=https%3A%2F%2Fstudio.example.test%2Fjobs%3Fa%3D1")


@pytest.mark.parametrize("target", ["", None])
def test_no_return_address_is_fine(target):
    assert sign_in_url("https://api.example.test", target or "").endswith(
        "/auth/sign-in"
    )
