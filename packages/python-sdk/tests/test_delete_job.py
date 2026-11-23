"""`DELETE /jobs/{id}` through the SDK — the other half of a storage quota.

The engine has published this route since quotas existed, for a stated reason:
without a way to delete, someone who reaches the ceiling can never do anything
again. No client could call it — the SDK is the only door to `/api/v1`
(ADR 0015), and the door had no handle, so the CLI, the MCP server and the three
surfaces could all refuse a job but none could remove one.

These tests freeze the verb, the path, the `delivered` flag and the two refusals
a caller has to tell apart: 409 *while it runs* (cancel first) and 404.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from content_sdk import AsyncContentClient, ContentClient, NotFound
from content_sdk.errors import Conflict


class DeleteAPI:
    """Records every call, so the request is asserted rather than assumed."""

    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(
            (request.method, request.url.path, request.url.query.decode())
        )
        if request.method != "DELETE":
            return httpx.Response(405, json={"detail": "wrong verb"})
        if request.url.path == "/api/v1/jobs/job_running":
            return httpx.Response(
                409, json={"detail": "job is running; cancel it first, then delete it."}
            )
        if request.url.path == "/api/v1/jobs/job_missing":
            return httpx.Response(404, json={"detail": "job not found"})
        return httpx.Response(204)


def _client(api: DeleteAPI) -> ContentClient:
    return ContentClient(
        "http://testserver",
        http_client=httpx.Client(transport=httpx.MockTransport(api.handler)),
    )


def test_delete_job_sends_delete_to_the_jobs_route():
    api = DeleteAPI()
    assert _client(api).delete_job("job_1") is None
    assert api.calls == [("DELETE", "/api/v1/jobs/job_1", "delivered=false")]


def test_the_library_copies_are_kept_unless_asked_for():
    """`delivered` is off by default on the engine *and* in the client, so the
    default is stated in the request rather than inherited from a server the
    caller cannot see."""
    api = DeleteAPI()
    client = _client(api)
    client.delete_job("job_1")
    client.delete_job("job_2", delivered=True)
    assert [call[2] for call in api.calls] == ["delivered=false", "delivered=true"]


def test_deleting_a_running_job_is_a_conflict_not_a_silent_no_op():
    api = DeleteAPI()
    with pytest.raises(Conflict) as excinfo:
        _client(api).delete_job("job_running")
    assert excinfo.value.status == 409


def test_an_unknown_job_is_not_found():
    api = DeleteAPI()
    with pytest.raises(NotFound):
        _client(api).delete_job("job_missing")


def test_a_delete_is_never_retried():
    """The retry policy covers safe GETs and keyed creations; a DELETE that
    fails transport-side must surface, not be replayed."""
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url.path)
        raise httpx.ConnectError("boom", request=request)

    client = ContentClient(
        "http://testserver",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(Exception):
        client.delete_job("job_1")
    assert len(attempts) == 1


def test_the_async_client_deletes_the_same_way():
    api = DeleteAPI()

    async def scenario():
        http = httpx.AsyncClient(transport=httpx.MockTransport(api.handler))
        async with AsyncContentClient("http://testserver", http_client=http) as client:
            await client.delete_job("job_1")
            await client.delete_job("job_2", delivered=True)
            with pytest.raises(Conflict):
                await client.delete_job("job_running")

    asyncio.run(scenario())
    assert api.calls == [
        ("DELETE", "/api/v1/jobs/job_1", "delivered=false"),
        ("DELETE", "/api/v1/jobs/job_2", "delivered=true"),
        ("DELETE", "/api/v1/jobs/job_running", "delivered=false"),
    ]
