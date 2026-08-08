"""MCP service logic, hermetic via a real SDK client over httpx.MockTransport.

Proves each intention maps to the right SDK/API calls, and that get_artifact
never inlines large/binary content (refinement 6). No MCP, no network.
"""

from __future__ import annotations

import httpx
from content_mcp import service
from content_sdk import ContentClient


def _api(handler) -> ContentClient:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return ContentClient("http://testserver", http_client=http)


def _default_handler(request: httpx.Request) -> httpx.Response:
    path, method = request.url.path, request.method
    if path == "/api/v1/analyses":
        return httpx.Response(
            200,
            json={
                "analysis_id": "ana_1",
                "created_at": "t",
                "sources": [
                    {
                        "source_id": "main",
                        "resource": {"resource_type": "video", "title": "T"},
                    }
                ],
            },
        )
    if path == "/api/v1/capabilities":
        return httpx.Response(
            200,
            json={
                "analysis_id": "ana_1",
                "sources": [
                    {
                        "source_id": "main",
                        "capabilities": [
                            {"id": "audio.download", "status": "available"},
                            {"id": "video.download", "status": "available"},
                        ],
                    }
                ],
            },
        )
    if path == "/api/v1/jobs" and method == "POST":
        return httpx.Response(201, json={"job_id": "job_1", "status": "queued"})
    if path == "/api/v1/jobs/job_1":
        return httpx.Response(200, json={"job_id": "job_1", "status": "succeeded"})
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
                    "size_bytes": 99,
                }
            ],
        )
    return httpx.Response(400, json={"detail": "unhandled"})


def test_analyze_source_reports_facts_and_capabilities():
    result = service.analyze_source(_api(_default_handler), "https://x/v")
    assert result["analysis_id"] == "ana_1"
    assert result["resource_type"] == "video"
    assert {c["id"] for c in result["capabilities"]} == {
        "audio.download",
        "video.download",
    }


def test_generate_and_get_job_with_artifacts():
    client = _api(_default_handler)
    gen = service.generate(client, "ana_1", ["audio"])
    assert gen == {"job_id": "job_1", "status": "queued"}
    job = service.get_job(client, "job_1")
    assert job["status"] == "succeeded"
    assert job["artifacts"][0]["filename"] == "a.opus"


def test_get_artifact_inlines_small_text():
    def handler(request):
        if request.url.path == "/api/v1/artifacts/art_txt":
            return httpx.Response(
                200,
                json={
                    "id": "art_txt",
                    "type": "transcript",
                    "filename": "t.txt",
                    "media_type": "text/plain",
                    "size_bytes": 11,
                },
            )
        if request.url.path == "/api/v1/artifacts/art_txt/content":
            return httpx.Response(200, content=b"hello world")
        return httpx.Response(400, json={})

    out = service.get_artifact(_api(handler), "art_txt")
    assert out["inlined"] is True
    assert out["content"] == "hello world"


def test_get_artifact_does_not_inline_binary_or_large():
    def handler(request):
        # a large video artifact — metadata only, never bytes over MCP
        return httpx.Response(
            200,
            json={
                "id": "art_vid",
                "type": "video",
                "filename": "v.mp4",
                "media_type": "video/mp4",
                "size_bytes": 500_000_000,
            },
        )

    out = service.get_artifact(_api(handler), "art_vid")
    assert out["inlined"] is False
    assert out["download_path"] == "/api/v1/artifacts/art_vid/content"
    assert "content" not in out


def test_get_artifact_does_not_inline_oversize_text():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "id": "big",
                "type": "transcript",
                "filename": "big.txt",
                "media_type": "text/plain",
                "size_bytes": service.MAX_INLINE_BYTES + 1,
            },
        )

    out = service.get_artifact(_api(handler), "big")
    assert out["inlined"] is False
    assert "content" not in out
