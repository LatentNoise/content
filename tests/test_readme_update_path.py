"""The documented update path has to refresh the stack definition, not only the images.

`docker compose pull` fetches images. It does not touch `docker-compose.yml` —
and on the Quick start path that file is a *copy* the operator curled once, not
a tracked file a `git pull` would carry forward. Since a container receives only
what its service's `environment:` block names, an operator whose compose file
has gone stale keeps silently dropping every setting added since they installed,
no matter how correctly they write it in `.env`.

That is the same class of failure `test_compose_forwards_documented_settings`
guards from the other side, and the cost is already on record: compose forwarded
none of the six sign-in variables shipped in 0.8.0, so `CONTENT_AUTH_MODE=token`
left a wide-open instance on the deployment path most self-hosters take. Fixing
the file in the repository does not reach anyone who follows an update procedure
that never re-fetches it — and no release reaches them either, because a release
re-points image tags and the compose file is not an image.

So the instruction, not just the file, is the thing to keep honest.

Standard library only, like the rest of the root suite.
"""

from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
README = REPO / "README.md"

# The published-images install and its update path both live under "Quick
# start"; the from-source path below it gets the compose file from the clone,
# so a `git pull` already carries it and this invariant does not apply there.
UPDATE_LEAD = "Update later with:"
COMPOSE_PATH = "deploy/docker-compose.yml"


def _update_block() -> str:
    """The fenced shell block the README offers as the update procedure."""
    text = README.read_text(encoding="utf-8")
    assert UPDATE_LEAD in text, (
        f"README no longer says {UPDATE_LEAD!r}. The update procedure moved or "
        "was reworded — re-point this guard at it rather than deleting it."
    )
    after = text.split(UPDATE_LEAD, 1)[1]
    block = re.search(r"```bash\n(.*?)```", after, re.DOTALL)
    assert block, "no fenced bash block follows the update instructions"
    return block.group(1)


def test_the_update_path_refetches_the_compose_file() -> None:
    """Pulling images is not an update if the stack definition stays behind."""
    block = _update_block()
    assert COMPOSE_PATH in block, (
        "The documented update path does not re-fetch "
        f"{COMPOSE_PATH}: an operator who follows it keeps their original "
        "compose file, so every setting added since they installed is "
        "silently dropped even when set in .env. Add the curl back."
    )


def test_the_update_path_does_not_clobber_the_operators_env() -> None:
    """`.env` is the operator's own file; the install seeds it, an update must not.

    The install writes `.env` from `.env.example`. Repeating that on update
    would overwrite the configuration the operator has since written into it —
    turning a routine upgrade into a silent reset of every knob, which is a
    worse failure than the stale compose file this procedure exists to fix.
    """
    block = _update_block()
    assert not re.search(r"-o\s+\.env\b", block), (
        "The update path writes over .env. It must refresh the compose file "
        "only and leave the operator's configuration alone; point them at "
        ".env.example to read the new knobs instead."
    )
