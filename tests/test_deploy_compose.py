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

from gen_deploy_compose import SOURCE, TARGET, render, strip_build_blocks


def test_the_committed_deploy_file_matches_its_source():
    assert TARGET.read_text() == render(), (
        "deploy/docker-compose.yml is stale — docker-compose.yml changed "
        "without regenerating it. Run: make deploy-compose"
    )


def _images(text: str) -> list[str]:
    return [
        line.strip() for line in text.splitlines() if line.strip().startswith("image:")
    ]


def test_the_deploy_file_never_builds():
    """A user running it has no source tree: every service must pull."""
    text = TARGET.read_text()
    for line in text.splitlines():
        assert line.strip() != "build:", "the deploy file must not build"


def test_every_service_of_the_source_can_be_pulled():
    """Counted against the source, never against a literal.

    This assertion used to say `== 4`, and a fifth service (the bundled
    language model) was added to the source without it noticing — the guard
    that exists to catch drift had drift of its own. Deriving the number from
    the file it is guarding is the only version that stays true.

    Third-party images are expected: a bundled runtime is somebody else's
    image by definition. What must hold is that Content's own services pull a
    published tag rather than build.
    """
    source_images = _images(SOURCE.read_text())
    deploy_images = _images(TARGET.read_text())
    assert len(deploy_images) == len(source_images), (
        f"the source declares {len(source_images)} images and the deploy file "
        f"{len(deploy_images)} — regenerate with: make deploy-compose"
    )
    ours = [i for i in deploy_images if "latentnoise" in i]
    assert len(ours) == 4, f"expected the four Content images, found {len(ours)}"
    for image in ours:
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
