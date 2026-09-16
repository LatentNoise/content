"""The engine tells the surfaces about each other (ADR 0038).

Each surface used to know one thing: where the engine is. Nothing knew where
the *other* surfaces were, so nothing could offer them, and the sign-in page
could only ever say "Sign in to Content" whichever surface had sent you.

`CONTENT_SURFACES` is declared once, on the engine, and every client reads it
from `/config`. The redirect allowlist follows from it: the surfaces are, by
definition, the places a sign-in comes back to.
"""

from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from content.api.app import create_app
from content.config import (
    _parse_surfaces,
    settings_from_env,
    sign_in_can_work,
    surface_at,
)

STUDIO = "https://studio.example.test"
CONSOLE = "https://console.example.test"


class CapturingMailer:
    def __init__(self):
        self.sent: list[dict] = []

    def send_template(self, to, template, variables):
        self.sent.append(variables)
        return "msg"


# --- the setting ----------------------------------------------------------------


def test_surfaces_are_parsed_as_kind_and_origin():
    assert _parse_surfaces(f"studio={STUDIO}/, console={CONSOLE}") == (
        ("studio", STUDIO),
        ("console", CONSOLE),
    )
    assert _parse_surfaces("") == ()


@pytest.mark.parametrize(
    "raw",
    [
        "blog=https://blog.example.test",  # not a surface anyone can reason about
        "studio=studio.example.test",  # no scheme: not an origin
        f"studio={STUDIO}/app",  # a path is not an origin
        f"studio={STUDIO},studio={CONSOLE}",  # twice
        "studio",  # no url at all
    ],
)
def test_a_surface_nobody_can_reason_about_is_refused(raw):
    with pytest.raises(ValueError, match="CONTENT_SURFACES"):
        _parse_surfaces(raw)


def test_the_redirect_allowlist_follows_the_surfaces(monkeypatch):
    """Listing them twice is how one goes missing from the allowlist and a
    sign-in lands on the default target instead of where the person was."""
    monkeypatch.setenv("CONTENT_SURFACES", f"studio={STUDIO},console={CONSOLE}")
    monkeypatch.delenv("CONTENT_ALLOWED_REDIRECT_ORIGINS", raising=False)
    assert settings_from_env().allowed_redirect_origins == (STUDIO, CONSOLE)


def test_an_explicit_allowlist_replaces_the_derived_one(monkeypatch):
    monkeypatch.setenv("CONTENT_SURFACES", f"studio={STUDIO}")
    monkeypatch.setenv("CONTENT_ALLOWED_REDIRECT_ORIGINS", "https://elsewhere.test")
    assert settings_from_env().allowed_redirect_origins == ("https://elsewhere.test",)


# --- what a client sees -----------------------------------------------------------


@pytest.fixture
def known(settings):
    return replace(
        settings,
        # Configured out of order on purpose: clients get the canonical order.
        surfaces=(("console", CONSOLE), ("studio", STUDIO)),
        allowed_redirect_origins=(CONSOLE, STUDIO),
    )


def test_config_lists_the_surfaces_in_canonical_order_with_titles(
    known, store, providers
):
    app = create_app(known, store=store, providers=providers, start_worker=False)
    with TestClient(app) as client:
        surfaces = client.get("/api/v1/config").json()["surfaces"]
    assert surfaces == [
        {"kind": "studio", "title": "Content Studio", "url": STUDIO},
        {"kind": "console", "title": "Content Admin", "url": CONSOLE},
    ]


def test_a_surface_is_recognised_by_origin(known):
    assert surface_at(known, f"{STUDIO}/?x=1")["kind"] == "studio"
    assert surface_at(known, "https://studio.example.test:8443") is None
    assert surface_at(known, "/relative") is None
    assert surface_at(known, "") is None


# --- the door names where it leads --------------------------------------------------


@pytest.fixture
def hosted(known, store, providers):
    mailer = CapturingMailer()
    hosted = replace(
        known,
        auth_mode="token",
        storage_layout="per_user",
        public_base_url="https://api.example.test",
    )
    app = create_app(
        hosted, store=store, providers=providers, start_worker=False, mailer=mailer
    )
    with TestClient(app, base_url="https://api.example.test") as client:
        yield client, mailer


def test_the_sign_in_page_names_the_surface_it_returns_to(hosted):
    client, _ = hosted
    page = client.get("/auth/sign-in", params={"next": STUDIO}).text
    assert "Sign in to Content Studio" in page
    page = client.get("/auth/sign-in", params={"next": CONSOLE}).text
    assert "Sign in to Content Admin" in page


def test_the_sign_in_page_falls_back_to_the_product_name(hosted):
    client, _ = hosted
    for target in ("", "https://elsewhere.test"):
        page = client.get("/auth/sign-in", params={"next": target}).text
        assert "Sign in to Content<" in page or "Sign in to Content</" in page
        assert "elsewhere" not in page


def test_the_email_names_the_surface_too(hosted):
    client, mailer = hosted
    response = client.post(
        "/api/v1/auth/link", json={"email": "someone@example.com", "next": STUDIO}
    )
    assert response.status_code == 202
    assert mailer.sent[-1]["product"] == "Content Studio"
    # And the link still carries the person back where they were.
    query = parse_qs(urlparse(mailer.sent[-1]["link"]).query)
    assert query["next"] == [STUDIO]


# --- the addresses are checked against each other -----------------------------------
#
# The public HomeTube once sent visitors to http://api.content.k3s.lab: the
# surface built its sign-in link from an address of its own, the engine wrote
# a different one into its emails, and nothing compared them. These pin the
# comparison, with the configuration that shipped the bug among the cases.


HOSTED = dict(
    auth_mode="token",
    public_base_url="https://api.example.test",
    session_cookie_domain=".example.test",
    session_cookie_secure=True,
    surfaces=(("studio", STUDIO), ("console", CONSOLE)),
)


def test_a_coherent_hosted_configuration_passes(settings):
    assert sign_in_can_work(replace(settings, **HOSTED)) == []


def test_a_self_hosted_instance_is_never_checked(settings):
    """Nobody signs in there, and it must never be refused for addresses it
    does not use."""
    assert (
        sign_in_can_work(replace(settings, auth_mode="none", public_base_url="")) == []
    )


def test_signing_in_needs_a_public_address(settings):
    problems = sign_in_can_work(replace(settings, **{**HOSTED, "public_base_url": ""}))
    assert problems and "CONTENT_PUBLIC_BASE_URL is required" in problems[0]


def test_a_lan_name_beside_a_public_domain_is_refused(settings):
    """The shape of the original bug: one surface on a name the session cookie
    can never reach."""
    leaking = replace(
        settings,
        **{**HOSTED, "surfaces": (("studio", "http://studio.content.k3s.lab"),)},
    )
    problems = sign_in_can_work(leaking)
    assert any("outside the session cookie's domain" in p for p in problems)
    assert any("Secure" in p for p in problems)


def test_a_host_only_cookie_needs_every_surface_on_the_same_host(settings):
    """localhost works — cookies ignore the port — and nothing else does."""
    local = replace(
        settings,
        auth_mode="token",
        public_base_url="http://localhost:8000",
        session_cookie_domain="",
        session_cookie_secure=False,
        surfaces=(("studio", "http://localhost:8502"),),
    )
    assert sign_in_can_work(local) == []
    split = replace(local, surfaces=(("studio", "http://studio.example.test"),))
    assert any("belongs to localhost alone" in p for p in sign_in_can_work(split))


def test_the_engine_refuses_to_start_on_incoherent_addresses(monkeypatch):
    monkeypatch.setenv("CONTENT_AUTH_MODE", "token")
    monkeypatch.setenv("CONTENT_PUBLIC_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("CONTENT_SESSION_COOKIE_DOMAIN", ".example.test")
    monkeypatch.setenv("CONTENT_SURFACES", "studio=http://studio.lan.test")
    with pytest.raises(ValueError, match="Signing in cannot work"):
        settings_from_env()


def test_config_publishes_the_public_api_address(known, store, providers):
    public = replace(known, public_base_url="https://api.example.test")
    app = create_app(public, store=store, providers=providers, start_worker=False)
    with TestClient(app) as client:
        assert client.get("/api/v1/config").json()["public_api_url"] == (
            "https://api.example.test"
        )
