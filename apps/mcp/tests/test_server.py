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
