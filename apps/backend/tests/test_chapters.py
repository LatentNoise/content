"""chapters.generate (prompt 09): source-declared facts first, LLM derivation
second — two honest variants, one canonical artifact.

Facts discipline (ADR 0013): the provider *describes* chapters; the resolver
selects from_source when they exist, from_transcript otherwise. The LLM path is
strictly validated — a wandering model is a clean step failure, never an
invalid artifact.
"""

import json

import pytest
from fastapi.testclient import TestClient

from content.api.app import create_app
from content.processors.chapters import (
    ChaptersProcessor,
    serialize_chapters,
    validate_chapters,
)
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ProviderRegistry
from tests.conftest import FakeProvider, FakeSummarizer, minimal_payload

CHAPTERED = "https://example.com/video-with-chapters"
PLAIN = "https://example.com/video"


# --- strict validation ----------------------------------------------------------


def test_validate_accepts_canonical_chapters():
    data = [{"start": 0, "end": 5, "title": "A"}, {"start": 5, "end": 9, "title": "B"}]
    assert [c["title"] for c in validate_chapters(data, 10)] == ["A", "B"]


@pytest.mark.parametrize(
    "bad",
    [
        [],  # empty
        [{"start": 5, "end": 4}],  # not increasing
        [{"start": 0, "end": 5}, {"start": 0, "end": 8}],  # overlap start
        [{"start": 0, "end": 500, "title": "x"}],  # past duration
        [{"title": "no bounds"}],  # missing bounds
        "not a list",
    ],
)
def test_validate_rejects_wandering_output(bad):
    with pytest.raises(ValueError):
        validate_chapters(bad, 120)


def test_ffmetadata_serialization():
    content, suffix, media = serialize_chapters(
        [{"start": 0.0, "end": 4.5, "title": "Intro"}], "ffmetadata"
    )
    assert content.startswith(";FFMETADATA1")
    assert "START=0" in content and "END=4500" in content and "title=Intro" in content
    assert media == "text/plain"


# --- API/jobs -------------------------------------------------------------------


@pytest.fixture
def client(settings):
    app = create_app(
        settings,
        providers=ProviderRegistry(
            [FakeProvider()],
            processors=[TranscriptProcessor(), ChaptersProcessor(), FakeSummarizer()],
        ),
        start_worker=False,
    )
    with TestClient(app) as tc:
        tc.app = app
        yield tc


def _run_queued(client) -> None:
    claimed = client.app.state.store.claim_next_queued()
    assert claimed is not None
    client.app.state.executor.execute(claimed)


def _submit(client, uri, options=None):
    output = {"id": "ch", "type": "chapters", "required": True}
    if options:
        output["options"] = options
    payload = minimal_payload(
        sources=[{"id": "main", "type": "url", "uri": uri}], outputs=[output]
    )
    return client.post("/api/v1/jobs", json=payload)


def _capabilities(client, uri) -> dict:
    response = client.post(
        "/api/v1/capabilities",
        json={"sources": [{"id": "m", "type": "url", "uri": uri}]},
    )
    return {c["id"]: c for c in response.json()["sources"][0]["capabilities"]}


def test_declared_chapters_select_the_source_variant(client):
    caps = _capabilities(client, CHAPTERED)
    chapters = caps["chapters.generate"]
    assert chapters["status"] == "available"
    assert chapters["selected_variant"] == "chapters.from_source"


def test_without_declared_chapters_the_transcript_variant_is_selected(client):
    caps = _capabilities(client, PLAIN)
    chapters = caps["chapters.generate"]
    assert chapters["status"] == "derivable"
    assert chapters["selected_variant"] == "chapters.from_transcript"


def test_export_job_serializes_the_declared_facts(client):
    response = _submit(client, CHAPTERED)
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]
    _run_queued(client)
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "succeeded", job
    assert {s["operation"] for s in job["steps"]} == {"chapters.export"}
    artifacts = client.get(f"/api/v1/jobs/{job_id}/artifacts").json()
    art = next(a for a in artifacts if a["type"] == "chapters")
    assert art["provenance"]["attributes"]["derived_from"] == "source"
    data = json.loads(client.get(f"/api/v1/artifacts/{art['id']}/content").content)
    assert [c["title"] for c in data["chapters"]] == ["Intro", "Main part", "Outro"]


def test_derive_job_runs_the_transcript_chain(client):
    response = _submit(client, PLAIN)
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]
    _run_queued(client)
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "succeeded", job
    operations = {s["operation"] for s in job["steps"]}
    assert {
        "media.acquire_subtitles",
        "subtitles.to_transcript",
        "chapters.derive",
    } <= operations
    artifacts = client.get(f"/api/v1/jobs/{job_id}/artifacts").json()
    art = next(a for a in artifacts if a["type"] == "chapters")
    attrs = art["provenance"]["attributes"]
    assert attrs["derived_from"] == "transcript"
    assert attrs["model"] == "fake-model"


def test_ffmetadata_format_from_source(client):
    response = _submit(client, CHAPTERED, options={"format": "ffmetadata"})
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]
    _run_queued(client)
    artifacts = client.get(f"/api/v1/jobs/{job_id}/artifacts").json()
    art = next(a for a in artifacts if a["type"] == "chapters")
    content = client.get(f"/api/v1/artifacts/{art['id']}/content").content.decode()
    assert content.startswith(";FFMETADATA1")
    assert "title=Main part" in content


def test_chapters_from_a_video_output_is_rejected(client):
    payload = minimal_payload(
        outputs=[
            {"id": "v", "type": "video", "required": True},
            {
                "id": "ch",
                "type": "chapters",
                "required": True,
                "from_outputs": ["v"],
            },
        ]
    )
    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 422
    codes_seen = {e["code"] for e in response.json()["detail"]["errors"]}
    assert "invalid_option" in codes_seen
