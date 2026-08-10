"""The deploy compose file is the generated twin of the source one.

deploy/docker-compose.yml is what a user without the source tree runs (curl +
`docker compose up -d`); the root docker-compose.yml is what a clone builds
from. They must describe the SAME deployment, so the deploy file is generated
(`make deploy-compose`) and this guard fails when the committed copy no longer
matches its source — the drift becomes a red test instead of a user's broken
install.

Standard library only, deliberately: the root suite runs in the plain test
venv, which carries no YAML parser.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from gen_deploy_compose import TARGET, render, strip_build_blocks


def test_the_committed_deploy_file_matches_its_source():
    assert TARGET.read_text() == render(), (
        "deploy/docker-compose.yml is stale — docker-compose.yml changed "
        "without regenerating it. Run: make deploy-compose"
    )


def test_the_deploy_file_never_builds():
    """A user running it has no source tree: every service must pull."""
    text = TARGET.read_text()
    for line in text.splitlines():
        assert line.strip() != "build:", "the deploy file must not build"
    images = [
        line.strip() for line in text.splitlines() if line.strip().startswith("image:")
    ]
    assert len(images) == 4, f"expected four services, found {len(images)}"
    for image in images:
        assert "ghcr.io/latentnoise/" in image, image


def test_stripping_leaves_the_surrounding_service_intact():
    """The generator removes the build block and nothing around it."""
    source = """\
services:
  app:
    image: example:latest
    build:
      context: .
      args:
        FOO: bar
    ports:
      - "1:1"
"""
    assert (
        strip_build_blocks(source)
        == """\
services:
  app:
    image: example:latest
    ports:
      - "1:1"
"""
    )
