"""The deploy compose file is the build-free twin of the source one.

deploy/docker-compose.yml is what a user without the source tree runs
(curl + docker compose up); the root docker-compose.yml is what a clone
builds from. They must describe the SAME deployment — same services, same
images, same environment, same ports, same volumes — differing only by the
`build:` blocks. Two hand-maintained copies drift; this guard makes the
drift a test failure instead of a support ticket.
"""

from __future__ import annotations

import pathlib

import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]


def _services(path: str) -> dict:
    return yaml.safe_load((REPO / path).read_text())["services"]


def test_deploy_compose_is_the_source_compose_minus_build():
    source = _services("docker-compose.yml")
    deploy = _services("deploy/docker-compose.yml")

    assert set(deploy) == set(source), "service sets differ"
    for name, service in source.items():
        expected = {key: value for key, value in service.items() if key != "build"}
        assert "build" not in deploy[name], f"{name}: deploy file must not build"
        assert deploy[name] == expected, (
            f"{name}: deploy/docker-compose.yml drifted from docker-compose.yml "
            f"— update it (everything except `build:` must match)"
        )


def test_deploy_compose_never_builds():
    for name, service in _services("deploy/docker-compose.yml").items():
        assert "image" in service, f"{name} has no image to pull"
        assert service["image"].startswith("ghcr.io/latentnoise/"), name
