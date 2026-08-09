"""Playlists as collection resources (02a — analysis).

A playlist URL analyses to a `collection` resource that exposes its flat member
listing. Per-item execution (scope each_item) is a later slice; here we only
prove detection + entries exposure, and that a single-item output over a
collection is rejected cleanly rather than crashing.
"""

import pytest

from content.analysis.service import AnalysisService
from content.domain.errors import RequestRejected
from content.domain.request import GenerationRequest
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


def test_each_item_emits_one_member_step_per_entry(analyze, providers, settings):
    """The collection plan orchestrates; it does not acquire.

    Before ADR 0019 this produced `media.acquire_video` steps built from
    invented parameters. It now produces one `collection.member` step per
    member, and how that member is produced is resolved by the canonical
    pipeline when it runs.
    """
    request = make_request(_each_item_video_payload())
    plan = build_plan(request, analyze(request), providers, settings)
    members = [s for s in plan.steps if s.operation == "collection.member"]
    assert len(members) == 2
    assert not [s for s in plan.steps if s.operation.startswith("media.acquire")], (
        "the collection itself must not plan acquisitions"
    )
    assert {b.artifact_request_id for b in plan.output_bindings} == {"vid"}
    assert len(plan.output_bindings) == 2
    assert {s.params["item_label"] for s in members} == {"001-first", "002-second"}
    # The ordinal is the collection's own index, carried for naming and
    # provenance rather than parsed back out of a filename later.
    assert sorted(s.params["member_index"] for s in members) == [1, 2]


def test_the_member_request_is_what_that_video_alone_would_have_been(
    analyze, providers, settings
):
    """The derivation is the whole abstraction: a member request is the same
    request with the collection source swapped for the member and the scope
    dropped. Options survive untouched — which is why languages, subtitles and
    SponsorBlock need no collection-specific handling any more."""
    payload = minimal_payload(
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
    request = make_request(payload)
    plan = build_plan(request, analyze(request), providers, settings)
    member = next(s for s in plan.steps if s.operation == "collection.member")

    derived = GenerationRequest.model_validate(member.params["member_request"])
    assert [s.uri for s in derived.sources] == [member.params["member_uri"]]
    assert derived.sources[0].type == "url"
    assert len(derived.outputs) == 1
    output = derived.outputs[0]
    assert output.scope == "single", "the fan-out instruction must not recurse"
    assert output.options.selection.audio_languages == ["fr", "en"]
    assert output.options.processing.embed_subtitles == ["en", "es"]


def test_a_member_is_planned_from_its_own_facts(analyze, providers, settings):
    """The acceptance criterion: a member behaves like that video submitted
    alone. Planning the derived request must give exactly the plan the same
    single video gives — same operations, same resolved parameters."""
    request = make_request(_each_item_video_payload())
    plan = build_plan(request, analyze(request), providers, settings)
    member = next(s for s in plan.steps if s.operation == "collection.member")
    derived = GenerationRequest.model_validate(member.params["member_request"])

    member_plan = build_plan(derived, analyze(derived), providers, settings)
    alone = make_request(
        minimal_payload(
            sources=[{"id": "main", "type": "url", "uri": member.params["member_uri"]}],
            outputs=[{"id": "vid", "type": "video"}],
        )
    )
    alone_plan = build_plan(alone, analyze(alone), providers, settings)

    assert [s.operation for s in member_plan.steps] == [
        s.operation for s in alone_plan.steps
    ]
    assert [s.params for s in member_plan.steps] == [s.params for s in alone_plan.steps]


def test_each_item_is_not_restricted_to_video_and_audio(analyze, providers, settings):
    """ADR 0019 removes the video/audio-only rule: it was a property of the
    guessing code (the only pair whose parameters could be invented), never of
    the domain. Any output the single-resource pipeline supports is meaningful
    per member."""
    request = make_request(
        minimal_payload(
            sources=_playlist_sources(),
            outputs=[{"id": "subs", "type": "subtitles", "scope": "each_item"}],
        )
    )
    plan = build_plan(request, analyze(request), providers, settings)
    members = [s for s in plan.steps if s.operation == "collection.member"]
    assert len(members) == 2
    derived = GenerationRequest.model_validate(members[0].params["member_request"])
    assert derived.outputs[0].type == "subtitles"


def test_each_item_requires_a_collection_source(analyze, providers, settings):
    from content.planning.planner import build_plan

    request = make_request(  # single video URL, not a playlist
        minimal_payload(outputs=[{"id": "v", "type": "video", "scope": "each_item"}])
    )
    with pytest.raises(RequestRejected) as excinfo:
        build_plan(request, analyze(request), providers, settings)
    assert "scope_not_supported" in [e.code for e in excinfo.value.result.errors]


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


def test_language_intent_reaches_every_member_unchanged(analyze, providers, settings):
    """Subtitles and audio-track preferences reach each member — but now by
    travelling in the member's own request rather than through a parallel
    parameter builder, so they are intersected against that member's real
    tracks exactly as for a single video."""
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
    members = [s for s in plan.steps if s.operation == "collection.member"]
    assert len(members) == 2
    for member in members:
        derived = GenerationRequest.model_validate(member.params["member_request"])
        options = derived.outputs[0].options
        assert options.selection.audio_languages == ["fr", "en"]
        assert options.processing.embed_subtitles == ["en", "es"]

    # And the member's own plan resolves them against its facts, which is the
    # whole point: the same code path a single video uses.
    derived = GenerationRequest.model_validate(members[0].params["member_request"])
    member_plan = build_plan(derived, analyze(derived), providers, settings)
    acquire = next(s for s in member_plan.steps if s.operation == "media.acquire_video")
    assert "audio_languages" in acquire.params["selection"]


def test_each_item_execution_yields_one_artifact_per_entry(pipeline, store, settings):
    """End to end: every member goes through the canonical pipeline and lands
    as its own artifact, named by the naming engine with the collection's
    ordinal in front."""
    job_id = pipeline(_each_item_video_payload())
    assert store.get_job(job_id)["status"] == "succeeded"
    artifacts = store.list_artifacts(job_id)
    assert len(artifacts) == 2

    names = sorted(a["display_filename"] for a in artifacts)
    assert names == ["001 - First.mp4", "002 - Second.mp4"], (
        "the ordinal prefixes the member's own title, zero-padded to 3"
    )


def test_member_artifacts_are_attributable_without_parsing_the_filename(
    pipeline, store
):
    """Artifacts of one collection output share an artifact_request_id, so
    provenance has to say which concrete member each came from — and where it
    sat in the collection."""
    job_id = pipeline(_each_item_video_payload())
    artifacts = store.list_artifacts(job_id)
    by_index = {
        a["provenance"]["attributes"]["member_index"]: a["provenance"]["attributes"]
        for a in artifacts
    }
    assert sorted(by_index) == [1, 2]
    assert by_index[1]["member_uri"] == "https://x/v1"
    assert by_index[2]["member_uri"] == "https://x/v2"
    for attributes in by_index.values():
        assert attributes["collection_source_id"] == "main"
        assert attributes["member_resource_key"], "the member's own resource identity"


def test_job_steps_expose_member_context_for_progress(pipeline, client):
    """A collection job's steps carry the member's title and ordinal, so a
    client can render "1/2 · First — 73%" instead of a step-id hash. The step
    table stores execution state only; the API joins the presentation context
    from the plan snapshot for every client at once."""
    job_id = pipeline(_each_item_video_payload())
    steps = client.get(f"/api/v1/jobs/{job_id}").json()["steps"]
    by_title = {step.get("item_title"): step for step in steps}
    assert set(by_title) == {"First", "Second"}
    assert by_title["First"]["member_index"] == 1
    assert by_title["First"]["member_total"] == 2
    assert by_title["Second"]["member_index"] == 2


def test_members_get_isolated_workdirs(pipeline, store, monkeypatch):
    """No two members may share a working directory.

    A member plan's step ids repeat identically for every member, so its files
    (``video-acquire_video_….mkv``) collide by name. Sequential execution only
    masks that; the moment members run concurrently a shared directory makes
    them overwrite each other mid-flight. The outer collection step id is
    unique per member and must key the isolation.
    """
    from tests.conftest import FakeProvider

    original = FakeProvider.execute
    seen: dict[str, object] = {}

    def record_workdir(self, step, ctx):
        seen[step.params.get("uri", "")] = ctx.workdir
        return original(self, step, ctx)

    monkeypatch.setattr(FakeProvider, "execute", record_workdir)

    job_id = pipeline(_each_item_video_payload())
    assert store.get_job(job_id)["status"] == "succeeded"

    workdirs = list(seen.values())
    assert len(workdirs) == 2
    assert len(set(workdirs)) == 2, "two members wrote to the same directory"
    # Isolation nests inside the job workdir under the member's own step id,
    # so the layout stays attributable per member.
    names = sorted(path.name for path in workdirs)
    assert all(name.startswith("member_vid_") for name in names), names
    parents = {path.parent for path in workdirs}
    assert len(parents) == 1, "member workdirs must share the job workdir"


def test_one_incapable_member_does_not_spoil_the_others(pipeline, store, monkeypatch):
    """A heterogeneous playlist: one member cannot satisfy the requested
    output. It gets the ordinary structured failure for that member, and the
    members that can are produced normally — no optimistic guessing, no
    collection-wide abort."""
    from tests.conftest import FakeProvider

    original = FakeProvider.analyze

    def analyze_with_one_audio_only(self, source, ctx):
        analysis = original(self, source, ctx)
        # The second member is audio-only, so a video output is genuinely
        # impossible for it — and only for it.
        if source.uri.endswith("/v2"):
            analysis.media.has_video = False
            analysis.media.video_heights = []
            analysis.media.video_codecs = []
        return analysis

    monkeypatch.setattr(FakeProvider, "analyze", analyze_with_one_audio_only)

    job_id = pipeline(_each_item_video_payload())

    # The output produced *something*, so the job succeeds: the aggregate rule
    # counts outputs, not members. What the collection owes the caller is the
    # per-member truth, and that is in the events below.
    assert store.get_job(job_id)["status"] == "succeeded"

    artifacts = store.list_artifacts(job_id)
    assert len(artifacts) == 1, "the capable member is still produced"
    assert artifacts[0]["provenance"]["attributes"]["member_index"] == 1

    failed = [
        event for event in store.list_events(job_id) if event["type"] == "step.failed"
    ]
    assert len(failed) == 1
    assert failed[0]["data"]["code"] == "member_not_feasible"
    # The reason is the member's own, structured — not a collection-level guess.
    assert failed[0]["data"]["details"]["member_uri"] == "https://x/v2"
