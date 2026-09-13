"""Signing in: a link in a mail, a session, and four surfaces that agree.

ADR 0030 built the lock and deliberately left the key unmade — `token` mode
refused every request, because a half-built check must fail closed. This is
the key, and these tests are mostly about the ways a sign-in door goes wrong
rather than the way it goes right.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from content.api.app import create_app
from content.identity import LOCAL_OWNER, credentials
from content.persistence.store import utcnow

SURFACE = "https://studio.example.test"


class CapturingMailer:
    """Stands in for the mail service and keeps what it was asked to send."""

    def __init__(self):
        self.sent: list[dict] = []

    def send_template(self, to, template, variables):
        self.sent.append({"to": to, "template": template, "variables": variables})
        return "msg-1"

    @property
    def last_link(self) -> str:
        return self.sent[-1]["variables"]["link"]


@pytest.fixture
def hosted(settings):
    return replace(
        settings,
        auth_mode="token",
        storage_layout="per_user",
        public_base_url="https://api.example.test",
        allowed_redirect_origins=(SURFACE,),
        session_cookie_domain=".example.test",
        magic_link_max_per_hour=3,
    )


@pytest.fixture
def mailer():
    return CapturingMailer()


@pytest.fixture
def client(hosted, store, providers, mailer):
    app = create_app(
        hosted, store=store, providers=providers, start_worker=False, mailer=mailer
    )
    # The base URL matters: the session cookie is Secure and scoped to the
    # parent domain, so a client on plain http://testserver would silently
    # drop it — and every test here would pass for the wrong reason.
    with TestClient(app, base_url="https://api.example.test") as test_client:
        yield test_client


def _token_of(link: str) -> str:
    return parse_qs(urlparse(link).query)["token"][0]


def _sign_in(client, mailer, email: str = "someone@example.com") -> None:
    assert client.post("/api/v1/auth/link", json={"email": email}).status_code == 202
    response = client.get(
        f"/api/v1/auth/callback?token={_token_of(mailer.last_link)}",
        follow_redirects=False,
    )
    assert response.status_code == 303


# --- the door is shut until it is opened ---------------------------------------


def test_hosted_mode_refuses_a_request_with_no_session(client):
    assert client.get("/api/v1/jobs").status_code == 401


def test_a_session_gets_in(client, mailer):
    _sign_in(client, mailer)
    assert client.get("/api/v1/jobs").status_code == 200


def test_self_hosted_mode_is_untouched(settings, store, providers):
    app = create_app(settings, store=store, providers=providers, start_worker=False)
    with TestClient(app) as local:
        assert local.get("/api/v1/jobs").status_code == 200
        assert local.get("/api/v1/auth/me").json()["owner_id"] == LOCAL_OWNER
        # No account row is invented for the implicit user.
        assert local.get("/api/v1/auth/me").json()["account"] is False


# --- the token ------------------------------------------------------------------


def test_the_token_is_never_stored_in_the_clear(client, mailer, store):
    client.post("/api/v1/auth/link", json={"email": "someone@example.com"})
    token = _token_of(mailer.last_link)
    with store._conn() as conn:
        rows = conn.execute("SELECT token_hash FROM auth_tokens").fetchall()
    assert rows and all(row["token_hash"] != token for row in rows)
    assert rows[0]["token_hash"] == credentials.fingerprint(token)


def test_a_link_works_exactly_once(client, mailer):
    client.post("/api/v1/auth/link", json={"email": "someone@example.com"})
    token = _token_of(mailer.last_link)
    first = client.get(f"/api/v1/auth/callback?token={token}", follow_redirects=False)
    assert first.status_code == 303
    client.cookies.clear()
    second = client.get(f"/api/v1/auth/callback?token={token}", follow_redirects=False)
    assert second.status_code == 200 and "no longer works" in second.text
    assert client.get("/api/v1/jobs").status_code == 401


def test_an_expired_token_is_refused(client, mailer, store):
    client.post("/api/v1/auth/link", json={"email": "someone@example.com"})
    token = _token_of(mailer.last_link)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with store._conn() as conn:
        conn.execute("UPDATE auth_tokens SET expires_at = ?", (past,))
    response = client.get(
        f"/api/v1/auth/callback?token={token}", follow_redirects=False
    )
    assert response.status_code == 200 and "no longer works" in response.text


def test_an_invented_token_gets_the_same_answer_as_a_spent_one(client):
    response = client.get("/api/v1/auth/callback?token=not-a-token")
    assert response.status_code == 200 and "no longer works" in response.text


# --- telling nothing to a stranger ----------------------------------------------


def test_a_known_and_an_unknown_address_get_identical_answers(client, mailer, store):
    _sign_in(client, mailer, "known@example.com")
    client.cookies.clear()

    known = client.post("/api/v1/auth/link", json={"email": "known@example.com"})
    unknown = client.post("/api/v1/auth/link", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()


def test_the_rate_limit_never_shows_itself(client, hosted, mailer):
    for _ in range(hosted.magic_link_max_per_hour):
        assert (
            client.post(
                "/api/v1/auth/link", json={"email": "someone@example.com"}
            ).status_code
            == 202
        )
    sent_before = len(mailer.sent)
    blocked = client.post("/api/v1/auth/link", json={"email": "someone@example.com"})
    # Same answer, and no mail: a different status would be an oracle of its own.
    assert blocked.status_code == 202
    assert len(mailer.sent) == sent_before


# --- where a sign-in may send the browser ---------------------------------------


def test_an_allowed_destination_is_honoured(client, mailer):
    client.post(
        "/api/v1/auth/link",
        json={"email": "someone@example.com", "next": f"{SURFACE}/jobs"},
    )
    response = client.get(
        f"/api/v1/auth/callback?token={_token_of(mailer.last_link)}"
        f"&next={SURFACE}/jobs",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"{SURFACE}/jobs"


def test_an_open_redirect_is_refused_when_the_link_is_asked_for(client):
    response = client.post(
        "/api/v1/auth/link",
        json={"email": "someone@example.com", "next": "https://phishing.example"},
    )
    assert response.status_code == 422


def test_an_open_redirect_is_refused_again_at_the_callback(client, mailer):
    """The link carries `next`, so a tampered mail must be caught twice."""
    client.post("/api/v1/auth/link", json={"email": "someone@example.com"})
    response = client.get(
        f"/api/v1/auth/callback?token={_token_of(mailer.last_link)}"
        "&next=https://phishing.example",
        follow_redirects=False,
    )
    assert response.status_code == 400


# --- the session cookie ---------------------------------------------------------


def test_the_cookie_is_defended(client, mailer):
    client.post("/api/v1/auth/link", json={"email": "someone@example.com"})
    response = client.get(
        f"/api/v1/auth/callback?token={_token_of(mailer.last_link)}",
        follow_redirects=False,
    )
    header = response.headers["set-cookie"].lower()
    assert "httponly" in header  # JavaScript must never read it
    assert "secure" in header  # never over plain HTTP
    assert "samesite=lax" in header
    # The PARENT domain is what makes one session work across four surfaces.
    assert "domain=.example.test" in header


def test_the_session_secret_is_never_stored_in_the_clear(client, mailer, store):
    _sign_in(client, mailer)
    presented = client.cookies["content_session"]
    with store._conn() as conn:
        rows = conn.execute("SELECT session_hash FROM sessions").fetchall()
    assert rows[0]["session_hash"] == credentials.fingerprint(presented)
    assert rows[0]["session_hash"] != presented


def test_signing_out_revokes_the_session(client, mailer):
    _sign_in(client, mailer)
    secret = client.cookies["content_session"]
    assert client.post("/api/v1/auth/logout").status_code == 204
    # Even a client that kept the cookie is done.
    client.cookies.set("content_session", secret)
    assert client.get("/api/v1/jobs").status_code == 401


def test_a_revoked_session_stops_immediately(client, mailer, store):
    _sign_in(client, mailer)
    owner = client.get("/api/v1/auth/me").json()["owner_id"]
    assert store.revoke_all_sessions(owner) == 1
    assert client.get("/api/v1/jobs").status_code == 401


# --- accounts -------------------------------------------------------------------


def test_the_same_address_is_always_the_same_account(client, mailer):
    _sign_in(client, mailer, "someone@example.com")
    first = client.get("/api/v1/auth/me").json()["owner_id"]
    client.cookies.clear()
    _sign_in(client, mailer, "someone@example.com")
    assert client.get("/api/v1/auth/me").json()["owner_id"] == first


def test_case_and_spacing_do_not_make_a_second_account(client, mailer):
    _sign_in(client, mailer, "someone@example.com")
    first = client.get("/api/v1/auth/me").json()["owner_id"]
    client.cookies.clear()
    _sign_in(client, mailer, "  SomeOne@Example.COM  ")
    assert client.get("/api/v1/auth/me").json()["owner_id"] == first


def test_an_account_owner_id_can_never_be_local(client, mailer):
    _sign_in(client, mailer)
    owner = client.get("/api/v1/auth/me").json()["owner_id"]
    assert owner != LOCAL_OWNER and owner.startswith("usr_")


def test_whoami_reports_the_address(client, mailer):
    _sign_in(client, mailer, "someone@example.com")
    body = client.get("/api/v1/auth/me").json()
    assert body["email"] == "someone@example.com" and body["account"] is True


# --- the point of all of it -----------------------------------------------------


def test_two_signed_in_users_never_see_each_other(
    client, hosted, store, providers, mailer
):
    """The whole reason the door exists: ADR 0030's isolation, proven through
    a real sign-in rather than through a hand-made owner id."""
    _sign_in(client, mailer, "alice@example.com")
    alice = client.get("/api/v1/auth/me").json()["owner_id"]
    store.create_job(alice, {"sources": []}, "fail_fast", None)

    client.cookies.clear()
    _sign_in(client, mailer, "bob@example.com")
    bob = client.get("/api/v1/auth/me").json()["owner_id"]

    assert bob != alice
    assert client.get("/api/v1/jobs").json() == []


def test_a_session_for_a_deleted_account_still_resolves_to_its_owner(
    client, mailer, store
):
    """A session names an owner id, not an address: the identity survives the
    account row being corrected or replaced."""
    _sign_in(client, mailer)
    owner = client.get("/api/v1/auth/me").json()["owner_id"]
    with store._conn() as conn:
        conn.execute("DELETE FROM users WHERE owner_id = ?", (owner,))
    assert client.get("/api/v1/jobs").status_code == 200
    assert client.get("/api/v1/auth/me").json()["account"] is False


def test_housekeeping_clears_spent_tokens_and_dead_sessions(client, mailer, store):
    _sign_in(client, mailer)
    assert store.delete_expired_auth_tokens(utcnow()) == 1
    owner = client.get("/api/v1/auth/me").json()["owner_id"]
    store.revoke_all_sessions(owner)
    assert store.delete_expired_sessions(utcnow()) == 1


# --- where a sign-in lands --------------------------------------------------------


def test_a_configured_landing_page_is_used_when_the_link_names_none(
    hosted, store, providers, mailer
):
    """Following a link from a mail should not drop someone on API docs."""
    landing = replace(hosted, sign_in_default_target=SURFACE)
    app = create_app(
        landing, store=store, providers=providers, start_worker=False, mailer=mailer
    )
    with TestClient(app, base_url="https://api.example.test") as client:
        client.post("/api/v1/auth/link", json={"email": "someone@example.com"})
        response = client.get(
            f"/api/v1/auth/callback?token={_token_of(mailer.last_link)}",
            follow_redirects=False,
        )
    assert response.headers["location"] == SURFACE


def test_the_link_still_wins_over_the_configured_landing_page(
    hosted, store, providers, mailer
):
    landing = replace(hosted, sign_in_default_target=SURFACE)
    app = create_app(
        landing, store=store, providers=providers, start_worker=False, mailer=mailer
    )
    with TestClient(app, base_url="https://api.example.test") as client:
        client.post(
            "/api/v1/auth/link",
            json={"email": "someone@example.com", "next": f"{SURFACE}/jobs"},
        )
        response = client.get(
            f"/api/v1/auth/callback?token={_token_of(mailer.last_link)}"
            f"&next={SURFACE}/jobs",
            follow_redirects=False,
        )
    assert response.headers["location"] == f"{SURFACE}/jobs"


def test_a_landing_page_outside_the_allowlist_is_ignored(
    hosted, store, providers, mailer
):
    """A misconfigured default must not become the one redirect nobody checks."""
    landing = replace(hosted, sign_in_default_target="https://phishing.example")
    app = create_app(
        landing, store=store, providers=providers, start_worker=False, mailer=mailer
    )
    with TestClient(app, base_url="https://api.example.test") as client:
        client.post("/api/v1/auth/link", json={"email": "someone@example.com"})
        response = client.get(
            f"/api/v1/auth/callback?token={_token_of(mailer.last_link)}",
            follow_redirects=False,
        )
    assert response.headers["location"] != "https://phishing.example"
    assert response.headers["location"] == hosted.public_base_url
