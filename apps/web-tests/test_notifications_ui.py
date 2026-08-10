"""The notification bar, across the three UIs (prompt 09).

Hermetic: the fake client supplies the notifications, so no backend and no
network. What is guarded here is the behaviour the feature promises —

* an instance with nothing to say changes nothing on the page;
* a notification is shown, once, in every UI;
* dismissing it makes it stay gone, across a rerun *and* a fresh session
  (the reload case, which `st.session_state` alone would not survive);
* a backend that cannot answer is silent rather than fatal.
"""

import conftest
import pytest
from conftest import FakeContentClient

APPS = ["hometube", "studio", "console"]

RELEASE_NOTE = {
    "id": "release:9.9.9",
    "level": "success",
    "title": "A new version is available",
    "message": "Content 9.9.9 is out — this instance runs an older release.",
    "action_label": "View the release",
    "action_url": "https://example.invalid/releases",
}


@pytest.fixture(autouse=True)
def _no_notifications_by_default():
    """Every other test in the suite must see the pre-feature page."""
    conftest.NOTIFICATIONS = []
    yield
    conftest.NOTIFICATIONS = []


def _text(at) -> str:
    parts = []
    for kind in ("markdown", "caption", "info", "warning", "error"):
        for el in getattr(at, kind):
            parts.append(getattr(el, "value", "") or "")
    return " ".join(parts)


def _dismiss_buttons(at):
    return [b for b in at.button if (b.label or "") == "Dismiss"]


# --- silence when there is nothing to say --------------------------------------


@pytest.mark.parametrize("app", APPS)
def test_no_notifications_renders_no_banner(run_app, app):
    at = run_app(app)
    assert not at.exception, at.exception
    assert not _dismiss_buttons(at)
    assert "A new version is available" not in _text(at)


@pytest.mark.parametrize("app", APPS)
def test_a_failing_backend_is_silent_not_fatal(run_app, app, monkeypatch):
    """An unreachable/slow notifications endpoint must not break the page."""

    def boom(self):
        raise RuntimeError("notifications endpoint unreachable")

    monkeypatch.setattr(FakeContentClient, "notifications", boom)
    at = run_app(app)
    assert not at.exception, at.exception
    assert not _dismiss_buttons(at)


# --- the banner ----------------------------------------------------------------


@pytest.mark.parametrize("app", APPS)
def test_the_banner_shows_in_every_ui(run_app, app):
    conftest.NOTIFICATIONS = [RELEASE_NOTE]
    at = run_app(app)
    assert not at.exception, at.exception
    text = _text(at)
    assert "A new version is available" in text
    assert "9.9.9" in text
    # The action is a link the UI built from data, not markup from the backend.
    assert RELEASE_NOTE["action_url"] in text
    assert len(_dismiss_buttons(at)) == 1


def test_a_warning_renders_too(run_app):
    conftest.NOTIFICATIONS = [
        {
            "id": "ytdlp-stale:2026.01.01",
            "level": "warning",
            "title": "yt-dlp is out of date",
            "message": "The installed yt-dlp (2026.01.01) is 211 days old.",
            "action_label": None,
            "action_url": None,
        }
    ]
    at = run_app("hometube")
    assert not at.exception, at.exception
    assert "yt-dlp is out of date" in _text(at)


def test_several_notifications_each_get_a_dismiss(run_app):
    conftest.NOTIFICATIONS = [
        RELEASE_NOTE,
        {**RELEASE_NOTE, "id": "ytdlp-stale:x", "title": "yt-dlp is out of date"},
    ]
    at = run_app("hometube")
    assert not at.exception, at.exception
    assert len(_dismiss_buttons(at)) == 2


# --- dismissal -----------------------------------------------------------------


def test_dismissing_hides_it_on_the_rerun(run_app):
    conftest.NOTIFICATIONS = [RELEASE_NOTE]
    at = run_app("hometube")
    _dismiss_buttons(at)[0].click().run()
    assert not at.exception, at.exception
    assert "A new version is available" not in _text(at)


def test_dismissal_survives_a_reload(run_app):
    """A page reload is a *new* Streamlit session: session_state is gone, so this
    is what proves the dismissal is actually persisted."""
    conftest.NOTIFICATIONS = [RELEASE_NOTE]

    first = run_app("hometube")
    _dismiss_buttons(first)[0].click().run()
    assert not first.exception, first.exception

    reloaded = run_app("hometube")  # fresh AppTest == fresh session
    assert not reloaded.exception, reloaded.exception
    assert "A new version is available" not in _text(reloaded)
    assert not _dismiss_buttons(reloaded)


def test_dismissing_one_release_does_not_silence_the_next(run_app):
    conftest.NOTIFICATIONS = [RELEASE_NOTE]
    at = run_app("hometube")
    _dismiss_buttons(at)[0].click().run()

    # A later release is a different id, so it must show again.
    conftest.NOTIFICATIONS = [
        {**RELEASE_NOTE, "id": "release:10.0.0", "message": "Content 10.0.0 is out."}
    ]
    later = run_app("hometube")
    assert not later.exception, later.exception
    assert "10.0.0" in _text(later)
    assert len(_dismiss_buttons(later)) == 1


# --- AGPL §13 source offer -----------------------------------------------------


@pytest.mark.parametrize("app", APPS)
def test_every_ui_offers_the_source_code(run_app, app):
    """AGPL §13: a user interacting with the instance over a network must be
    able to reach its Corresponding Source."""
    at = run_app(app)
    assert not at.exception, at.exception
    text = _text(at)
    assert "AGPL-3.0-or-later" in text
    assert "https://example.invalid/content" in text


@pytest.mark.parametrize("app", APPS)
def test_the_source_link_comes_from_the_instance_not_the_ui(run_app, app, monkeypatch):
    """An operator running a fork sets CONTENT_SOURCE_URL and their users get
    *their* source. The UI must never substitute a hard-coded upstream link."""
    monkeypatch.setattr(
        FakeContentClient,
        "system",
        lambda self: {
            "version": conftest.ENGINE_VERSION,
            "license": "AGPL-3.0-or-later",
            "source_url": "https://forked.invalid/mine",
            "cache_enabled": True,
            "analysis_ttl_hours": 72,
            "max_concurrent_jobs": 2,
            "credentials": [],
            "language": {
                "primary": "",
                "secondaries": [],
                "vo_first": True,
                "primary_include_subtitles": True,
            },
            "runners": [],
            "environment": [],
            "paths": {},
        },
    )
    at = run_app(app)
    assert not at.exception, at.exception
    text = _text(at)
    assert "https://forked.invalid/mine" in text
    assert "LatentNoise" not in text  # no upstream fallback baked in


# --- version mismatch (the launch check) ----------------------------------------


# Deliberately impossible release numbers: a divergence fabricated with a
# real-looking version broke the moment the monorepo reached it (0.2.0 did).
DIVERGED = "9.8.7"
DIVERGED_NEXT = "9.8.8"


def _system_with_version(version):
    """The fake's full system payload with only the version changed."""
    base = FakeContentClient.system

    def patched(self):
        payload = dict(base(self))
        payload["version"] = version
        return payload

    return patched


@pytest.mark.parametrize("app", APPS)
def test_a_version_mismatch_warns_in_every_ui(run_app, app, monkeypatch):
    """The one notification the client builds itself: the backend cannot know
    its clients, so only the UI can notice a torn deployment."""
    monkeypatch.setattr(FakeContentClient, "system", _system_with_version(DIVERGED))
    at = run_app(app)
    assert not at.exception, at.exception
    text = _text(at)
    assert "UI and backend versions differ" in text
    assert DIVERGED in text
    assert "docker compose pull" in text
    assert len(_dismiss_buttons(at)) == 1


def test_matching_versions_show_no_mismatch_banner(run_app):
    """The fake reports the apps' own version, so the default page is clean —
    every other test in this file relies on that silence."""
    at = run_app("hometube")
    assert not at.exception, at.exception
    assert "UI and backend versions differ" not in _text(at)


def test_a_backend_without_a_version_is_silent(run_app, monkeypatch):
    """An older backend that reports no version must not trigger a false alarm."""
    monkeypatch.setattr(FakeContentClient, "system", _system_with_version(""))
    at = run_app("hometube")
    assert not at.exception, at.exception
    assert "UI and backend versions differ" not in _text(at)


def test_a_system_endpoint_failure_is_silent_not_fatal(run_app, monkeypatch):
    """The launch check is a courtesy like every other notification: a backend
    that cannot answer /system must not break the page or leave a banner."""

    def boom(self):
        raise RuntimeError("system endpoint unreachable")

    monkeypatch.setattr(FakeContentClient, "system", boom)
    at = run_app("hometube")
    assert not at.exception, at.exception
    assert "UI and backend versions differ" not in _text(at)


def test_dismissing_the_mismatch_survives_a_reload(run_app, monkeypatch):
    monkeypatch.setattr(FakeContentClient, "system", _system_with_version(DIVERGED))

    first = run_app("hometube")
    _dismiss_buttons(first)[0].click().run()
    assert not first.exception, first.exception

    reloaded = run_app("hometube")  # fresh AppTest == fresh session
    assert not reloaded.exception, reloaded.exception
    assert "UI and backend versions differ" not in _text(reloaded)


def test_a_new_divergence_notifies_after_an_old_dismissal(run_app, monkeypatch):
    """Dismissal is keyed by the exact version pair: silencing one divergence
    must not also silence the next one."""
    monkeypatch.setattr(FakeContentClient, "system", _system_with_version(DIVERGED))
    at = run_app("hometube")
    _dismiss_buttons(at)[0].click().run()

    monkeypatch.setattr(
        FakeContentClient, "system", _system_with_version(DIVERGED_NEXT)
    )
    later = run_app("hometube")
    assert not later.exception, later.exception
    assert "UI and backend versions differ" in _text(later)
    assert DIVERGED_NEXT in _text(later)


def test_the_mismatch_joins_backend_notifications_in_one_banner(run_app, monkeypatch):
    """Client-built and backend-sent notifications share the bar — same shape,
    same dismissal store, no separate rendering path."""
    monkeypatch.setattr(FakeContentClient, "system", _system_with_version(DIVERGED))
    conftest.NOTIFICATIONS = [RELEASE_NOTE]
    at = run_app("hometube")
    assert not at.exception, at.exception
    text = _text(at)
    assert "UI and backend versions differ" in text
    assert "A new version is available" in text
    assert len(_dismiss_buttons(at)) == 2
