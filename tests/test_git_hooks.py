"""The commit guards must stay committable, executable and armed.

The hooks refuse a commit authored by the wrong identity, and a message
carrying tool attribution. Both are protections whose failure is *silent* —
nothing tells you a hook was never installed, and a wrong author in a public
commit cannot be corrected without rewriting published history. So the guard
guards the guard:

* the allowlist `.gitignore` hides extensionless files by default, and a hook
  MUST be named exactly `pre-commit` — the same rule that once hid the Typst
  template (D-41) and the renamed extension's sources;
* a hook without its execute bit is ignored by Git without a word;
* `core.hooksPath` is what makes tracked hooks run at all.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]
HOOKS = REPO / ".githooks"
NAMES = ("pre-commit", "commit-msg")


def test_the_hooks_exist_and_are_executable():
    for name in NAMES:
        hook = HOOKS / name
        assert hook.is_file(), f"{name} is missing"
        assert os.access(hook, os.X_OK), (
            f"{name} has no execute bit — Git skips it silently"
        )


def test_the_allowlist_does_not_hide_them():
    """Extensionless by Git's own contract, so the allowlist must name them."""
    result = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=REPO,
        input="\n".join(f".githooks/{name}" for name in NAMES),
        capture_output=True,
        text=True,
        check=False,
    )
    hidden = [line for line in result.stdout.split("\n") if line.strip()]
    assert not hidden, (
        f"the allowlist hides {hidden} — they could never be committed, so "
        "every clone would be unguarded"
    )


def test_the_identity_guard_names_the_public_address():
    hook = (HOOKS / "pre-commit").read_text()
    assert 'expected="yann@orieult.com"' in hook
    assert "git config user.email" in hook, "the message must say how to fix it"


def test_the_attribution_guard_matches_trailers_not_prose():
    """A commit that legitimately discusses the Anthropic summarizer or a
    model id must pass; only attribution trailers and badges are refused."""
    hook = (HOOKS / "commit-msg").read_text()
    assert "co-authored-by:" in hook.lower()
    assert "generated with" in hook.lower()
    # Anchored to the start of a line: prose mentioning a vendor stays legal.
    assert "'^(co-authored-by:" in hook


def test_make_install_arms_them():
    """A fresh clone must be guarded before its first commit, without anyone
    remembering to run an extra step."""
    makefile = (REPO / "Makefile").read_text()
    assert "install: hooks" in makefile, "make install must depend on hooks"
    assert "git config core.hooksPath .githooks" in makefile
