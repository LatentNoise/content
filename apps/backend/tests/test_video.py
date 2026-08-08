"""Video output: contract options, D4 feasibility, selector building, and
end-to-end execution with fakes. Real yt-dlp/ffmpeg runs live in
test_video_external.py / test_file_sources.py."""

import pytest
from pydantic import ValidationError

from content.analysis.service import AnalysisService
from content.application.submit import submit_generation
from content.domain.errors import RequestRejected
from content.execution.executor import JobExecutor
from content.persistence.store import IdempotencyKeyActive
from content.planning.planner import build_plan
from content.providers.base import ProviderRegistry
from content.providers.ytdlp import (
    audio_format_args,
    audio_language_selector,
    build_video_profiles,
    embedding_args,
)
from tests.conftest import (
    FakeFileProvider,
    FakeProvider,
    make_request,
    minimal_payload,
)


def video_payload(options: dict | None = None, **overrides) -> dict:
    output = {"id": "video_main", "type": "video"}
    if options is not None:
        output["options"] = options
    return minimal_payload(outputs=[output], **overrides)


@pytest.fixture
def plan_url(store, providers, settings):
    service = AnalysisService(store, providers, settings)

    def _plan(payload):
        request = make_request(payload)
        analysis = service.analyze_sources(list(request.sources))
        return build_plan(request, analysis, providers, settings)

    return _plan


def rejected(plan_fn, payload):
    with pytest.raises(RequestRejected) as excinfo:
        plan_fn(payload)
    return excinfo.value.result


# --- contract ------------------------------------------------------------------


def test_invalid_codec_value_rejected_by_schema():
    with pytest.raises(ValidationError):
        make_request(video_payload({"selection": {"video_codec": {"value": "hevc"}}}))


def test_video_options_defaults():
    request = make_request(video_payload())
    options = request.outputs[0].options
    assert options.container == "source"
    assert options.processing.mode == "auto"
    assert options.selection.video_codec is None


# --- planner (D4 semantics) -----------------------------------------------------


def test_default_video_plan(plan_url):
    plan = plan_url(video_payload())
    step = plan.steps[0]
    assert step.operation == "media.acquire_video"
    assert step.provider == "ytdlp"
    assert step.params["container"] is None
    assert step.params["selection"]["video_codec"] is None
    assert step.params["embed_chapters"] is False
    assert step.params["embed_subtitles"] == []


# --- embedding (chapters + subtitles) ------------------------------------------


def test_embed_chapters_threaded_into_params(plan_url):
    plan = plan_url(video_payload({"processing": {"embed_chapters": True}}))
    assert plan.steps[0].params["embed_chapters"] is True


def test_embed_subtitles_available_languages_planned(plan_url):
    # FakeProvider offers manual en/fr, automatic de.
    plan = plan_url(video_payload({"processing": {"embed_subtitles": ["en", "fr"]}}))
    assert plan.steps[0].params["embed_subtitles"] == ["en", "fr"]
    assert not any(w.path.endswith("embed_subtitles") for w in plan.warnings)


def test_embed_subtitles_unavailable_language_dropped_with_warning(plan_url):
    plan = plan_url(video_payload({"processing": {"embed_subtitles": ["en", "ja"]}}))
    assert plan.steps[0].params["embed_subtitles"] == ["en"]
    warning = next(w for w in plan.warnings if w.path.endswith("embed_subtitles"))
    assert warning.code == "partial_output"


def test_embed_subtitles_duplicates_normalized_by_schema():
    request = make_request(
        video_payload({"processing": {"embed_subtitles": ["en", "en", " fr "]}})
    )
    assert request.outputs[0].options.processing.embed_subtitles == ["en", "fr"]


# --- audio languages (S10) -----------------------------------------------------


def test_audio_languages_available_ordered_into_selection(plan_url):
    # FakeProvider offers audio languages en, ja (original ja).
    plan = plan_url(video_payload({"selection": {"audio_languages": ["ja", "en"]}}))
    assert plan.steps[0].params["selection"]["audio_languages"] == ["ja", "en"]
    assert not any(w.path.endswith("audio_languages") for w in plan.warnings)


def test_audio_languages_unavailable_dropped_with_warning(plan_url):
    plan = plan_url(video_payload({"selection": {"audio_languages": ["ja", "zz"]}}))
    assert plan.steps[0].params["selection"]["audio_languages"] == ["ja"]
    warning = next(w for w in plan.warnings if w.path.endswith("audio_languages"))
    assert warning.code == "partial_output"


def test_audio_output_language_threaded(plan_url):
    payload = minimal_payload(
        outputs=[{"id": "a", "type": "audio", "options": {"languages": ["ja"]}}]
    )
    plan = plan_url(payload)
    assert plan.steps[0].params["audio_languages"] == ["ja"]


def test_audio_selector_prefers_language_then_falls_back():
    assert audio_language_selector([]) == "bestaudio/best"
    assert (
        audio_language_selector(["ja", "en"])
        == "ba[language^=ja]/ba[language^=en]/bestaudio/best"
    )


def test_video_profiles_embed_requested_audio_languages():
    profiles = build_video_profiles(
        {"max_height": None, "audio_languages": ["ja", "en"]}
    )
    # each profile requests both audio tracks (multi-audio)
    assert "+ba[language^=ja]+ba[language^=en]" in profiles[0]


# --- provider argument helpers -------------------------------------------------


def test_embedding_args_empty_when_nothing_requested():
    assert embedding_args({}) == []


def test_embedding_args_builds_all_flags():
    args = embedding_args(
        {
            "embed_metadata": True,
            "embed_thumbnail": True,
            "embed_chapters": True,
            "embed_subtitles": ["en", "fr"],
        }
    )
    assert "--embed-metadata" in args
    assert "--embed-thumbnail" in args and "--convert-thumbnails" in args
    assert "--embed-chapters" in args
    assert args[args.index("--embed-subs") + 1 : args.index("--embed-subs") + 3] == [
        "--sub-langs",
        "en,fr",
    ]


def test_audio_format_args_source_is_noop():
    assert audio_format_args({}) == []
    assert audio_format_args({"audio_format": None}) == []


def test_audio_format_args_extracts_explicit_format():
    assert audio_format_args({"audio_format": "opus"}) == [
        "--extract-audio",
        "--audio-format",
        "opus",
    ]


def test_prefer_available_codec_no_warning(plan_url):
    plan = plan_url(
        video_payload(
            {"selection": {"video_codec": {"mode": "prefer", "value": "h264"}}}
        )
    )
    assert plan.warnings == []
    assert plan.steps[0].params["selection"]["video_codec"]["available"] is True


def test_prefer_unavailable_codec_warns_and_plans(plan_url):
    plan = plan_url(
        video_payload(
            {"selection": {"video_codec": {"mode": "prefer", "value": "av1"}}}
        )
    )
    assert [w.code for w in plan.warnings] == ["preference_unavailable"]
    assert plan.steps[0].params["selection"]["video_codec"]["available"] is False


def test_require_unavailable_codec_fails_feasibility(plan_url):
    result = rejected(
        plan_url,
        video_payload(
            {"selection": {"video_codec": {"mode": "require", "value": "av1"}}}
        ),
    )
    assert [e.code for e in result.errors] == ["capability_unavailable"]
    assert result.errors[0].path == "outputs[0].options.selection.video_codec"


def test_transcode_mode_not_supported(plan_url):
    result = rejected(plan_url, video_payload({"processing": {"mode": "transcode"}}))
    assert [e.code for e in result.errors] == ["option_not_supported"]


def test_copy_mode_with_container_change_is_invalid(plan_url):
    result = rejected(
        plan_url, video_payload({"processing": {"mode": "copy"}, "container": "mkv"})
    )
    assert [e.code for e in result.errors] == ["invalid_option"]


def test_remux_requires_explicit_container(plan_url):
    result = rejected(plan_url, video_payload({"processing": {"mode": "remux"}}))
    assert [e.code for e in result.errors] == ["invalid_option"]


# --- planner (file sources) -----------------------------------------------------


@pytest.fixture
def plan_file(store, settings):
    registry = ProviderRegistry([FakeProvider(), FakeFileProvider()])
    service = AnalysisService(store, registry, settings)

    def _plan(payload):
        request = make_request(payload)
        analysis = service.analyze_sources(list(request.sources))
        return build_plan(request, analysis, registry, settings)

    return _plan


def file_video_payload(options: dict | None = None) -> dict:
    output = {"id": "video_main", "type": "video"}
    if options is not None:
        output["options"] = options
    return {
        "schema_version": "1.0",
        "sources": [{"id": "vid", "type": "file", "path": "/input/movie.mp4"}],
        "outputs": [output],
    }


def test_file_video_remux_plan(plan_file):
    plan = plan_file(file_video_payload({"container": "mkv"}))
    step = plan.steps[0]
    assert step.provider == "ffmpeg"
    assert step.params["container"] == "mkv"


def test_file_video_max_height_below_source_needs_transcoding(plan_file):
    with pytest.raises(RequestRejected) as excinfo:
        plan_file(file_video_payload({"selection": {"max_height": 720}}))
    assert [e.code for e in excinfo.value.result.errors] == ["option_not_supported"]


def test_file_video_require_matching_codec_ok(plan_file):
    plan = plan_file(
        file_video_payload(
            {"selection": {"video_codec": {"mode": "require", "value": "h264"}}}
        )
    )
    assert plan.warnings == []


def test_file_video_embed_subtitles_ignored_with_warning(plan_file):
    plan = plan_file(file_video_payload({"processing": {"embed_subtitles": ["en"]}}))
    assert plan.steps[0].params["embed_subtitles"] == []
    warning = next(w for w in plan.warnings if w.path.endswith("embed_subtitles"))
    assert warning.code == "preference_unavailable"


def test_file_audio_format_conversion_rejected(plan_file):
    payload = {
        "schema_version": "1.0",
        "sources": [{"id": "vid", "type": "file", "path": "/input/movie.mp4"}],
        "outputs": [{"id": "a", "type": "audio", "options": {"format": "mp3"}}],
    }
    with pytest.raises(RequestRejected) as excinfo:
        plan_file(payload)
    assert [e.code for e in excinfo.value.result.errors] == ["option_not_supported"]


# --- yt-dlp profile building (provider dialect, pure) ---------------------------


def selection(max_height=None, video=None, audio=None):
    return {"max_height": max_height, "video_codec": video, "audio_codec": audio}


def test_profiles_no_constraints_are_ordered_codecs_then_generic():
    profiles = build_video_profiles(selection())
    # av1, vp9, h264 (priority order), then the generic best-effort selector
    assert profiles == [
        "bv*[vcodec~='^av01']+ba",
        "bv*[vcodec~='^vp0?9']+ba",
        "bv*[vcodec~='^(avc|h264)']+ba",
        "bv*+ba/b",
    ]


def test_profiles_restricted_to_available_codecs():
    profiles = build_video_profiles(selection(), available_codecs=["h264"])
    assert profiles == ["bv*[vcodec~='^(avc|h264)']+ba", "bv*+ba/b"]


def test_profiles_max_height_applied_everywhere():
    profiles = build_video_profiles(
        selection(max_height=1080), available_codecs=["vp9"]
    )
    assert all("[height<=?1080]" in p for p in profiles)


def test_profiles_max_height_still_accepts_an_unknown_height():
    """A ceiling must not become a requirement that the height be reported.

    yt-dlp's strict `[height<=N]` drops formats whose height it does not know,
    so a direct media URL matched no profile at all and the job failed with
    `format_unavailable` — even at HomeTube's permissive 2160 default, which
    reads to a user as "no limit". The `?` keeps the ceiling for formats that
    declare a height and attempts the ones that do not.
    """
    profiles = build_video_profiles(selection(max_height=2160))
    assert profiles, "a ceiling must never eliminate every profile"
    assert all("height<=?2160" in p for p in profiles)
    assert not any("[height<=2160]" in p for p in profiles), (
        "the strict form silently excludes unknown-height formats"
    )


def test_profiles_preferred_codec_comes_first():
    profiles = build_video_profiles(
        selection(video={"mode": "prefer", "value": "h264", "available": True}),
        available_codecs=["av1", "h264"],
    )
    assert profiles[0] == "bv*[vcodec~='^(avc|h264)']+ba"
    assert profiles[-1] == "bv*+ba/b"  # generic fallback still present


def test_profiles_required_codec_is_strict():
    profiles = build_video_profiles(
        selection(video={"mode": "require", "value": "av1", "available": True})
    )
    assert profiles == ["bv*[vcodec~='^av01']+ba"]  # no downgrade, no generic


def test_profiles_unavailable_preference_ignored():
    profiles = build_video_profiles(
        selection(video={"mode": "prefer", "value": "av1", "available": False}),
        available_codecs=["h264"],
    )
    assert profiles == ["bv*[vcodec~='^(avc|h264)']+ba", "bv*+ba/b"]


def test_profiles_audio_codec_filter_applied():
    profiles = build_video_profiles(
        selection(audio={"mode": "prefer", "value": "opus", "available": True}),
        available_codecs=["vp9"],
    )
    assert profiles[0] == "bv*[vcodec~='^vp0?9']+ba[acodec~='^opus']"


def test_player_client_rotation_only_for_youtube():
    from content.providers.ytdlp import _is_youtube

    assert _is_youtube("https://www.youtube.com/watch?v=x")
    assert _is_youtube("https://youtu.be/x")
    assert not _is_youtube("https://example.com/v.mp4")


# --- end-to-end with fakes ------------------------------------------------------


def test_video_job_end_to_end(store, providers, settings):
    service = AnalysisService(store, providers, settings)
    payload = video_payload({"container": "mkv"})
    request = make_request(payload)
    result = submit_generation(
        payload,
        request,
        store=store,
        settings=settings,
        providers=providers,
        analysis_service=service,
    )
    claimed = store.claim_next_queued()
    JobExecutor(store, settings, providers).execute(claimed)

    assert store.get_job(result.job_id)["status"] == "succeeded"
    artifact = store.list_artifacts(result.job_id)[0]
    assert artifact["type"] == "video"
    assert artifact["filename"] == "video_main.mkv"


# --- idempotency race guard (T3) ------------------------------------------------


def test_second_active_job_with_same_key_is_blocked_by_store(store):
    store.create_job({"a": 1}, "required_only", "race-key")
    with pytest.raises(IdempotencyKeyActive):
        store.create_job({"a": 1}, "required_only", "race-key")


def test_key_released_after_terminal_failure(store):
    job_id = store.create_job({"a": 1}, "required_only", "release-key")
    for status in ("validating", "planning", "queued", "running", "failed"):
        store.transition_job(job_id, status)
    # Key released: a new job may claim it.
    store.create_job({"a": 1}, "required_only", "release-key")
