"""Handing the MCP server a file that lives on this machine (ADR 0020).

The rule under test is one sentence: a path given to this server is always a
path *here*, never on the engine. So a local file is uploaded, always — never
passed through as a `file` source in the hope that the same string means the
same thing on the other host. Identical paths on two machines do not imply
identical filesystems, and guessing wrong either fails confusingly or reads a
different file entirely.
"""

from __future__ import annotations

import os

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
def document(tmp_path, monkeypatch):
    # Reads are refused by default (CONTENT_MCP_ALLOWED_READ_DIRS empty); these
    # tests are about the upload behaviour, so opt the fixture's own directory
    # in, the same way the download tests opt CONTENT_MCP_DOWNLOAD_DIR in.
    monkeypatch.setenv(service.READ_DIRS_ENV, str(tmp_path))
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


# --- the read boundary (CONTENT_MCP_ALLOWED_READ_DIRS) --------------------------
#
# The engine bounds `file` sources with an allowlist that refuses everything by
# default (ffmpeg.py `check_path_allowed`). This server never creates a `file`
# source — it reads the path itself and uploads the bytes (ADR 0020) — so that
# allowlist never applies here. Without a boundary of its own, analyze_source
# would read anything this process can, unbounded, on an agent's say-so. These
# tests are the read-side counterpart of test_download_artifact.py's boundary.


def test_reads_are_refused_by_default(client, tmp_path, monkeypatch):
    monkeypatch.delenv(service.READ_DIRS_ENV, raising=False)
    document = tmp_path / "report.pdf"
    document.write_bytes(b"hello")
    with pytest.raises(ValueError, match="local file reads are disabled"):
        service.analyze_source(client, str(document))


def test_the_refusal_names_the_variable_that_would_widen_it(
    client, tmp_path, monkeypatch
):
    monkeypatch.delenv(service.READ_DIRS_ENV, raising=False)
    document = tmp_path / "report.pdf"
    document.write_bytes(b"hello")
    with pytest.raises(ValueError, match=service.READ_DIRS_ENV):
        service.analyze_source(client, str(document))


def test_a_path_outside_the_allowed_dirs_is_refused(client, tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv(service.READ_DIRS_ENV, str(allowed))
    outside = tmp_path / "elsewhere" / "secret.pdf"
    outside.parent.mkdir()
    outside.write_bytes(b"hello")
    with pytest.raises(ValueError, match="outside the allowed read directories"):
        service.analyze_source(client, str(outside))


def test_a_path_inside_an_allowed_dir_is_read(client, seen, tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv(service.READ_DIRS_ENV, str(allowed))
    document = allowed / "report.pdf"
    document.write_bytes(b"hello")
    service.analyze_source(client, str(document))
    assert seen["uploaded"]


def test_several_allowed_dirs_are_accepted(client, seen, tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv(
        service.READ_DIRS_ENV, os.pathsep.join([str(first), str(second)])
    )
    document = second / "report.pdf"
    document.write_bytes(b"hello")
    service.analyze_source(client, str(document))
    assert seen["uploaded"]


def test_a_symlink_pointing_outside_the_allowed_dirs_is_refused(
    client, tmp_path, monkeypatch
):
    """The boundary resolves the path before comparing, so a symlink inside the
    allowed directory that targets something outside it does not smuggle a read
    through — the same property the download-side fix (§4.2 of the audit) adds
    to the write boundary, applied here to reads."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv(service.READ_DIRS_ENV, str(allowed))
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"top secret")
    link = allowed / "innocuous.pdf"
    link.symlink_to(secret)
    with pytest.raises(ValueError, match="outside the allowed read directories"):
        service.analyze_source(client, str(link))
