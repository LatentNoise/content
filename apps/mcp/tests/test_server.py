"""The MCP server wires the intention tools + resources over the SDK. This
smoke test builds it and asserts the expected surface (no stdio, no network)."""

from __future__ import annotations

import asyncio

import httpx
from content_mcp.server import build_server
from content_sdk import ContentClient


def _client() -> ContentClient:
    http = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    )
    return ContentClient("http://testserver", http_client=http)


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
