"""Establishing who the caller is — once, at the edge.

This module is the only place in the codebase that turns a request into an
identity. Everything downstream receives an owner id and never asks again.

The rule it enforces is the one that makes the rest safe (ADR 0030):

    **the client sends a secret, the server derives the identity.**

A client never declares who it is. There is no ``user_id`` in a request body,
a query string or a custom header — if there were, changing one line of JSON
would be enough to read someone else's jobs.

Two modes, one code path:

``none``   the self-hosted contract of ADR 0024, unchanged. No credential is
           asked for and every request belongs to ``local``.
``token``  the hosted contract. A request without a valid credential is
           rejected with 401; the owner comes from the credential.

Because both modes return an owner id, no route and no service ever branches
on the mode. A self-hosted instance is simply an instance with one user.
"""

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status

from content.identity import LOCAL_OWNER, AuthMode, credentials
from content.persistence.store import utcnow

# How stale a session's last_seen_at must be before the expiry is slid
# forward. Sliding on every request would make a page view a write; never
# sliding would sign out someone who uses the product daily.
_SLIDE_AFTER_SECONDS = 3600


def _is_recent(timestamp: str) -> bool:
    """Was this seen inside the write-coalescing window?

    An unreadable or missing timestamp counts as *not* recent, so the write
    happens and repairs the row rather than being skipped forever.
    """
    if not timestamp:
        return False
    try:
        seen = datetime.fromisoformat(timestamp)
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - seen).total_seconds() < _SLIDE_AFTER_SECONDS


class Identity:
    """Resolves the owner of a request. One instance per application."""

    def __init__(self, mode: str = AuthMode.NONE.value, store=None, settings=None):
        self.mode = AuthMode(mode)
        self._store = store
        self._settings = settings

    async def __call__(self, request: Request) -> str:
        if self.mode is AuthMode.NONE:
            return LOCAL_OWNER
        owner = self._from_credential(request)
        if owner is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return owner

    def _from_credential(self, request: Request) -> str | None:
        """Derive the owner from what the request carries.

        Two carriers, one outcome — the API key of a program and the session
        cookie of a browser are different transports for the same question.
        Only the cookie is wired today; API keys join here and change nothing
        else in the codebase.
        """
        if self._store is None or self._settings is None:
            # Nothing to check against: refuse rather than let one through.
            # An unfinished check fails closed.
            return None
        # The Bearer header first: a program states its credential explicitly,
        # and a browser that happens to carry both should be treated as the
        # program it is pretending to be.
        return self._from_api_key(request) or self._from_session_cookie(request)

    def _from_api_key(self, request: Request) -> str | None:
        scheme, _, presented = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not presented.strip():
            return None
        key = self._store.api_key_owner(credentials.fingerprint(presented.strip()))
        if key is None:
            return None
        self._maybe_touch_key(key)
        return key["owner_id"]

    def _maybe_touch_key(self, key: dict) -> None:
        """Record use, but not on every single request."""
        if _is_recent(key.get("last_used_at", "")):
            return
        self._store.touch_api_key(key["id"])

    def _from_session_cookie(self, request: Request) -> str | None:
        presented = request.cookies.get(self._settings.session_cookie_name, "")
        if not presented:
            return None
        session_hash = credentials.fingerprint(presented)
        now = utcnow()
        session = self._store.live_session(session_hash, now)
        if session is None:
            return None
        self._maybe_slide(session_hash, session)
        return session["owner_id"]

    def _maybe_slide(self, session_hash: str, session: dict) -> None:
        """Keep an actively used session alive, without writing every time."""
        if _is_recent(session.get("last_seen_at", "")):
            return
        expires_at = (
            datetime.now(timezone.utc)
            + timedelta(hours=self._settings.session_ttl_hours)
        ).isoformat()
        self._store.touch_session(session_hash, expires_at)


def owner_dependency(identity: Identity):
    """FastAPI dependency yielding the owner id of the current request."""
    return Depends(identity)
