"""Who is calling.

Same rule as the engine (ADR 0030): the client sends a secret, the server
derives the identity. A product never names itself in a field — the key it
holds is what says which product it is.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request, status


def client_from_key(api_keys: dict[str, str], presented: str) -> str | None:
    """Return the client name for a presented key, comparing in constant time.

    The loop runs over every key on purpose: returning early on the first
    mismatch would leak, through timing, how many keys were checked.
    """
    found: str | None = None
    for secret, name in api_keys.items():
        if hmac.compare_digest(secret, presented):
            found = name
    return found


async def require_client(request: Request, authorization: str = Header(default="")) -> str:
    scheme, _, presented = authorization.partition(" ")
    if scheme.lower() != "bearer" or not presented.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    client = client_from_key(request.app.state.settings.api_keys, presented.strip())
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unknown credential.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return client
