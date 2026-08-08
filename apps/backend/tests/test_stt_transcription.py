"""Speech-to-text activation (prompt 06, ADR 0013 R2/R7 in action).

The `transcript.from_audio` / `summary.from_audio` variants were declared long
before any STT runner existed. These tests prove the two worlds:

- WITHOUT an STT runner: sources without subtitles keep answering
  `unavailable / implementation_unavailable: audio.transcribe` (unchanged);
- WITH one (the hermetic FakeStt): the same sources become transcribable and
  summarizable — no catalog change, only the implementation inventory.
"""

import pytest
from fastapi.testclient import TestClient

from content.api.app import create_app
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ProviderRegistry
from tests.conftest import FakeProvider, FakeStt, FakeSummarizer, minimal_payload

NOSUBS_URI = "https://example.com/nosubs-podcast"


def _registry(with_stt: bool) -> ProviderRegistry:
    processors = [TranscriptProcessor(), FakeSummarizer()]
    if with_stt:
        processors.append(FakeStt())
    return ProviderRegistry([FakeProvider()], processors=processors)


@pytest.fixture
def client_no_stt(settings):
    app = create_app(settings, providers=_registry(False), start_worker=False)
    with TestClient(app) as tc:
        tc.app = app
        yield tc


@pytest.fixture
def client_stt(settings):
    app = create_app(settings, providers=_registry(True), start_worker=False)
    with TestClient(app) as tc:
        tc.app = app
        yield tc


def _capabilities(client, uri=NOSUBS_URI) -> dict:
    response = client.post(
        "/api/v1/capabilities",
        json={"sources": [{"id": "main", "type": "url", "uri": uri}]},
    )
    assert response.status_code == 200, response.text
    return {c["id"]: c for c in response.json()["sources"][0]["capabilities"]}


def _run_queued(client) -> None:
    store = client.app.state.store
    claimed = store.claim_next_queued()
    assert claimed is not None
    client.app.state.executor.execute(claimed)


def _submit(client, outputs, uri=NOSUBS_URI):
    payload = minimal_payload(
        sources=[{"id": "main", "type": "url", "uri": uri}], outputs=outputs
    )
    return client.post("/api/v1/jobs", json=payload)


# --- resolution: the two worlds -------------------------------------------------


def test_without_stt_nosubs_source_stays_unavailable(client_no_stt):
    caps = _capabilities(client_no_stt)
    transcript = caps["transcript.generate"]
    assert transcript["status"] == "unavailable"
    assert transcript["reason"]["code"] == "implementation_unavailable"
    assert "audio.transcribe" in transcript["reason"]["missing_operations"]
    assert caps["summary.generate"]["status"] == "unavailable"


def test_with_stt_nosubs_source_becomes_derivable(client_stt):
    caps = _capabilities(client_stt)
    transcript = caps["transcript.generate"]
    assert transcript["status"] == "derivable"
    assert transcript["selected_variant"] == "transcript.from_audio"
    assert transcript["derived_from"] == ["audio"]
    summary = caps["summary.generate"]
    assert summary["status"] == "derivable"
    assert summary["selected_variant"] == "summary.from_audio"


def test_with_stt_subtitled_source_still_prefers_subtitles(client_stt):
    caps = _capabilities(client_stt, uri="https://example.com/video")
    assert caps["transcript.generate"]["selected_variant"] == (
        "transcript.from_subtitles"
    )


# --- planning + execution -------------------------------------------------------


def test_transcript_job_runs_the_audio_chain(client_stt):
    response = _submit(
        client_stt, [{"id": "t", "type": "transcript", "required": True}]
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]
    _run_queued(client_stt)
    job = client_stt.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "succeeded", job
    operations = {s["operation"] for s in job["steps"]}
    assert "media.acquire_audio" in operations
    assert "audio.transcribe" in operations
    assert "media.acquire_subtitles" not in operations
    artifacts = client_stt.get(f"/api/v1/jobs/{job_id}/artifacts").json()
    transcript = next(a for a in artifacts if a["type"] == "transcript")
    attrs = transcript["provenance"]["attributes"]
    assert attrs["derived_from"] == "audio"
    assert attrs["model"] == "fake-stt-model"


def test_summary_job_synthesizes_the_audio_chain(client_stt):
    response = _submit(client_stt, [{"id": "s", "type": "summary", "required": True}])
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]
    _run_queued(client_stt)
    job = client_stt.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "succeeded", job
    operations = {s["operation"] for s in job["steps"]}
    assert {"media.acquire_audio", "audio.transcribe", "text.summarize"} <= operations
    artifacts = client_stt.get(f"/api/v1/jobs/{job_id}/artifacts").json()
    assert any(a["type"] == "summary" for a in artifacts)


def test_forced_speech_to_text_on_subtitled_source(client_stt):
    response = _submit(
        client_stt,
        [
            {
                "id": "t",
                "type": "transcript",
                "required": True,
                "options": {"source": "speech_to_text"},
            }
        ],
        uri="https://example.com/video",
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]
    job = client_stt.get(f"/api/v1/jobs/{job_id}").json()
    operations = {s["operation"] for s in job["steps"]}
    assert "audio.transcribe" in operations
    assert "media.acquire_subtitles" not in operations


def test_forced_speech_to_text_without_runner_still_rejected(client_no_stt):
    response = _submit(
        client_no_stt,
        [
            {
                "id": "t",
                "type": "transcript",
                "required": True,
                "options": {"source": "speech_to_text"},
            }
        ],
        uri="https://example.com/video",
    )
    assert response.status_code == 422
    codes_seen = {e["code"] for e in response.json()["detail"]["errors"]}
    assert "option_not_supported" in codes_seen


def test_transcript_from_bound_audio_output(client_stt):
    response = _submit(
        client_stt,
        [
            {"id": "a", "type": "audio", "required": True},
            {
                "id": "t",
                "type": "transcript",
                "required": True,
                "from_outputs": ["a"],
            },
        ],
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]
    _run_queued(client_stt)
    job = client_stt.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "succeeded", job
    artifacts = client_stt.get(f"/api/v1/jobs/{job_id}/artifacts").json()
    assert {a["type"] for a in artifacts} >= {"audio", "transcript"}
