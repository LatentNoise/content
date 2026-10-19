"""Architectural guard-rail: consumers speak to the engine ONLY through the SDK.

The CLI, the MCP server and the web UIs must never import an HTTP client — the
only door to the REST API is ``content_sdk``. This structurally enforces "no
direct HTTP, no duplicated transport" across the repo (test files are excluded:
they legitimately use httpx to build mock transports).
"""

from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]

# Runtime source of every consumer (NOT their tests, NOT the SDK itself).
CONSUMER_SOURCES = [
    "apps/cli/content_cli",
    "apps/mcp/content_mcp",
    "apps/web-hometube/app.py",
    "apps/web-studio/app.py",
    "apps/web-admin/app.py",
]

# Direct HTTP clients. urllib.parse is fine (URL manipulation, not requests).
# `httpx2?` covers both spellings deliberately: plain `httpx\b` cannot match
# `import httpx2` (there is no word boundary between "x" and "2"), and httpx2 is
# not hypothetical here -- `mcp` depends on it, so it is importable today from
# apps/mcp/content_mcp, the consumer most likely to reach for it.
FORBIDDEN = re.compile(
    r"^\s*(?:import|from)\s+"
    r"(requests|httpx2?|aiohttp|urllib3|urllib\.request|http\.client)\b",
    re.MULTILINE,
)


def _iter_runtime_py():
    for entry in CONSUMER_SOURCES:
        path = REPO / entry
        if path.is_dir():
            yield from (p for p in path.rglob("*.py") if "test" not in p.parts)
        elif path.exists():
            yield path


def test_consumers_never_import_an_http_client():
    offenders = []
    for file in _iter_runtime_py():
        for match in FORBIDDEN.finditer(file.read_text()):
            offenders.append(f"{file.relative_to(REPO)}: {match.group(0).strip()}")
    assert not offenders, (
        "HTTP must go through content_sdk only; direct HTTP imports found:\n"
        + "\n".join(offenders)
    )


def test_the_sdk_is_the_only_place_that_imports_httpx():
    """Positive control: the SDK *does* own the transport, so the scan above is
    meaningful (it would catch a consumer that bypassed the SDK)."""
    sdk_transport = (REPO / "packages/python-sdk/content_sdk/_transport.py").read_text()
    assert "import httpx" in sdk_transport


def test_the_pattern_catches_every_spelling_it_claims_to():
    """The scan above is only worth its name if the pattern matches what a real
    bypass would look like. `import httpx2` slipped through until 2026-10-19:
    `httpx\\b` finds no word boundary before the "2", and httpx2 ships in this
    venv as a dependency of `mcp`. Negative cases keep the pattern honest the
    other way -- urllib.parse and a name that merely starts with a client's.
    """
    for line in (
        "import requests",
        "import httpx",
        "import httpx2",
        "from httpx2 import Client",
        "    import aiohttp",
        "from urllib.request import urlopen",
        "import http.client",
        "import urllib3",
    ):
        assert FORBIDDEN.search(line), f"should be forbidden but is not: {line!r}"

    for line in (
        "from urllib.parse import urlparse",
        "import requests_like_helper",
        "from content_sdk import Client",
        "# import httpx",
    ):
        assert not FORBIDDEN.search(line), f"should be allowed but is not: {line!r}"
