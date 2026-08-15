"""Resolving an `upload` source to the file it stands for (ADR 0020).

The whole feature rests on one invariant: **upload is acquisition, never
processing**. An uploaded PDF must behave exactly like the same PDF sitting in
an allowed input root — same analysis, same capabilities, same planner, same
naming, delivery and provenance.

The cheapest way to guarantee that is to resolve the upload *before* anything
dispatches on source type: an `UploadSource` becomes a `FileSource` pointing at
the stored bytes, and no provider, analyzer or planner ever learns that uploads
exist. The alternative — teaching each provider a second source type — is how a
second pipeline grows, one `if source.type == "upload"` at a time.

Resolution also restarts the expiry clock: an upload a job just used must
survive long enough for that job to be retried.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from content.domain.errors import ValidationIssue, codes
from content.domain.request import FileSource, SourceDescriptor, UploadSource


def resolve_upload_sources(
    sources: list[SourceDescriptor], store, settings
) -> tuple[list[SourceDescriptor], list[ValidationIssue]]:
    """Swap every `upload` source for the `file` it stands for.

    Returns the rewritten list and the issues for uploads that could not be
    resolved. An unknown id and an expired one are told apart deliberately:
    "it expired" tells a caller to upload again, "no such id" tells them they
    have the wrong reference, and answering both with the same words would
    leave them guessing.
    """
    resolved: list[SourceDescriptor] = []
    issues: list[ValidationIssue] = []
    for index, source in enumerate(sources):
        if not isinstance(source, UploadSource):
            resolved.append(source)
            continue
        path = f"sources[{index}].upload_id"
        row = store.get_upload(source.upload_id)
        if row is None:
            issues.append(
                ValidationIssue(
                    code=codes.UPLOAD_NOT_FOUND,
                    path=path,
                    message=f"No upload '{source.upload_id}'.",
                )
            )
            continue
        if _is_expired(row, settings):
            issues.append(
                ValidationIssue(
                    code=codes.UPLOAD_EXPIRED,
                    path=path,
                    message=(
                        f"Upload '{source.upload_id}' has expired and its bytes "
                        "are gone. Upload the file again."
                    ),
                )
            )
            continue
        store.touch_upload(source.upload_id)
        resolved.append(
            FileSource(
                id=source.id,
                type="file",
                path=row["path"],
                role=source.role,
                hints=source.hints,
                auth=source.auth,
            )
        )
    return resolved, issues


def _is_expired(row: dict, settings) -> bool:
    """Past its TTL, counted from the last reference rather than creation."""
    ttl = getattr(settings, "upload_ttl_hours", 0) or 0
    if ttl <= 0:
        return False
    try:
        last = datetime.fromisoformat(row["last_referenced_at"])
    except (KeyError, ValueError):
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last > timedelta(hours=ttl)
