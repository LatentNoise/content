"""A server-side surface in front of a hosted engine.

The three UIs are Streamlit applications: they run on a server, and the
browser's session cookie reaches *them*, never the engine. They forward it, so
the engine derives the identity exactly as it does for a direct caller — the UI
never declares who the visitor is (ADR 0030).

This is an integration test on purpose. The SDK suite proves the mechanism with
a mock transport; what is worth proving here is that a real engine, with real
sessions, tells two visitors apart through one shared client — because the
failure mode is not a crash, it is one person quietly reading another's work.
"""

from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import pytest
from content_sdk.compat import ContentClient as SurfaceClient
from content_sdk.compat import streamlit_visitor_headers
from fastapi.testclient import TestClient

from content.api.app import create_app


class CapturingMailer:
    def __init__(self):
        self.links: list[str] = []

    def send_template(self, to, template, variables):
        self.links.append(variables["link"])
        return "msg"


class Context:
    """Stands in for `st.context`, one per visitor being served."""

    def __init__(self, cookies=None):
        self.cookies = cookies or {}


@pytest.fixture
def hosted(settings):
    return replace(
        settings,
        auth_mode="token",
        public_base_url="http://engine",
        session_cookie_secure=False,
    )


@pytest.fixture
def mailer():
    return CapturingMailer()


@pytest.fixture
def engine(hosted, store, providers, mailer):
    app = create_app(
        hosted, store=store, providers=providers, start_worker=False, mailer=mailer
    )
    with TestClient(app, base_url="http://engine") as client:
        yield client


@pytest.fixture
def surface(engine):
    """The UI: ONE client for the whole process, as `@st.cache_resource` makes
    it, with the visitor resolved per request."""
    context = Context()
    client = SurfaceClient(
        "http://engine",
        headers_provider=lambda: streamlit_visitor_headers(context),
    )
    # Point the SDK transport at the in-process engine.
    client._t._client = engine
    client._t._owns_client = False
    client._t._static_headers = {}
    return client, context


def _sign_in(engine, mailer, email: str) -> str:
    engine.cookies.clear()
    engine.post("/api/v1/auth/link", json={"email": email})
    token = parse_qs(urlparse(mailer.links[-1]).query)["token"][0]
    engine.get(f"/api/v1/auth/callback?token={token}", follow_redirects=False)
    secret = engine.cookies["content_session"]
    engine.cookies.clear()
    return secret


def test_the_surface_acts_as_the_visitor_in_front_of_it(engine, mailer, surface):
    client, context = surface
    alice = _sign_in(engine, mailer, "alice@example.test")

    context.cookies = {"content_session": alice}
    assert client._t.get("/auth/me")["email"] == "alice@example.test"


def test_two_visitors_of_one_process_are_never_confused(engine, mailer, surface):
    """The regression that matters. One cached client, two people."""
    client, context = surface
    alice = _sign_in(engine, mailer, "alice@example.test")
    bob = _sign_in(engine, mailer, "bob@example.test")

    context.cookies = {"content_session": alice}
    first = client._t.get("/auth/me")
    context.cookies = {"content_session": bob}
    second = client._t.get("/auth/me")

    assert first["owner_id"] != second["owner_id"]
    assert (first["email"], second["email"]) == (
        "alice@example.test",
        "bob@example.test",
    )


def test_one_visitors_work_stays_theirs(engine, mailer, surface, store):
    client, context = surface
    alice = _sign_in(engine, mailer, "alice@example.test")
    context.cookies = {"content_session": alice}
    owner = client._t.get("/auth/me")["owner_id"]
    store.create_job(owner, {"sources": []}, "fail_fast", None)
    assert len(client._t.get("/jobs")) == 1

    bob = _sign_in(engine, mailer, "bob@example.test")
    context.cookies = {"content_session": bob}
    assert client._t.get("/jobs") == []


def test_a_visitor_without_a_session_is_refused(engine, surface):
    from content_sdk.errors import APIError

    client, context = surface
    context.cookies = {}
    with pytest.raises(APIError) as refusal:
        client._t.get("/auth/me")
    # 401 and not 403: the surface turns this into "sign in", not "forbidden".
    assert refusal.value.status == 401


def test_signing_out_ends_it_for_the_surface_too(engine, mailer, surface):
    from content_sdk.errors import APIError

    client, context = surface
    alice = _sign_in(engine, mailer, "alice@example.test")
    context.cookies = {"content_session": alice}
    assert client._t.get("/auth/me")["email"] == "alice@example.test"

    engine.cookies.set("content_session", alice)
    engine.post("/api/v1/auth/logout")
    engine.cookies.clear()

    with pytest.raises(APIError):
        client._t.get("/auth/me")


def test_the_same_surface_still_works_self_hosted(settings, store, providers):
    """`none` mode sends no cookie and needs none: the surface is unchanged."""
    app = create_app(settings, store=store, providers=providers, start_worker=False)
    with TestClient(app, base_url="http://engine") as engine:
        context = Context()
        client = SurfaceClient(
            "http://engine",
            headers_provider=lambda: streamlit_visitor_headers(context),
        )
        client._t._client = engine
        client._t._owns_client = False
        client._t._static_headers = {}
        assert client._t.get("/auth/me")["owner_id"] == "local"
        assert client._t.get("/jobs") == []
