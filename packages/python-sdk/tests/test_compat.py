"""The dict-returning compat client (used by the Streamlit UIs) freezes the
former content_client surface against the contract, hermetic over MockTransport.
"""

from __future__ import annotations

import httpx
import pytest
from content_sdk.compat import ApiError, ContentClient


def _client(handler) -> ContentClient:
    client = ContentClient("http://backend:8000")
    client._t._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def test_paths_and_verbs_match_the_contract():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"ok": True, "folders": []})

    client = _client(handler)
    client.health()
    client.capabilities([{"id": "s", "type": "url", "uri": "u"}], {"x": 1})
    client.catalog()
    client.purge_cache()
    client.job("job_1")
    client.cancel("job_1")
    assert ("GET", "/api/v1/health") in calls
    assert ("POST", "/api/v1/capabilities") in calls
    assert ("GET", "/api/v1/catalog") in calls
    assert ("POST", "/api/v1/cache/purge") in calls
    assert ("GET", "/api/v1/jobs/job_1") in calls
    assert ("POST", "/api/v1/jobs/job_1/cancel") in calls


def test_capabilities_omits_constraints_when_absent():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"sources": []})

    client = _client(handler)
    client.capabilities([{"id": "s", "type": "url", "uri": "u"}])
    assert seen["body"] == {"sources": [{"id": "s", "type": "url", "uri": "u"}]}


def test_non_2xx_raises_apierror_with_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "nope"})

    client = _client(handler)
    with pytest.raises(ApiError) as exc:
        client.submit({"bad": True})
    assert exc.value.status == 422
    assert exc.value.body == {"detail": "nope"}


def test_the_full_surface_is_present():
    for name in [
        "health",
        "config",
        "system",
        "storage",
        "catalog",
        "cache",
        "purge_cache",
        "openapi",
        "call_raw",
        "folders",
        "analyze",
        "capabilities",
        "submit",
        "list_jobs",
        "job",
        "events",
        "logs",
        "artifacts",
        "cancel",
        "retry",
        "artifact_bytes",
    ]:
        assert callable(getattr(ContentClient, name)), name
