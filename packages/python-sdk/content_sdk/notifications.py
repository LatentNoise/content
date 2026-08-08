"""The UI half of instance notifications: dismissal state and one renderer.

The backend decides *what* is worth saying (`GET /api/v1/notifications`); this
module decides *whether this browser has already seen it* and draws the banner.
It lives in the SDK for one reason: the three Streamlit UIs each ship as a
single `app.py` plus this package, so the SDK is the only place all three can
share code from. Discovery D-21 records what happened the last time a helper was
copy-pasted into three UIs — three diverging md5s.

One notification is built here rather than by the backend: the **version
mismatch** warning. The backend deliberately knows nothing about its clients,
so "this UI and the backend are different releases" is a fact only the client
side can observe — the UI passes its own version, the backend's comes from
`GET /api/v1/system`, and the comparison happens once per Streamlit session
(the launch check the banner promises). Same dict shape, same banner, same
dismissal store as the backend's notifications, so the UIs treat all of them
uniformly.

Streamlit is imported **lazily, inside the renderer**. The SDK's other consumers
(the CLI, the MCP server) never call it and never pay for it; there is no
dependency to add.

Dismissal is per-browser-host, not per-user: Content has no accounts (scope.md),
so the honest scope is "this installation's UI". State is a small JSON file in
the same `{"dismissed": {}, "shown": {}}` shape HomeTube used.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "DismissalStore",
    "fetch",
    "pending",
    "render_streamlit",
    "version_mismatch",
]

_LEVEL_ICONS = {
    "success": "🎉",
    "warning": "⚠️",
    "error": "🚫",
    "info": "ℹ️",
}


def _state_path() -> Path:
    """Where dismissals are remembered. `CONTENT_UI_STATE_DIR` overrides it; the
    default is a stable directory under the system temp root, which exists and is
    writable in every deployment we ship (including the non-root containers)."""
    base = os.getenv("CONTENT_UI_STATE_DIR") or os.path.join(
        tempfile.gettempdir(), "content-ui"
    )
    return Path(base) / "notifications.json"


class DismissalStore:
    """Remembers which notification ids this installation has dismissed.

    Every filesystem error is swallowed: a read-only or full disk degrades
    dismissal to "lasts for this session", which is a far better outcome than a
    UI that will not start.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or _state_path()

    def _load(self) -> dict[str, dict[str, str]]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {"dismissed": {}, "shown": {}}
        if not isinstance(data, dict):
            return {"dismissed": {}, "shown": {}}
        data.setdefault("dismissed", {})
        data.setdefault("shown", {})
        return data

    def _save(self, state: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(state, indent=2))
        except OSError:
            pass  # dismissal degrades to session-only; never a crash

    def is_dismissed(self, notification_id: str) -> bool:
        return notification_id in self._load()["dismissed"]

    def dismiss(self, notification_id: str) -> None:
        state = self._load()
        state["dismissed"][notification_id] = datetime.now(timezone.utc).isoformat()
        self._save(state)

    def mark_shown(self, notification_id: str) -> None:
        state = self._load()
        state["shown"][notification_id] = datetime.now(timezone.utc).isoformat()
        self._save(state)


def fetch(client: Any) -> list[dict]:
    """The instance's notifications, or `[]`.

    Failure-silent by design: an older backend without the endpoint, a network
    blip, or a client that does not implement it must never stop a page from
    rendering — a banner is a courtesy.
    """
    try:
        return list(client.notifications())
    except Exception:  # noqa: BLE001 - a banner must never break a page
        return []


def pending(client: Any, store: DismissalStore | None = None) -> list[dict]:
    """The notifications this installation has not dismissed yet."""
    store = store or DismissalStore()
    out = []
    for note in fetch(client):
        nid = note.get("id")
        # `is_dismissed` cannot raise: DismissalStore._load swallows an
        # unreadable or corrupt state file and answers with an empty one.
        if not nid or store.is_dismissed(nid):
            continue
        out.append(note)
    return out


# --- version mismatch (the one client-built notification) ----------------------

_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def _parse_version(value: str) -> tuple[int, int, int]:
    """``"v0.2.1"`` → ``(0, 2, 1)``; anything unparseable → ``(0, 0, 0)``."""
    match = _VERSION_RE.search((value or "").lstrip("vV").strip())
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def version_mismatch(app_version: str, backend_version: str) -> dict | None:
    """A warning when this UI and the backend are different releases, or None.

    The whole monorepo ships in lockstep (one ``make version``), so the official
    images are meant to run the same number on both sides; a difference means a
    torn deployment — one image was updated and the other was not — and the two
    sides may disagree about the contract. Any difference counts, patches
    included: the release banner's major/minor-only rule is about *news*, this
    is about *coherence*.

    Silent (returns None) when either side is missing or unparseable: an older
    backend that does not report a version, or a dev build with a placeholder,
    must degrade to no banner — never to a false alarm.
    """
    ours = _parse_version(app_version)
    theirs = _parse_version(backend_version)
    if ours == (0, 0, 0) or theirs == (0, 0, 0) or ours == theirs:
        return None
    older = "UI" if ours < theirs else "backend"
    return {
        # Keyed by the exact pair: dismissing 0.1.0/0.2.0 stays dismissed, but
        # the next divergence is a new id and notifies again.
        "id": f"version-mismatch:{app_version}:{backend_version}",
        "level": "warning",
        "title": "UI and backend versions differ",
        "message": (
            f"This UI runs {app_version} but the backend runs {backend_version} "
            f"— the {older} image is behind. Update your images so both run the "
            "same release: docker compose pull, then docker compose up -d."
        ),
        "action_label": None,
        "action_url": None,
    }


_BACKEND_VERSION_KEY = "_content_backend_version"


def _backend_version(client: Any, session_state) -> str:
    """The backend's reported version, fetched once per Streamlit session.

    "Check when the UI is launched": the first successful lookup is cached for
    the whole session, so reruns cost nothing. A failed lookup is *not* cached —
    a backend that was still starting when the page first loaded gets another
    chance on the next rerun instead of silencing the check for the session.
    """
    cached = session_state.get(_BACKEND_VERSION_KEY)
    if isinstance(cached, str) and cached:
        return cached
    try:
        version = str(client.system().get("version") or "")
    except Exception:  # noqa: BLE001 - a banner must never break a page
        return ""
    if version:
        session_state[_BACKEND_VERSION_KEY] = version
    return version


def render_streamlit(
    client: Any,
    store: DismissalStore | None = None,
    *,
    app_version: str | None = None,
) -> None:
    """Draw the notification bar at the top of a Streamlit page.

    With *app_version*, the launch check runs first: the caller's version is
    compared against the backend's (`/api/v1/system`) and a mismatch joins the
    banner ahead of the backend's own notifications. Official UIs pass their
    ``__version__``; a third-party Streamlit app should not — the SDK promises
    contract compatibility across releases, so a version gap is only a problem
    for the lockstep-shipped UIs.

    A no-op when there is nothing pending — the page then renders exactly as it
    would without this call, which is what the UI AppTests assert.
    """
    import streamlit as st  # lazy: only Streamlit consumers pay for this

    store = store or DismissalStore()
    notes = pending(client, store)
    if app_version:
        mismatch = version_mismatch(
            app_version, _backend_version(client, st.session_state)
        )
        if mismatch and not store.is_dismissed(mismatch["id"]):
            notes.insert(0, mismatch)
    if not notes:
        return

    # `st.session_state` covers the current session so a dismissal takes effect
    # on the immediate rerun; the store covers reloads and new sessions.
    hidden = st.session_state.setdefault("_dismissed_notifications", set())

    for note in notes:
        nid = note["id"]
        if nid in hidden:
            continue
        icon = _LEVEL_ICONS.get(note.get("level", "info"), "ℹ️")
        with st.container(border=True):
            message, action = st.columns([6, 1])
            with message:
                st.markdown(f"{icon} **{note.get('title', '')}**")
                body = note.get("message", "")
                url, label = note.get("action_url"), note.get("action_label")
                if url and label:
                    body = f"{body} [{label}]({url})"
                st.caption(body)
            with action:
                if st.button("Dismiss", key=f"dismiss_{nid}", use_container_width=True):
                    hidden.add(nid)
                    store.dismiss(nid)
                    st.rerun()
        store.mark_shown(nid)
