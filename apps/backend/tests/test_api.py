"""API surface tests: hermetic app with the fake provider, worker disabled
(jobs are executed synchronously through the injected executor)."""

import pytest
from fastapi.testclient import TestClient

from content.api.app import create_app
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ProviderRegistry
from tests.conftest import FakeProvider, minimal_payload


@pytest.fixture
def client(settings):
    app = create_app(
        settings,
        providers=ProviderRegistry(
            [FakeProvider()], processors=[TranscriptProcessor()]
        ),
        start_worker=False,
    )
    with TestClient(app) as test_client:
        test_client.app = app
        yield test_client


def run_queued_job(client) -> None:
    store = client.app.state.store
    executor = client.app.state.executor
    claimed = store.claim_next_queued()
    assert claimed is not None
    executor.execute(claimed)


def test_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"database": "ok", "data_dir": "ok"}


def test_health_fails_when_the_database_is_gone(client):
    """The check that makes the container healthcheck worth believing.

    A static 200 reported "ok" with an unmounted volume, so orchestration kept
    routing work to an engine that could not accept a single job.
    """

    def broken_ping():
        raise OSError("unable to open database file")

    client.app.state.store.ping = broken_ping
    response = client.get("/api/v1/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert "unreachable" in body["checks"]["database"]


def test_health_fails_when_the_data_directory_is_not_writable(client, settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.data_dir.chmod(0o500)
    try:
        response = client.get("/api/v1/health")
    finally:
        settings.data_dir.chmod(0o700)
    assert response.status_code == 503
    assert "not writable" in response.json()["checks"]["data_dir"]


def test_health_ignores_missing_optional_tools(client, monkeypatch):
    """ "Not installed" is not "broken".

    No ffmpeg, no yt-dlp, no LLM daemon: those decide which capabilities
    resolve, which /capabilities reports per source. An engine that can still
    read a page is not unhealthy, and restarting it would fix nothing.
    """
    monkeypatch.setattr("shutil.which", lambda _name: None)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_folders_endpoint_lists_root_then_subfolders(client, settings):
    response = client.get("/api/v1/folders")
    assert response.status_code == 200
    assert response.json() == {"folders": [""]}

    root = settings.delivery_dir or (settings.data_dir / "delivery")
    (root / "movies" / "2026").mkdir(parents=True)
    response = client.get("/api/v1/folders")
    assert response.json() == {"folders": ["", "movies", "movies/2026"]}


def test_analysis_endpoint(client):
    response = client.post(
        "/api/v1/analyses",
        json={
            "sources": [{"id": "main", "type": "url", "uri": "https://example.com/v"}]
        },
    )
    assert response.status_code == 200
    body = response.json()
    source = body["sources"][0]
    # Analysis returns facts only (ADR 0013) — capabilities live at /capabilities.
    assert source["resource"]["resource_type"] == "video"
    assert source["media"]["has_audio"] is True
    assert "en" in source["media"]["audio_languages"]
    assert "capabilities" not in source


def test_capabilities_endpoint_lists_available_actions(client):
    response = client.post(
        "/api/v1/capabilities",
        json={
            "sources": [{"id": "main", "type": "url", "uri": "https://example.com/v"}]
        },
    )
    assert response.status_code == 200
    caps = {c["id"]: c["status"] for c in response.json()["sources"][0]["capabilities"]}
    assert caps["audio.download"] == "available"
    assert caps["transcript.generate"] == "derivable"


def test_submit_job_and_download_artifact(client):
    response = client.post("/api/v1/jobs", json=minimal_payload())
    assert response.status_code == 201
    job_id = response.json()["job_id"]
    assert response.json()["status"] == "queued"

    run_queued_job(client)

    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "succeeded"
    assert [step["status"] for step in job["steps"]] == ["succeeded"]

    events = client.get(f"/api/v1/jobs/{job_id}/events").json()
    assert events[-1]["type"] == "job.succeeded"
    tail = client.get(
        f"/api/v1/jobs/{job_id}/events",
        params={"after_sequence": events[-2]["sequence"]},
    ).json()
    assert [event["type"] for event in tail] == ["job.succeeded"]

    artifacts = client.get(f"/api/v1/jobs/{job_id}/artifacts").json()
    assert len(artifacts) == 1
    artifact_id = artifacts[0]["id"]

    meta = client.get(f"/api/v1/artifacts/{artifact_id}").json()
    assert meta["media_type"] == "audio/mp4"

    content = client.get(f"/api/v1/artifacts/{artifact_id}/content")
    assert content.status_code == 200
    assert content.content == b"fake-audio-bytes"


def test_structural_error_shape(client):
    payload = minimal_payload(
        outputs=[{"id": "audio", "type": "audio", "from_sources": ["missing"]}]
    )
    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["valid"] is False
    assert detail["errors"][0]["code"] == "unknown_source_reference"
    assert detail["errors"][0]["path"] == "outputs[0].from_sources[0]"


def test_feasibility_error_for_reserved_type(client):
    # `ocr` stands in for "declared but not implemented". keyframes used to
    # play this role and became executable in prompt 11 — the reserved list is
    # meant to shrink, so this fixture names a type that is still on it.
    payload = minimal_payload(outputs=[{"id": "k", "type": "ocr"}])
    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["phase"] == "feasibility"
    assert detail["errors"][0]["code"] == "output_type_not_supported"


def test_idempotency_conflict_maps_to_409(client):
    payload = minimal_payload(execution={"idempotency_key": "key-1"})
    first = client.post("/api/v1/jobs", json=payload)
    assert first.status_code == 201

    replay = client.post("/api/v1/jobs", json=payload)
    assert replay.status_code == 200
    assert replay.json()["job_id"] == first.json()["job_id"]

    conflicting = minimal_payload(
        outputs=[{"id": "meta", "type": "metadata"}],
        execution={"idempotency_key": "key-1"},
    )
    response = client.post("/api/v1/jobs", json=conflicting)
    assert response.status_code == 409


def test_cancel_queued_job(client):
    response = client.post("/api/v1/jobs", json=minimal_payload())
    job_id = response.json()["job_id"]
    assert client.post(f"/api/v1/jobs/{job_id}/cancel").status_code == 200
    assert client.get(f"/api/v1/jobs/{job_id}").json()["status"] == "cancelled"


def test_unknown_job_and_artifact_return_404(client):
    assert client.get("/api/v1/jobs/nope").status_code == 404
    assert client.get("/api/v1/artifacts/nope").status_code == 404


def test_a_malformed_body_uses_the_same_422_shape_as_the_engine(client):
    """One 422 body, not two (D-09).

    Pydantic used to answer a schema failure with its own `[{type, loc, msg}]`
    list while every engine rejection used `{valid, errors:[{code, path,
    message}]}` — one status code, two formats, told apart only by looking.
    """
    engine_rejection = client.post(
        "/api/v1/jobs",
        json=minimal_payload(
            outputs=[{"id": "audio", "type": "audio", "from_sources": ["nope"]}]
        ),
    )
    schema_rejection = client.post(
        "/api/v1/jobs",
        json={"schema_version": "1.0", "sources": "not-a-list", "outputs": []},
    )
    assert engine_rejection.status_code == 422
    assert schema_rejection.status_code == 422

    for response in (engine_rejection, schema_rejection):
        detail = response.json()["detail"]
        assert detail["valid"] is False
        assert detail["errors"], "every 422 lists its errors"
        for error in detail["errors"]:
            assert set(error) >= {"code", "path", "message"}
            assert error["code"], "error codes are stable machine identifiers"

    schema_errors = schema_rejection.json()["detail"]["errors"]
    assert all(e["code"] == "schema_violation" for e in schema_errors)
    # The path is the contract's own dotted form, not Pydantic's ("body", …) tuple.
    assert any(e["path"] == "sources" for e in schema_errors), schema_errors


def test_a_nested_schema_violation_reports_a_dotted_indexed_path(client):
    response = client.post(
        "/api/v1/jobs",
        json={
            "schema_version": "1.0",
            "sources": [{"id": "a", "type": "url", "uri": "https://x/v"}],
            "outputs": [{"id": "v", "type": "video", "options": {"container": 7}}],
        },
    )
    assert response.status_code == 422
    paths = [e["path"] for e in response.json()["detail"]["errors"]]
    assert any(p.startswith("outputs[0].options") for p in paths), paths


def test_an_unimplementable_combination_refuses_instead_of_crashing(settings):
    """No valid request may answer 500.

    Feasibility is checked rule by rule before the builder runs, so a
    combination nobody enumerated slipped through: a `text` source asking for
    `metadata` reached the builder and escaped as `UnknownTransformation` — an
    internal name for "no runner implements this", surfaced to the caller as a
    crash. It is a feasibility answer and is now returned as one.
    """
    # The real registry: the fake provider implements everything, so it cannot
    # reproduce "no runner for this operation".
    app = create_app(settings, start_worker=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/jobs",
            json={
                "schema_version": "1.0",
                "sources": [{"id": "t", "type": "text", "content": "hello"}],
                "outputs": [{"id": "o", "type": "metadata"}],
            },
        )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["phase"] == "feasibility"
    assert [e["code"] for e in detail["errors"]] == ["capability_unavailable"]
