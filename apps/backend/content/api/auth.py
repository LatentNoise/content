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

from fastapi import Depends, HTTPException, Request, status

from content.identity import LOCAL_OWNER, AuthMode


class Identity:
    """Resolves the owner of a request. One instance per application."""

    def __init__(self, mode: str = AuthMode.NONE.value):
        self.mode = AuthMode(mode)

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

        Not implemented yet: this is the seam the hosted work plugs into
        (API keys from the Console, then magic-link sessions). Until then
        ``token`` mode refuses every request rather than letting one through,
        which is the only safe direction for an unfinished check.
        """
        return None


def owner_dependency(identity: Identity):
    """FastAPI dependency yielding the owner id of the current request."""
    return Depends(identity)
