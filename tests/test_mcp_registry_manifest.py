"""`server.json` — the MCP registry's copy of what `content-mcp` is.

The registry entry pins a **version**, and a stale entry is worse than no
entry: it tells people 0.5.0 is current while 0.6.0 is on PyPI. So the file is
tracked, versioned by `make version-update` like every other declaration, and
checked here.

The schema itself lives at a URL and this suite is hermetic, so what is
verified here is everything that does not need the network: the shape the
registry requires, the constraints that actually bit (the description length),
and agreement with the package this repository publishes. Validating against
the live schema is a step in the publish runbook
(docs/operations/mcp-registry.md), where a newer schema can also be noticed.
"""

from __future__ import annotations

import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = json.loads((REPO / "server.json").read_text())


def _pypi_package() -> dict:
    return next(p for p in MANIFEST["packages"] if p["registryType"] == "pypi")


def test_the_namespace_is_ours_and_the_name_is_the_bare_word():
    """Registry names are namespaced, so `content` is available inside our own
    namespace — no `latentcontent`-style workaround needed."""
    assert MANIFEST["name"] == "io.github.LatentNoise/content"


def test_the_description_fits_what_the_registry_accepts():
    """maxLength 100. The first draft was 158 characters and would have been
    rejected at publish time, after the login dance."""
    assert 0 < len(MANIFEST["description"]) <= 100
    assert 0 < len(MANIFEST["title"]) <= 100


def test_the_required_package_keys_are_present():
    package = _pypi_package()
    for key in ("registryType", "identifier", "transport"):
        assert key in package, f"the registry requires packages[].{key}"
    assert package["identifier"] == "content-mcp"
    assert package["transport"]["type"] == "stdio"


def test_the_two_versions_agree_with_the_published_package():
    """The manifest carries the version twice — the server's and the package's
    — and `make version-update` rewrites both. They must not drift apart, and
    they must match the wheel this repository builds."""
    mcp_pyproject = (REPO / "apps/mcp/pyproject.toml").read_text()
    published = next(
        line.split('"')[1]
        for line in mcp_pyproject.splitlines()
        if line.startswith("version = ")
    )
    assert MANIFEST["version"] == published
    assert _pypi_package()["version"] == published


def test_the_environment_variables_are_the_ones_the_server_reads():
    """What a registry entry is *for*: someone finding it should learn how to
    point the server at their engine without opening the repository."""
    declared = {v["name"] for v in _pypi_package().get("environmentVariables", [])}
    assert "CONTENT_API_URL" in declared
    server = (REPO / "apps/mcp/content_mcp/server.py").read_text()
    service = (REPO / "apps/mcp/content_mcp/service.py").read_text()
    for name in declared:
        assert name in server + service, f"{name} is advertised but never read"


def test_the_pypi_readme_carries_the_ownership_marker():
    """How the registry proves we own `content-mcp`: it looks for
    `mcp-name: <server name>` in the package README, which becomes the PyPI
    description. Without it, publishing is rejected — and because the check
    reads the **published** description, the marker has to be on PyPI *before*
    the registry entry can be published. 0.5.0 went out without it.
    """
    readme = (REPO / "apps/mcp/README.md").read_text()
    assert f"mcp-name: {MANIFEST['name']}" in readme

    # It must reach PyPI, which means being inside the file pyproject declares
    # as the long description — not a sibling doc.
    pyproject = (REPO / "apps/mcp/pyproject.toml").read_text()
    assert 'readme = "README.md"' in pyproject


def test_no_secret_is_declared_as_a_default():
    for variable in _pypi_package().get("environmentVariables", []):
        assert not variable.get("isSecret"), (
            "a secret must never be declared with a default in a public manifest"
        )
