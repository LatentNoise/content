"""Typed exceptions for the Content SDK.

Every non-2xx from the API maps to one of these, so callers can `except`
precisely (a 410 is a `Gone`, a 422 is a `ValidationError` carrying the stable
error codes) instead of inspecting status codes by hand.
"""

from __future__ import annotations


class ContentError(Exception):
    """Base class for every SDK error."""


class TransportError(ContentError):
    """The request never got a response (connect/read timeout, reset). These are
    the only errors the SDK may transparently retry on a safe call."""


class APIError(ContentError):
    """A non-2xx HTTP response. Carries the status and the parsed error body."""

    def __init__(self, status: int, body: object):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body}")

    @property
    def codes(self) -> list[str]:
        """Stable machine codes carried in the body, when present (the contract's
        ValidationResult shape, or a single {code, message})."""
        body = self.body
        if isinstance(body, dict):
            if isinstance(body.get("code"), str):
                return [body["code"]]
            errors = body.get("errors")
            if isinstance(errors, list):
                return [
                    e["code"] for e in errors if isinstance(e, dict) and "code" in e
                ]
            detail = body.get("detail")
            if isinstance(detail, dict):
                return APIError(self.status, detail).codes
        return []


class ValidationError(APIError):
    """422 (or 409 idempotency conflict): the request was rejected. `.codes`
    lists the stable structural/feasibility codes."""


class NotFound(APIError):
    """404 — the resource does not exist."""


class Gone(APIError):
    """410 — the resource existed but has expired (e.g. an analysis or its
    referenced facts)."""


class Conflict(APIError):
    """409 — a state conflict (e.g. retrying a non-terminal job)."""


def error_for(status: int, body: object) -> APIError:
    """Map an HTTP status to the most specific SDK exception."""
    if status == 404:
        return NotFound(status, body)
    if status == 410:
        return Gone(status, body)
    if status == 422:
        return ValidationError(status, body)
    if status == 409:
        # Idempotency conflicts are validation-shaped; other 409s are state.
        codes = APIError(status, body).codes
        if "idempotency_conflict" in codes:
            return ValidationError(status, body)
        return Conflict(status, body)
    return APIError(status, body)
