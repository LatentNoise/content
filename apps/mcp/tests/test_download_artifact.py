"""Bringing an artifact to the machine running the MCP server.

Delivery puts a copy in the *engine's* library; this puts one on the *caller's*
disk, which for a homelab engine was otherwise an scp. That makes the MCP server
write to a real filesystem on an agent's say-so, so most of what is pinned here
is the boundary: everything lands inside CONTENT_MCP_DOWNLOAD_DIR, and anything
aimed outside it is refused rather than quietly moved back inside.
"""

from __future__ import annotations

import httpx
import pytest
from content_mcp import service
from content_sdk import ContentClient
from content_sdk.errors import NotFound

ARTIFACT = {
    "id": "art_1",
    "job_id": "job_1",
    "artifact_request_id": "a",
    "type": "audio",
    "filename": "a.opus",
    "display_filename": "My Talk - audio.opus",
    "delivered_path": "Tech/My Talk - audio.opus",
    "media_type": "audio/opus",
    "size_bytes": 11,
    "created_at": "t",
}
PAYLOAD = b"hello audio"


def _client(payload: bytes = PAYLOAD, status: int = 200) -> ContentClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/content"):
            return httpx.Response(status, content=payload)
        return httpx.Response(200, json=ARTIFACT)

    return ContentClient(
        "http://testserver",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A download directory of our own, so no test writes to ~/Downloads."""
    target = tmp_path / "downloads"
    monkeypatch.setenv(service.DOWNLOAD_DIR_ENV, str(target))
    return target


# --- the ordinary path ----------------------------------------------------------


def test_no_destination_uses_the_artifacts_own_name(root):
    result = service.download_artifact(_client(), "art_1")
    written = root / "My Talk - audio.opus"
    assert written.read_bytes() == PAYLOAD
    assert result["path"] == str(written)
    assert result["size_bytes"] == 11


def test_a_relative_destination_resolves_inside_the_root(root):
    result = service.download_artifact(_client(), "art_1", "talks/keep.opus")
    assert (root / "talks" / "keep.opus").read_bytes() == PAYLOAD
    assert result["filename"] == "keep.opus"


def test_an_existing_directory_keeps_the_artifact_name(root):
    (root / "talks").mkdir(parents=True)
    service.download_artifact(_client(), "art_1", "talks")
    assert (root / "talks" / "My Talk - audio.opus").read_bytes() == PAYLOAD


def test_the_answer_says_where_the_file_is_and_what_it_did_not_touch(root):
    result = service.download_artifact(_client(), "art_1")
    # An agent reports this to a human, so it must be unambiguous about which
    # of the two copies — engine library vs local disk — was just written.
    assert "MCP server" in result["note"]
    assert result["media_type"] == "audio/opus"


# --- the boundary ---------------------------------------------------------------


@pytest.mark.parametrize(
    "destination",
    ["/etc/passwd", "../escaped.opus", "talks/../../escaped.opus", "~/escaped.opus"],
)
def test_a_destination_outside_the_root_is_refused(root, destination):
    with pytest.raises(ValueError, match="outside"):
        service.download_artifact(_client(), "art_1", destination)


def test_a_refusal_writes_nothing_at_all(root, tmp_path):
    with pytest.raises(ValueError):
        service.download_artifact(_client(), "art_1", str(tmp_path / "elsewhere.opus"))
    assert not (tmp_path / "elsewhere.opus").exists()
    assert list(root.rglob("*")) == [] or all(p.is_dir() for p in root.rglob("*"))


def test_the_refusal_names_the_variable_that_would_widen_it(root):
    with pytest.raises(ValueError, match=service.DOWNLOAD_DIR_ENV):
        service.download_artifact(_client(), "art_1", "/tmp/x.opus")


# --- failure leaves no debris ---------------------------------------------------


def test_an_http_failure_leaves_no_partial_file(root):
    with pytest.raises(NotFound):
        service.download_artifact(_client(status=404), "art_1")
    assert list(root.rglob("*.part")) == []
    assert not (root / "My Talk - audio.opus").exists()


def test_the_default_root_is_under_the_users_downloads(monkeypatch):
    monkeypatch.delenv(service.DOWNLOAD_DIR_ENV, raising=False)
    assert service.download_root().parts[-2:] == ("Downloads", "Content")
