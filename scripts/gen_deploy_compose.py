#!/usr/bin/env python3
"""Generate deploy/docker-compose.yml from the repository's docker-compose.yml.

Two compose files describe one deployment: the root one builds from the source
tree, the deploy one pulls the published images (the README's clone-free
install). They must never diverge, so the deploy file is *generated* rather
than hand-maintained — `make deploy-compose` writes it and
`tests/test_deploy_compose.py` fails if the committed copy is stale.

The transformation is deliberately mechanical: drop every `build:` block (a
user without the source has nothing to build from) and swap the header.
Nothing else is rewritten, which is what makes the twin trustworthy — any
service, image, port, volume or comment added to the source appears verbatim
in the deploy file after regeneration.

Standard library only: this runs inside the plain test venv on CI.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
SOURCE = REPO / "docker-compose.yml"
TARGET = REPO / "deploy" / "docker-compose.yml"

HEADER = """\
# Content — deployment without the source tree.
#
# GENERATED from ../docker-compose.yml by scripts/gen_deploy_compose.py
# (`make deploy-compose`). Edit the source file, then regenerate.
#
# The published images, nothing to build:
#
#   mkdir content && cd content
#   curl -fsSLO https://raw.githubusercontent.com/LatentNoise/content/main/deploy/docker-compose.yml
#   curl -fsSL -o .env https://raw.githubusercontent.com/LatentNoise/content/main/.env.example
#   docker compose up -d
#
# Identical to the source compose — same services, images, environment, ports
# and volumes — minus the `build:` blocks a clone uses to build locally.
#
"""


def strip_build_blocks(text: str) -> str:
    """Remove each `build:` key and the indented block beneath it."""
    kept: list[str] = []
    skipping = False
    block_indent = 0
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        if skipping:
            indent = len(stripped) - len(stripped.lstrip(" "))
            # A non-blank line at or above the `build:` indent ends the block.
            if stripped.strip() and indent <= block_indent:
                skipping = False
            else:
                continue
        if stripped.strip() == "build:":
            block_indent = len(stripped) - len(stripped.lstrip(" "))
            skipping = True
            continue
        kept.append(line)
    return "".join(kept)


def render() -> str:
    body = strip_build_blocks(SOURCE.read_text())
    # The source header describes the clone workflow; the deploy file has its
    # own. Everything from `services:` on is shared.
    return HEADER + body[body.index("services:") :]


def main() -> int:
    generated = render()
    if "--check" in sys.argv:
        current = TARGET.read_text() if TARGET.exists() else ""
        if current != generated:
            print(f"{TARGET.relative_to(REPO)} is stale — run: make deploy-compose")
            return 1
        print(f"{TARGET.relative_to(REPO)} is up to date")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(generated)
    print(f"wrote {TARGET.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
