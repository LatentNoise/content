"""The runtime dependency set must describe what the engine actually needs.

Between 0.5.0 and 0.6.3 every published backend image failed to boot. The
uploads route (ADR 0020) takes an `UploadFile`, FastAPI requires
`python-multipart` to *register* such a route, and the package was declared
nowhere — so `create_app()` raised before the healthcheck could even run, and
`docker compose up -d` said only "container content is unhealthy".

Two properties made it survive four releases:

* **Nothing imports it.** `grep -rn multipart apps/backend/content/` finds
  nothing; FastAPI imports it internally and lazily. No import-vs-declaration
  audit can see it.
* **The development environment supplies it by accident.** `make install` also
  installs `apps/mcp`, whose `mcp>=1.2` dependency pulls `python-multipart`.
  So the CI suite was green while the shipped artifact was broken.

The real guard is in `ci.yml`: the image is built and `create_app()` is called
inside it before anything is pushed. This file guards the declaration itself,
which is cheap and fails with a clearer message than a container that exits 1.
"""

from __future__ import annotations

import pathlib
import re
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - the project requires 3.11+
    import tomli as tomllib

REPO = pathlib.Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((REPO / "apps/backend/pyproject.toml").read_text())
DOCKERFILE = (REPO / "apps/backend/Dockerfile").read_text()


def _names(requirements: list[str]) -> set[str]:
    return {
        re.split(r"[<>=!\[; ]", r, maxsplit=1)[0].strip().lower() for r in requirements
    }


def test_python_multipart_is_a_declared_runtime_dependency():
    """The one FastAPI needs to build the app, not to serve a request."""
    assert "python-multipart" in _names(PYPROJECT["project"]["dependencies"])


def test_the_image_installs_from_the_pyproject_rather_than_a_copied_list():
    """The duplication is what allowed the divergence: the code gained a
    feature, the image's hand-written list did not. One source of truth means
    the two cannot disagree again — so the Dockerfile must keep reading the
    pyproject, and must not go back to naming packages by hand."""
    assert "requirements.txt" in DOCKERFILE and "tomllib" in DOCKERFILE
    # Shell continuations joined first, so one command reads as one line.
    joined = DOCKERFILE.replace("\\\n", " ")
    installs = [line for line in joined.splitlines() if "pip install" in line]
    runtime = [i for i in installs if "faster-whisper" not in i]
    for line in runtime:
        assert "-r /tmp/requirements.txt" in line, (
            "the runtime install must come from the pyproject, not a list "
            f"repeated in the Dockerfile: {line!r}"
        )


def test_the_boot_check_runs_before_anything_is_pushed():
    """A unit test cannot see a missing dependency that only the built image
    lacks. The workflow must construct the app inside the image, and must do it
    *before* the pushing step, or the guard proves nothing."""
    workflow = (REPO / ".github/workflows/ci.yml").read_text()
    assert "create_app()" in workflow, "no boot check in the image"
    assert workflow.index("create_app()") < workflow.index("push: true"), (
        "the boot check must run before the push, not after"
    )
