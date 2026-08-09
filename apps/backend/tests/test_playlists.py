"""Playlists as collection resources (02a — analysis).

A playlist URL analyses to a `collection` resource that exposes its flat member
listing. Per-item execution (scope each_item) is a later slice; here we only
prove detection + entries exposure, and that a single-item output over a
collection is rejected cleanly rather than crashing.
"""

import pytest

from content.analysis.service import AnalysisService
from content.domain.errors import RequestRejected
from content.planning.planner import build_plan
from tests.conftest import make_request, minimal_payload


@pytest.fixture
def analyze(store, providers, settings):
    service = AnalysisService(store, providers, settings)
    return lambda request: service.analyze_sources(list(request.sources))


def _playlist_sources() -> list[dict]:
    return [{"id": "main", "type": "url", "uri": "https://example.com/playlist?list=X"}]


def test_playlist_analyses_to_collection_with_entries(analyze):
    request = make_request(minimal_payload(sources=_playlist_sources()))
    analysis = analyze(request)
    entry = analysis.sources[0]
    assert entry.resource.resource_type == "collection"
    assert [e.id for e in entry.entries] == ["v1", "v2"]
    assert len(entry.entries) == 2


def test_collection_entries_exposed_over_api(client):
    body = client.post("/api/v1/analyses", json={"sources": _playlist_sources()}).json()
    source = body["sources"][0]
    assert source["resource"]["resource_type"] == "collection"
    assert [e["title"] for e in source["entries"]] == ["First", "Second"]


def test_single_video_still_analyses_as_video(analyze):
    request = make_request(minimal_payload())  # non-playlist URL
    entry = analyze(request).sources[0]
    assert entry.resource.resource_type == "video"
    assert entry.entries == []


def test_single_item_output_over_collection_is_rejected(analyze, providers, settings):
    request = make_request(
        minimal_payload(
            sources=_playlist_sources(),
            outputs=[{"id": "video_main", "type": "video"}],
        )
    )
    with pytest.raises(RequestRejected) as excinfo:
        build_plan(request, analyze(request), providers, settings)
    # honest: the collection can't yield a single video yet (each_item is next)
    assert excinfo.value.result.phase == "feasibility"


# --- each_item execution (02b) -------------------------------------------------


def _each_item_video_payload() -> dict:
    return minimal_payload(
        sources=_playlist_sources(),
        outputs=[{"id": "vid", "type": "video", "scope": "each_item"}],
    )


def test_each_item_expands_one_step_per_entry(analyze, providers, settings):
    from content.planning.planner import build_plan

    request = make_request(_each_item_video_payload())
    plan = build_plan(request, analyze(request), providers, settings)
    acquire = [s for s in plan.steps if s.operation == "media.acquire_video"]
    assert len(acquire) == 2  # one per playlist entry
    # all bound to the single output
    assert {b.artifact_request_id for b in plan.output_bindings} == {"vid"}
    assert len(plan.output_bindings) == 2
    assert {s.params["item_label"] for s in acquire} == {"001-first", "002-second"}


def test_each_item_execution_yields_one_artifact_per_entry(pipeline, store, settings):
    job_id = pipeline(_each_item_video_payload())
    assert store.get_job(job_id)["status"] == "succeeded"
    artifacts = store.list_artifacts(job_id)
    assert len(artifacts) == 2
    names = sorted(a["filename"] for a in artifacts)
    assert names == ["vid-001-first.mp4", "vid-002-second.mp4"]


def test_each_item_requires_a_collection_source(analyze, providers, settings):
    from content.planning.planner import build_plan

    request = make_request(  # single video URL, not a playlist
        minimal_payload(outputs=[{"id": "v", "type": "video", "scope": "each_item"}])
    )
    with pytest.raises(RequestRejected) as excinfo:
        build_plan(request, analyze(request), providers, settings)
    assert "scope_not_supported" in [e.code for e in excinfo.value.result.errors]


def test_each_item_rejects_unsupported_output_type(analyze, providers, settings):
    from content.planning.planner import build_plan

    request = make_request(
        minimal_payload(
            sources=_playlist_sources(),
            outputs=[
                {
                    "id": "s",
                    "type": "subtitles",
                    "scope": "each_item",
                    "options": {"languages": ["en"]},
                }
            ],
        )
    )
    with pytest.raises(RequestRejected) as excinfo:
        build_plan(request, analyze(request), providers, settings)
    assert "option_not_supported" in [e.code for e in excinfo.value.result.errors]


@pytest.fixture
def pipeline(store, providers, settings):
    from content.analysis.service import AnalysisService
    from content.application.submit import submit_generation
    from content.execution.executor import JobExecutor

    service = AnalysisService(store, providers, settings)
    executor = JobExecutor(store, settings, providers)

    def run(payload: dict) -> str:
        request = make_request(payload)
        result = submit_generation(
            payload,
            request,
            store=store,
            settings=settings,
            providers=providers,
            analysis_service=service,
        )
        executor.execute(store.claim_next_queued())
        return result.job_id

    return run


@pytest.fixture
def client(settings):
    from fastapi.testclient import TestClient

    from content.api.app import create_app
    from content.processors.transcript import TranscriptProcessor
    from content.providers.base import ProviderRegistry
    from tests.conftest import FakeProvider

    app = create_app(
        settings,
        providers=ProviderRegistry(
            [FakeProvider()], processors=[TranscriptProcessor()]
        ),
        start_worker=False,
    )
    with TestClient(app) as test_client:
        yield test_client


def test_each_item_carries_language_intent_to_every_entry(analyze, providers, settings):
    """Subtitles and audio-track preferences must survive the each_item
    expansion, for every entry.

    A playlist's items are listed, never probed, so the planner cannot check a
    language against a track list the way it does for a single video — it
    passes the intent through optimistically (the same contract its codec
    preferences already use) and the provider resolves per entry. HomeTube had
    stopped *sending* these for playlists, which is what made every downloaded
    item arrive with one audio track and no subtitles.
    """
    request = make_request(
        minimal_payload(
            sources=_playlist_sources(),
            outputs=[
                {
                    "id": "vid",
                    "type": "video",
                    "scope": "each_item",
                    "options": {
                        "selection": {"audio_languages": ["fr", "en"]},
                        "processing": {"embed_subtitles": ["en", "es"]},
                    },
                }
            ],
        )
    )
    plan = build_plan(request, analyze(request), providers, settings)
    acquire = [s for s in plan.steps if s.operation == "media.acquire_video"]
    assert len(acquire) == 2  # one per entry, and both carry the intent
    for step in acquire:
        assert step.params["selection"]["audio_languages"] == ["fr", "en"]
        assert step.params["embed_subtitles"] == ["en", "es"]


def test_each_item_language_intent_reaches_the_ytdlp_arguments(
    analyze, providers, settings
):
    """The end of the chain: the profile ladder asks for each requested audio
    language and still ends in a language-free fallback, so an entry that has
    none of them keeps its best audio instead of failing."""
    from content.providers.ytdlp import build_video_profiles, embedding_args

    request = make_request(
        minimal_payload(
            sources=_playlist_sources(),
            outputs=[
                {
                    "id": "vid",
                    "type": "video",
                    "scope": "each_item",
                    "options": {
                        "selection": {"audio_languages": ["fr", "en"]},
                        "processing": {"embed_subtitles": ["en"]},
                    },
                }
            ],
        )
    )
    plan = build_plan(request, analyze(request), providers, settings)
    step = next(s for s in plan.steps if s.operation == "media.acquire_video")

    selection = step.params["selection"]
    profiles = build_video_profiles(
        selection, step.params.get("available_video_codecs")
    )
    assert any("[language^=fr]" in p for p in profiles)
    assert any("[language^=en]" in p for p in profiles)
    # The safety net: a plain profile with no language filter, last.
    assert any("language^=" not in p for p in profiles)

    args = embedding_args(step.params)
    assert "--embed-subs" in args
    assert args[args.index("--sub-langs") + 1] == "en"
