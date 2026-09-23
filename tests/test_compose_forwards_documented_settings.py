"""`.env.example` promises a knob works; compose is what makes it reach the engine.

Compose passes to a container **only** what the service's `environment:` block
names. So a variable documented in `.env.example` and read by the engine, but
absent from that block, is a setting an operator configures and nobody reads —
and the failure is silent and points the wrong way: the stack comes up healthy
and behaves as though the line had never been written.

That is not hypothetical. All six sign-in variables shipped in 0.8.0 alongside
the feature they configure, and compose forwarded none of them until 0.8.6, so
`CONTENT_AUTH_MODE=token` left a wide-open instance on the deployment path most
self-hosters take. Thirty-two variables were in that state in all.

This guard closes the class rather than the instance: the next batch of
settings is caught by a red test instead of by whoever trusted the file.

Standard library only, deliberately: the root suite runs in the plain test
venv, which carries no YAML parser.
"""

from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
ENV_EXAMPLE = REPO / ".env.example"
SOURCE = REPO / "docker-compose.yml"
ENGINE = REPO / "apps" / "backend" / "content"

# No exclusion list, deliberately: with the thirty-two forwarded, every
# variable `.env.example` documents and the engine reads is now passed, so
# there is nothing to carve out. The paths pinned to the container
# (CONTENT_DATA_DIR, CONTENT_DB_PATH, CONTENT_DELIVERY_DIR,
# CONTENT_ALLOWED_INPUT_ROOTS) are forwarded as literals rather than `${}`
# forms and are not offered in `.env.example`, so they never enter the set.
# An exclusion list here would exclude nothing and imply otherwise.


def _documented_in_env_example() -> set[str]:
    """Every CONTENT_* the operator's own file presents as a knob, commented
    examples included — a commented default is still a documented promise."""
    return set(
        re.findall(
            r"^#?\s*(CONTENT_[A-Z0-9_]+)=", ENV_EXAMPLE.read_text(), re.MULTILINE
        )
    )


def _read_by_the_engine() -> set[str]:
    names: set[str] = set()
    for path in ENGINE.rglob("*.py"):
        names |= set(re.findall(r'"(CONTENT_[A-Z0-9_]+)"', path.read_text()))
    return names


def _forwarded_to_the_engine() -> set[str]:
    """The names in the `content` service's own environment block."""
    text = SOURCE.read_text()
    block = text.split("  content:", 1)[1].split("    ports:", 1)[0]
    return set(re.findall(r"-\s*(CONTENT_[A-Z0-9_]+)=", block))


def test_every_documented_engine_setting_reaches_the_engine():
    expected = _documented_in_env_example() & _read_by_the_engine()
    missing = sorted(expected - _forwarded_to_the_engine())
    assert not missing, (
        "documented in .env.example and read by the engine, but not forwarded "
        "by docker-compose.yml — an operator sets these and nobody reads them:\n  "
        + "\n  ".join(missing)
    )


def test_a_boolean_setting_never_forwards_an_empty_default():
    """`${VAR:-}` is safe for a string and a trap for a flag.

    The engine's `_to_bool` treats an empty string as a *value* — `False` —
    rather than as "unset", so `${VAR:-}` on a flag defaulting to true silently
    inverts it. `CONTENT_WORKER_ENABLED` would accept jobs and run none;
    `CONTENT_SESSION_COOKIE_SECURE` would ship cookies without the flag.
    """
    config = (ENGINE / "config.py").read_text()
    flags = {
        name
        for name in re.findall(
            r"_to_bool\(\s*os\.getenv\(\s*\"(CONTENT_[A-Z0-9_]+)\"", config
        )
    }
    # Multi-line calls put the name on the next line; catch those too.
    flags |= set(
        re.findall(
            r"_to_bool\(\s*\n?\s*os\.getenv\(\s*\n?\s*\"(CONTENT_[A-Z0-9_]+)\"",
            config,
        )
    )
    text = SOURCE.read_text()
    block = text.split("  content:", 1)[1].split("    ports:", 1)[0]
    offenders = [name for name in sorted(flags) if f"- {name}=${{{name}:-}}" in block]
    assert not offenders, (
        "these are booleans read through _to_bool, where an empty string means "
        "False rather than 'unset' — give each an explicit default in "
        f"docker-compose.yml: {offenders}"
    )
