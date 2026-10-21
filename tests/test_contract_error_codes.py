"""`docs/contract.md` §6 publishes the error codes; `errors.py` is what emits them.

A `code` is the one part of a refusal a client is allowed to branch on —
`AGENTS.md`: *"Error codes are stable machine identifiers; only human messages
may change."* So the §6 list is not prose about the engine, it is the published
form of the engine's answer, and a client that handles exactly what it names is
behaving correctly.

That list had fallen **ten codes behind** the module, and the gap was invisible
from either side: nothing in `errors.py` points at a reader, and nothing in the
document knows a constant was added next to it. Two of the ten appeared nowhere
in `contract.md` at all — `delivery_not_supported`, which an installation with
`CONTENT_DELIVERY_SCOPE=off` returns for a perfectly valid request, and the
`reuse_unavailable` warning. The other eight were documented beside their route
in §8 but missing from the enumeration a client reads to know the set. All ten
are emitted from live code paths and all ten are asserted by tests, so the
engine was right and only the contract was stale — the failure mode this guard
exists to make loud.

It checks both directions, because each catches a different mistake: a code in
the module and not in the document is an undocumented public answer, and a code
in the document and not in the module is a promise to clients that nothing
keeps. It also checks the error/warning split, since a code documented as a
warning but emitted as an error tells a client it may continue when it may not.

Standard library only, and the module is read as text rather than imported: the
root suite is a repository-coherence suite and must not depend on the backend
being importable.
"""

from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
ERRORS = REPO / "apps" / "backend" / "content" / "domain" / "errors.py"
CONTRACT = REPO / "docs" / "contract.md"

# The italic label that opens each grouped run in §6. The three error groups
# are listed separately so a failure message can say where a new code belongs,
# but the contract only distinguishes errors from warnings, so that is what the
# comparison is made on.
ERROR_GROUPS = ("Structural", "Analysis resources and uploads", "Feasibility")
WARNING_GROUP = "Warnings"


def _emitted() -> tuple[set[str], set[str]]:
    """The codes `errors.py` defines, split on its own `# Warning codes` marker."""
    source = ERRORS.read_text()
    marker = source.index("# Warning codes")
    errors: set[str] = set()
    warnings: set[str] = set()
    for match in re.finditer(r'^([A-Z][A-Z0-9_]*)\s*=\s*"([a-z0-9_]+)"', source, re.M):
        bucket = warnings if match.start() > marker else errors
        bucket.add(match.group(2))
    assert errors and warnings, "the constant scan found nothing — has errors.py moved?"
    return errors, warnings


def _without_glosses(run: str) -> str:
    """Drop the parenthesised glosses, keeping only what the run names at top level.

    That is the document's own convention: a code is named directly, and the
    prose explaining it sits in parentheses after it. The glosses legitimately
    mention fields in backticks too (`sources`, `analysis_id`, `details`), and
    those are not codes — reading them as codes is what this strips. Depth is
    tracked rather than matched with a regex because one gloss contains
    `http(s)`.
    """
    out: list[str] = []
    depth = 0
    for char in run:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(char)
    return "".join(out)


def _published(label: str) -> set[str]:
    """The codes named in one of §6's grouped runs, by its italic label."""
    pattern = rf"^\*{re.escape(label)}\* — (.+)$"
    matches = re.findall(pattern, CONTRACT.read_text(), re.M)
    assert len(matches) == 1, (
        f"expected exactly one '*{label}*' run, found {len(matches)}"
    )
    return set(re.findall(r"`([a-z][a-z0-9_]+)`", _without_glosses(matches[0])))


def test_every_emitted_code_is_published() -> None:
    """A code the engine can return that §6 does not name is an undocumented answer."""
    errors, warnings = _emitted()
    published = set().union(*(_published(g) for g in ERROR_GROUPS)) | _published(
        WARNING_GROUP
    )
    missing = sorted((errors | warnings) - published)
    assert not missing, (
        "these codes are emitted but absent from docs/contract.md §6: "
        f"{missing} — add each to its group ({', '.join(ERROR_GROUPS)} or "
        f"{WARNING_GROUP}), because a code is part of the public contract"
    )


def test_no_published_code_is_invented() -> None:
    """A code §6 names that nothing defines is a promise to clients with no keeper."""
    errors, warnings = _emitted()
    published = set().union(*(_published(g) for g in ERROR_GROUPS)) | _published(
        WARNING_GROUP
    )
    phantom = sorted(published - (errors | warnings))
    assert not phantom, (
        f"docs/contract.md §6 names codes nothing defines: {phantom} — "
        "either errors.py lost them or the document invented them"
    )


def test_errors_and_warnings_are_not_swapped() -> None:
    """A code documented as a warning but emitted as an error (or the reverse)
    tells a client it may carry on when the request was in fact refused."""
    errors, warnings = _emitted()
    published_warnings = _published(WARNING_GROUP)
    published_errors = set().union(*(_published(g) for g in ERROR_GROUPS))
    assert sorted(published_warnings & errors) == [], (
        "§6 lists these under *Warnings*, but errors.py defines them as errors: "
        f"{sorted(published_warnings & errors)}"
    )
    assert sorted(published_errors & warnings) == [], (
        "§6 lists these as errors, but errors.py defines them as warnings: "
        f"{sorted(published_errors & warnings)}"
    )
