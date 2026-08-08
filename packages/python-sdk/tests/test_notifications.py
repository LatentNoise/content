"""The UI half of notifications: fetching, dismissal state, failure silence.

The renderer itself is exercised by the Streamlit AppTests in `apps/web-tests`
(it needs a running app); this covers the parts that are pure Python.
"""

from content_sdk import legal, notifications


class FakeClient:
    def __init__(self, notes=None, error: Exception | None = None):
        self._notes = notes or []
        self._error = error
        self.calls = 0

    def notifications(self):
        self.calls += 1
        if self._error:
            raise self._error
        return list(self._notes)


NOTE = {
    "id": "release:9.9.9",
    "level": "success",
    "title": "A new version is available",
    "message": "Content 9.9.9 is out.",
    "action_label": None,
    "action_url": None,
}


def test_fetch_returns_what_the_backend_says():
    assert notifications.fetch(FakeClient([NOTE])) == [NOTE]


def test_fetch_is_silent_when_the_backend_fails():
    assert notifications.fetch(FakeClient(error=RuntimeError("boom"))) == []


def test_fetch_is_silent_on_a_backend_without_the_endpoint():
    class Older:
        pass  # no notifications() at all

    assert notifications.fetch(Older()) == []


def test_dismissal_round_trips(tmp_path):
    store = notifications.DismissalStore(tmp_path / "state.json")
    assert store.is_dismissed("release:9.9.9") is False
    store.dismiss("release:9.9.9")
    assert store.is_dismissed("release:9.9.9") is True
    # A fresh store over the same file sees it — this is what makes a dismissal
    # survive a page reload (a new Streamlit session).
    assert notifications.DismissalStore(tmp_path / "state.json").is_dismissed(
        "release:9.9.9"
    )


def test_dismissal_is_per_notification_id(tmp_path):
    store = notifications.DismissalStore(tmp_path / "state.json")
    store.dismiss("release:0.2.0")
    assert store.is_dismissed("release:0.3.0") is False  # the next release notifies


def test_pending_filters_dismissed(tmp_path):
    store = notifications.DismissalStore(tmp_path / "state.json")
    client = FakeClient([NOTE])
    assert notifications.pending(client, store) == [NOTE]
    store.dismiss(NOTE["id"])
    assert notifications.pending(client, store) == []


def test_an_unwritable_state_path_does_not_raise(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    store = notifications.DismissalStore(blocker / "nested" / "state.json")
    store.dismiss("release:9.9.9")  # must not raise
    assert store.is_dismissed("release:9.9.9") is False  # degrades to session-only


def test_corrupt_state_file_is_ignored(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    store = notifications.DismissalStore(path)
    assert store.is_dismissed("anything") is False


# --- AGPL §13 source offer (content_sdk.legal) ---------------------------------


class SystemClient:
    def __init__(self, payload=None, error: Exception | None = None):
        self._payload = payload
        self._error = error

    def system(self):
        if self._error:
            raise self._error
        return self._payload


def test_source_offer_reads_what_the_instance_reports():
    assert legal.source_offer(
        SystemClient({"license": "AGPL-3.0-or-later", "source_url": "https://x/fork"})
    ) == ("AGPL-3.0-or-later", "https://x/fork")


def test_source_offer_survives_an_unreachable_backend():
    """A missing link degrades the footer; it must not break the page."""
    assert legal.source_offer(SystemClient(error=RuntimeError("down"))) == (
        "AGPL-3.0-or-later",
        "",
    )


def test_source_offer_survives_an_older_backend_without_the_field():
    assert legal.source_offer(SystemClient({"version": "0.1.0"})) == (
        "AGPL-3.0-or-later",
        "",
    )


def test_source_offer_never_invents_a_url():
    """It must not fall back to a hard-coded upstream link: on a fork that would
    point users at source that is not the software they are running."""
    _, url = legal.source_offer(SystemClient({"source_url": ""}))
    assert url == ""


# --- version mismatch (the one client-built notification) -----------------------


def test_equal_versions_are_silent():
    assert notifications.version_mismatch("0.1.0", "0.1.0") is None


def test_a_cosmetic_difference_is_not_a_mismatch():
    """`v0.1.0` and `0.1.0` are the same release; only the parsed number counts."""
    assert notifications.version_mismatch("v0.1.0", "0.1.0") is None


def test_a_newer_backend_warns_and_names_the_ui_as_behind():
    note = notifications.version_mismatch("0.1.0", "0.2.0")
    assert note is not None
    assert note["level"] == "warning"
    assert note["id"] == "version-mismatch:0.1.0:0.2.0"
    assert "0.1.0" in note["message"] and "0.2.0" in note["message"]
    assert "UI image is behind" in note["message"]


def test_a_newer_ui_warns_and_names_the_backend_as_behind():
    note = notifications.version_mismatch("0.2.0", "0.1.0")
    assert note is not None
    assert "backend image is behind" in note["message"]


def test_a_patch_difference_still_warns():
    """The release banner's major/minor-only rule is about news; a mismatch is
    about coherence — a torn deployment is torn at any distance."""
    assert notifications.version_mismatch("0.1.0", "0.1.1") is not None


def test_a_missing_backend_version_is_silent():
    """An older backend that does not report a version must degrade to no
    banner, never to a false alarm."""
    assert notifications.version_mismatch("0.1.0", "") is None


def test_unparseable_versions_are_silent():
    assert notifications.version_mismatch("dev", "0.1.0") is None
    assert notifications.version_mismatch("0.1.0", "unknown") is None


def test_the_id_changes_with_the_pair():
    """Dismissing one divergence must not silence the next one."""
    first = notifications.version_mismatch("0.1.0", "0.2.0")
    later = notifications.version_mismatch("0.1.0", "0.3.0")
    assert first["id"] != later["id"]
