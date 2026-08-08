"""What the public contract accepts but the engine does not act on.

Publishing makes the API someone else's dependency, and a field that is accepted
with no effect is the worst kind of promise: it reads as supported, it costs
nothing to send, and it teaches clients to depend on behaviour that was never
there (D-01). "Valid but not implemented" is already a first-class answer in
this engine (INV-014) — silent acceptance is its opposite.

So every accepted-but-unread field is declared here, exactly once, with what
happens when a client sets it:

- ``refuse`` — the field asks for behaviour the engine does not perform, and
  proceeding anyway would be a lie. The request is rejected at validation with
  a stable ``option_not_supported`` code and a message naming the remedy.
- ``warn`` — the field is advisory. The artifact a client gets is still the one
  they asked for, so refusing would be obstructive; the run says out loud that
  the preference had no effect. This mirrors how `reuse_existing` is handled
  when the cache is off.

Restrictions always refuse. Ignoring "keep this local" is not a missing
optimisation, it is a broken guarantee — so `execution_location` and
`allow_remote_processing` reject rather than warn, and name the constraint that
*is* enforced.

`tests/test_contract_validation.py` walks the public request model and fails if
a field appears that is neither read by the engine nor declared here, so the
next silent-ignore cannot arrive unnoticed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from content.domain import errors as codes
from content.domain.errors import ValidationIssue
from content.domain.request import Retention


@dataclass(frozen=True)
class ReservedField:
    """One accepted-but-unimplemented field of the public contract."""

    path: str
    disposition: str  # "refuse" | "warn"
    triggered: Callable[[object], bool]
    reason: str
    remedy: str = ""
    # Sub-paths this entry covers, for the model sweep in the contract tests.
    covers: tuple[str, ...] = field(default_factory=tuple)

    def issue(self) -> ValidationIssue:
        message = self.reason if not self.remedy else f"{self.reason} {self.remedy}"
        return ValidationIssue(
            code=codes.OPTION_NOT_SUPPORTED,
            path=self.path,
            message=message,
            details={"disposition": self.disposition},
        )


def _hints_set(request) -> bool:
    return any(
        source.hints is not None
        and any(
            getattr(source.hints, name) is not None
            for name in ("resource_type", "language", "preferred_provider")
        )
        for source in (request.sources or ())
    )


RESERVED_FIELDS: tuple[ReservedField, ...] = (
    ReservedField(
        path="execution.mode",
        disposition="refuse",
        triggered=lambda r: r.execution.mode != "async",
        reason=(
            "Synchronous submission is not implemented: every job is queued and "
            "executed by the worker."
        ),
        remedy="Omit `mode` and poll GET /api/v1/jobs/{job_id}, or read its events.",
    ),
    ReservedField(
        path="execution.priority",
        disposition="refuse",
        triggered=lambda r: r.execution.priority != "normal",
        reason=(
            "Priorities are not implemented: the queue is strictly first-in, first-out."
        ),
        remedy="Omit `priority`; submit in the order you want the work done.",
    ),
    ReservedField(
        path="execution.retention",
        disposition="refuse",
        # Compared by value, never by `model_fields_set`: a canonical request is
        # replayed verbatim on retry (and echoed by clients), so "the client
        # mentioned this field" would refuse the engine's own round trip.
        triggered=lambda r: r.execution.retention != Retention(),
        reason=(
            "Retention is not implemented: nothing is deleted automatically, so "
            "accepting a lifetime would promise a cleanup that never runs. The "
            "default values are inert too — see docs/contract.md."
        ),
        remedy=(
            "Omit `retention` and manage the data directory yourself "
            "(see docs/storage.md)."
        ),
        covers=(
            "execution.retention.outputs",
            "execution.retention.working_files",
            "execution.retention.logs",
        ),
    ),
    ReservedField(
        path="preferences.execution_location",
        disposition="refuse",
        triggered=lambda r: r.preferences.execution_location != "any",
        reason=(
            "Pinning where a step runs is not implemented, and ignoring it would "
            "silently break the guarantee it is asked for."
        ),
        remedy=(
            "Use `constraints.privacy.allow_cloud_providers: false`, which is "
            "enforced: it removes every cloud-located runner from planning."
        ),
    ),
    ReservedField(
        path="constraints.network.allow_remote_processing",
        disposition="refuse",
        triggered=lambda r: r.constraints.network.allow_remote_processing is not True,
        reason=(
            "This restriction is not enforced, and a restriction that is ignored "
            "is worse than one that is refused."
        ),
        remedy=(
            "Use `constraints.privacy.allow_cloud_providers: false`, which is "
            "enforced during planning."
        ),
    ),
    ReservedField(
        path="preferences.language",
        disposition="refuse",
        triggered=lambda r: r.preferences.language is not None,
        reason=(
            "A request-wide language preference is not read by the planner, so it "
            "would not change which subtitle, transcript or translation you get."
        ),
        remedy=(
            "Ask per output instead — `subtitles.languages`, `audio.languages`, "
            "or the target of a `translation` output."
        ),
    ),
    ReservedField(
        path="sources[].hints",
        disposition="refuse",
        triggered=_hints_set,
        reason=(
            "Hints are not read: routing and resource typing are decided by "
            "analysis facts (ADR 0013), never by what the caller asserts."
        ),
        remedy=(
            "Omit `hints`. To force a provider, name it in `preferences.providers`."
        ),
        covers=(
            "sources[].hints.resource_type",
            "sources[].hints.language",
            "sources[].hints.preferred_provider",
        ),
    ),
    ReservedField(
        path="preferences.optimize_for",
        disposition="warn",
        triggered=lambda r: r.preferences.optimize_for != "balanced",
        reason=(
            "No planner rule consults it yet, so the plan is the same whichever "
            "value is sent."
        ),
        remedy="It is advisory: the artifact you asked for is still produced.",
    ),
)

# Paths that exist in the model, are read by nothing, and are covered above.
RESERVED_PATHS: frozenset[str] = frozenset(
    {entry.path for entry in RESERVED_FIELDS}
    | {covered for entry in RESERVED_FIELDS for covered in entry.covers}
)


def check_reserved(request) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    """Split the reserved fields a request touches into refusals and warnings."""
    refusals: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    for entry in RESERVED_FIELDS:
        if not entry.triggered(request):
            continue
        (refusals if entry.disposition == "refuse" else warnings).append(entry.issue())
    return refusals, warnings
