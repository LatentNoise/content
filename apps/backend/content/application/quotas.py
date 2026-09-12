"""What one owner may use of this installation.

Three limits, all optional, all off by default — a self-hosted instance is not
a customer of itself, and neither is an operator: the quotas protect the
installation from its users, and the operator *is* the installation.

The design rests on one choice, and it is worth stating because the obvious
alternative is worse. **Media is counted in seconds of source, not in
processing time.** A source's duration is known at analysis, before any work
happens, so a refusal arrives before the expense instead of after it. And it
is fairer: processing time is twenty times higher for a 4K video than for an
audio clip, at identical service rendered.

A refusal names the limit, what is used, and what is allowed. A limit a person
cannot see themselves approaching is a trap rather than a rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from content.domain import errors as codes
from content.domain.errors import (
    RequestRejected,
    ValidationIssue,
    ValidationResult,
)

# A rolling window rather than a calendar month. Someone who signs up on the
# 30th should not get a fresh allowance the next morning, and nobody should
# have to explain why their month resets at midnight UTC.
WINDOW_DAYS = 30


def window_start(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    return (moment - timedelta(days=WINDOW_DAYS)).isoformat()


@dataclass(frozen=True)
class Usage:
    """What this owner is using right now, in the units the limits are in."""

    media_minutes: float
    storage_bytes: int
    active_jobs: int


@dataclass(frozen=True)
class Limits:
    media_minutes: float
    storage_bytes: int
    active_jobs: int

    @classmethod
    def from_settings(cls, settings) -> "Limits":
        return cls(
            media_minutes=float(getattr(settings, "quota_media_minutes_per_month", 0)),
            storage_bytes=int(getattr(settings, "quota_storage_bytes", 0)),
            active_jobs=int(getattr(settings, "quota_concurrent_jobs", 0)),
        )

    @property
    def any_set(self) -> bool:
        return bool(self.media_minutes or self.storage_bytes or self.active_jobs)


def current_usage(owner_id: str, store, settings) -> Usage:
    """Read the three counters. Cheap on purpose: two indexed queries and one
    directory walk of this owner's own subtree, never the whole disk."""
    from content.storage.paths import owner_storage_report

    seconds = store.media_seconds_since(owner_id, window_start())
    try:
        held = owner_storage_report(settings, owner_id)["total_bytes"]
    except ValueError:
        # An owner id that is not a usable path segment holds nothing, by
        # construction — there is no directory it could have written to.
        held = 0
    return Usage(
        media_minutes=seconds / 60.0,
        storage_bytes=int(held),
        active_jobs=store.count_active_jobs(owner_id),
    )


def describe(owner_id: str, store, settings, *, is_operator: bool = False) -> dict:
    """Usage and limits, for a person to see where they stand."""
    limits = Limits.from_settings(settings)
    usage = current_usage(owner_id, store, settings)
    return {
        "owner_id": owner_id,
        "window_days": WINDOW_DAYS,
        "exempt": is_operator,
        "media_minutes": {
            "used": round(usage.media_minutes, 2),
            "allowed": limits.media_minutes or None,
        },
        "storage_bytes": {
            "used": usage.storage_bytes,
            "allowed": limits.storage_bytes or None,
        },
        "active_jobs": {
            "used": usage.active_jobs,
            "allowed": limits.active_jobs or None,
        },
    }


def _refuse(limit: str, used: float, allowed: float, message: str) -> None:
    raise RequestRejected(
        ValidationResult.failure(
            [
                ValidationIssue(
                    code=codes.QUOTA_EXCEEDED,
                    path="execution",
                    message=message,
                    details={"limit": limit, "used": used, "allowed": allowed},
                )
            ],
            phase="feasibility",
        )
    )


def check(
    owner_id: str,
    store,
    settings,
    *,
    media_seconds: float = 0.0,
    is_operator: bool = False,
) -> None:
    """Refuse the submission if it would take this owner over a limit.

    Called after analysis and before the job row exists, which is the only
    moment where the media duration is known *and* nothing has been spent yet.
    """
    limits = Limits.from_settings(settings)
    if is_operator or not limits.any_set:
        return

    usage = current_usage(owner_id, store, settings)

    if limits.active_jobs and usage.active_jobs >= limits.active_jobs:
        _refuse(
            "active_jobs",
            usage.active_jobs,
            limits.active_jobs,
            f"You already have {usage.active_jobs} job(s) running and this "
            f"installation allows {limits.active_jobs} at a time. "
            "Wait for one to finish.",
        )

    if limits.storage_bytes and usage.storage_bytes >= limits.storage_bytes:
        _refuse(
            "storage_bytes",
            usage.storage_bytes,
            limits.storage_bytes,
            f"You are holding {usage.storage_bytes} bytes and this "
            f"installation allows {limits.storage_bytes}. "
            "Delete something you no longer need.",
        )

    if limits.media_minutes:
        # The *requested* minutes count toward the check, so a single oversized
        # request is refused up front rather than accepted and then blowing
        # through the limit on its own.
        wanted = usage.media_minutes + media_seconds / 60.0
        if wanted > limits.media_minutes:
            _refuse(
                "media_minutes",
                round(wanted, 2),
                limits.media_minutes,
                f"This would bring you to {wanted:.0f} minutes of media in the "
                f"last {WINDOW_DAYS} days, and this installation allows "
                f"{limits.media_minutes:g}.",
            )


def media_seconds_of(analysis) -> float:
    """How much source media a request is asking the engine to handle.

    A collection counts its members, since asking for a playlist asks for every
    video in it. A source whose duration is unknown counts as zero rather than
    blocking the request: refusing what cannot be measured would turn every
    unusual source into a support ticket.
    """
    total = 0.0
    for source in getattr(analysis, "sources", []) or []:
        entries = getattr(source, "entries", None) or []
        if entries:
            total += sum(float(entry.duration_seconds or 0) for entry in entries)
            continue
        resource = getattr(source, "resource", None)
        total += float(getattr(resource, "duration_seconds", 0) or 0)
    return total
