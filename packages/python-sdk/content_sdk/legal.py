"""The licence and the source link, shown to the people using an instance.

Content is source-available under FSL-1.1-ALv2. Unlike the AGPL this footer once
discharged, the licence imposes no duty to offer anyone the source — the link is
kept because a source-available project that hides its source is worth less than
one that shows it. Someone who can see what an instance runs can audit it, and
that is the point of publishing the code at all.

The link is whatever the *backend* reports (`GET /api/v1/system` → `source_url`,
configured by `CONTENT_SOURCE_URL`), never a constant compiled into the UI. That
distinction is the whole point: an operator running a fork points the setting at
their own source, and their users get a link that is actually true. Hard-coding
upstream here would make every modified deployment tell its users something
false.

Shared through the SDK for the same reason the notification bar is (D-21): three
UIs, one implementation.
"""

from __future__ import annotations

from typing import Any

__all__ = ["render_streamlit_footer", "source_offer"]

_LICENSE = "FSL-1.1-ALv2"


def source_offer(client: Any) -> tuple[str, str]:
    """``(licence, source_url)`` as reported by the instance.

    Falls back to ``(FSL-1.1-ALv2, "")`` when the backend is unreachable or
    predates the field: a missing link is a degraded footer, never a broken page.
    """
    try:
        system = client.system()
    except Exception:  # noqa: BLE001 - the footer must never break a page
        return _LICENSE, ""
    if not isinstance(system, dict):
        return _LICENSE, ""
    license_id = system.get("license") or _LICENSE
    url = system.get("source_url") or ""
    return str(license_id), str(url)


def render_streamlit_footer(client: Any) -> None:
    """Render the licence + source link. Call it inside `st.sidebar`."""
    import streamlit as st  # lazy: only Streamlit consumers pay for this

    license_id, url = source_offer(client)
    if url:
        st.caption(f"{license_id} · [Source code]({url})")
    else:
        # No URL configured: still state the licence rather than say nothing.
        st.caption(license_id)
