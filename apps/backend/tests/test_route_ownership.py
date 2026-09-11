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
from content.api.auth import Identity
from content.identity import LOCAL_OWNER

# Routes that legitimately carry no owner, each with the reason it is safe.
#
# The rule for this list: a route belongs here only if it returns facts about
# the *installation* — never a byte that a caller stored. When the hosted
# offering ships, the ones marked OPERATOR stop being public and become
# operator-only; they are not owner-filtered because they have no owner, not
# because they are harmless.
#
# `/api/v1/folders` used to be here. It left the list when the delivery library
# gained a per-owner policy: under `per_owner` it lists the caller's own
# subtree, so it has an owner and is filtered like any other data route.
OWNERLESS = {
    ("GET", "/"): "service banner",
    ("GET", "/api/v1/health"): "liveness for the container healthcheck",
    ("GET", "/api/v1/system"): "engine version and capabilities",
    ("GET", "/api/v1/notifications"): "installation-level notices",
    ("GET", "/api/v1/catalog"): "what the engine can do — static",
    ("GET", "/api/v1/config"): "OPERATOR: effective configuration",
    ("GET", "/api/v1/storage"): "OPERATOR: disk occupancy of the instance",
    ("GET", "/api/v1/cache"): "OPERATOR: shared resource-fact cache",
    ("POST", "/api/v1/cache/purge"): "OPERATOR: clears the shared fact cache",
    ("POST", "/api/v1/capabilities"): "resolves against the installation",
}
# (The OpenAPI schema and its UIs are not APIRoutes, so they never reach this
# check and need no exemption.)


def _routes(app):
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            yield method, route.path, route


def test_every_data_route_resolves_an_owner():
    app = create_app(start_worker=False)
    unguarded = []
    for method, path, route in _routes(app):
        if (method, path) in OWNERLESS:
            continue
        resolves_owner = any(
            isinstance(dependency.call, Identity)
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
