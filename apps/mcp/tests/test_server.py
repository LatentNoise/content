"""The MCP server wires the intention tools + resources over the SDK. This
smoke test builds it and asserts the expected surface (no stdio, no network)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from content_mcp.server import (
    DEFAULT_HTTP_HOST,
    DEFAULT_HTTP_PORT,
    _transport_config,
    build_server,
)
from content_sdk import ContentClient


def _client() -> ContentClient:
    http = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    )
    return ContentClient("http://testserver", http_client=http)


def _call(server, name: str, arguments: dict | None = None):
    return asyncio.run(server.call_tool(name, arguments or {}))


def test_server_exposes_the_intention_tools():
    server = build_server(_client())
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert {
        "analyze_source",
        "list_capabilities",
        "generate",
        "get_job",
        "cancel_job",
        "list_jobs",
        "get_artifact",
        "get_config",
    } <= names


def test_server_exposes_content_resource_templates():
    server = build_server(_client())
    templates = asyncio.run(server.list_resource_templates())
    uris = {t.uri_template for t in templates}
    assert "content://analyses/{analysis_id}" in uris
    assert "content://jobs/{job_id}" in uris
    assert "content://artifacts/{artifact_id}" in uris


def test_instructions_warn_that_inlined_text_is_untrusted():
    """The data/instruction boundary the security audit flags (§3.4) has no
    control that can enforce it — the model has to be told, in the one place
    it reliably reads before acting: its own instructions."""
    server = build_server(_client())
    assert "untrusted" in server.instructions.lower()
    assert "CONTENT_MCP_ALLOWED_READ_DIRS" in server.instructions


# --- local paths over a network transport ---------------------------------------
#
# Promised on r/mcp to u/International_Emu772 alongside streamable-http itself:
# a local path only ever meant "on the machine that spawned this process",
# which stdio guarantees and a network transport does not. Over
# streamable-http the server refuses local paths rather than read them on
# whatever host happens to be running it.


def test_local_paths_are_refused_when_not_allowed():
    server = build_server(_client(), local_paths_allowed=False)
    with pytest.raises(Exception) as excinfo:
        _call(server, "analyze_source", {"url": "/etc/passwd"})
    message = str(excinfo.value)
    assert "network transport" in message
    assert "URL" in message


def test_a_url_is_never_refused_regardless_of_local_paths_allowed():
    server = build_server(_client(), local_paths_allowed=False)
    # Reaches the service layer rather than being refused up front — proven
    # by it *not* raising the local-path message (the mock client answers a
    # bare {} for everything, so the call still fails, just differently).
    with pytest.raises(Exception) as excinfo:
        _call(server, "analyze_source", {"url": "https://example.com/v"})
    assert "network transport" not in str(excinfo.value)


def test_local_paths_still_work_when_allowed_by_default():
    server = build_server(_client())  # local_paths_allowed defaults to True
    with pytest.raises(Exception) as excinfo:
        _call(server, "analyze_source", {"url": "/no/such/file"})
    # Falls through to the ordinary "not a file" rejection, not the
    # transport refusal — proves the default (stdio) path is unchanged.
    assert "neither a URL nor a file" in str(excinfo.value)


# --- transport selection (CONTENT_MCP_TRANSPORT and friends) --------------------


def test_default_transport_is_stdio(monkeypatch):
    monkeypatch.delenv("CONTENT_MCP_TRANSPORT", raising=False)
    assert _transport_config() == ("stdio", {})


def test_streamable_http_defaults_to_loopback(monkeypatch):
    monkeypatch.setenv("CONTENT_MCP_TRANSPORT", "streamable-http")
    monkeypatch.delenv("CONTENT_MCP_HTTP_HOST", raising=False)
    monkeypatch.delenv("CONTENT_MCP_HTTP_PORT", raising=False)
    transport, kwargs = _transport_config()
    assert transport == "streamable-http"
    assert kwargs["host"] == DEFAULT_HTTP_HOST == "127.0.0.1"
    assert kwargs["port"] == DEFAULT_HTTP_PORT


def test_streamable_http_host_and_port_are_configurable(monkeypatch):
    monkeypatch.setenv("CONTENT_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("CONTENT_MCP_HTTP_HOST", "0.0.0.0")
    monkeypatch.setenv("CONTENT_MCP_HTTP_PORT", "9999")
    _transport, kwargs = _transport_config()
    assert kwargs == {"host": "0.0.0.0", "port": 9999}


def test_an_unknown_transport_is_refused(monkeypatch):
    monkeypatch.setenv("CONTENT_MCP_TRANSPORT", "carrier-pigeon")
    with pytest.raises(SystemExit, match="carrier-pigeon"):
        _transport_config()
