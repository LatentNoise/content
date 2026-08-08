"""The REST API stays directly reachable (prompt 05): no auth, no mandatory
SDK — any HTTP client can always go straight to the backend. The whole API test
suite already exercises raw REST (TestClient without the SDK); these tests pin
the two access-policy facts: no auth gate anywhere, and CORS off by default /
opt-in via CONTENT_CORS_ORIGINS (the one browser-specific switch)."""

import pytest
from fastapi.testclient import TestClient

from content.api.app import create_app
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ProviderRegistry
from tests.conftest import FakeProvider


def _app(settings):
    return create_app(
        settings,
        providers=ProviderRegistry(
            [FakeProvider()], processors=[TranscriptProcessor()]
        ),
        start_worker=False,
    )


def test_api_needs_no_auth_and_no_sdk(settings):
    """A bare HTTP client (no SDK, no headers beyond content-type) drives the
    full flow: health → analyze → capabilities by id → submit → inspect."""
    with TestClient(_app(settings)) as client:
        assert client.get("/api/v1/health").status_code == 200
        analysis = client.post(
            "/api/v1/analyses",
            json={"sources": [{"id": "m", "type": "url", "uri": "https://x/v"}]},
        )
        assert analysis.status_code == 200
        analysis_id = analysis.json()["analysis_id"]
        caps = client.post("/api/v1/capabilities", json={"analysis_id": analysis_id})
        assert caps.status_code == 200
        job = client.post(
            "/api/v1/jobs",
            json={
                "schema_version": "1.0",
                "analysis_id": analysis_id,
                "outputs": [{"id": "a", "type": "audio"}],
            },
        )
        assert job.status_code == 201
        assert client.get(f"/api/v1/jobs/{job.json()['job_id']}").status_code == 200


def test_cors_is_off_by_default(settings):
    with TestClient(_app(settings)) as client:
        response = client.get(
            "/api/v1/health", headers={"Origin": "https://example.com"}
        )
        assert response.status_code == 200  # the request itself is never blocked
        assert "access-control-allow-origin" not in response.headers


def test_cors_opt_in_serves_the_configured_origin(settings):
    settings_cors = type(settings)(
        **{**settings.__dict__, "cors_origins": ("https://myapp.example",)}
    )
    with TestClient(_app(settings_cors)) as client:
        ok = client.get("/api/v1/health", headers={"Origin": "https://myapp.example"})
        assert ok.headers.get("access-control-allow-origin") == "https://myapp.example"
        other = client.get("/api/v1/health", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in other.headers


@pytest.mark.parametrize("path", ["/api/v1/health", "/api/v1/jobs", "/api/v1/system"])
def test_no_endpoint_answers_401_or_403(settings, path):
    with TestClient(_app(settings)) as client:
        assert client.get(path).status_code not in (401, 403)
