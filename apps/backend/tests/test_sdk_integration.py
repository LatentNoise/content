"""SDK ↔ API integration, in-process and hermetic.

Drives the real `content_sdk.ContentClient` against the real FastAPI app via the
Starlette TestClient as the HTTP layer — no network, fake provider. Proves the
whole flow, including the addressable-analysis path (analyze → get_analysis →
capabilities(id) → generate(id) → artifacts).
"""

import pytest
from content_sdk import ContentClient, NotFound, outputs
from fastapi.testclient import TestClient

from content.api.app import create_app
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ProviderRegistry
from tests.conftest import FakeProvider


@pytest.fixture
def sdk(settings):
    app = create_app(
        settings,
        providers=ProviderRegistry(
            [FakeProvider()], processors=[TranscriptProcessor()]
        ),
        start_worker=False,
    )
    with TestClient(app) as tc:
        client = ContentClient("http://testserver", http_client=tc)
        client._app = app  # expose for manual job execution
        yield client


def _run_queued(client) -> None:
    store = client._app.state.store
    executor = client._app.state.executor
    claimed = store.claim_next_queued()
    assert claimed is not None
    executor.execute(claimed)


def test_full_flow_via_analysis_id(sdk):
    analysis = sdk.analyze(outputs.url_source("https://example.com/video"))
    assert analysis.id.startswith("ana_")
    assert analysis.sources[0].resource_type == "video"

    # The analysis is addressable: fetch it back by id (ADR 0014).
    fetched = sdk.get_analysis(analysis.id)
    assert fetched.id == analysis.id

    caps = sdk.get_capabilities(analysis.id)
    offered = [c.id for c in caps.sources[0].capabilities if c.is_offered]
    assert "audio.download" in offered

    job = analysis.generate([outputs.audio_output()])
    assert job.status == "queued"
    _run_queued(sdk)
    job.refresh()
    assert job.is_terminal and job.succeeded
    assert any(a.type == "audio" for a in job.artifacts)


def test_get_unknown_analysis_raises_not_found(sdk):
    with pytest.raises(NotFound) as exc:
        sdk.get_analysis("ana_nope")
    assert exc.value.codes == ["analysis_not_found"]
