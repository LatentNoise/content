"""Telling a visitor they are not signed in, on every surface, without lying.

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

So the challenge is **asked**, never awaited. `GET /api/v1/auth/me` needs an
identity and returns one, which makes it the one honest question, and it is
asked on every run rather than cached: a session deleted between two clicks
must stop working on the next one. In `none` mode it answers `local` and nobody
is ever asked to sign in, exactly as ADR 0030 requires — no surface branches on
the deployment mode.

**The page stays.** Replacing the interface with a door was the first attempt
and it was wrong: someone arriving at a public instance should see what the
product is before being asked for anything, and a surface that blanks itself
teaches nothing. So the interface renders, and a banner above it says plainly
that nothing will work until you sign in, with the button that does it. The
banner is deliberately not a dismissible dialog: a dialog is read once and then
gone, while the reason the page is not working lasts until it is fixed.

**It is a button and not a redirect**, and not by preference. Streamlit
components render inside an iframe sandboxed without `allow-top-navigation`, so
a script cannot move the browser out of the app at all.

The door itself stays in the engine (ADR 0033). This module points at it and
never becomes a second one. It lives in the SDK because that is the only place
three single-file Streamlit apps can share code from — D-21 records what
happened the last time a helper was copy-pasted into three UIs.
"""

from __future__ import annotations

from typing import Any

from content_sdk.compat import is_unauthenticated, sign_in_url

__all__ = ["render_identity"]


def render_identity(
    client: Any, *, app_title: str, api_base_url: str
) -> dict[str, Any]:
    """Ask the engine who the visitor is, and show the answer where it matters.

    Returns the identity, so a caller that wants to greet someone or hide an
    operator view does not ask twice. Returns `{}` both when the visitor is
    refused and when the engine could not be reached — the two are told apart
    where it counts, which is what gets rendered: a way in for the first, and
    nothing at all for the second, because an engine that is down is not an
    engine that refused, and sending someone to a door they cannot use would
    hide the one fact they need.
    """
    try:
        identity = client.whoami()
    except Exception as exc:  # noqa: BLE001 — every failure is handled here
        if is_unauthenticated(exc):
            _offer_the_door(app_title=app_title, api_base_url=api_base_url)
        return {}
    if not isinstance(identity, dict):
        return {}
    _show_who_and_offer_the_way_out(client, identity)
    return identity


def _show_who_and_offer_the_way_out(client: Any, identity: dict[str, Any]) -> None:
    """Who you are, and how to stop being them.

    An account that cannot be left is a defect of the sign-in feature, not a
    missing extra: a shared machine, a borrowed laptop, or simply wanting to
    see what a new visitor sees. Nothing else in the product could do it, so
    the only way out was deleting a cookie by hand in browser settings.

    Nothing is drawn where there is no account to leave. A self-hosted
    instance has one implicit user who never signed in (ADR 0030), and
    offering to sign them out would be offering to break their own install.
    """
    import streamlit as st

    if not identity.get("account"):
        return

    with st.sidebar:
        who = identity.get("email") or identity.get("owner_id", "")
        badge = " · operator" if identity.get("is_operator") else ""
        st.caption(f"Signed in as **{who}**{badge}")
        if st.button("Sign out", use_container_width=True, key="_sign_out"):
            try:
                client.sign_out()
            except Exception:  # noqa: BLE001,S110 — already gone is already out
                pass
            # The engine revokes the session; the browser keeps a cookie that
            # now unlocks nothing. The next run asks who the visitor is, gets
            # a refusal, and draws the door — which is the correct page for
            # somebody who just signed out.
            st.rerun()


def _offer_the_door(*, app_title: str, api_base_url: str) -> None:
    """A banner above the interface, and a button in the sidebar with it.

    Two places because they fail differently: the banner is unmissable on
    arrival and then scrolls away, while the sidebar button stays put and is
    where somebody looks once they have scrolled past it and wondered why
    nothing happens.
    """
    import streamlit as st

    url = sign_in_url(api_base_url, _current_url())

    with st.container(border=True):
        message, action = st.columns([3, 1], vertical_alignment="center")
        message.markdown(
            "#### 🔒 You are not signed in\n"
            f"{app_title} needs to know who you are before it can do anything. "
            "Your work, your files and your history are yours, so nothing "
            "below will run until you sign in. It takes an email address and "
            "one click — no password."
        )
        action.link_button("Sign in", url, type="primary", use_container_width=True)

    with st.sidebar:
        st.link_button("🔒 Sign in", url, type="primary", use_container_width=True)
        st.caption("Signed in on another surface? Reload — one session covers all.")


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
