"""Every route that touches user data must resolve an owner. Mechanically.

This is the guard rail ADR 0030 leans on, and it exists because the danger was
never writing the filter — it was forgetting one. A single route that reads
without an owner makes the other twenty-six pointless, and no amount of care
survives six months of new endpoints.

So the check is not a convention, a review note or a docstring. It enumerates
the live FastAPI application and fails if a route outside the explicit
operator/system allow-list does not depend on `Identity`. Adding an endpoint
without an owner breaks the build; adding one deliberately without an owner
means writing its name and its reason below, where the next reader sees it.
"""

import pytest
from fastapi.routing import APIRoute

from content.api.app import create_app
from content.api.auth import Identity, Operator
from content.identity import LOCAL_OWNER

# Routes that legitimately carry no owner, each with the reason it is safe.
#
# The rule for this list: a route belongs here only if it returns facts about
# the *installation* — never a byte that a caller stored, and nothing an
# operator would mind a stranger reading.
#
# Two routes left this list rather than staying in it with a promise:
#
# - `/api/v1/folders` gained an owner when the delivery library became
#   per-owner: it lists the caller's own subtree.
# - `/api/v1/storage` was split. The owner-scoped half kept the name and
#   reports what *you* hold; the installation-wide half moved to
#   `/api/v1/operator/storage`. It used to publish the server's disk paths to
#   anyone who asked, which went unnoticed while a password sat in front of it.
#
# `/api/v1/cache` and `/cache/purge` are operator-only for the same reason, and
# `is_operator` guards them — they have no owner, they have a privilege.
OWNERLESS = {
    ("GET", "/"): "service banner",
    ("GET", "/api/v1/health"): "liveness for the container healthcheck",
    ("GET", "/api/v1/system"): "engine version and capabilities",
    ("GET", "/api/v1/notifications"): "installation-level notices",
    ("GET", "/api/v1/catalog"): "what the engine can do — static",
    ("GET", "/api/v1/config"): "client-facing settings; holds nobody's data",
    ("POST", "/api/v1/capabilities"): "resolves against the installation",
    # The sign-in door (ADR 0033). These five run *before* anyone is known —
    # they are what establishes an identity, so requiring one would be
    # circular. They are the only routes in the codebase allowed to be in
    # that position, which is why they are named one by one rather than
    # exempted by prefix.
    ("POST", "/api/v1/auth/link"): "AUTH: asks for a sign-in link",
    ("GET", "/api/v1/auth/callback"): "AUTH: burns a token, opens a session",
    ("GET", "/auth/sign-in"): "AUTH: the sign-in form",
    # Leaving needs no proof of who you are: an unknown cookie is nothing to
    # revoke, and the worst a forged link achieves is signing somebody out.
    ("GET", "/auth/sign-out"): "AUTH: burns the session, clears the cookie",
    ("POST", "/auth/sign-in"): "AUTH: the sign-in form's submission",
    ("GET", "/auth/check-your-mail"): "AUTH: confirmation page, holds nothing",
}
# (The OpenAPI schema and its UIs are not APIRoutes, so they never reach this
# check and need no exemption.)


def _routes(app):
    """Every APIRoute the application actually serves, routers included.

    Walking `app.routes` alone is not enough and the difference is a hole, not
    a detail: a router mounted with `include_router` appears as one opaque
    entry whose own routes never surface. Every route added that way — the
    sign-in door was the first — would have been exempt from this guard
    without ever being listed as an exemption.
    """
    for route in _walk(app.routes):
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            yield method, route.path, route


def _walk(routes):
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        # FastAPI wraps an included router; its real routes hang off it.
        nested = getattr(route, "original_router", None)
        if nested is not None:
            yield from _walk(nested.routes)
        elif hasattr(route, "routes"):
            yield from _walk(route.routes)


def test_every_data_route_resolves_an_owner():
    app = create_app(start_worker=False)
    unguarded = []
    for method, path, route in _routes(app):
        if (method, path) in OWNERLESS:
            continue
        # Either door counts: a plain identity, or an identity plus the
        # privilege of operating the machine. Both end at an owner id, which is
        # what this guard is about.
        resolves_owner = any(
            isinstance(dependency.call, (Identity, Operator))
            for dependency in route.dependant.dependencies
        )
        if not resolves_owner:
            unguarded.append(f"{method} {path}")
    assert not unguarded, (
        "These routes touch user data without resolving an owner:\n  "
        + "\n  ".join(unguarded)
        + "\n\nAdd `owner_id: str = owner` to the handler, or — if the route "
        "really returns nothing a caller stored — list it in OWNERLESS above "
        "with the reason."
    )


def test_ownerless_list_has_no_stale_entries():
    """A route that disappears must not leave a silent exemption behind."""
    app = create_app(start_worker=False)
    live = {(method, path) for method, path, _ in _routes(app)}
    stale = sorted(f"{m} {p}" for m, p in OWNERLESS if (m, p) not in live)
    assert not stale, (
        "OWNERLESS exempts routes that no longer exist — remove them so the "
        "list keeps meaning something:\n  " + "\n  ".join(stale)
    )


@pytest.mark.parametrize("mode", ["none", "token"])
def test_identity_returns_one_owner_or_refuses(mode):
    """Both modes end at the same place: an owner id, or no request at all."""
    from fastapi import HTTPException
    from starlette.requests import Request

    identity = Identity(mode)
    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
    request = Request(scope)

    import asyncio

    if mode == "none":
        assert asyncio.run(identity(request)) == LOCAL_OWNER
    else:
        # Nothing is wired to issue credentials yet, so `token` must refuse
        # rather than fall through — an unfinished check fails closed.
        with pytest.raises(HTTPException) as exc:
            asyncio.run(identity(request))
        assert exc.value.status_code == 401
