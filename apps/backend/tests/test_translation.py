"""translation.generate (prompt 08): subtitles/transcript → target language.

The reserved `translation` output type becomes executable. Subtitle structure
is never handed to the LLM in bulk: cue texts travel through a numbered-list
protocol and are re-attached to their original timings (asserted identical).
The fake LLM tags each item, exercising the real processors/translate.py logic.
"""

import pytest
from fastapi.testclient import TestClient

from content.api.app import create_app
from content.processors.transcript import TranscriptProcessor
from content.processors.translate import (
    parse_numbered_response,
    reassemble_subtitles,
    split_subtitle_cues,
)
from content.providers.base import ProviderRegistry
from tests.conftest import (
    FakeCloudSummarizer,
    FakeProvider,
    FakeSummarizer,
    minimal_payload,
)

SRT = """1
00:00:00,000 --> 00:00:01,000
hello

2
00:00:01,500 --> 00:00:03,000
world of content
"""


# --- pure helpers ---------------------------------------------------------------


def test_split_and_reassemble_preserves_timings():
    preamble, cues = split_subtitle_cues(SRT)
    assert [c.text for c in cues] == ["hello", "world of content"]
    rebuilt = reassemble_subtitles(preamble, cues, ["bonjour", "monde du contenu"])
    assert "00:00:00,000 --> 00:00:01,000" in rebuilt
    assert "00:00:01,500 --> 00:00:03,000" in rebuilt
    assert "bonjour" in rebuilt and "monde du contenu" in rebuilt


def test_vtt_preamble_is_preserved():
    vtt = "WEBVTT\n\n00:00.000 --> 00:01.000\nhello\n"
    preamble, cues = split_subtitle_cues(vtt)
    assert preamble.startswith("WEBVTT")
    assert cues[0].text == "hello"


def test_numbered_response_count_mismatch_is_an_error():
    with pytest.raises(ValueError):
        parse_numbered_response("1. only one", expected=2)


# --- API/jobs -------------------------------------------------------------------


@pytest.fixture
def client(settings):
    app = create_app(
        settings,
        providers=ProviderRegistry(
            [FakeProvider()], processors=[TranscriptProcessor(), FakeSummarizer()]
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


def _submit(client, outputs, uri="https://example.com/video"):
    payload = minimal_payload(
        sources=[{"id": "main", "type": "url", "uri": uri}], outputs=outputs
    )
    return client.post("/api/v1/jobs", json=payload)


def test_capability_resolves_on_a_subtitled_source(client):
    response = client.post(
        "/api/v1/capabilities",
        json={"sources": [{"id": "m", "type": "url", "uri": "https://x/v"}]},
    )
    caps = {c["id"]: c for c in response.json()["sources"][0]["capabilities"]}
    translation = caps["translation.generate"]
    assert translation["status"] == "derivable"
    assert translation["selected_variant"] == "translation.from_subtitles"


def test_capability_unavailable_without_subtitles(client):
    response = client.post(
        "/api/v1/capabilities",
        json={"sources": [{"id": "m", "type": "url", "uri": "https://x/nosubs"}]},
    )
    caps = {c["id"]: c for c in response.json()["sources"][0]["capabilities"]}
    assert caps["translation.generate"]["status"] == "unavailable"
    assert caps["translation.generate"]["reason"]["code"] == "missing_material"


def test_target_language_is_required(client):
    response = _submit(client, [{"id": "t", "type": "translation", "required": True}])
    assert response.status_code == 422  # options.target_language missing


def test_translated_subtitles_keep_timings(client):
    response = _submit(
        client,
        [
            {
                "id": "t",
                "type": "translation",
                "required": True,
                "options": {"target_language": "fr"},
            }
        ],
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]
    _run_queued(client)
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "succeeded", job
    operations = {s["operation"] for s in job["steps"]}
    assert {"media.acquire_subtitles", "text.translate"} <= operations
    artifacts = client.get(f"/api/v1/jobs/{job_id}/artifacts").json()
    art = next(a for a in artifacts if a["type"] == "translation")
    attrs = art["provenance"]["attributes"]
    assert attrs["target_language"] == "fr"
    assert attrs["model"] == "fake-model"
    content = client.get(f"/api/v1/artifacts/{art['id']}/content").content.decode()
    # timings intact, texts tagged by the fake LLM
    assert "-->" in content and "[T]" in content


def test_translation_from_a_transcript_output(client):
    response = _submit(
        client,
        [
            {"id": "tr", "type": "transcript", "required": True},
            {
                "id": "t",
                "type": "translation",
                "required": True,
                "from_outputs": ["tr"],
                "options": {"target_language": "es"},
            },
        ],
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]
    _run_queued(client)
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "succeeded", job
    artifacts = client.get(f"/api/v1/jobs/{job_id}/artifacts").json()
    art = next(a for a in artifacts if a["type"] == "translation")
    assert art["media_type"] == "text/plain"
    content = client.get(f"/api/v1/artifacts/{art['id']}/content").content.decode()
    assert "[T]" in content


def test_translation_from_a_video_output_is_rejected(client):
    response = _submit(
        client,
        [
            {"id": "v", "type": "video", "required": True},
            {
                "id": "t",
                "type": "translation",
                "required": True,
                "from_outputs": ["v"],
                "options": {"target_language": "fr"},
            },
        ],
    )
    assert response.status_code == 422
    codes_seen = {e["code"] for e in response.json()["detail"]["errors"]}
    assert "invalid_option" in codes_seen


def test_privacy_constraint_excludes_cloud_only_translation(settings):
    app = create_app(
        settings,
        providers=ProviderRegistry(
            [FakeProvider()],
            processors=[TranscriptProcessor(), FakeCloudSummarizer()],
        ),
        start_worker=False,
    )
    with TestClient(app) as client:
        payload = minimal_payload(
            outputs=[
                {
                    "id": "t",
                    "type": "translation",
                    "required": True,
                    "options": {"target_language": "fr"},
                }
            ],
            constraints={"privacy": {"allow_cloud_providers": False}},
        )
        response = client.post("/api/v1/jobs", json=payload)
        assert response.status_code == 422
        codes_seen = {e["code"] for e in response.json()["detail"]["errors"]}
        assert "constraint_unsatisfiable" in codes_seen
