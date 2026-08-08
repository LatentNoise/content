"""The full agent journey over a REAL in-process engine — no mocks in the path.

MCP service → SDK → FastAPI app (TestClient) → planner → executor → SQLite →
delivery library. This is the test behind the claim "the MCP server is
functional": every layer below the stdio transport is the real one. The stdio
wiring itself is covered by test_server.py (real `mcp` library, list_tools);
what neither covers — an interactive MCP host — is stated in the README.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from content.api.app import create_app
from content.config import ContentSettings
from content.domain.analysis import (
    MediaFacts,
    NormalizedResource,
    SourceAnalysis,
)
from content.execution.executor import JobExecutor
from content.persistence.store import Store
from content.providers.base import (
    ProducedFile,
    ProviderRegistry,
)
from content_mcp import service
from content_sdk import ContentClient
from fastapi.testclient import TestClient


@dataclass
class _FakeMediaProvider:
    """Deterministic yt-dlp stand-in: one video resource, audio producible."""

    name: str = "ytdlp"
    tool_version: str = "fake-1.0"
    location: str = "local"
    operations: tuple = (
        "media.acquire_video",
        "media.acquire_audio",
        "metadata.export",
    )
    executed: list = field(default_factory=list)

    def supports(self, source) -> bool:
        return getattr(source, "type", "") == "url"

    def resource_key(self, source, ctx) -> str:
        return f"{self.name}:url:{source.uri}"

    def analyze(self, source, ctx) -> SourceAnalysis:
        return SourceAnalysis(
            source_id=source.id,
            resource_key=self.resource_key(source, ctx),
            resource=NormalizedResource(
                resource_type="video",
                title="An MCP Test Talk",
                duration_seconds=60.0,
            ),
            media=MediaFacts(has_video=True, has_audio=True),
        )

    def execute(self, step, ctx) -> list[ProducedFile]:
        self.executed.append(step.operation)
        if step.operation != "media.acquire_audio":
            raise AssertionError(f"unexpected operation {step.operation}")
        path = ctx.workdir / f"audio-{step.id}.m4a"
        path.write_bytes(b"mcp-audio-bytes")
        return [ProducedFile(path=path, media_type="audio/mp4")]


@pytest.fixture
def engine(tmp_path):
    """A real engine (policy on) + an SDK client speaking to it in-process."""
    settings = ContentSettings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "content.db",
        delivery_default=True,
        max_concurrent_jobs=1,
        step_timeout_seconds=30,
    )
    providers = ProviderRegistry([_FakeMediaProvider()])
    app = create_app(settings, providers=providers, start_worker=False)
    with TestClient(app) as http:
        client = ContentClient("http://testserver", http_client=http)

        def run_queued_job() -> None:
            store = Store(settings.db_path)
            claimed = store.claim_next_queued()
            assert claimed is not None, "no queued job to execute"
            JobExecutor(store, settings, providers).execute(claimed)

        yield client, run_queued_job, settings


def test_the_full_agent_journey(engine):
    client, run_queued_job, settings = engine

    # 1. The agent asks what the server offers.
    config = service.get_config(client)
    assert config["delivery_by_default"] is True
    assert config["folders"] == [""]  # the library root always exists as a choice

    # 2. Analyze a source and see what it can produce.
    analyzed = service.analyze_source(client, "https://example.com/talk")
    assert analyzed["title"] == "An MCP Test Talk"
    capability_ids = {c["id"] for c in analyzed["capabilities"]}
    assert any("audio" in cid for cid in capability_ids)

    # 3. Generate audio — bare spec, no delivery block: the server policy
    #    delivers, the naming engine names.
    started = service.generate(client, analyzed["analysis_id"], ["audio"])
    assert started["status"] == "queued"
    run_queued_job()

    # 4. Poll the job; once terminal the artifacts say what and *where*.
    job = service.get_job(client, started["job_id"])
    assert job["status"] == "succeeded"
    (artifact,) = job["artifacts"]
    assert artifact["filename"] == "An MCP Test Talk.m4a"
    assert artifact["delivered_path"] == "An MCP Test Talk.m4a"
    delivered = (settings.data_dir / "delivery") / artifact["delivered_path"]
    assert delivered.is_file() and delivered.read_bytes() == b"mcp-audio-bytes"

    # 5. get_artifact: binary audio is never inlined — reference only.
    detail = service.get_artifact(client, artifact["id"])
    assert detail["inlined"] is False
    assert detail["download_path"].endswith(f"/artifacts/{artifact['id']}/content")

    # 6. The job list knows about it.
    listed = service.list_jobs(client)
    assert started["job_id"] in {j["job_id"] for j in listed["jobs"]}


def test_delivery_intent_reaches_the_library(engine):
    client, run_queued_job, settings = engine

    analyzed = service.analyze_source(client, "https://example.com/talk")
    started = service.generate(
        client,
        analyzed["analysis_id"],
        [
            {
                "type": "audio",
                "delivery": {"folder": "podcasts", "filename": "Episode 1"},
            }
        ],
    )
    run_queued_job()

    job = service.get_job(client, started["job_id"])
    assert job["status"] == "succeeded"
    (artifact,) = job["artifacts"]
    assert artifact["delivered_path"] == "podcasts/Episode 1.m4a"
    assert (
        Path(settings.data_dir / "delivery") / "podcasts" / "Episode 1.m4a"
    ).is_file()

    # The folder now shows up as a destination for the next request.
    assert "podcasts" in service.get_config(client)["folders"]


def test_mode_none_keeps_the_library_untouched(engine):
    client, run_queued_job, settings = engine

    analyzed = service.analyze_source(client, "https://example.com/talk")
    started = service.generate(
        client,
        analyzed["analysis_id"],
        [{"type": "audio", "delivery": {"mode": "none"}}],
    )
    run_queued_job()

    job = service.get_job(client, started["job_id"])
    assert job["status"] == "succeeded"
    assert job["artifacts"][0]["delivered_path"] == ""
    assert not (settings.data_dir / "delivery").exists()
