"""AGPL §13: the offer of Corresponding Source, shown to network users.

Content is AGPL-3.0-or-later. Section 13 says that if you **modify** it and let
users interact with your modified version over a network, those users must be
offered its Corresponding Source. A UI is exactly such an interaction, so each
one carries a link.

The link is whatever the *backend* reports (`GET /api/v1/system` → `source_url`,
configured by `CONTENT_SOURCE_URL`), never a constant compiled into the UI. That
distinction is the whole point: an operator running a fork points the setting at
their own source, and their users get a link that is actually true. Hard-coding
upstream here would make every modified deployment tell its users something
false — and would not discharge the operator's obligation.

Shared through the SDK for the same reason the notification bar is (D-21): three
UIs, one implementation.
"""

from __future__ import annotations

from typing import Any

__all__ = ["render_streamlit_footer", "source_offer"]

_LICENSE = "AGPL-3.0-or-later"


def source_offer(client: Any) -> tuple[str, str]:
    """``(licence, source_url)`` as reported by the instance.

    Falls back to ``(AGPL-3.0-or-later, "")`` when the backend is unreachable or
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
