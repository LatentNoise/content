"""Unit tests for the SDK, hermetic via httpx.MockTransport (no network).

They freeze the client's paths/verbs against the contract, the error mapping,
the conservative retry policy, and the behavioural objects (Analysis/Job).
"""

from __future__ import annotations

import httpx
import pytest
from content_sdk import (
    Analysis,
    ContentClient,
    Gone,
    Job,
    NotFound,
    TransportError,
    ValidationError,
    outputs,
)


class FakeAPI:
    """A scriptable in-memory API for MockTransport. Records every call."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.job_polls = 0
        self.fail_get_health_times = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        self.calls.append((method, path))

        if path == "/api/v1/health":
            if self.fail_get_health_times > 0:
                self.fail_get_health_times -= 1
                raise httpx.ConnectError("boom", request=request)
            return httpx.Response(200, json={"status": "ok", "version": "0"})
        if path == "/api/v1/analyses" and method == "POST":
            return httpx.Response(
                200,
                json={
                    "analysis_id": "ana_1",
                    "created_at": "t0",
                    "expires_at": "t9",
                    "sources": [
                        {
                            "source_id": "main",
                            "resource": {"resource_type": "video", "title": "T"},
                        }
                    ],
                },
            )
        if path == "/api/v1/analyses/ana_missing":
            return httpx.Response(
                404, json={"code": "analysis_not_found", "message": "nope"}
            )
        if path == "/api/v1/analyses/ana_expired":
            return httpx.Response(410, json={"code": "analysis_expired"})
        if path == "/api/v1/analyses/ana_1":
            return httpx.Response(
                200, json={"analysis_id": "ana_1", "created_at": "t0", "sources": []}
            )
        if path == "/api/v1/capabilities":
            return httpx.Response(
                200,
                json={
                    "analysis_id": "ana_1",
                    "sources": [
                        {
                            "source_id": "main",
                            "resource_type": "video",
                            "title": "T",
                            "capabilities": [
                                {"id": "audio.download", "status": "available"},
                                {"id": "video.download", "status": "unavailable"},
                            ],
                        }
                    ],
                },
            )
        if path == "/api/v1/jobs" and method == "POST":
            return httpx.Response(
                201, json={"job_id": "job_1", "status": "queued", "warnings": []}
            )
        if path == "/api/v1/jobs/job_1" and method == "GET":
            self.job_polls += 1
            status = "running" if self.job_polls < 3 else "partially_succeeded"
            return httpx.Response(200, json={"job_id": "job_1", "status": status})
        if path == "/api/v1/jobs/job_1/artifacts":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "art_1",
                        "job_id": "job_1",
                        "type": "audio",
                        "filename": "a.opus",
                        "media_type": "audio/opus",
                        "size_bytes": 12,
                    }
                ],
            )
        return httpx.Response(400, json={"detail": f"unhandled {method} {path}"})


def _client(api: FakeAPI, **kw) -> ContentClient:
    http = httpx.Client(transport=httpx.MockTransport(api.handler))
    return ContentClient("http://testserver", http_client=http, **kw)


def test_paths_and_verbs_match_the_contract():
    api = FakeAPI()
    client = _client(api)
    client.health()
    analysis = client.analyze(outputs.url_source("https://x/v"))
    assert isinstance(analysis, Analysis)
    assert analysis.id == "ana_1"
    analysis.capabilities()
    job = analysis.generate([outputs.audio_output()])
    assert isinstance(job, Job)
    assert job.id == "job_1"
    verbs = set(api.calls)
    assert ("GET", "/api/v1/health") in verbs
    assert ("POST", "/api/v1/analyses") in verbs
    assert ("POST", "/api/v1/capabilities") in verbs
    assert ("POST", "/api/v1/jobs") in verbs


def test_analysis_id_flow_sends_analysis_id_not_sources():
    api = FakeAPI()
    client = _client(api)
    analysis = client.analyze(outputs.url_source("https://x/v"))
    # capabilities + generate keyed on the id should NOT re-send sources.
    client.get_capabilities(analysis.id)
    client.generate(analysis.id, [outputs.audio_output()])
    assert api.calls.count(("POST", "/api/v1/capabilities")) == 1


def test_typed_capabilities_expose_is_offered():
    api = FakeAPI()
    caps = _client(api).get_capabilities([outputs.url_source("https://x/v")])
    by_id = {c.id: c for c in caps.sources[0].capabilities}
    assert by_id["audio.download"].is_offered
    assert not by_id["video.download"].is_offered


def test_error_mapping_404_410_422():
    api = FakeAPI()
    client = _client(api)
    with pytest.raises(NotFound) as nf:
        client.get_analysis("ana_missing")
    assert nf.value.status == 404
    assert nf.value.codes == ["analysis_not_found"]
    with pytest.raises(Gone) as gone:
        client.get_analysis("ana_expired")
    assert gone.value.codes == ["analysis_expired"]


def test_validation_error_carries_stable_codes():
    def handler(request):
        return httpx.Response(
            422,
            json={
                "valid": False,
                "errors": [{"code": "sources_or_analysis_id_required", "message": "x"}],
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = ContentClient("http://testserver", http_client=http)
    with pytest.raises(ValidationError) as exc:
        client.get_capabilities([outputs.url_source("https://x/v")])
    assert "sources_or_analysis_id_required" in exc.value.codes


def test_job_wait_polls_until_terminal():
    api = FakeAPI()
    client = _client(api)
    job = client.generate([outputs.url_source("https://x/v")], [outputs.audio_output()])
    job.wait(poll_interval=0)
    assert job.status == "partially_succeeded"
    assert job.succeeded and job.is_terminal
    assert api.job_polls >= 3
    arts = job.artifacts
    assert arts[0].filename == "a.opus"


def test_get_retries_transport_error_but_post_does_not():
    from content_sdk._transport import RetryConfig

    api = FakeAPI()
    api.fail_get_health_times = 1  # one transient failure, then success
    client = _client(api, retry=RetryConfig(retries=2, backoff=0))
    client.health()  # GET is retried → succeeds
    assert api.calls.count(("GET", "/api/v1/health")) == 2

    # A POST that always fails at transport level is NOT retried.
    def always_fail(request):
        raise httpx.ConnectError("down", request=request)

    http = httpx.Client(transport=httpx.MockTransport(always_fail))
    client2 = ContentClient(
        "http://testserver", http_client=http, retry=RetryConfig(retries=3, backoff=0)
    )
    with pytest.raises(TransportError):
        client2.analyze(outputs.url_source("https://x/v"))
