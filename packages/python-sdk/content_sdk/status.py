"""The contract's status vocabulary, in one place.

Capability statuses and job statuses are *contract* words: the engine emits
them, every client renders them, and none of them is free to invent its own
reading. They lived as byte-identical copies in HomeTube, Studio and the
Console — the ranking table three times, the display map three times — which is
the shape a definition takes just before the copies start disagreeing.

The SDK is the right home because it is what all three already import, and it
already carries `TERMINAL_STATUSES` (``resources.py``) and shared Streamlit
helpers (``legal.py``, ``notifications.py``) for exactly this reason.

The browser extension keeps its own copy of the same table, unavoidably: it is
JavaScript and cannot import this (ADR 0016). That one is a *translation*, not
a duplicate, and `tests/test_browser_extension.py` is where the two are held
together.
"""

from __future__ import annotations

from datetime import datetime, timezone

#: A capability the source can yield. `unknown` is included on purpose: the
#: engine could not decide before execution, and the honest answer is to let the
#: attempt happen rather than hide the option.
PRODUCIBLE_STATUSES = frozenset({"available", "derivable", "unknown"})

#: Which status wins when several capabilities share one output type — as
#: `video.download` and `video.clip` both do. Higher is better.
CAPABILITY_STATUS_RANK: dict[str, int] = {
    "available": 5,
    "derivable": 4,
    "unknown": 3,
    "restricted": 2,
    "unavailable": 1,
}

#: `status -> (icon, colour)` for a job or a step. Presentation, but shared
#: presentation: the same state must not look different depending on which of
#: the three UIs you are in.
JOB_STATUS_DISPLAY: dict[str, tuple[str, str]] = {
    "succeeded": ("✅", "#3fca6b"),
    "partially_succeeded": ("🟡", "#e8b64c"),
    "failed": ("❌", "#e85d5d"),
    "cancelled": ("⚪️", "#8b93a3"),
    "running": ("🔵", "#5b9dff"),
    "queued": ("🕒", "#5b9dff"),
    "planning": ("🧩", "#5b9dff"),
    "validating": ("🧪", "#5b9dff"),
    "created": ("•", "#8b93a3"),
}

#: `status -> (icon, colour)` for a *capability*. A different vocabulary from a
#: job's, and deliberately different icons: "derivable" is not "succeeded".
CAPABILITY_STATUS_DISPLAY: dict[str, tuple[str, str]] = {
    "available": ("✅", "#3fca6b"),
    "derivable": ("🟢", "#3fca6b"),
    "restricted": ("🔒", "#e8b64c"),
    "unavailable": ("⛔️", "#e85d5d"),
    "unknown": ("❔", "#8b93a3"),
}

#: What to show for a status this build has never heard of. A client must not
#: crash on a status the engine gained (docs/contract.md §9: new values are
#: additive).
UNKNOWN_STATUS_DISPLAY = ("•", "#8b93a3")


def is_producible(status: str) -> bool:
    return status in PRODUCIBLE_STATUSES


def better_status(current: str | None, candidate: str) -> str:
    """The stronger of two capability statuses, for folding a list into one."""
    if current is None:
        return candidate
    if CAPABILITY_STATUS_RANK.get(candidate, 0) > CAPABILITY_STATUS_RANK.get(
        current, 0
    ):
        return candidate
    return current


def display(status: str) -> tuple[str, str]:
    """`(icon, colour)` for a job or step status, never raising on an
    unfamiliar one."""
    return JOB_STATUS_DISPLAY.get(status, UNKNOWN_STATUS_DISPLAY)


def capability_display(status: str) -> tuple[str, str]:
    """`(icon, colour)` for a capability status, never raising."""
    return CAPABILITY_STATUS_DISPLAY.get(status, UNKNOWN_STATUS_DISPLAY)


def ago(iso: str | None) -> str:
    """``"2026-08-07T10:00:00+00:00"`` → ``"2.1d ago"``; ``None`` → ``"—"``.

    The one relative-time renderer for the UIs (the console's job list, the
    credential freshness captions) — shared here for the same reason as the
    status tables: three apps, one definition.
    """
    if not iso:
        return "—"
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = (datetime.now(timezone.utc) - ts).total_seconds()
    except ValueError:
        return iso
    if delta < 60:
        return f"{delta:.0f}s ago"
    if delta < 3600:
        return f"{delta / 60:.0f}m ago"
    if delta < 86400:
        return f"{delta / 3600:.1f}h ago"
    return f"{delta / 86400:.1f}d ago"
