"""The refusal a person can act on, and the approach to it.

`quota_exceeded` is where a hosted installation either converts or loses
someone, and the two halves of doing that honestly both live on the client
side:

**The engine stays neutral.** `application/quotas.py` says *"this installation
allows 60"* on purpose — the overwhelming majority of installations are one
person's homelab, where "the free tier", "upgrade" and "our server" are
nonsense. Nothing commercial may enter the engine's messages, so the engine
sends a stable code and three numbers, and the surface writes the sentence.

**The offer is configuration, not code.** What a refused visitor is offered
comes from `/config` → `quota_wall` (`CONTENT_QUOTA_WALL_*`), which is empty
unless an operator filled it in. Empty means the surfaces show the engine's
own sentence and nothing more — a self-hosted instance has no offer to make.

Two rules the wall may not break, and they are the difference between a
refusal and a gift:

- **the self-host command comes before any other exit**, always. A wall that
  hides the free alternative is detected in three seconds by this audience;
- the second exit carries **no price and no buy button** — only whatever label
  and URL the operator configured.

And one rule the *page* may not break: a surface that shows the wall without
ever having shown the approach turns a rule into a trap (ADR 0036). Hence
`render_streamlit_usage`, which belongs in the sidebar, before the refusal —
not only after it.

Shared through the SDK for the reason D-21 records: three UIs, one
implementation, or three diverging copies of it. Streamlit is imported lazily
inside the renderers, so the CLI and the MCP server never pay for it.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "QUOTA_EXCEEDED",
    "refusal_of",
    "render_streamlit_usage",
    "render_streamlit_wall",
    "sentence_for",
]

QUOTA_EXCEEDED = "quota_exceeded"


def refusal_of(exc: Any) -> dict[str, Any] | None:
    """The quota refusal carried by an `APIError`, or None if it is not one.

    Read structurally — `.codes` and the issue's `details` — never by matching
    the prose. The message is written for a human and may be reworded; `limit`,
    `used` and `allowed` are the contract.
    """
    codes = getattr(exc, "codes", None) or []
    if QUOTA_EXCEEDED not in codes:
        return None
    body = getattr(exc, "body", None)
    detail = body.get("detail", body) if isinstance(body, dict) else None
    errors = detail.get("errors") if isinstance(detail, dict) else None
    for issue in errors or []:
        if isinstance(issue, dict) and issue.get("code") == QUOTA_EXCEEDED:
            details = issue.get("details")
            return details if isinstance(details, dict) else {}
    # The code is there but the shape is not one we know: still a quota
    # refusal, just one we cannot itemise.
    return {}


def _megabytes(value: float) -> str:
    return f"{value / 1_000_000:.0f} MB"


def sentence_for(refusal: dict[str, Any], fallback: str) -> str:
    """What to say about *this* limit, since the three mean different things.

    Minutes age out on their own, storage needs a deletion, concurrency needs
    patience — a single sentence for all three would be useless for two of
    them. An unrecognised limit falls back to the engine's own message, which
    is always correct if less specific.
    """
    limit = refusal.get("limit")
    allowed = refusal.get("allowed")
    used = refusal.get("used")
    if limit == "media_minutes" and allowed is not None:
        return (
            f"This would take you past {float(allowed):g} minutes of media in "
            "the last 30 days. Your oldest minutes free themselves up as they "
            "age out — or see the two ways forward below."
        )
    if limit == "storage_bytes" and allowed is not None and used is not None:
        return (
            f"You are holding {_megabytes(float(used))}, and "
            f"{_megabytes(float(allowed))} is the ceiling here. Delete "
            "something you no longer need, or see below."
        )
    if limit == "active_jobs" and allowed is not None:
        count = int(float(allowed))
        one = "One job" if count == 1 else f"{count} jobs"
        return (
            f"{one} at a time on this instance. Wait for the current one to "
            "finish — it keeps running, nothing is lost."
        )
    return fallback


def _retention_note(config: dict[str, Any]) -> str:
    """The closing reassurance, said only when the engine reports a window.

    The number is the instance's own `CONTENT_RETENTION_DAYS`, never a
    constant: a wall that promises days the sweep does not honour is worse than
    one that says nothing.
    """
    days = (config.get("retention") or {}).get("days") or 0
    if not days:
        return ""
    return (
        f"Whatever you choose: what you produced here stays downloadable from "
        f"your library for {float(days):g} days."
    )


def render_streamlit_usage(client: Any) -> None:
    """The three counters against the three limits. Call it in the sidebar.

    Silent in exactly the cases where it has nothing true to say: an instance
    with no limits set, an owner who is exempt (the operator is never counted,
    ADR 0035/0036), or a backend that cannot be reached — a usage panel must
    never be the thing that breaks a page.
    """
    import streamlit as st  # lazy: only Streamlit consumers pay for this

    try:
        usage = client.usage()
    except Exception:  # noqa: BLE001 - never break a page over a side panel
        return
    if not isinstance(usage, dict) or usage.get("exempt"):
        return

    rows: list[str] = []
    minutes = usage.get("media_minutes") or {}
    if minutes.get("allowed"):
        rows.append(
            f"{float(minutes.get('used') or 0):.0f}"
            f" / {float(minutes['allowed']):g} min of media"
        )
    storage = usage.get("storage_bytes") or {}
    if storage.get("allowed"):
        rows.append(
            f"{_megabytes(float(storage.get('used') or 0))}"
            f" / {_megabytes(float(storage['allowed']))} stored"
        )
    jobs = usage.get("active_jobs") or {}
    if jobs.get("allowed"):
        rows.append(f"{int(jobs.get('used') or 0)} / {int(jobs['allowed'])} jobs")
    if not rows:
        return

    window = int(usage.get("window_days") or 30)
    st.caption(f"**Your usage** (rolling {window} days)")
    for row in rows:
        st.caption(row)


def render_streamlit_wall(exc: Any, config: dict[str, Any] | None) -> bool:
    """Draw the refusal. Returns True if this was a quota refusal at all.

    It is an **error state, not a page**: Streamlit reruns, so there is no
    navigation to build around it and nothing to remember between runs. A
    caller that gets False should report `exc.message` the way it always did.
    """
    import streamlit as st  # lazy: only Streamlit consumers pay for this

    refusal = refusal_of(exc)
    if refusal is None:
        return False

    fallback = getattr(exc, "message", None) or str(exc)
    st.error(sentence_for(refusal, fallback))

    wall = (config or {}).get("quota_wall") or {}
    command = wall.get("self_host_command") or ""
    docs_url = wall.get("docs_url") or ""
    cta_label = wall.get("cta_label") or ""
    cta_url = wall.get("cta_url") or ""
    if not (command or (cta_label and cta_url)):
        # Nothing configured: the engine's sentence is the whole answer, which
        # is the right answer on a self-hosted instance.
        return True

    st.markdown("**Two ways forward, and both are fine by us.**")
    # The free one first, always — see the module docstring.
    if command:
        st.markdown("Run it yourself. Free, no account, no quota.")
        st.code(command, language="bash")
        if docs_url:
            st.markdown(f"Full setup: {docs_url}")
    if cta_label and cta_url:
        st.markdown(
            f"Or stay here and let someone else run the server — "
            f"[{cta_label}]({cta_url})"
        )

    note = _retention_note(config or {})
    if note:
        st.caption(note)
    return True
