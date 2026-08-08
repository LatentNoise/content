"""POST /api/v1/capabilities — the resolved capability feed a dynamic UI renders
from (ADR 0013, phase 3). Analysis stays separate; this endpoint recomputes
availability against the live installation and the effective policy."""

from fastapi.testclient import TestClient

from content.api.app import create_app
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ProviderRegistry
from content.providers.cloud_llm import CloudSummarizer
from tests.conftest import FakeProvider

VIDEO = {"id": "main", "type": "url", "uri": "https://example.com/watch?v=1"}


def _client(settings, with_cloud=False):
    processors = [TranscriptProcessor()]
    if with_cloud:
        processors.append(CloudSummarizer("anthropic", "key", "model"))
    app = create_app(
        settings,
        providers=ProviderRegistry([FakeProvider()], processors=processors),
        start_worker=False,
    )
    return TestClient(app)


def _by_id(source_out):
    return {c["id"]: c for c in source_out["capabilities"]}


def test_capabilities_endpoint_resolves_per_source(settings):
    with _client(settings) as client:
        resp = client.post("/api/v1/capabilities", json={"sources": [VIDEO]})
    assert resp.status_code == 200
    body = resp.json()
    source = body["sources"][0]
    assert source["source_id"] == "main"
    assert source["resource_type"] == "video"
    # The naming engine's editable proposal (ADR 0017): what a UI prefills
    # instead of re-implementing the display profile client-side.
    assert source["suggested_filename"] == "Fake conference"
    caps = _by_id(source)

    # Direct downloads the fake source supports.
    for cap_id in (
        "video.download",
        "audio.download",
        "subtitles.download",
        "thumbnail.download",
        "metadata.export",
    ):
        assert caps[cap_id]["status"] == "available", cap_id

    # Derived output carries what it comes from and the chosen variant.
    transcript = caps["transcript.generate"]
    assert transcript["status"] == "derivable"
    assert transcript["selected_variant"] == "transcript.from_subtitles"
    assert transcript["derived_from"] == ["subtitles"]


def test_missing_runner_makes_a_capability_unavailable(settings):
    # No ffmpeg installed → video.cut has no implementation → clip unavailable.
    with _client(settings) as client:
        caps = _by_id(
            client.post("/api/v1/capabilities", json={"sources": [VIDEO]}).json()[
                "sources"
            ][0]
        )
    assert caps["video.clip"]["status"] == "unavailable"
    assert caps["video.clip"]["reason"]["code"] == "implementation_unavailable"
    assert "video.cut" in caps["video.clip"]["reason"]["missing_operations"]
    # No summarizer either → summary unavailable (structural, not policy).
    assert caps["summary.generate"]["status"] == "unavailable"


def test_request_constraint_restricts_cloud_capabilities(settings):
    # A cloud summarizer is installed → summary is derivable by default...
    with _client(settings, with_cloud=True) as client:
        default = _by_id(
            client.post("/api/v1/capabilities", json={"sources": [VIDEO]}).json()[
                "sources"
            ][0]
        )
        assert default["summary.generate"]["status"] == "derivable"

        # ...but a request that forbids cloud can only restrict it (R4).
        restricted = _by_id(
            client.post(
                "/api/v1/capabilities",
                json={
                    "sources": [VIDEO],
                    "constraints": {"allow_cloud_providers": False},
                },
            ).json()["sources"][0]
        )
    assert restricted["summary.generate"]["status"] == "restricted"
    assert restricted["summary.generate"]["reason"]["code"] == "policy_restricted"
    assert (
        "text.summarize"
        in restricted["summary.generate"]["reason"]["blocked_operations"]
    )


def test_capabilities_rejects_duplicate_source_ids(settings):
    with _client(settings) as client:
        resp = client.post(
            "/api/v1/capabilities",
            json={"sources": [VIDEO, {**VIDEO, "uri": "https://example.com/2"}]},
        )
    assert resp.status_code == 422


def test_catalog_endpoint_describes_the_architecture(settings):
    # The console inspector feed: public capabilities + internal operations with
    # their implementations and availability (ADR 0013).
    with _client(settings, with_cloud=True) as client:
        body = client.get("/api/v1/catalog").json()
    cap_ids = {c["id"] for c in body["capabilities"]}
    assert {"video.download", "summary.generate", "transcript.generate"} <= cap_ids
    ops = {o["operation"]: o for o in body["operations"]}
    # A capability's variant references real operations.
    summary = next(c for c in body["capabilities"] if c["id"] == "summary.generate")
    assert summary["variants"][0]["operations"][0] == "media.acquire_subtitles"
    # text.summarize is implemented (FakeSummarizer); no cloud key here so no STT.
    assert any(i["available"] for i in ops["text.summarize"]["implementations"])
    assert ops["audio.transcribe"]["implementations"] == []
