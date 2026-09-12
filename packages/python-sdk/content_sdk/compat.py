"""Dict-returning compatibility client for the Streamlit UIs.

The web UIs (HomeTube, Studio, Console) were written against the former
``content_client`` — a thin, dict-returning wrapper. This module preserves that
exact surface **inside the SDK**, built on the SDK's own httpx transport, so
there is a single package and a single HTTP layer (the guard-rail in the tests
forbids any HTTP outside ``content_sdk``). New consumers should prefer the
object API in ``content_sdk.ContentClient``; this exists so the UIs did not need
a risky rewrite during consolidation.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from ._transport import (
    DEFAULT_TIMEOUT,
    RetryConfig,
    SyncTransport,
    resolve_base_url,
    session_cookie_header,
)
from .errors import APIError as ApiError

__all__ = [
    "ApiError",
    "ContentClient",
    "is_unauthenticated",
    "sign_in_url",
    "streamlit_visitor_headers",
]

SESSION_COOKIE = "content_session"


def sign_in_url(api_base_url: str, come_back_to: str = "") -> str:
    """Where to send a visitor the engine refused.

    The sign-in door lives in the engine (ADR 0033), not in the surface: it is
    the one place allowed to turn a request into an identity. `come_back_to` is
    checked by the engine against its own allowlist, so a tampered value is
    refused there rather than trusted here.
    """
    base = (api_base_url or "").rstrip("/")
    target = f"{base}/auth/sign-in"
    if come_back_to:
        return f"{target}?next={quote(come_back_to, safe='')}"
    return target


def is_unauthenticated(error: Exception) -> bool:
    """Did the engine refuse for want of an identity, rather than fail?

    401 means "sign in"; 403 would mean "signed in, not allowed", which is a
    different message and must not send someone back to a form they already
    filled.
    """
    return (
        getattr(error, "status_code", None) == 401
        or getattr(error, "status", None) == 401
    )


def streamlit_visitor_headers(context: Any) -> dict[str, str]:
    """The identity of the visitor a Streamlit script is currently serving.

    Pass `st.context`. The browser sends its session cookie to the UI, never to
    the engine — the UI runs on a server and calls the engine from its own
    process. Forwarding that cookie is what makes one sign-in work through a
    server-rendered surface.

    🔴 **Call this per request, never once into a cached client.** The three UIs
    cache a single client per process (`@st.cache_resource`), and a credential
    stored on that shared object would be inherited by the next visitor — one
    person's session silently becoming everybody's.

    An absent or unreadable context yields no headers, which is the right answer
    for a self-hosted instance: it asks for no credential at all.
    """
    try:
        secret = (context.cookies or {}).get(SESSION_COOKIE, "")
    except Exception:  # noqa: BLE001 — no context is not an error here
        return {}
    return session_cookie_header(secret, SESSION_COOKIE)


class ContentClient:
    """The former ``content_client.ContentClient`` surface: every method returns
    parsed JSON (dict/list); non-2xx raises ``ApiError`` (status + body)."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        session: Any = None,
        headers_provider: Callable[[], dict[str, str]] | None = None,
    ):
        # `session` is accepted for signature compatibility (the old client took a
        # requests.Session); the SDK transport manages its own httpx client.
        #
        # `headers_provider` is what makes a server-side UI usable by more than
        # one person. It is called on every request, so the identity belongs to
        # the request rather than to this object — which matters because the
        # Streamlit UIs cache one client per process and share it across every
        # visitor. See `streamlit_visitor_headers`.
        self._t = SyncTransport(
            resolve_base_url(base_url),
            timeout,
            RetryConfig(),
            headers_provider=headers_provider,
        )

    @property
    def base_url(self) -> str:
        return self._t.base_url

    # --- system / observability ------------------------------------------------

    def health(self) -> dict:
        return self._t.get("/health")

    def config(self) -> dict:
        return self._t.get("/config")

    def system(self) -> dict:
        return self._t.get("/system")

    def storage(self) -> dict:
        """What the CURRENT owner occupies. Bytes, not paths."""
        return self._t.get("/storage")

    def operator_storage(self) -> dict:
        """Disk usage across the whole installation. Operator-only, and it
        reports the server's own paths — which is why it is."""
        return self._t.get("/operator/storage")

    def catalog(self) -> dict:
        return self._t.get("/catalog")

    def cache(self) -> dict:
        return self._t.get("/cache")

    def notifications(self) -> list[dict]:
        return self._t.get("/notifications").get("notifications", [])

    def purge_cache(self) -> dict:
        return self._t.post("/cache/purge")

    def openapi(self) -> dict:
        # OpenAPI lives at the server root, not under /api/v1.
        return self._t._client.get(f"{self._t.base_url}/openapi.json").json()

    def call_raw(self, method: str, path: str, body: dict | None = None):
        """Generic caller (console request tester). Returns (status, parsed)."""
        resp = self._t._client.request(
            method, f"{self._t.base_url}/api/v1{path}", json=body
        )
        try:
            parsed: Any = resp.json()
        except ValueError:
            parsed = resp.text
        return resp.status_code, parsed

    def folders(self) -> list[str]:
        return self._t.get("/folders").get("folders", [])

    # --- uploads (ADR 0020) ------------------------------------------------------

    def upload_bytes(self, filename: str, data: bytes, media_type: str = "") -> dict:
        """Send bytes to the engine and return the upload record.

        The UIs never have a file on disk to point at: a browser upload arrives
        as bytes in the app's memory, and the Streamlit apps share no
        filesystem with the engine. This is how a file on the *user's* device
        becomes a source.
        """
        return self._t.post_bytes("/uploads", filename, data, media_type)

    # --- analysis / capabilities -----------------------------------------------

    def analyze(self, sources: list[dict]) -> dict:
        return self._t.post("/analyses", {"sources": sources})

    def capabilities(
        self, sources: list[dict], constraints: dict | None = None
    ) -> dict:
        body: dict = {"sources": sources}
        if constraints:
            body["constraints"] = constraints
        return self._t.post("/capabilities", body)

    # --- jobs -------------------------------------------------------------------

    def submit(self, request: dict) -> dict:
        return self._t.post("/jobs", request)

    def list_jobs(self, limit: int = 30) -> list[dict]:
        return self._t.get("/jobs", params={"limit": limit})

    def job(self, job_id: str) -> dict:
        return self._t.get(f"/jobs/{job_id}")

    def events(self, job_id: str, after_sequence: int = 0) -> list[dict]:
        return self._t.get(
            f"/jobs/{job_id}/events", params={"after_sequence": after_sequence}
        )

    def logs(self, job_id: str, tail: int = 400) -> dict:
        return self._t.get(f"/jobs/{job_id}/logs", params={"tail": tail})

    def artifacts(self, job_id: str) -> list[dict]:
        return self._t.get(f"/jobs/{job_id}/artifacts")

    def cancel(self, job_id: str) -> dict:
        return self._t.post(f"/jobs/{job_id}/cancel")

    def retry(self, job_id: str) -> dict:
        return self._t.post(f"/jobs/{job_id}/retry")

    def artifact_bytes(self, artifact_id: str) -> bytes:
        return self._t.content(f"/artifacts/{artifact_id}/content")
