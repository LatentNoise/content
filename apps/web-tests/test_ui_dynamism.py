"""UI non-regression: the three apps must render, and their dynamism must hold —
what a source lets you do is driven by the resolved capabilities (ADR 0013).

Hermetic: a fake client (see conftest) supplies canned contract-shaped answers;
no backend, no network.
"""


def _all_text(at) -> str:
    parts = []
    for kind in ("markdown", "caption", "info", "warning", "error"):
        for el in getattr(at, kind):
            parts.append(getattr(el, "value", "") or "")
    return " ".join(parts)


def _labels(at, kind) -> list[str]:
    return [getattr(el, "label", "") or "" for el in getattr(at, kind)]


def _generation_request(at) -> dict:
    """The body the page says it will send (its `st.json` preview) — the same
    dict `build_request()` produced, so a test can assert on real intent."""
    import json as _json

    value = at.json[0].value
    return _json.loads(value) if isinstance(value, str) else value


# --- HomeTube ------------------------------------------------------------------


def test_hometube_video_offers_the_derived_outputs(run_app):
    at = run_app("hometube", "https://x/video")
    assert not at.exception, at.exception
    labels = _labels(at, "checkbox")
    assert any("Video" in x for x in labels)
    assert any("Transcript" in x for x in labels)  # derivable, offered
    assert any("Summary" in x for x in labels)


def test_hometube_audio_source_hides_video(run_app):
    at = run_app("hometube", "https://x/audio-track")
    assert not at.exception, at.exception
    labels = _labels(at, "checkbox")
    assert any("Audio" in x for x in labels)
    # Video is NOT offered for a pure-audio source…
    assert not any("🎬 Video" in x for x in labels)
    # …and the blocked outputs are listed with a reason.
    assert "not available for this source" in _all_text(
        at
    ).lower() or "Not available" in _all_text(at)


def test_hometube_language_prefs_drive_the_defaults(run_app):
    """Server prefs (fr primary, en/es secondaries, VO first, primary subs OFF)
    must pre-fill the selectors — never an empty 'Choose options', never every
    track. Fake source: audio en+ja (VO ja), subtitles en/fr manual + de auto."""
    at = run_app("hometube", "https://x/video")
    assert not at.exception, at.exception
    ms = {m.label: m for m in at.multiselect}
    # Audio: VO (ja) then wanted secondaries present (en) — fr/es not offered.
    assert ms["Audio languages"].value == ["ja", "en"]
    # Subtitles: primary fr EXCLUDED (include=false), secondaries ∩ avail = en.
    assert ms["Subtitles"].value == ["en"]


def test_hometube_playlist_uses_each_item_choice(run_app):
    at = run_app("hometube", "https://x/playlist?list=1")
    assert not at.exception, at.exception
    radios = [tuple(r.options) for r in at.radio]
    assert any("🎬 Video" in o and "🎵 Audio only" in o for o in radios)


# --- Content Studio ------------------------------------------------------------


def _studio_analyze(run_app, uri):
    at = run_app("studio")
    assert not at.exception, at.exception
    at.text_input(key="uri-0").set_value(uri).run()
    analyze = [b for b in at.button if "Analyze" in (b.label or "")]
    assert analyze, "Analyze button not found"
    analyze[0].click().run()
    return at


def test_studio_audio_source_blocks_video_output(run_app):
    at = _studio_analyze(run_app, "https://x/audio-track")
    assert not at.exception, at.exception
    text = _all_text(at)
    # the video output is gated off with a reason; audio stays available
    assert "no source can produce this" in text
    assert "audio" in text.lower()


def test_studio_video_source_offers_outputs(run_app):
    at = _studio_analyze(run_app, "https://x/video")
    assert not at.exception, at.exception
    # capability pills / summary mention the producible outputs
    assert "video" in _all_text(at).lower()


# --- Content Console -----------------------------------------------------------


def test_console_renders_without_error(run_app):
    at = run_app("console")
    assert not at.exception, at.exception
    assert "Content Admin" in _all_text(at)


def test_console_says_what_a_job_was_about(run_app, monkeypatch):
    """A job must be recognizable at a glance — outputs ← source in the list
    and the detail, plus the first artifact's name as a human title. All of it
    comes from data the API already returns; the console only renders it."""
    from conftest import FakeContentClient

    request = {
        "schema_version": "1.0",
        "sources": [
            {
                "id": "v",
                "type": "url",
                "uri": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
            }
        ],
        "outputs": [{"id": "a", "type": "audio"}, {"id": "b", "type": "video"}],
    }
    row = {
        "job_id": "job_abcdef123456",
        "status": "succeeded",
        "created_at": "2026-08-09T10:00:00+00:00",
        "started_at": "2026-08-09T10:00:01+00:00",
        "finished_at": "2026-08-09T10:00:30+00:00",
        "failure_policy": "required_only",
        "error": "",
        "cancel_requested": False,
        "retry_of": "",
        "plan_id": "plan_x",
        "request": request,
        # Server-provided human label (first artifact's display name + count):
        # the list row shows it instead of the job id, the detail titles on it.
        "artifact_name": "Me at the zoo.webm",
        "artifact_count": 2,
    }
    monkeypatch.setattr(
        FakeContentClient, "list_jobs", lambda self, limit=30: [row], raising=False
    )
    monkeypatch.setattr(
        FakeContentClient,
        "job",
        lambda self, job_id: {**row, "steps": []},
        raising=False,
    )
    monkeypatch.setattr(
        FakeContentClient,
        "artifacts",
        lambda self, job_id: [
            {
                "id": "art_1",
                "filename": "Me at the zoo.webm",
                "media_type": "audio/webm",
                "size_bytes": 252182,
                "checksum": "sha256:abc",
                "provenance": {"producer": {"operation": "media.acquire_audio"}},
            }
        ],
        raising=False,
    )
    monkeypatch.setattr(
        FakeContentClient,
        "events",
        lambda self, job_id, after_sequence=0: [],
        raising=False,
    )
    monkeypatch.setattr(
        FakeContentClient, "logs", lambda self, job_id: {"logs": {}}, raising=False
    )

    at = run_app("console")
    assert not at.exception, at.exception
    text = _all_text(at)
    assert "audio + video" in text  # the outputs, from the request
    assert "youtube.com/watch?v=jNQXAC9IVRw" in text  # the source, scheme stripped
    assert "Me at the zoo" in text  # the artifact as the human title


def test_hometube_shows_which_cookie_file_is_in_use(run_app, monkeypatch):
    """ "Are my cookies actually used?" — the expander answers with the file's
    own facts: path, presence, freshness. Server metadata only; contents never
    travel."""
    from conftest import FakeContentClient

    base_config = FakeContentClient.config

    def with_credentials(self):
        payload = dict(base_config(self))
        payload["credentials"] = ["youtube"]
        payload["credentials_info"] = [
            {
                "id": "youtube",
                "path": "/config/youtube_cookies.txt",
                "exists": True,
                "size_bytes": 1234,
                "updated_at": "2026-08-07T10:00:00+00:00",
            }
        ]
        return payload

    monkeypatch.setattr(FakeContentClient, "config", with_credentials)
    at = run_app("hometube")
    assert not at.exception, at.exception
    # Select the credential in the Cookie Management expander and rerun.
    auth_boxes = [s for s in at.selectbox if "none" in (s.options or [])]
    box = next(s for s in auth_boxes if "youtube" in s.options)
    box.select("youtube").run()
    text = _all_text(at)
    assert "/config/youtube_cookies.txt" in text
    assert "updated" in text


def test_console_reports_credential_files_and_freshness(run_app, monkeypatch):
    """The credentials card shows each id with its file's path and state —
    including the dangling case (declared in .env, file never dropped)."""
    from conftest import FakeContentClient

    base_system = FakeContentClient.system

    def with_credentials(self):
        payload = dict(base_system(self))
        payload["credentials"] = ["vimeo", "youtube"]
        payload["credentials_info"] = [
            {
                "id": "vimeo",
                "path": "/config/vimeo_cookies.txt",
                "exists": False,
                "size_bytes": None,
                "updated_at": None,
            },
            {
                "id": "youtube",
                "path": "/config/youtube_cookies.txt",
                "exists": True,
                "size_bytes": 1234,
                "updated_at": "2026-08-07T10:00:00+00:00",
            },
        ]
        return payload

    monkeypatch.setattr(FakeContentClient, "system", with_credentials)
    at = run_app("console")
    assert not at.exception, at.exception
    text = _all_text(at)
    assert "/config/youtube_cookies.txt" in text
    assert "updated" in text
    assert "file not found" in text  # the dangling declaration is visible


def test_hometube_flags_a_declared_but_missing_cookie_file(run_app, monkeypatch):
    """The default deployment declares the youtube credential before the file
    exists — deliberately. The UI must turn that into guided setup (what to
    drop, where, then what to run), visible without selecting anything."""
    from conftest import FakeContentClient

    base_config = FakeContentClient.config

    def with_missing_credential(self):
        payload = dict(base_config(self))
        payload["credentials"] = ["youtube"]
        payload["credentials_info"] = [
            {
                "id": "youtube",
                "path": "/config/youtube_cookies.txt",
                "exists": False,
                "size_bytes": None,
                "updated_at": None,
            }
        ]
        return payload

    monkeypatch.setattr(FakeContentClient, "config", with_missing_credential)
    at = run_app("hometube")
    assert not at.exception, at.exception
    text = _all_text(at)
    assert "not there yet" in text
    assert "/config/youtube_cookies.txt" in text
    assert "docker-update" in text  # the instruction, not just the alarm


def test_hometube_playlist_still_asks_for_languages(run_app):
    """A playlist's entries are listed, never probed, so there is no track list
    to offer — the selectors used to vanish and the request went out with no
    `audio_languages` and no `embed_subtitles` at all: every downloaded item
    silently lost its subtitles and its extra audio tracks. The preferences now
    stand in as intent (server prefs: primary fr, secondaries en/es, VO first,
    primary excluded from subtitles)."""
    at = run_app("hometube", "https://x/playlist?list=1")
    assert not at.exception, at.exception
    ms = {m.label: m for m in at.multiselect}
    assert "Audio languages" in ms, "a playlist must still let you ask for audio"
    # VO leads, as an unresolved token: the engine expands "original" against
    # each member's own analysis when that member is planned (ADR 0022). This
    # list used to start at "fr", because a playlist had no way to ask for the
    # original voice at all.
    assert ms["Audio languages"].value == ["original", "fr", "en", "es"]
    assert "Subtitles" in ms, "a playlist must still let you ask for subtitles"
    # primary_include_subtitles=false → fr excluded, the secondaries remain.
    # No token here: a subtitle list refuses "original" (it has no meaning for
    # a translated track), which is why the subtitle caller leaves VO off.
    assert ms["Subtitles"].value == ["en", "es"]


def test_hometube_prefills_the_engines_proposed_name(run_app):
    """The name field shows the engine's own proposal (ADR 0017), editable.

    It used to show the raw title as a mere placeholder — a name that is not
    what the file gets called, since the display profile rewrites it (a slash
    becomes " - "). The proposal is the real answer, so the user sees the
    truth and can edit it.
    """
    at = run_app("hometube", "https://x/video")
    assert not at.exception, at.exception
    names = [i for i in at.text_input if (i.label or "").endswith("name")]
    assert names, "the name field disappeared"
    assert names[0].value == "Fake - Official Video"


def test_hometube_sends_no_filename_when_the_proposal_is_untouched(run_app):
    """Leaving the proposal alone is not naming intent: the request carries no
    `delivery.filename`, and the server names the artifacts itself — landing on
    the same name by construction. Editing it does send the raw text."""
    at = run_app("hometube", "https://x/video")
    assert not at.exception, at.exception
    request = _generation_request(at)
    assert "filename" not in (request["outputs"][0].get("delivery") or {})

    names = [i for i in at.text_input if (i.label or "").endswith("name")]
    names[0].set_value("My Own Name").run()
    assert not at.exception, at.exception
    edited = _generation_request(at)
    assert edited["outputs"][0]["delivery"]["filename"] == "My Own Name"
