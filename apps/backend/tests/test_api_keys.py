"""Named API keys: the credential a program can hold.

A session cookie belongs to a browser. The CLI, the MCP server and anything
scripting the API need something they can carry in a header — the sixth
decision of ADR 0030, and the second carrier through the one door that
establishes identity.
"""

from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from content.api.app import create_app
from content.identity import LOCAL_OWNER, credentials

SURFACE = "https://studio.example.test"


class CapturingMailer:
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
    )


@pytest.fixture
def mailer():
    return CapturingMailer()


@pytest.fixture
def client(hosted, store, providers, mailer):
    app = create_app(
        hosted, store=store, providers=providers, start_worker=False, mailer=mailer
    )
    with TestClient(app, base_url="https://api.example.test") as test_client:
        yield test_client


def _sign_in(client, mailer, email: str = "someone@example.com") -> str:
    client.post("/api/v1/auth/link", json={"email": email})
    token = parse_qs(urlparse(mailer.last_link).query)["token"][0]
    client.get(f"/api/v1/auth/callback?token={token}", follow_redirects=False)
    return client.get("/api/v1/auth/me").json()["owner_id"]


def _mint(client, name: str = "my laptop") -> dict:
    response = client.post("/api/v1/auth/keys", json={"name": name})
    assert response.status_code == 201
    return response.json()


# --- the key itself -------------------------------------------------------------


def test_a_key_carries_a_readable_prefix(client, mailer):
    _sign_in(client, mailer)
    # The prefix makes a key recognisable in a log, and detectable by the
    # secret scanners that watch public repositories.
    assert _mint(client)["key"].startswith("ck_live_")


def test_the_key_is_shown_once_and_never_again(client, mailer, store):
    _sign_in(client, mailer)
    secret = _mint(client)["key"]
    listed = client.get("/api/v1/auth/keys").json()
    assert listed and "key" not in listed[0]
    with store._conn() as conn:
        rows = conn.execute("SELECT key_hash FROM api_keys").fetchall()
    assert rows[0]["key_hash"] == credentials.fingerprint(secret)
    assert secret not in str(dict(rows[0]))


def test_a_key_needs_a_name(client, mailer):
    _sign_in(client, mailer)
    assert client.post("/api/v1/auth/keys", json={"name": "   "}).status_code == 422


# --- the same door --------------------------------------------------------------


def test_a_key_authenticates_a_program(client, mailer):
    owner = _sign_in(client, mailer)
    secret = _mint(client)["key"]
    client.cookies.clear()
    assert client.get("/api/v1/jobs").status_code == 401

    headers = {"Authorization": f"Bearer {secret}"}
    assert client.get("/api/v1/jobs", headers=headers).status_code == 200
    assert client.get("/api/v1/auth/me", headers=headers).json()["owner_id"] == owner


def test_a_wrong_key_is_refused(client, mailer):
    _sign_in(client, mailer)
    _mint(client)
    client.cookies.clear()
    bad = {"Authorization": "Bearer ck_live_not-a-real-key"}
    assert client.get("/api/v1/jobs", headers=bad).status_code == 401


def test_a_revoked_key_stops_immediately(client, mailer):
    _sign_in(client, mailer)
    key = _mint(client)
    headers = {"Authorization": f"Bearer {key['key']}"}
    assert client.get("/api/v1/jobs", headers=headers).status_code == 200

    assert client.delete(f"/api/v1/auth/keys/{key['id']}").status_code == 204
    client.cookies.clear()
    assert client.get("/api/v1/jobs", headers=headers).status_code == 401


def test_revoking_one_key_leaves_the_others_working(client, mailer):
    _sign_in(client, mailer)
    doomed, kept = _mint(client, "old laptop"), _mint(client, "new laptop")
    client.delete(f"/api/v1/auth/keys/{doomed['id']}")
    client.cookies.clear()
    assert (
        client.get(
            "/api/v1/jobs", headers={"Authorization": f"Bearer {kept['key']}"}
        ).status_code
        == 200
    )


def test_use_is_recorded(client, mailer, store):
    _sign_in(client, mailer)
    key = _mint(client)
    assert client.get("/api/v1/auth/keys").json()[0]["last_used_at"] == ""
    client.get("/api/v1/jobs", headers={"Authorization": f"Bearer {key['key']}"})
    assert client.get("/api/v1/auth/keys").json()[0]["last_used_at"] != ""


# --- one owner's keys are their own ---------------------------------------------


def test_a_key_never_belongs_to_someone_else(client, mailer):
    _sign_in(client, mailer, "alice@example.com")
    alice_key = _mint(client, "alice's")
    client.cookies.clear()

    _sign_in(client, mailer, "bob@example.com")
    assert client.get("/api/v1/auth/keys").json() == []
    # An id copied from someone else's list is *not found*, not forbidden:
    # 403 would confirm it exists.
    assert client.delete(f"/api/v1/auth/keys/{alice_key['id']}").status_code == 404


def test_a_key_sees_only_its_owners_work(client, mailer, store):
    alice = _sign_in(client, mailer, "alice@example.com")
    store.create_job(alice, {"sources": []}, "fail_fast", None)
    client.cookies.clear()

    _sign_in(client, mailer, "bob@example.com")
    bob_key = _mint(client, "bob's")["key"]
    client.cookies.clear()
    listed = client.get(
        "/api/v1/jobs", headers={"Authorization": f"Bearer {bob_key}"}
    ).json()
    assert listed == []


# --- self-hosted ----------------------------------------------------------------


def test_self_hosted_can_mint_a_key_before_it_needs_one(settings, store, providers):
    """`none` mode has one implicit user, so a key there belongs to `local`.

    It authenticates nothing that was not already open — and it means an
    operator can prepare keys *before* switching the instance to `token`,
    instead of being locked out at the moment they flip it.
    """
    app = create_app(settings, store=store, providers=providers, start_worker=False)
    with TestClient(app) as local:
        key = local.post("/api/v1/auth/keys", json={"name": "prepared"}).json()
        assert key["key"].startswith("ck_live_")
        assert (
            store.api_key_owner(credentials.fingerprint(key["key"]))["owner_id"]
            == LOCAL_OWNER
        )
