"""MCP service logic, hermetic via a real SDK client over httpx.MockTransport.

Proves each intention maps to the right SDK/API calls, and that get_artifact
never inlines large/binary content (refinement 6). No MCP, no network.
"""

from __future__ import annotations

import httpx
import pytest
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


# --- output specs: refuse rather than reinterpret ------------------------------


class TestOutputSpecStrictness:
    """An agent that mis-shapes an output must be told, not obeyed loosely.

    Found by driving the server as an agent: `{"type": "subtitles",
    "languages": ["es"]}` — options flattened onto the spec, a very plausible
    guess — was accepted, the intent dropped, and a job producing *English*
    subtitles reported success. A caller that cannot inspect the result has no
    way to notice. The engine's own models forbid unknown fields; this layer
    now stops undoing that.
    """

    def test_flattened_options_are_refused_with_the_correct_shape(self):
        from content_mcp.service import _output_from_spec

        with pytest.raises(ValueError) as excinfo:
            _output_from_spec({"type": "subtitles", "languages": ["es"]})
        message = str(excinfo.value)
        assert "languages" in message, "name the offending key"
        assert '"options": {"languages": ["es"]}' in message, "show the fix"

    def test_unknown_key_is_refused(self):
        from content_mcp.service import _output_from_spec

        with pytest.raises(ValueError, match="totally_bogus"):
            _output_from_spec({"type": "audio", "totally_bogus": 1})

    def test_a_missing_type_is_refused(self):
        from content_mcp.service import _output_from_spec

        with pytest.raises(ValueError, match="no 'type'"):
            _output_from_spec({"options": {"languages": ["en"]}})

    def test_a_non_object_is_refused(self):
        from content_mcp.service import _output_from_spec

        with pytest.raises(TypeError, match="type string or an object"):
            _output_from_spec(42)

    def test_every_documented_key_is_still_accepted(self):
        """The refusal must not narrow what legitimately works."""
        from content_mcp.service import _output_from_spec

        spec = _output_from_spec(
            {
                "type": "video",
                "id": "vid",
                "from_sources": ["main"],
                "from_outputs": [],
                "scope": "each_item",
                "required": True,
                "delivery": {"mode": "deliver", "folder": "Talks"},
                "options": {"selection": {"max_height": 1080}},
            }
        )
        assert spec["type"] == "video"
        assert spec["scope"] == "each_item"
        assert spec["options"]["selection"]["max_height"] == 1080

    def test_a_plain_type_string_still_works(self):
        from content_mcp.service import _output_from_spec

        assert _output_from_spec("audio")["type"] == "audio"


def test_a_failed_job_reports_why():
    """`{"status": "failed", "error": ""}` is a dead end for an agent.

    The engine records a failure on the step that failed, so the job's own
    error is usually empty. Found by driving the server as an agent: a job
    that failed because the requested subtitle language did not exist said
    only "failed", leaving nothing to tell the user and no basis for deciding
    whether a different request would work.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/artifacts"):
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json={
                "job_id": "job_x",
                "status": "failed",
                "error": "",
                "steps": [
                    {
                        "step_id": "acquire_subtitles_x",
                        "operation": "media.acquire_subtitles",
                        "status": "failed",
                        "error": "provider_error: yt-dlp exited with code 1.",
                    },
                    {
                        "step_id": "other",
                        "operation": "metadata.export",
                        "status": "succeeded",
                        "error": "",
                    },
                ],
            },
        )

    result = service.get_job(_api(handler), "job_x")

    assert result["status"] == "failed"
    assert result["failures"] == [
        {
            "step": "acquire_subtitles_x",
            "operation": "media.acquire_subtitles",
            "error": "provider_error: yt-dlp exited with code 1.",
        }
    ], "only the failed step, with its reason"
    assert result["error"] == "provider_error: yt-dlp exited with code 1.", (
        "the empty job-level error must be filled from the first real reason"
    )


def test_a_succeeded_job_carries_no_failure_noise():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/artifacts"):
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json={
                "job_id": "job_ok",
                "status": "succeeded",
                "error": "",
                "steps": [{"step_id": "s", "status": "succeeded", "error": ""}],
            },
        )

    result = service.get_job(_api(handler), "job_ok")
    assert "failures" not in result
    assert result["error"] == ""


def test_retry_job_reruns_the_request_and_names_its_ancestor():
    """`cancel_job` had no counterpart: an agent that watched a job fail could
    report the failure and nothing else. The answer carries `retry_of` so the
    agent can tell the user which run this replaces."""
    seen: dict = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["method"] = request.method
        return httpx.Response(201, json={"job_id": "job_new", "status": "queued"})

    client = ContentClient(
        "http://engine",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    answer = service.retry_job(client, "job_old")

    assert seen == {"path": "/api/v1/jobs/job_old/retry", "method": "POST"}
    assert answer == {"job_id": "job_new", "status": "queued", "retry_of": "job_old"}
