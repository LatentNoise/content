"""The identity gate every server-rendered surface passes before it draws.

A Streamlit surface runs on a server and calls the engine from its own process,
so nothing about the visitor's browser reaches the engine except the cookie the
surface forwards. When that cookie is gone, the engine refuses — but only the
calls that *need* an identity, and a page can be built almost entirely from the
ones that do not.

That is the failure this module exists to prevent, and it was real: a surface
booted on `/health` and `/config`, which are open by design, listed jobs inside
a `try/except` that degrades to a dash, and rendered its whole interface to
someone with no session at all. Nothing was leaked — every owner-scoped call
still refused — but the visitor was shown a working product that quietly did
nothing, with no way in.

So the challenge cannot be a by-product of some call failing. It is asked
first, explicitly, on every run: **who am I?** `/api/v1/auth/me` needs an
identity and returns one, which makes it the one honest question. In `none`
mode it answers `local` and nobody is ever asked to sign in, exactly as ADR
0030 requires: no surface branches on the deployment mode.

**Why a page and not a redirect.** Streamlit components render inside an iframe
sandboxed without `allow-top-navigation`, so a script cannot move the browser
out of the app; `window.open` survives, but a new tab for a sign-in is worse
than a button. The block below therefore replaces the page and stops the
script, which is the same outcome a redirect would produce, reached by a click.

The door itself stays in the engine (ADR 0033). This module points at it and
never becomes a second one.
"""

from __future__ import annotations

from typing import Any

from content_sdk.compat import is_unauthenticated, sign_in_url

__all__ = ["require_identity"]


def require_identity(
    client: Any, *, app_title: str, api_base_url: str
) -> dict[str, Any]:
    """Ask the engine who the visitor is; show the way in if it refuses.

    Returns the identity, so a caller that wants to greet someone or hide an
    operator view does not ask twice. Raises nothing: an engine that is down is
    not an engine that refused, and reporting it as "sign in" would send
    somebody to a door that is not the problem.
    """
    try:
        identity = client.whoami()
    except Exception as exc:  # noqa: BLE001 — every failure is handled below
        if is_unauthenticated(exc):
            _ask_to_sign_in(app_title=app_title, api_base_url=api_base_url)
        # Unreachable, timing out, misconfigured: the caller's own health check
        # reports that with the address, which is the useful message.
        return {}
    return identity if isinstance(identity, dict) else {}


def _ask_to_sign_in(*, app_title: str, api_base_url: str) -> None:
    """Replace the page with a way in, and stop the script."""
    import streamlit as st

    st.markdown(f"## Sign in to {app_title}")
    st.write(
        "This instance asks who you are before it does anything. "
        "Enter your email on the next page and we will send you a link — "
        "no password, and it works once."
    )
    st.link_button("Sign in", sign_in_url(api_base_url, _current_url()), type="primary")
    st.caption(
        "Already signed in on another surface? Reload this page — "
        "one session covers all of them."
    )
    st.stop()


def _current_url() -> str:
    """Where to send the visitor back afterwards.

    Read from the browser's own address rather than built from configuration,
    so a surface reached under any of its names returns to that same name. The
    engine checks it against its allowlist anyway, so a value that cannot be
    read is simply omitted.
    """
    import streamlit as st

    try:
        return str(st.context.url or "")
    except Exception:  # noqa: BLE001 — older Streamlit, or no context
        return ""
