"""Instance notifications: version comparison, yt-dlp staleness, the endpoint.

Adapted from HomeTube's tests/test_notifications.py — the version-comparison
cases are its, the staleness and endpoint cases are Content's.

Hermetic: no network. `fetch_latest_release` is exercised through a stub, and
the failure-silence contract is asserted directly (a banner must never be able
to break a page).
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from content import notifications as notif
from content.api.app import create_app
from content.config import ContentSettings
from content.providers.base import ProviderRegistry
from tests.conftest import FakeProvider


@pytest.fixture(autouse=True)
def _clear_release_cache():
    notif._release_cache.clear()
    yield
    notif._release_cache.clear()


def _settings(tmp_path, **overrides) -> ContentSettings:
    return ContentSettings(
        data_dir=tmp_path, db_path=tmp_path / "content.db", **overrides
    )


# --- version comparison --------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2.5.0", (2, 5, 0)),
        ("v2.6.1", (2, 6, 1)),
        ("  V3.0  ", (3, 0, 0)),
        ("2.6", (2, 6, 0)),
        ("not-a-version", (0, 0, 0)),
        ("", (0, 0, 0)),
    ],
)
def test_parse_version(raw, expected):
    assert notif.parse_version(raw) == expected


@pytest.mark.parametrize(
    "current,latest,notifies",
    [
        ("2.5.0", "2.6.0", True),  # minor
        ("2.5.0", "3.0.0", True),  # major
        ("2.5.0", "2.5.1", False),  # patch only — deliberately silent
        ("2.5.0", "2.5.0", False),  # same
        ("2.6.0", "2.5.0", False),  # older upstream
        ("3.0.0", "2.9.9", False),  # older major
        ("0.1.0", "garbage", False),  # unparseable is never "newer"
    ],
)
def test_is_major_or_minor_update(current, latest, notifies):
    assert notif.is_major_or_minor_update(current, latest) is notifies


def test_release_notification_is_keyed_by_target_version():
    note = notif.release_notification("0.1.0", "v0.2.0", "https://example/rel")
    assert note is not None
    # Dismissing 0.2 must not silence 0.3 later.
    assert note.id == "release:0.2.0"
    assert note.action_url == "https://example/rel"
    assert "0.2.0" in note.message and "0.1.0" in note.message


def test_release_notification_without_page_url_has_no_action():
    note = notif.release_notification("0.1.0", "0.2.0")
    assert note is not None and note.action_label is None and note.action_url is None


def test_release_notification_silent_on_patch():
    assert notif.release_notification("0.1.0", "0.1.9") is None


# --- yt-dlp staleness (D-20) ---------------------------------------------------


@pytest.mark.parametrize(
    "version,expected",
    [
        ("2026.07.04", 27),
        ("2026.07.31", 0),
        ("2026.07.04.123456", 27),  # nightly suffix
        ("", None),
        ("unknown", None),
        ("2026.13.99", None),  # not a real date
    ],
)
def test_ytdlp_age_days(version, expected):
    assert notif.ytdlp_age_days(version, today=date(2026, 7, 31)) == expected


def test_ytdlp_notification_warns_when_stale():
    note = notif.ytdlp_notification("2026.05.01", 30, today=date(2026, 7, 31))
    assert note is not None
    assert note.level == "warning"
    assert note.id == "ytdlp-stale:2026.05.01"
    assert "No video formats found" in note.message  # the symptom operators see


def test_ytdlp_notification_silent_when_fresh_unknown_or_disabled():
    today = date(2026, 7, 31)
    assert notif.ytdlp_notification("2026.07.30", 30, today=today) is None
    assert notif.ytdlp_notification("", 30, today=today) is None
    assert notif.ytdlp_notification("2020.01.01", 0, today=today) is None  # off


# --- assembly / failure silence ------------------------------------------------


def test_no_release_url_means_no_outbound_call(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(
        notif, "fetch_latest_release", lambda *a, **k: called.append(1) or ""
    )
    assert notif.build_notifications(_settings(tmp_path)) == []
    assert called == []  # the feature is opt-in: no URL, no network


def test_release_check_failure_is_silent(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("endpoint down")

    monkeypatch.setattr(notif, "fetch_latest_release", boom)
    settings = _settings(tmp_path, release_check_url="https://example/latest")
    assert notif.build_notifications(settings) == []  # no notification, no raise


def test_release_check_is_cached(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        notif, "fetch_latest_release", lambda *a, **k: calls.append(1) or "99.0.0"
    )
    settings = _settings(tmp_path, release_check_url="https://example/latest")
    first = notif.build_notifications(settings)
    second = notif.build_notifications(settings)
    assert first and second  # both render the banner…
    assert len(calls) == 1  # …but a page render never re-calls out


def test_ytdlp_staleness_comes_from_the_registry(tmp_path, monkeypatch):
    class FakeRegistry:
        def describe(self):
            return [{"name": "ytdlp", "tool_version": "2026.01.01"}]

    settings = _settings(tmp_path, ytdlp_max_age_days=30)
    notes = notif.build_notifications(settings, FakeRegistry(), today=date(2026, 7, 31))
    assert [n["id"] for n in notes] == ["ytdlp-stale:2026.01.01"]


def test_the_staleness_check_is_off_by_default(tmp_path):
    """A fresh install must never open on the yt-dlp age warning: age alone
    cannot tell "stale" from "newest available" (yt-dlp releases irregularly,
    and the image pins the latest), so an unactionable banner would greet
    every new user and invite needless bug reports. The check is opt-in."""

    class FakeRegistry:
        def describe(self):
            return [{"name": "ytdlp", "tool_version": "2020.01.01"}]  # ancient

    settings = _settings(tmp_path)  # defaults: no CONTENT_YTDLP_MAX_AGE_DAYS
    notes = notif.build_notifications(settings, FakeRegistry(), today=date(2026, 8, 8))
    assert notes == []


def test_a_broken_registry_does_not_break_notifications(tmp_path):
    class BadRegistry:
        def describe(self):
            raise RuntimeError("inventory unavailable")

    assert notif.build_notifications(_settings(tmp_path), BadRegistry()) == []


def test_notifications_are_data_not_markup(tmp_path, monkeypatch):
    monkeypatch.setattr(notif, "fetch_latest_release", lambda *a, **k: "99.0.0")
    settings = _settings(tmp_path, release_check_url="https://example/latest")
    (note,) = notif.build_notifications(settings)
    assert set(note) == {
        "id",
        "level",
        "title",
        "message",
        "action_label",
        "action_url",
    }
    assert "<" not in note["message"]  # the UI owns presentation


# --- the endpoint --------------------------------------------------------------


def _api(settings) -> TestClient:
    app = create_app(
        settings,
        providers=ProviderRegistry([FakeProvider()]),
        start_worker=False,
    )
    return TestClient(app)


def test_endpoint_is_empty_when_unconfigured(settings):
    """The default instance says nothing — the UIs must render as they do today."""
    with _api(settings) as client:
        response = client.get("/api/v1/notifications")
    assert response.status_code == 200
    assert response.json() == {"notifications": []}


def test_endpoint_reports_a_new_release(tmp_path, monkeypatch):
    monkeypatch.setattr(notif, "fetch_latest_release", lambda *a, **k: "99.0.0")
    settings = _settings(
        tmp_path,
        release_check_url="https://example/latest",
        release_page_url="https://example/releases",
    )
    with _api(settings) as client:
        payload = client.get("/api/v1/notifications").json()
    (note,) = payload["notifications"]
    assert note["id"] == "release:99.0.0"
    assert note["level"] == "success"
    assert note["action_url"] == "https://example/releases"


def test_endpoint_stays_200_when_the_release_check_fails(tmp_path, monkeypatch):
    """Failure silence is the contract: a down endpoint is not an API error."""

    def boom(*_a, **_k):
        raise RuntimeError("unreachable")

    monkeypatch.setattr(notif, "fetch_latest_release", boom)
    settings = _settings(tmp_path, release_check_url="https://example/latest")
    with _api(settings) as client:
        response = client.get("/api/v1/notifications")
    assert response.status_code == 200
    assert response.json() == {"notifications": []}


# --- the release fetch itself (shape handling, no socket) ----------------------


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _stub_urlopen(monkeypatch, body: bytes | Exception):
    def _open(_request, timeout=None):
        if isinstance(body, Exception):
            raise body
        return _FakeResponse(body)

    monkeypatch.setattr(notif.urllib.request, "urlopen", _open)


def test_fetch_reads_tag_name(monkeypatch):
    _stub_urlopen(monkeypatch, b'{"tag_name": "v1.2.3", "name": "Release 1.2.3"}')
    assert notif.fetch_latest_release("https://example/latest") == "v1.2.3"


def test_fetch_falls_back_to_name(monkeypatch):
    """Some forges omit tag_name on a draft/untagged release."""
    _stub_urlopen(monkeypatch, b'{"name": "1.2.3"}')
    assert notif.fetch_latest_release("https://example/latest") == "1.2.3"


def test_fetch_accepts_a_list_endpoint_newest_first(monkeypatch):
    _stub_urlopen(monkeypatch, b'[{"tag_name": "v2.0.0"}, {"tag_name": "v1.0.0"}]')
    assert notif.fetch_latest_release("https://example/releases") == "v2.0.0"


@pytest.mark.parametrize(
    "body",
    [
        b"<html>not json</html>",  # a login page or a proxy error
        b"[]",  # no releases yet
        b'"just a string"',  # unexpected shape
        b'{"tag_name": 42}',  # wrong type
    ],
)
def test_fetch_returns_empty_on_unusable_payloads(monkeypatch, body):
    _stub_urlopen(monkeypatch, body)
    assert notif.fetch_latest_release("https://example/latest") == ""


@pytest.mark.parametrize(
    "error",
    [
        notif.urllib.error.URLError("unreachable"),
        notif.urllib.error.HTTPError("u", 404, "Not Found", {}, None),
        notif.urllib.error.HTTPError("u", 403, "rate limited", {}, None),
        TimeoutError("slow"),
        OSError("connection reset"),
    ],
)
def test_fetch_is_silent_on_every_transport_failure(monkeypatch, error):
    _stub_urlopen(monkeypatch, error)
    assert notif.fetch_latest_release("https://example/latest") == ""


def test_fetch_without_a_url_never_opens_a_connection(monkeypatch):
    def _explode(*_a, **_k):
        raise AssertionError("no outbound call may be made without a URL")

    monkeypatch.setattr(notif.urllib.request, "urlopen", _explode)
    assert notif.fetch_latest_release("") == ""


# --- AGPL §13: the source offer ------------------------------------------------


def test_system_publishes_the_licence_and_source_offer(settings):
    """A network user must be able to find the Corresponding Source (AGPL §13)."""
    with _api(settings) as client:
        system = client.get("/api/v1/system").json()
    assert system["license"] == "AGPL-3.0-or-later"
    assert system["source_url"].startswith("http")


def test_a_modified_deployment_can_point_at_its_own_source(tmp_path):
    """The link is configuration, not a constant: an operator running a fork
    must be able to offer *their* source, not upstream's."""
    fork = "https://example.invalid/my-fork"
    settings = _settings(tmp_path, source_url=fork)
    with _api(settings) as client:
        assert client.get("/api/v1/system").json()["source_url"] == fork


def test_the_source_offer_is_never_a_secret(settings):
    """It is published deliberately — assert it is not masked like a credential."""
    with _api(settings) as client:
        system = client.get("/api/v1/system").json()
    assert "set ·" not in system["source_url"]
