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
