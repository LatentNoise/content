"""An error an agent cannot act on is a bug, whatever the status code says.

Every tool delegates to the SDK, and the SDK's exceptions carry HTTP truth
rather than guidance. Unwrapped, an engine that is not running answered
`[Errno 61] Connection refused` — which names neither what failed nor what to
do, and is the *first* thing a new user meets when they point the server at
the wrong port (the engine listens on 8010 on the host, 8000 only inside its
container).

Found by driving the published wheel over stdio against a real engine, which
is the only place this shows up: in-process tests never reach a closed socket.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from content_mcp.server import build_server
from content_sdk import ContentClient


def _client(handler) -> ContentClient:
    return ContentClient(
        "http://engine.example:8010",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _call(server, name: str, arguments: dict | None = None):
    return asyncio.run(server.call_tool(name, arguments or {}))


def _message(excinfo) -> str:
    return str(excinfo.value)


def test_an_unreachable_engine_names_the_url_and_the_remedy():
    def refuse(request):
        raise httpx.ConnectError("[Errno 61] Connection refused")

    server = build_server(_client(refuse))
    with pytest.raises(Exception) as excinfo:
        _call(server, "get_config")

    message = _message(excinfo)
    assert "http://engine.example:8010" in message, "say which engine"
    assert "docker compose up -d" in message, "say how to start it"
    assert "CONTENT_API_URL" in message, "say how to point elsewhere"
    assert "8010" in message and "8000" in message, "name the port confusion"


def test_an_expired_analysis_says_to_analyze_again():
    def gone(request):
        return httpx.Response(410, json={"code": "analysis_expired"})

    server = build_server(_client(gone))
    with pytest.raises(Exception) as excinfo:
        _call(server, "list_capabilities", {"analysis_id": "ana_stale"})

    message = _message(excinfo)
    assert "expired" in message.lower()
    assert "analyze_source" in message, "name the tool that fixes it"


def test_a_refused_request_carries_the_stable_codes():
    def refused(request):
        return httpx.Response(
            422,
            json={
                "detail": {
                    "valid": False,
                    "errors": [{"code": "output_type_not_supported", "message": "no"}],
                }
            },
        )

    server = build_server(_client(refused))
    with pytest.raises(Exception) as excinfo:
        _call(
            server,
            "generate",
            {"analysis_id": "ana_1", "outputs": [{"id": "x", "type": "ocr"}]},
        )

    assert "output_type_not_supported" in _message(excinfo)


def test_a_malformed_output_is_caught_before_the_engine_is_called():
    """The server already answers this one well, and it must keep doing so:
    the guidance is worth more than the round trip it saves."""

    def unreachable(request):  # pragma: no cover - must never be reached
        raise AssertionError("the engine should not have been called")

    server = build_server(_client(unreachable))
    with pytest.raises(Exception) as excinfo:
        _call(server, "generate", {"analysis_id": "ana_1", "outputs": [{"id": "x"}]})

    message = _message(excinfo)
    assert "no 'type'" in message and "Example" in message


def test_a_missing_record_is_not_dressed_up_as_something_else():
    def missing(request):
        return httpx.Response(404, json={"detail": "job not found"})

    server = build_server(_client(missing))
    with pytest.raises(Exception) as excinfo:
        _call(server, "get_job", {"job_id": "job_nope"})

    assert "no such record" in _message(excinfo).lower()


def test_wrapping_did_not_break_the_tool_surface():
    """The wrapper must stay invisible to the protocol: same tools, same
    schemas. `functools.wraps` is what keeps the generated input schema
    correct, and a decorator that quietly emptied it would disable every
    argument."""

    def ok(request):
        return httpx.Response(200, json={})

    server = build_server(_client(ok))
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert "analyze_source" in tools
    schema = tools["analyze_source"].input_schema
    assert schema["required"] == ["url"]
    assert set(schema["properties"]) == {"url", "credential"}
