"""What the surfaces call must exist, on the real client and on the fake.

This guard rail exists because of a bug the rest of the suite could not see.
The three UIs talk to `content_sdk.compat.ContentClient`; four methods they
call — `whoami`, `api_keys`, `create_api_key`, `revoke_api_key` — were only on
the *typed* client, so every one of those calls raised `AttributeError` in
production. The Console's Access tab had never worked, and the sign-in banner
read the failure as "the engine is unreachable" and drew nothing at all.

Every UI test passed throughout, because `FakeContentClient` implemented all
four. **A fake more capable than the thing it stands for turns a whole suite
green over a broken product**, and no amount of behavioural testing catches it:
the fake is what the tests exercise.

So this compares three surfaces against each other — what the apps call, what
the real client offers, what the fake offers — and fails on any gap. It is
mechanical on purpose: the danger was never writing a method, it was forgetting
one.
"""

import re
from pathlib import Path

import pytest
from content_sdk.compat import ContentClient

REPO = Path(__file__).resolve().parents[2]

# Where a surface can call its client: the three apps, plus the shared helpers
# in the SDK that take a client and call it on the apps' behalf.
CALLERS = [
    REPO / "apps" / "web-studio" / "app.py",
    REPO / "apps" / "web-admin" / "app.py",
    REPO / "apps" / "web-hometube" / "app.py",
    REPO / "packages" / "python-sdk" / "content_sdk" / "signin.py",
    REPO / "packages" / "python-sdk" / "content_sdk" / "legal.py",
    REPO / "packages" / "python-sdk" / "content_sdk" / "notifications.py",
    REPO / "packages" / "python-sdk" / "content_sdk" / "uploads.py",
]

CALL = re.compile(r"\bclient\.([a-z_][a-z0-9_]*)\s*\(")


def _called() -> set[str]:
    names: set[str] = set()
    for path in CALLERS:
        if path.exists():
            names |= set(CALL.findall(path.read_text()))
    return names


def _method_names(cls) -> set[str]:
    return {name for name in dir(cls) if not name.startswith("_")}


def test_the_surfaces_call_nothing_the_client_does_not_have():
    missing = sorted(_called() - _method_names(ContentClient))
    assert not missing, (
        f"the UIs call {missing} on content_sdk.compat.ContentClient, "
        "which does not define them — every such call raises AttributeError "
        "in production while the tests pass against the fake"
    )


def test_the_fake_is_never_more_capable_than_the_real_client():
    """The half that would have caught the original bug.

    A fake that answers a call the real client cannot make is a test suite
    reporting on a product that does not exist.
    """
    from conftest import FakeContentClient

    # The fake's own bookkeeping, not a client method: the harness records
    # every instance so a test can assert how a surface built it.
    harness_only = {"instances"}

    extra = sorted(
        _method_names(FakeContentClient) - _method_names(ContentClient) - harness_only
    )
    assert not extra, (
        f"FakeContentClient offers {extra}, which the real client does not. "
        "Either add them to content_sdk.compat.ContentClient or drop them "
        "from the fake — a fake ahead of the client hides a broken surface"
    )


@pytest.mark.parametrize(
    "name", ["whoami", "api_keys", "create_api_key", "revoke_api_key"]
)
def test_the_four_that_were_missing(name):
    """Named one by one, so the regression reads as itself in a failure."""
    assert hasattr(ContentClient, name)
