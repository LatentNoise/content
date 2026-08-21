"""Language tokens in the public contract (ADR 0022).

A language list is otherwise made of free-form codes the engine crosses with
what a source offers. One reserved word lives inside those lists:
``"original"`` — *the source's own audio language*, whatever that turns out to
be. It exists because that fact is per-resource, so a caller cannot resolve it
in advance for a playlist: members are deliberately not analyzed at submission
(ADR 0019), and "give me each video in its own language" had no expression at
all before this token.

Pure module — no warnings, no planner, no I/O. Where the token is *accepted*
is a contract question (``content.domain.request``); where it is *resolved* is
a planning one (``content.planning.planner``); this file is only what it is.
"""

# Reserved inside audio language lists. Not an ISO 639 code, which is what
# makes it safe to reserve — but the contract has to say so, or a future code
# spelled "original" would be a genuinely nasty surprise (docs/contract.md §8).
ORIGINAL = "original"


def contains_original(languages: list[str]) -> bool:
    return ORIGINAL in languages


def expand_original(languages: list[str], original: str) -> list[str]:
    """Replace the reserved token with the resource's own audio language.

    *original* is what the analysis reports (``""`` when the source declares
    none). An unknown original **drops** the token rather than failing: the
    list is an ordered preference, so the next entry takes over — the same
    degradation an unavailable language already gets. Order and uniqueness are
    preserved, so ``["original", "ja"]`` on a Japanese source is ``["ja"]``,
    not ``["ja", "ja"]``.

    Never returns the token: no provider ever sees a word it cannot resolve.
    """
    expanded: list[str] = []
    for language in languages:
        candidate = original if language == ORIGINAL else language
        if candidate and candidate not in expanded:
            expanded.append(candidate)
    return expanded
