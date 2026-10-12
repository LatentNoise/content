"""Every directory `make lint` checks must resolve this repository's own baseline.

`make lint` advertises "ruff: E, F, W, I" over all of `SRC`, but for most of that
list it was not true. Only three directories declared the baseline --
`apps/backend`, `apps/web-hometube` and `services/mailer`, each in its own
`pyproject.toml`. The other eight resolved no ruff configuration at all, because
a `pyproject.toml` without a `[tool.ruff]` section does not stop ruff's upward
search and there was nothing at the repository root to stop it either. Those
directories were therefore linted against whatever ruff's *built-in defaults*
happened to be, which makes the gate a property of the installed tool rather
than of this repository.

That bill arrived on 2026-10-09. `ruff>=0.8` is unpinned, 0.17.0 was published at
19:46 UTC that evening -- fifteen hours after the last green run on `main` -- and
it widened ruff's defaults to include `UP`, `FURB`, `B`, `S` and `ASYNC`. Ten
pre-existing call sites in `content_sdk`, `content_mcp` and `tests/` became
errors without a line of code changing, and the next push to `main` would have
been red for a reason no commit could explain.

So `ruff.toml` at the root closes the search, and this file guards the two
properties that matter: no linted directory falls back to the defaults, and every
configuration in the repository asks for the same rules.
"""

from __future__ import annotations

import pathlib
import re
import tomllib

REPO = pathlib.Path(__file__).resolve().parents[1]
MAKEFILE = (REPO / "Makefile").read_text()

# The baseline, deliberately conservative: real errors, pyflakes, warnings,
# import order. It excludes the opinionated rewrites (UP, RUF, FURB) on purpose
# -- see docs/development/validation.md and the comment in ruff.toml.
BASELINE = ["E", "F", "W", "I"]

# A pyproject.toml only governs ruff when it carries a [tool.ruff] section;
# otherwise ruff keeps walking up. These are the file names that do stop it.
STOP_FILES = (".ruff.toml", "ruff.toml")


def _linted_directories() -> list[str]:
    match = re.search(r"^SRC\s*:=\s*(.+)$", MAKEFILE, re.MULTILINE)
    assert match, "the Makefile no longer defines SRC -- this guard cannot see it"
    return match.group(1).split()


def _governing_config(start: pathlib.Path) -> pathlib.Path | None:
    """The config ruff would use for a file in `start`, as ruff resolves it."""
    for directory in [start, *start.parents]:
        for name in STOP_FILES:
            if (directory / name).is_file():
                return directory / name
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file() and "[tool.ruff" in pyproject.read_text():
            return pyproject
        if directory == REPO:
            break
    return None


def _select_of(config: pathlib.Path) -> list[str]:
    data = tomllib.loads(config.read_text())
    if config.name == "pyproject.toml":
        data = data["tool"]["ruff"]
    return data["lint"]["select"]


def test_the_repository_root_declares_the_baseline():
    """The file that stops ruff's upward search, so defaults are never reached."""
    root = REPO / "ruff.toml"
    assert root.is_file(), "ruff.toml is missing -- linting falls back to ruff defaults"
    assert _select_of(root) == BASELINE


def test_every_linted_directory_resolves_a_ruff_configuration():
    """Not one entry of SRC may be linted against ruff's built-in defaults."""
    unconfigured = [
        directory
        for directory in _linted_directories()
        if _governing_config(REPO / directory) is None
    ]
    assert not unconfigured, (
        "these directories resolve no ruff configuration and would be linted "
        f"against whatever the installed ruff defaults to: {unconfigured}"
    )


def test_every_ruff_configuration_asks_for_the_same_rules():
    """One baseline, so a per-directory config cannot drift from the Makefile."""
    configs = {
        config
        for directory in _linted_directories()
        if (config := _governing_config(REPO / directory)) is not None
    }
    drifting = {
        str(config.relative_to(REPO)): select
        for config in configs
        if (select := _select_of(config)) != BASELINE
    }
    assert not drifting, f"these configurations no longer select {BASELINE}: {drifting}"


def test_the_makefile_still_advertises_the_baseline_it_enforces():
    """The help text is the promise this file exists to keep honest."""
    advertised = re.search(r"^lint:.*##\s*(.+)$", MAKEFILE, re.MULTILINE)
    assert advertised, "the Makefile no longer documents what `make lint` checks"
    assert "E, F, W, I" in advertised.group(1)
