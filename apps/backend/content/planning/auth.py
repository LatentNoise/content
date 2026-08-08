"""Source authentication resolution.

Pilot extraction from the planner (first step of its decomposition): a small,
pure, independently testable module. Server-side credentials only — a source
references a configured credential by ``auth.credential_id`` and the secret
never enters the request (INV-009). Shared by feasibility (planner) and
analysis so both honour or reject ``auth`` consistently (INV-100).
"""

from collections.abc import Iterable

from content.domain import errors as codes
from content.domain.errors import ValidationIssue
from content.domain.request import SourceDescriptor


def resolve_source_credential(
    source: SourceDescriptor,
    credential_ids: Iterable[str],
    path: str = "",
) -> tuple[str | None, ValidationIssue | None]:
    """Validate ``source.auth`` against the configured credential ids.

    Returns ``(credential_id, issue)`` — exactly one is non-None, or both are
    None when the source declares no auth:

    - no auth → ``(None, None)``
    - ``session_id`` → ``auth_method_not_supported``
    - ``credential_id`` absent from the configuration → ``credential_not_available``
    - ``credential_id`` configured → ``(credential_id, None)``
    """
    auth = getattr(source, "auth", None)
    if auth is None:
        return None, None
    where = f"{path}.auth" if path else "auth"
    if auth.session_id:
        return None, ValidationIssue(
            code=codes.AUTH_METHOD_NOT_SUPPORTED,
            path=where,
            message=(
                "Ephemeral session auth (session_id) is not supported; use a "
                "server-configured credential_id."
            ),
        )
    if auth.credential_id:
        if auth.credential_id not in set(credential_ids):
            return None, ValidationIssue(
                code=codes.CREDENTIAL_NOT_AVAILABLE,
                path=f"{where}.credential_id",
                message=(
                    f"Credential '{auth.credential_id}' is not configured on "
                    "this server."
                ),
                details={"credential_id": auth.credential_id},
            )
        return auth.credential_id, None
    return None, None
