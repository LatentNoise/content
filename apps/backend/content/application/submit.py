"""Use case: submit a GenerationRequest.

Both client paths (direct submission, analyze-then-submit) converge here:
structural validation → idempotency → analysis (cached) → feasibility + plan
→ persisted job with snapshots and an auditable event trail.

Everything is validated *before* the job exists, so submission is atomic for
the client: a 422 never leaves a half-created job behind.
"""

import json
from dataclasses import dataclass

from content.analysis.service import AnalysisService
from content.application.uploads import resolve_request_uploads
from content.config import ContentSettings
from content.domain import errors as codes
from content.domain.errors import (
    RequestRejected,
    ValidationIssue,
    ValidationResult,
)
from content.domain.plan import ExecutionPlan
from content.domain.request import GenerationRequest
from content.domain.reserved import check_reserved
from content.domain.validation import validate_structure
from content.events.publisher import EventPublisher
from content.persistence.store import IdempotencyKeyActive, Store
from content.planning.planner import build_plan
from content.providers.base import ProviderRegistry
from content.storage.layout import JobStorage


@dataclass
class SubmissionResult:
    job_id: str
    status: str
    created: bool  # False when an idempotency key matched an existing job
    warnings: list[ValidationIssue]


def submit_generation(
    raw_request: dict,
    request: GenerationRequest,
    *,
    store: Store,
    settings: ContentSettings,
    providers: ProviderRegistry,
    analysis_service: AnalysisService,
    retry_of: str = "",
) -> SubmissionResult:
    structural = validate_structure(request)
    if not structural.valid:
        raise RequestRejected(structural)

    canonical = request.canonical_dump()

    key = request.execution.idempotency_key

    def replay_or_conflict() -> SubmissionResult:
        existing = store.find_job_by_idempotency_key(key)
        if existing is not None and existing["request"] == canonical:
            return SubmissionResult(
                job_id=existing["id"],
                status=existing["status"],
                created=False,
                warnings=[],
            )
        raise RequestRejected(
            ValidationResult.failure(
                [
                    ValidationIssue(
                        code=codes.IDEMPOTENCY_CONFLICT,
                        path="execution.idempotency_key",
                        message=(
                            "This idempotency key was already used with a "
                            "different request body."
                        ),
                        details={"job_id": existing["id"] if existing else None},
                    )
                ],
                phase="feasibility",
            )
        )

    if key and store.find_job_by_idempotency_key(key) is not None:
        return replay_or_conflict()

    # An `upload` source becomes the `file` it stands for before anything
    # dispatches on source type, so analysis and planning both see one
    # concrete file and neither learns that uploads exist (ADR 0020).
    request = resolve_request_uploads(request, store, settings)
    analysis = analysis_service.analyze_sources(list(request.sources))
    plan: ExecutionPlan = build_plan(request, analysis, providers, settings)

    # reuse_existing is accepted but inert while the cache is disabled (ADR
    # 0009): tell the client honestly rather than silently ignoring it.
    warnings = list(plan.warnings)
    # Advisory reserved fields (content/domain/reserved.py): the artifact is
    # still the one that was asked for, so the run says the preference had no
    # effect rather than refusing the work over it.
    warnings.extend(check_reserved(request)[1])
    if request.execution.reuse_existing and not settings.cache_enabled:
        warnings.append(
            ValidationIssue(
                code=codes.REUSE_UNAVAILABLE,
                path="execution.reuse_existing",
                message=(
                    "reuse_existing was requested but the inter-job cache is "
                    "disabled; the job will run every step."
                ),
            )
        )

    try:
        job_id = store.create_job(
            canonical, request.execution.failure_policy, key, retry_of=retry_of
        )
    except IdempotencyKeyActive:
        # Lost a concurrent-submission race (T3): the winner holds the key.
        return replay_or_conflict()
    events = EventPublisher(store)
    events.publish(job_id, "job.created", {"retry_of": retry_of} if retry_of else {})

    storage = JobStorage(settings.data_dir, job_id).ensure()
    storage.write_snapshot("request", raw_request)
    storage.write_snapshot("request_normalized", canonical)
    storage.write_snapshot("analysis", json.loads(analysis.model_dump_json()))
    storage.write_snapshot("plan", json.loads(plan.model_dump_json()))

    store.transition_job(job_id, "validating")
    events.publish(job_id, "job.validating", {})
    store.transition_job(job_id, "planning", plan_id=plan.plan_id)
    events.publish(
        job_id,
        "job.planned",
        {
            "plan_id": plan.plan_id,
            "steps": [step.id for step in plan.steps],
            "warnings": [issue.model_dump() for issue in warnings],
        },
    )
    store.create_steps(
        job_id,
        [
            {"id": step.id, "operation": step.operation, "provider": step.provider}
            for step in plan.steps
        ],
    )
    store.transition_job(job_id, "queued")
    events.publish(job_id, "job.queued", {})

    return SubmissionResult(
        job_id=job_id, status="queued", created=True, warnings=warnings
    )
