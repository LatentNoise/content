"""The engine's side of the mail boundary (ADR 0031).

Every other test in this suite substitutes a capturing stub for the mailer,
which is the right thing for testing sign-in but leaves the class that
actually speaks HTTP — `content.mail.client.Mailer` — never executed. It is
the only way a sign-in link leaves a hosted engine, so a regression in it
means nobody can get in, and nothing here would have noticed.

What is pinned, because it is what the mail service's `SendRequest` model
accepts on the other side of the boundary: the address is a *list*, the
template and its variables are named, the secret travels in the
`Authorization` header and nowhere else, and a refusal becomes a
`MailerError` that does not carry the key into whatever logs the exception.

No network: `urllib.request.urlopen` is replaced, so these stay hermetic.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

import pytest

from content.mail.client import LoggingMailer, Mailer, MailerError, build_mailer

API_KEY = "mk_live_secret"


class _FakeResponse:
    """The context manager `urlopen` returns, reduced to what is read."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


@pytest.fixture
def captured(monkeypatch):
    """Capture the request the mailer builds, and answer it with an id."""
    seen: dict = {}

    def fake_urlopen(request, timeout=None):
        seen["request"] = request
        seen["timeout"] = timeout
        return _FakeResponse(json.dumps({"id": "msg_42", "status": "queued"}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


def _send(url: str = "https://mail.example.test") -> str:
    return Mailer(url, API_KEY, timeout=3.0).send_template(
        "someone@example.com", "magic-link", {"link": "https://x.test", "product": "C"}
    )


def test_the_message_is_posted_where_the_mail_service_listens(captured):
    assert _send() == "msg_42"
    request = captured["request"]
    assert request.full_url == "https://mail.example.test/v1/messages"
    assert request.method == "POST"
    assert captured["timeout"] == 3.0


def test_a_trailing_slash_does_not_double_the_path(captured):
    _send("https://mail.example.test/")
    assert captured["request"].full_url == "https://mail.example.test/v1/messages"


def test_the_payload_is_the_shape_the_mail_service_accepts(captured):
    _send()
    payload = json.loads(captured["request"].data.decode("utf-8"))
    # `to` is a list on the wire even for one address: the service's
    # SendRequest declares list[EmailStr] with min_length=1, and a bare
    # string would be refused with a 422 nobody sees until a sign-in fails.
    assert payload == {
        "to": ["someone@example.com"],
        "template": "magic-link",
        "variables": {"link": "https://x.test", "product": "C"},
    }


def test_the_secret_travels_in_the_authorization_header_only(captured):
    _send()
    request = captured["request"]
    assert request.get_header("Authorization") == f"Bearer {API_KEY}"
    assert request.get_header("Content-type") == "application/json"
    # Never in the URL (history, Referer) and never in the body (logs).
    assert API_KEY not in request.full_url
    assert API_KEY not in request.data.decode("utf-8")


def test_a_refusal_names_the_status_and_not_the_secret(monkeypatch):
    def refuse(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(MailerError) as refused:
        _send()
    assert "401" in str(refused.value)
    assert API_KEY not in str(refused.value)


def test_an_unreachable_service_is_a_mailer_error_not_a_raw_oserror(monkeypatch):
    def unreachable(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", unreachable)
    with pytest.raises(MailerError):
        _send()


def test_an_unreadable_answer_is_a_mailer_error_too(monkeypatch):
    """A 200 carrying something that is not JSON must not escape as ValueError.

    The caller handles `MailerError` and nothing else; a bare ValueError from
    `json.loads` would reach the route and answer 500 on a path whose whole
    point is to answer the same thing to everybody.
    """

    def garbled(request, timeout=None):
        return _FakeResponse(b"<html>502 Bad Gateway</html>")

    monkeypatch.setattr(urllib.request, "urlopen", garbled)
    with pytest.raises(MailerError):
        _send()


def test_an_answer_without_an_id_is_not_an_error(monkeypatch):
    """The id is informational — the engine ignores it — so its absence is not
    a failed send, and must not be reported as one."""

    def no_id(request, timeout=None):
        return _FakeResponse(b'{"status": "queued"}')

    monkeypatch.setattr(urllib.request, "urlopen", no_id)
    assert _send() == ""


@dataclass
class _Settings:
    mailer_url: str
    mailer_api_key: str = API_KEY
    mailer_timeout_seconds: float = 10.0


def test_no_mailer_url_means_the_link_is_logged_not_a_failure(caplog):
    """A self-hosted instance has no mail service and must not be broken by
    that: the link goes to the log, which is the operator's."""
    mailer = build_mailer(_Settings(mailer_url=""))
    assert isinstance(mailer, LoggingMailer)
    with caplog.at_level("WARNING", logger="content.mail"):
        assert mailer.send_template("a@b.test", "magic-link", {"link": "L"}) == ""
    assert "magic-link" in caplog.text


def test_a_configured_url_builds_the_real_client():
    mailer = build_mailer(
        _Settings(mailer_url="https://mail.example.test/", mailer_timeout_seconds=5.0)
    )
    assert isinstance(mailer, Mailer)
    assert mailer.url == "https://mail.example.test"
    assert mailer.timeout == 5.0
