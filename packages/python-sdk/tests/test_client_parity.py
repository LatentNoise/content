"""The three clients offer the same verbs, or say why not.

`content_sdk.client.ContentClient` (typed, sync), `content_sdk.aio` (typed,
async) and `content_sdk.compat.ContentClient` (the surfaces' dict-returning
client) are three doors to one contract. A verb on one and not another is not
a design choice, it is a method somebody forgot — and the last time that
happened, the three Streamlit apps called four methods that raised
`AttributeError` in production while every test stayed green.

The exemptions are named one by one, with the reason, so a new gap cannot hide
among old ones.
"""

from content_sdk.aio import AsyncContentClient
from content_sdk.client import ContentClient
from content_sdk.compat import ContentClient as SurfaceClient


def _verbs(cls) -> set[str]:
    return {name for name in dir(cls) if not name.startswith("_")}


# Sync-only by design, each with the reason. Nothing is exempt without one.
NOT_ASYNC = {
    "close": "the async client closes with `aclose`",
    "stream_events": "needs an SSE reader on the async transport, not written",
    "download_artifact": "needs streaming-to-file on the async transport, not written",
}
NOT_SYNC = {
    "aclose": "the sync client closes with `close`",
}


def test_the_async_client_lacks_nothing_unexplained():
    missing = _verbs(ContentClient) - _verbs(AsyncContentClient) - set(NOT_ASYNC)
    assert not missing, f"async client is missing {sorted(missing)}"


def test_the_sync_client_lacks_nothing_unexplained():
    missing = _verbs(AsyncContentClient) - _verbs(ContentClient) - set(NOT_SYNC)
    assert not missing, f"sync client is missing {sorted(missing)}"


def test_every_exemption_still_names_a_real_gap():
    """An exemption for a method that exists on both is stale, and stale
    exemptions are how the next real gap gets waved through."""
    for name in NOT_ASYNC:
        assert name in _verbs(ContentClient) and name not in _verbs(AsyncContentClient)
    for name in NOT_SYNC:
        assert name in _verbs(AsyncContentClient) and name not in _verbs(ContentClient)


def test_what_a_surface_can_ask_the_typed_client_can_ask_too():
    """The surfaces' client is a different shape (dicts, not models), not a
    different contract: every route it reaches, the typed client reaches."""
    surface_only = {
        # dict-shaped conveniences that the typed client covers otherwise
        "base_url",
        "call_raw",
        "openapi",
        "job",
        "capabilities",
        "upload_bytes",
        "events",
    }
    missing = _verbs(SurfaceClient) - _verbs(ContentClient) - surface_only
    assert not missing, f"typed client is missing {sorted(missing)}"
