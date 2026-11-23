"""Architectural guard-rail, the other half: every published route has a door.

`test_layering.py` proves the consumers speak to the engine **only** through
`content_sdk`. That is one of the two halves of ADR 0015, and alone it is a
guarantee with a hole in it: if the SDK does not wrap a route, nothing else is
allowed to call it either, so a perfectly real endpoint becomes unreachable from
every client at once — the CLI, the MCP server and the three surfaces.

That is not hypothetical. `DELETE /api/v1/jobs/{job_id}` was published,
documented in `docs/contract.md` and tested in the engine, and **no client could
call it** — the one route a person needs when a storage quota refuses them,
which is the stated reason it exists. `test_client_parity.py` could not see it,
because it compares the three clients *to each other*: a verb missing from all
three is parity. Nothing compared them to the engine's route table.

Both sides are read as **text**, standard library only: the root suite is a
repository-coherence suite and must not depend on the backend being importable
(the same rule `test_contract_error_codes.py` states). Decorators and transport
calls are parsed with `ast`, not matched with a regex, so a path spread over two
lines is seen exactly as Python sees it.
"""

from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
API_SOURCES = [
    REPO / "apps/backend/content/api/app.py",
    REPO / "apps/backend/content/api/auth_routes.py",
]
SDK_DIR = REPO / "packages/python-sdk/content_sdk"
PREFIX = "/api/v1"

# The transport helpers, and the verb each one sends. `request` carries its verb
# as the first argument instead, which is how DELETE is spelled.
TRANSPORT_VERBS = {
    "get": "GET",
    "post": "POST",
    "post_file": "POST",
    "post_bytes": "POST",
    "content": "GET",
    "stream_sse": "GET",
    "stream_to": "GET",
}

# Published routes that no SDK method reaches, each with the reason. The rule
# for this list: a route belongs here only if its caller is **not a program** —
# a browser following a link, or a human filling a form. Anything a client could
# want to do belongs in the SDK instead of here.
NO_SDK_METHOD = {
    ("POST", "/auth/link"): (
        "the sign-in flow is a browser's: the engine serves the form itself at "
        "/auth/sign-in (ADR 0033) and posts this. A program authenticates with "
        "an API key (ADR 0030), it never asks for a link in a mailbox."
    ),
    ("GET", "/auth/callback"): (
        "the link in the mail, clicked in a browser — it burns the token, sets "
        "the session cookie and redirects. An SDK call would consume the "
        "visitor's one-time token and throw the cookie away."
    ),
}


def _route_path(node: ast.Call) -> str | None:
    """The literal path of an `@app.get("/…")` / `@router.post("/…")` call."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    owner = func.value
    if not (isinstance(owner, ast.Name) and owner.id in ("app", "router")):
        return None
    if func.attr.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        return None
    if not node.args or not isinstance(node.args[0], ast.Constant):
        return None
    value = node.args[0].value
    return value if isinstance(value, str) else None


def published_routes() -> set[tuple[str, str]]:
    """Every `/api/v1` route the engine serves, as (verb, path-without-prefix).

    Path parameter names are dropped: the engine calls it `{job_id}` and the SDK
    may call it anything, and this guard is about the route existing, not about
    what the local variable is named.
    """
    routes = set()
    for source in API_SOURCES:
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            path = _route_path(node)
            if path is None or not path.startswith(PREFIX):
                continue
            verb = node.func.attr.upper()  # type: ignore[union-attr]
            routes.add((verb, _normalize(path[len(PREFIX) :])))
    return routes


def sdk_calls() -> set[tuple[str, str]]:
    """Every (verb, path) the SDK sends, read off its transport calls."""
    calls = set()
    for file in sorted(SDK_DIR.glob("*.py")):
        tree = ast.parse(file.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call = _transport_call(node)
            if call is not None:
                calls.add(call)
    return calls


def _transport_call(node: ast.Call) -> tuple[str, str] | None:
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    owner = func.value
    if not (isinstance(owner, ast.Attribute) and owner.attr == "_t"):
        return None
    if func.attr == "request":
        if len(node.args) < 2 or not isinstance(node.args[0], ast.Constant):
            return None
        verb, path_node = str(node.args[0].value).upper(), node.args[1]
    elif func.attr in TRANSPORT_VERBS:
        if not node.args:
            return None
        verb, path_node = TRANSPORT_VERBS[func.attr], node.args[0]
    else:
        return None
    path = _literal_path(path_node)
    return None if path is None else (verb, _normalize(path))


def _literal_path(node: ast.expr) -> str | None:
    """A plain string, or an f-string with its interpolations blanked out."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts = []
        for piece in node.values:
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                parts.append(piece.value)
            else:
                parts.append("{}")
        return "".join(parts)
    return None


def _normalize(path: str) -> str:
    """`/jobs/{job_id}` and `/jobs/{id}` are the same route to this guard."""
    out, depth = [], 0
    for char in path:
        if char == "{":
            depth += 1
            if depth == 1:
                out.append("{}")
        elif char == "}":
            depth -= 1
        elif depth == 0:
            out.append(char)
    return "".join(out)


def test_every_published_route_is_reachable_through_the_sdk():
    missing = published_routes() - sdk_calls() - set(NO_SDK_METHOD)
    assert not missing, (
        "These /api/v1 routes exist and no client can call them:\n  "
        + "\n  ".join(f"{verb} {path}" for verb, path in sorted(missing))
        + "\n\nAdd the method to content_sdk (sync *and* async), or — if its "
        "caller is a browser rather than a program — name it in NO_SDK_METHOD "
        "above with the reason."
    )


def test_the_sdk_invents_no_route_the_engine_does_not_serve():
    """The other direction, and it catches a different mistake: a client method
    pointing at a path that was renamed or removed answers 404 at runtime, and
    nothing before this fails."""
    phantom = sdk_calls() - published_routes()
    assert not phantom, (
        "The SDK calls paths the engine does not serve:\n  "
        + "\n  ".join(f"{verb} {path}" for verb, path in sorted(phantom))
    )


def test_both_sides_were_actually_parsed():
    """A guard whose extraction silently returns nothing passes forever. The
    floors are deliberately loose — they catch a parser that broke, not a route
    that was added."""
    assert len(published_routes()) > 30
    assert len(sdk_calls()) > 25


def test_no_exemption_outlives_its_route():
    """A stale exemption is how the next real gap gets waved through."""
    routes = published_routes()
    stale = sorted(f"{v} {p}" for v, p in NO_SDK_METHOD if (v, p) not in routes)
    assert not stale, f"NO_SDK_METHOD names routes that no longer exist: {stale}"

    covered = sorted(f"{v} {p}" for v, p in NO_SDK_METHOD if (v, p) in sdk_calls())
    assert not covered, (
        f"These are exempted and the SDK calls them anyway: {covered} — "
        "drop them from NO_SDK_METHOD."
    )
