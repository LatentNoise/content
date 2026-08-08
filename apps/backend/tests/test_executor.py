"""End-to-end execution through the real pipeline with the fake provider."""

import pytest

from content.analysis.service import AnalysisService
from content.application.submit import submit_generation
from content.execution.executor import JobExecutor
from tests.conftest import make_request, minimal_payload


@pytest.fixture
def pipeline(store, providers, settings):
    analysis_service = AnalysisService(store, providers, settings)
    executor = JobExecutor(store, settings, providers)

    def submit_and_run(payload: dict) -> str:
        request = make_request(payload)
        result = submit_generation(
            payload,
            request,
            store=store,
            settings=settings,
            providers=providers,
            analysis_service=analysis_service,
        )
        claimed = store.claim_next_queued()
        assert claimed is not None and claimed["id"] == result.job_id
        executor.execute(claimed)
        return result.job_id

    return submit_and_run


def test_audio_job_succeeds_with_artifact_and_events(pipeline, store, settings):
    job_id = pipeline(minimal_payload())

    job = store.get_job(job_id)
    assert job["status"] == "succeeded"

    artifacts = store.list_artifacts(job_id)
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact["artifact_request_id"] == "audio_main"
    assert artifact["checksum"].startswith("sha256:")
    assert artifact["provenance"]["producer"]["operation"] == "media.acquire_audio"
    path = settings.data_dir / "jobs" / job_id / "artifacts" / artifact["filename"]
    assert path.is_file() and path.read_bytes() == b"fake-audio-bytes"

    events = store.list_events(job_id)
    types = [event["type"] for event in events]
    assert types == [
        "job.created",
        "job.validating",
        "job.planned",
        "job.queued",
        "job.started",
        "step.started",
        "artifact.created",
        "step.succeeded",
        "job.succeeded",
    ]
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))

    # working files are purged, artifacts are kept
    assert not any((settings.data_dir / "jobs" / job_id / "work").iterdir())


def test_optional_failure_yields_partial_success(pipeline, store):
    payload = minimal_payload(
        sources=[
            {"id": "main", "type": "url", "uri": "https://example.com/fail-thumbnail"}
        ],
        outputs=[
            {"id": "audio", "type": "audio"},
            {"id": "thumb", "type": "thumbnail", "required": False},
        ],
    )
    job_id = pipeline(payload)
    assert store.get_job(job_id)["status"] == "partially_succeeded"
    steps = {s["step_id"]: s["status"] for s in store.list_steps(job_id)}
    assert steps["acquire_audio_audio"] == "succeeded"
    assert steps["acquire_thumbnail_thumb"] == "failed"


def test_required_failure_fails_job_but_produces_optional_artifacts(pipeline, store):
    payload = minimal_payload(
        sources=[
            {"id": "main", "type": "url", "uri": "https://example.com/fail-audio"}
        ],
        outputs=[
            {"id": "audio", "type": "audio", "required": True},
            {"id": "meta", "type": "metadata", "required": False},
        ],
    )
    job_id = pipeline(payload)
    assert store.get_job(job_id)["status"] == "failed"
    produced = {a["artifact_request_id"] for a in store.list_artifacts(job_id)}
    assert produced == {"meta"}  # required_only keeps going


def test_fail_fast_skips_remaining_steps(pipeline, store):
    payload = minimal_payload(
        sources=[
            {"id": "main", "type": "url", "uri": "https://example.com/fail-audio"}
        ],
        outputs=[
            {"id": "audio", "type": "audio", "required": True},
            {"id": "meta", "type": "metadata", "required": False},
        ],
        execution={"failure_policy": "fail_fast"},
    )
    job_id = pipeline(payload)
    assert store.get_job(job_id)["status"] == "failed"
    steps = {s["step_id"]: s["status"] for s in store.list_steps(job_id)}
    assert steps["acquire_audio_audio"] == "failed"
    assert steps["export_meta"] == "skipped"
    assert store.list_artifacts(job_id) == []


def test_subtitles_produce_one_artifact_per_language(pipeline, store):
    payload = minimal_payload(
        outputs=[
            {
                "id": "subs",
                "type": "subtitles",
                "options": {"languages": ["en", "fr"]},
            }
        ]
    )
    job_id = pipeline(payload)
    assert store.get_job(job_id)["status"] == "succeeded"
    artifacts = store.list_artifacts(job_id)
    assert len(artifacts) == 2
    languages = {a["provenance"]["attributes"]["language"] for a in artifacts}
    assert languages == {"en", "fr"}
    filenames = {a["filename"] for a in artifacts}
    assert filenames == {"subs.en.srt", "subs.fr.srt"}


def test_idempotent_resubmission_returns_same_job(store, providers, settings):
    analysis_service = AnalysisService(store, providers, settings)
    payload = minimal_payload(execution={"idempotency_key": "client-1"})
    request = make_request(payload)
    kwargs = dict(
        store=store,
        settings=settings,
        providers=providers,
        analysis_service=analysis_service,
    )
    first = submit_generation(payload, request, **kwargs)
    second = submit_generation(payload, make_request(payload), **kwargs)
    assert first.created and not second.created
    assert first.job_id == second.job_id

    conflicting = minimal_payload(
        outputs=[{"id": "other", "type": "metadata"}],
        execution={"idempotency_key": "client-1"},
    )
    from content.domain.errors import RequestRejected

    with pytest.raises(RequestRejected) as excinfo:
        submit_generation(conflicting, make_request(conflicting), **kwargs)
    assert excinfo.value.result.errors[0].code == "idempotency_conflict"
