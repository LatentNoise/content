"""Handing the MCP server a file that lives on this machine (ADR 0020).

The rule under test is one sentence: a path given to this server is always a
path *here*, never on the engine. So a local file is uploaded, always — never
passed through as a `file` source in the hope that the same string means the
same thing on the other host. Identical paths on two machines do not imply
identical filesystems, and guessing wrong either fails confusingly or reads a
different file entirely.
"""

from __future__ import annotations

import httpx
import pytest
from content_mcp import service
from content_sdk import ContentClient

ANALYSIS = {
    "analysis_id": "ana_1",
    "created_at": "t",
    "sources": [
        {"source_id": "main", "resource": {"resource_type": "document", "title": "R"}}
    ],
}
CAPABILITIES = {
    "analysis_id": "ana_1",
    "sources": [
        {
            "source_id": "main",
            "capabilities": [{"id": "summary.generate", "status": "derivable"}],
        }
    ],
}
UPLOAD = {
    "upload_id": "upl_1",
    "filename": "report.pdf",
    "media_type": "application/pdf",
    "size_bytes": 5,
    "sha256": "sha256:x",
    "created_at": "t",
}


@pytest.fixture
def seen():
    return {"uploaded": False, "sources": None}


@pytest.fixture
def client(seen):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/uploads":
            seen["uploaded"] = True
            return httpx.Response(201, json=UPLOAD)
        if path == "/api/v1/analyses":
            import json as _json

            seen["sources"] = _json.loads(request.content)["sources"]
            return httpx.Response(200, json=ANALYSIS)
        if path == "/api/v1/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        return httpx.Response(404, json={"detail": "no"})

    return ContentClient(
        "http://testserver",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


@pytest.fixture
def document(tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"hello")
    return path


def test_a_local_file_is_uploaded_and_analysed(client, seen, document):
    result = service.analyze_source(client, str(document))
    assert seen["uploaded"], "the file should have been uploaded"
    assert seen["sources"] == [{"id": "main", "type": "upload", "upload_id": "upl_1"}]
    assert result["resource_type"] == "document"


def test_a_local_path_never_becomes_a_file_source(client, seen, document):
    """The forbidden optimization, stated as a test: the engine may be another
    machine, where this path means nothing or something else."""
    service.analyze_source(client, str(document))
    assert seen["sources"][0]["type"] != "file"


def test_a_url_is_still_a_url(client, seen):
    service.analyze_source(client, "https://example.com/watch?v=abc")
    assert not seen["uploaded"]
    assert seen["sources"][0]["type"] == "url"


def test_a_tilde_path_is_expanded(client, seen, document, monkeypatch):
    monkeypatch.setenv("HOME", str(document.parent))
    service.analyze_source(client, "~/report.pdf")
    assert seen["uploaded"]


def test_something_that_is_neither_says_so_usefully(client, document):
    with pytest.raises(ValueError, match="neither a URL nor a file"):
        service.analyze_source(client, str(document.parent / "absent.pdf"))


def test_a_directory_is_not_a_file(client, tmp_path):
    with pytest.raises(ValueError, match="neither a URL nor a file"):
        service.analyze_source(client, str(tmp_path))
