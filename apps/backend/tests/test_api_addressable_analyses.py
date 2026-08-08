"""Addressable analyses (ADR 0014): an analysis is a real public resource.

`GET /analyses/{id}` fetches it (never re-analyzing); `/capabilities` and
`/jobs` accept `analysis_id` XOR `sources` with stable-coded rejections; the
exclusivity is declared in the OpenAPI. These guard the new contract surface.
"""

import pytest
from fastapi.testclient import TestClient

from content.api.app import create_app
from content.domain import errors as codes
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


def _analyze(client, uri="https://example.com/video") -> str:
    response = client.post(
        "/api/v1/analyses",
        json={"sources": [{"id": "main", "type": "url", "uri": uri}]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["analysis_id"].startswith("ana_")
    assert body["expires_at"]  # the record has a lifecycle
    return body["analysis_id"]


# --- GET /analyses/{id} --------------------------------------------------------


def test_get_analysis_returns_the_persisted_facts(client):
    analysis_id = _analyze(client)
    response = client.get(f"/api/v1/analyses/{analysis_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["analysis_id"] == analysis_id
    assert body["expires_at"]
    assert body["sources"][0]["resource"]["resource_type"] == "video"
    assert body["sources"][0]["source_id"] == "main"


def test_get_unknown_analysis_is_404_not_found(client):
    response = client.get("/api/v1/analyses/ana_does_not_exist")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == codes.ANALYSIS_NOT_FOUND


def test_get_analysis_is_410_when_referenced_facts_are_gone(client):
    """GET never re-derives: with the record intact but its referenced facts
    gone from the cache, the read is deterministically *expired* — never a
    silent re-analysis (refinement 1)."""
    analysis_id = _analyze(client)
    store = client.app.state.store
    with store._conn() as conn:  # drop only the facts, keep the record
        conn.execute("DELETE FROM analyses")
    response = client.get(f"/api/v1/analyses/{analysis_id}")
    assert response.status_code == 410
    assert response.json()["detail"]["code"] == codes.ANALYSIS_EXPIRED


# --- analysis_id XOR sources ---------------------------------------------------


def test_capabilities_accepts_analysis_id(client):
    analysis_id = _analyze(client)
    response = client.post("/api/v1/capabilities", json={"analysis_id": analysis_id})
    assert response.status_code == 200, response.text
    assert response.json()["analysis_id"]
    assert response.json()["sources"][0]["source_id"] == "main"


def test_capabilities_analysis_id_equals_sources_path(client):
    uri = "https://example.com/video"
    analysis_id = _analyze(client, uri)
    by_id = client.post("/api/v1/capabilities", json={"analysis_id": analysis_id})
    by_sources = client.post(
        "/api/v1/capabilities",
        json={"sources": [{"id": "main", "type": "url", "uri": uri}]},
    )
    assert by_id.status_code == by_sources.status_code == 200
    caps_id = by_id.json()["sources"][0]["capabilities"]
    caps_src = by_sources.json()["sources"][0]["capabilities"]
    assert [c["id"] for c in caps_id] == [c["id"] for c in caps_src]
    assert [c["status"] for c in caps_id] == [c["status"] for c in caps_src]


@pytest.mark.parametrize("path", ["/api/v1/capabilities", "/api/v1/jobs"])
def test_neither_sources_nor_analysis_id_is_rejected(client, path):
    body = {"schema_version": "1.0", "outputs": [{"id": "a", "type": "audio"}]}
    if path == "/api/v1/capabilities":
        body = {}
    response = client.post(path, json=body)
    assert response.status_code == 422
    codes_seen = {e["code"] for e in response.json()["detail"]["errors"]}
    assert codes.SOURCES_OR_ANALYSIS_ID_REQUIRED in codes_seen


@pytest.mark.parametrize("path", ["/api/v1/capabilities", "/api/v1/jobs"])
def test_both_sources_and_analysis_id_is_rejected(client, path):
    source = {"id": "main", "type": "url", "uri": "https://example.com/video"}
    body = {"analysis_id": "ana_x", "sources": [source]}
    if path == "/api/v1/jobs":
        body = {
            "schema_version": "1.0",
            "analysis_id": "ana_x",
            "sources": [source],
            "outputs": [{"id": "a", "type": "audio"}],
        }
    response = client.post(path, json=body)
    assert response.status_code == 422
    codes_seen = {e["code"] for e in response.json()["detail"]["errors"]}
    assert codes.SOURCES_AND_ANALYSIS_ID_CONFLICT in codes_seen


def test_jobs_accepts_analysis_id(client):
    analysis_id = _analyze(client)
    payload = minimal_payload()
    payload.pop("sources")
    payload["analysis_id"] = analysis_id
    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["job_id"].startswith("job_")


def test_jobs_unknown_analysis_id_is_404(client):
    payload = minimal_payload()
    payload.pop("sources")
    payload["analysis_id"] = "ana_missing"
    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == codes.ANALYSIS_NOT_FOUND


# --- OpenAPI reflects the exclusivity ------------------------------------------


def test_openapi_declares_oneof_on_both_request_models(client):
    schema = client.get("/openapi.json").json()["components"]["schemas"]
    for model in ("CapabilitiesRequest", "GenerationRequest"):
        assert "oneOf" in schema[model], model
        required = [branch.get("required") for branch in schema[model]["oneOf"]]
        assert ["sources"] in required and ["analysis_id"] in required, model
