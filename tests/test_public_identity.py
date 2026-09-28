"""The private identity must not reach the public history.

`AGENTS.md` § *Public identity* states the rule this file mechanises:
everything the project publishes carries `Yann Orieult <yann@orieult.com>` /
`YannOrieult`, and the maintainer's other account and its gmail address must
appear nowhere public.

`.githooks/pre-commit` already guards the *author* of a commit. Nothing guarded
its *content*, and the two failures have the same shape: a wrong identity in a
published commit cannot be corrected without rewriting history that other
people have already pulled. So this guards the content side.

Deliberately not a blanket ban on the account name. It has legitimate uses in
tracked files — `README.md` links to the standalone HomeTube repository, which
really does live under that account, and a research document quotes a playlist
folder from the maintainer's own library. Banning the string outright would
either fail on those or teach the next person to add exceptions until the guard
means nothing. What is banned is the part with no legitimate use at all: the
private mail address.
"""

from __future__ import annotations

import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]

# Assembled rather than written out, so this guard is not itself the occurrence
# it forbids — and so `git grep` for the address does not simply find the test.
PRIVATE_MAIL = "egalitarianmonkey" + "@" + "gmail.com"

# Ignored on purpose (`.gitignore`, and the comment there says why): they carry
# the § Public identity section itself, which names what must stay private.
NEVER_TRACKED = ("AGENTS.md", "CLAUDE.md")


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.split("\n") if line.strip()]


def test_the_private_mail_address_is_in_no_tracked_file():
    """`git grep` over the index, so it sees exactly what a clone would get."""
    result = subprocess.run(
        ["git", "grep", "-nIiF", PRIVATE_MAIL],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    # git grep exits 1 for "no match", which is the state we want.
    hits = [line for line in result.stdout.split("\n") if line.strip()]
    assert not hits, (
        "the maintainer's private mail address is in tracked files, and "
        "publishing it cannot be undone:\n  " + "\n  ".join(hits)
    )


def test_the_agent_instructions_stay_unpublished():
    """The rule `.gitignore` explains — un-ignoring these leaks the addresses
    that AGENTS.md § Public identity exists to keep out of anything public."""
    tracked = set(_tracked_files())
    leaked = [name for name in NEVER_TRACKED if name in tracked]
    assert not leaked, (
        f"{leaked} became tracked — these spell out the private account and "
        "mail address. See the comment above them in .gitignore."
    )
