"""Operational layer: schema migrations, retry-as-new-job, SSE streaming,
reuse_existing (content-addressed artifact cache)."""

import sqlite3
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from content.analysis.service import AnalysisService
from content.api.app import create_app
from content.application.collections import attach_collection_runner
from content.application.submit import submit_generation
from content.execution.executor import JobExecutor
from content.persistence.store import Store
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ProviderRegistry
from tests.conftest import (
    FakeProvider,
    FakeSummarizer,
    make_request,
    minimal_payload,
)

# --- migrations -----------------------------------------------------------------

_LEGACY_SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, status TEXT NOT NULL, request TEXT NOT NULL,
    plan_id TEXT NOT NULL DEFAULT '',
    failure_policy TEXT NOT NULL DEFAULT 'required_only',
    idempotency_key TEXT, error TEXT NOT NULL DEFAULT '',
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
);
CREATE TABLE artifacts (
    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, artifact_request_id TEXT NOT NULL,
    type TEXT NOT NULL, filename TEXT NOT NULL, media_type TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0, checksum TEXT NOT NULL DEFAULT '',
    provenance TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE TABLE job_steps (job_id TEXT, step_id TEXT, status TEXT, operation TEXT,
    provider TEXT, error TEXT DEFAULT '', started_at TEXT, finished_at TEXT);
CREATE TABLE job_events (job_id TEXT, sequence INTEGER, type TEXT, timestamp TEXT,
    data TEXT DEFAULT '{}');
CREATE TABLE analyses (id TEXT PRIMARY KEY, resource_key TEXT, payload TEXT,
    created_at TEXT);
"""


def test_legacy_database_is_migrated(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_LEGACY_SCHEMA)
    conn.execute(
        "INSERT INTO jobs (id, status, request, created_at) "
        "VALUES ('job_old', 'failed', '{}', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    store = Store(db_path)  # applies migrations
    row = store.get_job("job_old")
    assert row["retry_of"] == ""  # new column, defaulted
    store.create_job({"a": 1}, "required_only", None, retry_of="job_old")
    assert (
        store.find_reusable_artifact_group("sig", "any") == []
    )  # new columns queryable
    # Migration 3 (ADR 0017): pre-naming artifacts read back with an empty
    # display_filename, and new registrations can carry one.
    store.register_artifact(
        {
            "id": "art_new",
            "job_id": "job_old",
            "artifact_request_id": "audio_main",
            "type": "audio",
            "filename": "audio_main.m4a",
            "display_filename": "My Conference.m4a",
            "media_type": "audio/mp4",
            "size_bytes": 1,
            "checksum": "sha256:x",
        }
    )
    assert store.get_artifact("art_new")["display_filename"] == "My Conference.m4a"

    # idempotent re-open
    Store(db_path)


def test_fresh_database_starts_at_latest_version(tmp_path):
    store = Store(tmp_path / "fresh.db")
    with sqlite3.connect(store.db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version >= 1


# --- shared pipeline fixture ----------------------------------------------------


@pytest.fixture
def pipeline(store, settings):
    # These tests exercise the inter-job cache, so they run with it enabled;
    # by default the cache is off (ADR 0009) and reuse is inert.
    settings = replace(settings, cache_enabled=True)
    fake = FakeProvider()
    registry = ProviderRegistry(
        [fake], processors=[TranscriptProcessor(), FakeSummarizer()]
    )
    service = AnalysisService(store, registry, settings)
    # Collections orchestrate the canonical pipeline (ADR 0019), so the
    # orchestrator belongs on any registry a collection job will run through.
    attach_collection_runner(registry, service, settings)
    executor = JobExecutor(store, settings, registry)

    def run(payload: dict, retry_of: str = "") -> str:
        request = make_request(payload)
        result = submit_generation(
            payload,
            request,
            store=store,
            settings=settings,
            providers=registry,
            analysis_service=service,
            retry_of=retry_of,
        )
        claimed = store.claim_next_queued()
        executor.execute(claimed)
        return result.job_id

    run.fake = fake
    run.store = store
    return run


# --- reuse_existing -------------------------------------------------------------


def test_identical_job_reuses_artifacts(pipeline, settings):
    first = pipeline(minimal_payload())
    assert pipeline.fake.executed_operations == ["media.acquire_audio"]

    second = pipeline(minimal_payload())
    # the provider did NOT run again
    assert pipeline.fake.executed_operations == ["media.acquire_audio"]

    store = pipeline.store
    assert store.get_job(second)["status"] == "succeeded"
    artifact = store.list_artifacts(second)[0]
    attributes = artifact["provenance"]["attributes"]
    original = store.list_artifacts(first)[0]
    assert attributes["reused_from_artifact_id"] == original["id"]
    assert artifact["checksum"] == original["checksum"]
    events = [e for e in store.list_events(second) if e["type"] == "step.succeeded"]
    assert events[0]["data"]["reused_from_job"] == first

    path = settings.data_dir / "jobs" / second / "artifacts" / artifact["filename"]
    assert path.is_file()  # a real copy, not a reference


def test_reuse_is_independent_of_client_output_ids(pipeline):
    pipeline(minimal_payload())
    second = pipeline(
        minimal_payload(outputs=[{"id": "totally_different_id", "type": "audio"}])
    )
    assert pipeline.fake.executed_operations == ["media.acquire_audio"]
    artifact = pipeline.store.list_artifacts(second)[0]
    assert artifact["filename"] == "totally_different_id.m4a"


def test_reuse_existing_false_runs_again(pipeline):
    pipeline(minimal_payload())
    pipeline(minimal_payload(execution={"reuse_existing": False}))
    assert pipeline.fake.executed_operations == [
        "media.acquire_audio",
        "media.acquire_audio",
    ]


def test_corrupt_cache_falls_back_to_execution(pipeline, settings):
    first = pipeline(minimal_payload())
    artifact = pipeline.store.list_artifacts(first)[0]
    (settings.data_dir / "jobs" / first / "artifacts" / artifact["filename"]).unlink()

    second = pipeline(minimal_payload())
    assert pipeline.fake.executed_operations == [
        "media.acquire_audio",
        "media.acquire_audio",
    ]
    assert pipeline.store.get_job(second)["status"] == "succeeded"


def test_transcript_chain_reuses_final_step(pipeline):
    payload = minimal_payload(outputs=[{"id": "transcript", "type": "transcript"}])
    first = pipeline(payload)
    operations_after_first = list(pipeline.fake.executed_operations)

    second = pipeline(payload)
    # the internal acquisition reruns (not cached), but the bound transcript
    # step is reused — no second parsing, artifact copied
    artifact = pipeline.store.list_artifacts(second)[0]
    assert "reused_from_artifact_id" in artifact["provenance"]["attributes"]
    original = pipeline.store.list_artifacts(first)[0]
    assert artifact["checksum"] == original["checksum"]
    assert pipeline.fake.executed_operations == operations_after_first + [
        "media.acquire_subtitles"
    ]


def test_different_options_do_not_reuse(pipeline):
    pipeline(minimal_payload())
    pipeline(
        minimal_payload(
            sources=[{"id": "main", "type": "url", "uri": "https://example.com/other"}]
        )
    )
    assert pipeline.fake.executed_operations == [
        "media.acquire_audio",
        "media.acquire_audio",
    ]


# --- retry ----------------------------------------------------------------------


@pytest.fixture
def client(settings):
    app = create_app(
        settings,
        providers=ProviderRegistry(
            [FakeProvider()], processors=[TranscriptProcessor(), FakeSummarizer()]
        ),
        start_worker=False,
    )
    with TestClient(app) as test_client:
        test_client.app = app
        yield test_client


def run_queued(client) -> None:
    store = client.app.state.store
    claimed = store.claim_next_queued()
    client.app.state.executor.execute(claimed)


def test_retry_creates_new_linked_job(client):
    payload = minimal_payload(
        sources=[
            {"id": "main", "type": "url", "uri": "https://example.com/fail-audio"}
        ],
        execution={"idempotency_key": "retry-key", "reuse_existing": False},
    )
    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]
    run_queued(client)
    assert client.get(f"/api/v1/jobs/{job_id}").json()["status"] == "failed"

    response = client.post(f"/api/v1/jobs/{job_id}/retry")
    assert response.status_code == 201
    new_id = response.json()["job_id"]
    assert new_id != job_id

    new_job = client.get(f"/api/v1/jobs/{new_id}").json()
    assert new_job["retry_of"] == job_id
    events = client.get(f"/api/v1/jobs/{new_id}/events").json()
    assert events[0]["data"] == {"retry_of": job_id}
    # the retried job never carries the original idempotency key
    store = client.app.state.store
    assert store.get_job(new_id)["idempotency_key"] is None
    # original untouched, still terminal
    assert client.get(f"/api/v1/jobs/{job_id}").json()["status"] == "failed"


def test_retry_requires_terminal_job(client):
    job_id = client.post("/api/v1/jobs", json=minimal_payload()).json()["job_id"]
    response = client.post(f"/api/v1/jobs/{job_id}/retry")  # still queued
    assert response.status_code == 409


def test_retry_unknown_job_404(client):
    assert client.post("/api/v1/jobs/nope/retry").status_code == 404


# --- SSE ------------------------------------------------------------------------


def collect_stream(
    client, url: str, headers: dict | None = None
) -> list[tuple[str, str]]:
    events = []
    with client.stream("GET", url, headers=headers or {}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        current_event = ""
        for line in response.iter_lines():
            if line.startswith("event: "):
                current_event = line[len("event: ") :]
            elif line.startswith("id: "):
                events.append(("__id__", line[len("id: ") :]))
            elif line.startswith("data: "):
                events.append((current_event, line[len("data: ") :]))
                if current_event == "stream.end":
                    break
    return events


def test_sse_streams_full_history_then_ends(client):
    job_id = client.post("/api/v1/jobs", json=minimal_payload()).json()["job_id"]
    run_queued(client)

    events = collect_stream(client, f"/api/v1/jobs/{job_id}/events/stream")
    types = [t for t, _ in events if t not in ("", "__id__")]
    assert types[0] == "job.created"
    assert "artifact.created" in types
    assert types[-2] == "job.succeeded"
    assert types[-1] == "stream.end"


def test_sse_resumes_after_last_event_id(client):
    job_id = client.post("/api/v1/jobs", json=minimal_payload()).json()["job_id"]
    run_queued(client)
    total = len(client.get(f"/api/v1/jobs/{job_id}/events").json())

    events = collect_stream(
        client,
        f"/api/v1/jobs/{job_id}/events/stream",
        headers={"Last-Event-ID": str(total - 1)},
    )
    types = [t for t, _ in events if t not in ("", "__id__")]
    assert types == ["job.succeeded", "stream.end"]


def test_sse_unknown_job_404(client):
    assert client.get("/api/v1/jobs/nope/events/stream").status_code == 404


# --- root / API landing ---------------------------------------------------------


def test_root_redirects_to_docs(client):
    # The backend has no UI of its own (ops UI = apps/web-admin); the old /ui
    # page was removed. Root lands on the interactive contract docs.
    assert client.get("/", follow_redirects=False).headers["location"] == "/docs"
    assert client.get("/ui").status_code == 404


def test_system_endpoint_reports_inventory(client):
    body = client.get("/api/v1/system").json()
    assert body["version"]
    assert "cache_enabled" in body
    names = {r["name"] for r in body["runners"]}
    assert "ytdlp" in names  # the fake provider registers under this name


def test_storage_endpoint_reports_families(client):
    body = client.get("/api/v1/storage").json()
    # Five since ADR 0020: uploads are their own lifecycle, and an operator
    # asking "what is using my disk" must be able to see them.
    assert set(body) == {"jobs", "delivery", "tmp", "uploads", "cache"}
    assert "bytes" in body["jobs"] and "count" in body["jobs"]
    assert {"bytes", "count", "ttl_hours"} <= set(body["uploads"])


def test_identical_playlist_reuses_every_entry(pipeline):
    """Re-submitting the same playlist must re-download nothing.

    Single-video reuse was covered; the playlist path (scope each_item) was
    not, and it is the one people re-run — a subscription feed re-submitted to
    pick up new videos would otherwise fetch every old one again. Each entry is
    its own step with its own content-addressed signature, so each is reused
    independently.
    """
    playlist = minimal_payload(
        sources=[
            {"id": "main", "type": "url", "uri": "https://example.com/playlist?list=X"}
        ],
        outputs=[{"id": "vid", "type": "video", "scope": "each_item"}],
    )
    first = pipeline(playlist)
    after_first = list(pipeline.fake.executed_operations)
    assert len(after_first) == 2, "one acquisition per entry on the first run"

    second = pipeline(playlist)
    assert pipeline.fake.executed_operations == after_first, (
        "the provider ran again for a playlist already downloaded"
    )

    store = pipeline.store
    assert store.get_job(second)["status"] == "succeeded"
    original_ids = {a["id"] for a in store.list_artifacts(first)}
    reused = store.list_artifacts(second)
    assert len(reused) == 2
    for artifact in reused:
        attributes = artifact["provenance"]["attributes"]
        assert attributes["reused_from_artifact_id"] in original_ids
