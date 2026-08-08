"""Async client mirror — hermetic via httpx.MockTransport, driven with
asyncio.run (no pytest-asyncio dependency)."""

from __future__ import annotations

import asyncio

import httpx
from content_sdk import AsyncContentClient, outputs


def _handler(request: httpx.Request):
    path, method = request.url.path, request.method
    if path == "/api/v1/analyses" and method == "POST":
        return httpx.Response(
            200, json={"analysis_id": "ana_1", "created_at": "t", "sources": []}
        )
    if path == "/api/v1/jobs" and method == "POST":
        return httpx.Response(201, json={"job_id": "job_1", "status": "queued"})
    if path == "/api/v1/jobs/job_1":
        return httpx.Response(200, json={"job_id": "job_1", "status": "succeeded"})
    if path == "/api/v1/jobs/job_1/artifacts":
        return httpx.Response(
            200, json=[{"id": "art_1", "type": "audio", "filename": "a.opus"}]
        )
    return httpx.Response(400, json={"detail": "unhandled"})


def test_async_analyze_generate_wait():
    async def scenario():
        http = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
        async with AsyncContentClient("http://testserver", http_client=http) as client:
            analysis = await client.analyze(outputs.url_source("https://x/v"))
            assert analysis.id == "ana_1"
            job = await analysis.generate([outputs.audio_output()])
            await job.wait(poll_interval=0)
            assert job.status == "succeeded"
            arts = await job.artifacts()
            return arts[0].filename

    assert asyncio.run(scenario()) == "a.opus"
