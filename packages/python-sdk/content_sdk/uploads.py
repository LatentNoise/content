"""Upload helpers for the Streamlit UIs (ADR 0020).

Lives in the SDK for the same reason `status` and `notifications` do: the UIs
are thin clients, and anything with a rule in it belongs where it can be tested
without a browser.

The rule here is small and easy to get wrong. Streamlit re-runs the whole
script on every interaction, so an uploader that sends on each run would
re-transmit the same file every time the user touched an unrelated widget — a
gigabyte of video per checkbox. The fix is a cache keyed on what identifies the
*selection* rather than on the picked object, which is new on every run.
"""

from __future__ import annotations

from collections.abc import Callable

# What identifies one chosen file well enough to notice a different one.
Signature = tuple[str, int]


def upload_once(
    cache: dict,
    key: object,
    filename: str,
    size: int,
    send: Callable[[], dict],
) -> str:
    """Return the upload id for this selection, sending it at most once.

    ``cache`` is the caller's own store (a Streamlit ``session_state`` entry);
    ``key`` distinguishes several pickers on one page; ``send`` performs the
    actual upload and returns the engine's record.

    Choosing a *different* file under the same key replaces the entry: the
    signature changed, so the new selection is what the user means.
    """
    signature: Signature = (filename, size)
    entry = cache.get(key)
    if entry is not None and entry.get("signature") == signature:
        return entry["upload_id"]
    record = send()
    cache[key] = {"signature": signature, "upload_id": record["upload_id"]}
    return record["upload_id"]
