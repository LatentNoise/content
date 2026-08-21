"""Every interactive prompt in the Makefile accepts Enter.

Asked for directly: "It's nice with the Y and the N, but I would like to be
able to press Enter to validate and not being forced to press Y."

The convention is one line: **Enter accepts what is offered**, and the bracket
says what that is — `[Y/n]` for a yes/no, `[value]` for a default. A prompt
that reads an answer without providing a default breaks it, which is easy to
reintroduce because each prompt is a separate blob of shell.

Nothing dangerous rides on a bare Enter: `version-tag` asks twice, and the
question that publishes is asked right after printing what pushing starts.
"""

from __future__ import annotations

import pathlib
import re

MAKEFILE = (pathlib.Path(__file__).resolve().parents[1] / "Makefile").read_text()
# Shell continuations joined, so one command reads as one line.
FLAT = MAKEFILE.replace("\\\n", " ")
READS = re.findall(r"read -r (\w+)", FLAT)


def test_there_are_prompts_to_check():
    """A guard on the guard: if the reads move or vanish, this file is testing
    nothing and should be updated rather than silently passing."""
    assert len(READS) >= 3, f"expected the version prompts, found {READS}"


def test_every_prompt_has_a_default_so_enter_is_enough():
    for variable in READS:
        assert f"${{{variable}:-" in FLAT, (
            f"`read -r {variable}` has no default: pressing Enter would fall "
            f"through to the refusal branch. Use ${{{variable}:-y}} (or a "
            f"value) so Enter accepts what the prompt offers."
        )


def test_a_yes_no_prompt_advertises_which_way_enter_goes():
    """`[y/N]` promises that Enter declines. Since Enter now accepts, the
    bracket has to say `[Y/n]` — a prompt that lies about its default is worse
    than one that demands a keystroke."""
    assert "[y/N]" not in MAKEFILE, "a prompt still advertises Enter as 'no'"
    assert MAKEFILE.count("[Y/n]") >= 2


def test_the_version_prompt_offers_the_patch_bump():
    """`version-update` asks for a value, not a yes/no: Enter takes the patch
    suggestion it just printed, and the bracket shows it."""
    assert "new version (x.y.z) [%s]: " in MAKEFILE
    # `$$` is Make's escape for a shell `$`, so the source reads `$${v:-...}`.
    assert "v=$${v:-$$suggested}" in FLAT
