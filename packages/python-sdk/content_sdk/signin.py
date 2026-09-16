"""Who is visiting a surface, where else they can go, and how they leave.

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

**The other surfaces come from the engine** (ADR 0038). A surface knows one
address, the engine's; it learns its siblings from `/config` rather than from
three more environment variables per deployment, and offers them as a row of
small chips in the sidebar — plain anchors, so the browser navigates in the
same tab, which is what moving between rooms of one product should feel like.

The door itself stays in the engine (ADR 0033). This module points at it and
never becomes a second one. It lives in the SDK because that is the only place
three single-file Streamlit apps can share code from — D-21 records what
happened the last time a helper was copy-pasted into three UIs.
"""

from __future__ import annotations

import html as _html
from dataclasses import dataclass, field
from typing import Any

from content_sdk.compat import is_unauthenticated, sign_in_url, sign_out_url

__all__ = ["Visitor", "public_api_url", "render_identity", "render_sidebar"]

# The icon each surface carries in its own sidebar heading, so a chip pointing
# at it looks like the place it leads to.
_ICONS = {"studio": "🧩", "console": "🛠️", "hometube": "🎬"}


def _engine_facts(client: Any) -> dict[str, Any]:
    """What the engine says about the deployment, read once per browser session.

    The deployment does not change while somebody is looking at it, and a
    Streamlit script runs on every click; asking on each run would be a request
    per click for an answer that cannot differ.
    """
    import streamlit as st

    key = "_content_engine_facts"
    if key not in st.session_state:
        try:
            config = client.config() or {}
        except Exception:  # noqa: BLE001 — an engine that says nothing is fine
            config = {}
        st.session_state[key] = {
            "surfaces": list(config.get("surfaces") or []),
            "public_api_url": str(config.get("public_api_url") or "").rstrip("/"),
        }
    return st.session_state[key]


def public_api_url(client: Any, *, fallback: str) -> str:
    """Where a BROWSER reaches the engine — the engine's answer first.

    Every link a surface draws for the visitor to follow — sign in, sign out,
    the API documentation, an artifact download — points at this address.
    It used to come from the surface's own `CONTENT_PUBLIC_API_URL`, which
    nothing compared with the address the engine writes into its emails, and
    the public HomeTube duly sent its visitors to a LAN name nobody outside the
    house could reach.

    So the engine is asked. It declares `public_api_url` in `/config`, and on
    an instance where people sign in it refuses to start without a coherent
    one. The surface's own setting remains the fallback for an engine that
    declares none — a self-hosted install, where nobody follows an email.
    """
    return _engine_facts(client)["public_api_url"] or fallback.rstrip("/")


@dataclass(frozen=True)
class Visitor:
    """What the engine said about whoever is looking at this run of the page."""

    identity: dict[str, Any] = field(default_factory=dict)
    # True when the engine answered 401 — a refusal, not a failure. An engine
    # that could not be reached leaves both this and `identity` empty.
    refused: bool = False
    sign_in_url: str = ""
    sign_out_url: str = ""

    @property
    def signed_in(self) -> bool:
        return bool(self.identity)

    @property
    def account(self) -> bool:
        """Is there an account behind this identity, or is it the implicit
        user of a self-hosted instance, who never signed in and cannot sign
        out (ADR 0030)?"""
        return bool(self.identity.get("account"))


def render_identity(client: Any, *, app_title: str, api_base_url: str) -> Visitor:
    """Ask the engine who the visitor is; put the door above the page if it
    refuses. Returns what it learned, for the sidebar and for anything that
    wants to greet someone or hide an operator view without asking twice.

    Nothing is drawn for an engine that could not be reached: that is not an
    engine that refused, and the caller's own health check reports it with
    the address, which is the useful message.
    """
    here = _current_url()
    url = sign_in_url(api_base_url, here)
    out = sign_out_url(api_base_url, here)
    try:
        identity = client.whoami()
    except Exception as exc:  # noqa: BLE001 — every failure is handled here
        if is_unauthenticated(exc):
            _banner(app_title=app_title, url=url)
            return Visitor(refused=True, sign_in_url=url, sign_out_url=out)
        return Visitor(sign_in_url=url, sign_out_url=out)
    if not isinstance(identity, dict):
        return Visitor(sign_in_url=url, sign_out_url=out)
    return Visitor(identity=identity, sign_in_url=url, sign_out_url=out)


def render_sidebar(visitor: Visitor, client: Any, *, surface: str) -> None:
    """The sidebar's first lines: where else to go, then who you are.

    Called by each app inside its own `with st.sidebar:` block, under its
    heading, so the app decides the layout and this decides the content.
    """
    import streamlit as st

    _elsewhere(client, surface)

    if visitor.refused:
        # The sidebar's copy of the door stays put after the banner has
        # scrolled away; it is where somebody looks once they have wondered
        # why nothing happens.
        st.link_button(
            "🔒 Sign in", visitor.sign_in_url, type="primary", use_container_width=True
        )
        st.caption("Signed in on another surface? Reload — one session covers all.")
        return

    if not visitor.account:
        # Nobody to name, nobody to sign out: a self-hosted instance has one
        # implicit user, and offering to sign them out would be offering to
        # break their own install.
        return

    who = visitor.identity.get("email") or visitor.identity.get("owner_id", "")
    badge = " · operator" if visitor.identity.get("is_operator") else ""
    st.caption(f"Signed in as **{who}**{badge}")
    # A link, not a button that calls the engine from here. Calling from here
    # revokes the session but leaves the cookie in the browser and in this
    # page's captured snapshot; sending the browser to the engine does both
    # and comes back with nothing to present. See `sign_out_url`.
    st.link_button("Sign out", visitor.sign_out_url, use_container_width=True)


def _elsewhere(client: Any, surface: str) -> None:
    """The other surfaces, as a row of small chips. Nothing when the engine
    declares none, or only this one."""
    import streamlit as st

    others = [
        s
        for s in _engine_facts(client)["surfaces"]
        if isinstance(s, dict) and s.get("kind") != surface and s.get("url")
    ]
    if not others:
        return

    chips = "".join(
        '<a href="{url}">{icon} {label}</a>'.format(
            url=_html.escape(str(s["url"]), quote=True),
            icon=_ICONS.get(str(s.get("kind")), "↗"),
            label=_html.escape(_short(str(s.get("title") or s["kind"]))),
        )
        for s in others
    )
    st.html(
        "<style>"
        ".content-elsewhere{display:flex;flex-wrap:wrap;gap:.35rem;"
        "margin:.1rem 0 .7rem}"
        ".content-elsewhere a{font-size:.8rem;line-height:1;padding:.34rem .62rem;"
        "border-radius:999px;border:1px solid rgba(128,128,128,.35);"
        "color:inherit;text-decoration:none;opacity:.78}"
        ".content-elsewhere a:hover{opacity:1;border-color:rgba(128,128,128,.7)}"
        "</style>"
        f'<nav class="content-elsewhere" aria-label="Other surfaces">{chips}</nav>'
    )


def _short(title: str) -> str:
    """`Content Studio` → `Studio`: a chip has no room for the family name, and
    the family is the one you are already in."""
    prefix = "Content "
    return title.removeprefix(prefix)


def _banner(*, app_title: str, url: str) -> None:
    """Above the interface, unmissable on arrival."""
    import streamlit as st

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
