"""Sending a local file to the engine (ADR 0020).

The ergonomic promise is one line — `source = client.upload_file(path)` — and
the caller never handles an upload id. What is pinned here is that promise, and
the two properties behind it: the request really is multipart, and an upload is
never retried, because the body is a handle the first attempt already consumed.
"""

from __future__ import annotations

import httpx
import pytest
from content_sdk import ContentClient

RECORD = {
    "upload_id": "upl_1",
    "filename": "report.pdf",
    "media_type": "application/pdf",
    "size_bytes": 11,
    "sha256": "sha256:abc",
    "created_at": "t",
}


@pytest.fixture
def document(tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"hello world")
    return path


def _client(handler) -> ContentClient:
    return ContentClient(
        "http://testserver",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_upload_file_returns_a_source_ready_to_use(document):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/uploads"
        assert request.headers["content-type"].startswith("multipart/form-data")
        return httpx.Response(201, json=RECORD)

    source = _client(handler).upload_file(document)
    # Exactly what analyze/capabilities/generate accept — no id juggling.
    assert source == {"id": "main", "type": "upload", "upload_id": "upl_1"}


def test_the_file_content_really_travels(document):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        return httpx.Response(201, json=RECORD)

    _client(handler).upload_file(document)
    assert b"hello world" in seen["body"]
    assert b"report.pdf" in seen["body"], "the filename travels as metadata"


def test_a_source_id_can_be_chosen(document):
    handler = lambda request: httpx.Response(201, json=RECORD)
    source = _client(handler).upload_file(document, id="attachment")
    assert source["id"] == "attachment"


def test_upload_returns_the_record_for_callers_who_want_it(document):
    handler = lambda request: httpx.Response(201, json=RECORD)
    record = _client(handler).upload(document)
    assert record["sha256"] == "sha256:abc"
    assert record["size_bytes"] == 11


def test_an_upload_is_never_retried(document):
    """A 5xx would be retried for a safe method. It must not be here: the body
    is a file handle the first attempt consumed, and re-sending an upload is
    the caller's decision, not the transport's."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500, json={"detail": "boom"})

    with pytest.raises(Exception):  # noqa: B017 — any API error; the count is the point
        _client(handler).upload_file(document)
    assert len(calls) == 1, f"the upload was sent {len(calls)} times"


def test_metadata_and_delete_are_available(document):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json=RECORD)

    client = _client(handler)
    assert client.get_upload("upl_1")["filename"] == "report.pdf"
    assert client.delete_upload("upl_1") is None
