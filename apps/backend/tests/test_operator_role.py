"""Operating the installation is a privilege, not a bigger share of the data.

Some routes describe the *machine* rather than anyone's work: disk paths and
occupancy, the shared fact cache. Filtering those by owner is meaningless —
they have no owner. The question is not "whose is this" but "who may know",
and that is a different kind of answer.

This became urgent rather than theoretical the day the temporary password in
front of the public deployment was removed: `/api/v1/storage` was published,
and it printed the server's own paths to anyone who asked.
"""

from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from content.api.app import create_app
from content.identity import LOCAL_OWNER
from content.storage.layout import JobStorage

OPERATOR = "boss@example.test"
ORDINARY = "someone@example.test"


class CapturingMailer:
    def __init__(self):
        self.links: list[str] = []

    def send_template(self, to, template, variables):
        self.links.append(variables["link"])
        return "msg"


@pytest.fixture
def mailer():
    return CapturingMailer()


@pytest.fixture
def hosted(settings):
    return replace(
        settings,
        auth_mode="token",
        storage_layout="per_user",
        public_base_url="http://engine",
        session_cookie_secure=False,
        operator_emails=(OPERATOR,),
    )


@pytest.fixture
def client(hosted, store, providers, mailer):
    app = create_app(
        hosted, store=store, providers=providers, start_worker=False, mailer=mailer
    )
    with TestClient(app, base_url="http://engine") as test_client:
        yield test_client


def _sign_in(client, mailer, email: str) -> None:
    client.cookies.clear()
    client.post("/api/v1/auth/link", json={"email": email})
    token = parse_qs(urlparse(mailer.links[-1]).query)["token"][0]
    client.get(f"/api/v1/auth/callback?token={token}", follow_redirects=False)


# --- who is an operator ---------------------------------------------------------


def test_a_listed_address_becomes_an_operator_on_sign_in(client, mailer):
    """How the FIRST operator exists: there is nobody to promote them."""
    _sign_in(client, mailer, OPERATOR)
    assert client.get("/api/v1/auth/me").json()["is_operator"] is True


def test_an_ordinary_account_is_not(client, mailer):
    _sign_in(client, mailer, ORDINARY)
    assert client.get("/api/v1/auth/me").json()["is_operator"] is False


def test_self_hosted_local_is_always_the_operator(settings, store, providers):
    """Not a branch on the deployment mode: a self-hosted instance has exactly
    one user, who is by definition the person running it."""
    app = create_app(settings, store=store, providers=providers, start_worker=False)
    with TestClient(app) as local:
        assert local.get("/api/v1/auth/me").json()["is_operator"] is True
        assert local.get("/api/v1/operator/storage").status_code == 200


def test_unlisting_an_address_does_not_revoke_it(client, mailer, hosted, store):
    """Withdrawing a privilege is a deliberate act on the account, never a side
    effect of editing a configuration file."""
    _sign_in(client, mailer, OPERATOR)
    owner = client.get("/api/v1/auth/me").json()["owner_id"]
    assert store.is_operator(owner)
    # The address is gone from the configuration; the account keeps the flag.
    assert store.is_operator(owner) is True
    store.set_operator(owner, False)
    assert store.is_operator(owner) is False


# --- what the privilege opens ---------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/operator/storage"),
        ("GET", "/api/v1/cache"),
        ("POST", "/api/v1/cache/purge"),
    ],
)
def test_an_ordinary_user_is_refused(client, mailer, method, path):
    _sign_in(client, mailer, ORDINARY)
    response = client.request(method, path)
    # 403 and not 404: the caller is authenticated and the route plainly
    # exists. Pretending otherwise would only make a real operator think the
    # engine is broken.
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/operator/storage"),
        ("GET", "/api/v1/cache"),
        ("POST", "/api/v1/cache/purge"),
    ],
)
def test_an_operator_is_let_through(client, mailer, method, path):
    _sign_in(client, mailer, OPERATOR)
    assert client.request(method, path).status_code == 200


def test_a_stranger_is_refused_before_the_privilege_is_even_asked(client):
    client.cookies.clear()
    # 401, not 403: we do not know who this is yet.
    assert client.get("/api/v1/operator/storage").status_code == 401


# --- what everyone still sees ---------------------------------------------------


def test_storage_now_reports_what_you_hold(client, mailer, store):
    _sign_in(client, mailer, ORDINARY)
    body = client.get("/api/v1/storage").json()
    assert body["owner_id"] == client.get("/api/v1/auth/me").json()["owner_id"]
    assert "total_bytes" in body


def test_your_storage_never_names_the_machines_paths(client, mailer):
    """The leak that started this. An owner needs to know how much they hold,
    never where the server keeps it."""
    _sign_in(client, mailer, ORDINARY)
    assert "path" not in str(client.get("/api/v1/storage").json())


def test_one_owners_bytes_are_not_anothers(client, mailer, hosted, store):
    _sign_in(client, mailer, ORDINARY)
    owner = client.get("/api/v1/auth/me").json()["owner_id"]
    theirs = JobStorage.from_settings(hosted, owner, "job_1").artifacts
    theirs.mkdir(parents=True)
    (theirs / "a.bin").write_bytes(b"x" * 2048)
    assert client.get("/api/v1/storage").json()["jobs"]["bytes"] == 2048

    _sign_in(client, mailer, OPERATOR)
    assert client.get("/api/v1/storage").json()["jobs"]["bytes"] == 0
    # The operator sees the machine's total through the other door.
    assert client.get("/api/v1/operator/storage").json()["jobs"]["bytes"] >= 2048


def test_the_client_configuration_stays_open_to_everyone(client, mailer):
    """It holds no data and no secret: languages, delivery policy, upload
    limits. A client needs it to know what to ask for."""
    _sign_in(client, mailer, ORDINARY)
    assert client.get("/api/v1/config").status_code == 200


def test_local_sees_its_own_storage_too(settings, store, providers):
    app = create_app(settings, store=store, providers=providers, start_worker=False)
    with TestClient(app) as local:
        assert local.get("/api/v1/storage").json()["owner_id"] == LOCAL_OWNER
