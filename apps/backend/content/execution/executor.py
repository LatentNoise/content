"""JobExecutor: runs a claimed job's ExecutionPlan step by step.

The executor is the only writer of step/job statuses during a run, and every
transition goes through the domain state machines.

Steps exchange **materials**: the products of a step's dependencies are handed
to it through ``ExecutionContext.input_materials``. A step bound to outputs
gets its products promoted as artifacts (write-then-register — the file is
moved into artifacts/ before the DB row exists, so a crash can never leave a
registered-but-missing artifact); an unbound step's products stay in work/ as
internal materials. A step whose dependency did not succeed is skipped.

Collection members are the one place steps run concurrently (ADR 0019): a run
of consecutive ``collection.member`` steps — independent by construction, no
member depends on another — is dispatched to a small thread pool bounded by
``CONTENT_COLLECTION_MEMBER_CONCURRENCY``. Everything else about a member step
is the ordinary step lifecycle; the pool only changes *when* members start,
never what a member is. Every other step keeps the strictly sequential loop.
"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from content.application.collections import MEMBER_OPERATION
from content.config import ContentSettings
from content.domain.job import aggregate_final_status, ensure_step_transition
from content.domain.plan import ExecutionPlan, PlanStep
from content.domain.request import GenerationRequest
from content.events.publisher import EventPublisher
from content.naming.engine import bind_filename
from content.persistence.store import Store, new_id, utcnow
from content.providers.base import (
    ExecutionContext,
    Material,
    ProducedFile,
    ProviderRegistry,
    StepExecutionError,
)
from content.storage.layout import DeliveryStore, JobStorage, checksum_sha256

_PROGRESS_MIN_DELTA = 1.0  # percent — event throttling, HomeTube-proven


class _RunState:
    """The mutable state one running job's steps share.

    With member concurrency the executor touches these from a thread pool, so
    every mutation goes through a method holding the one lock. The lock guards
    dict/flag work only — never a runner, a file move or a store call (the
    store is safe on its own: WAL, one short-lived connection per call). Steps
    never share ids, and member artifact names embed the member's ordinal
    (``item_slug``), so files cannot collide either — what genuinely needs
    protection is exactly what is here: the shared counters and flags.
    """

    def __init__(self, plan: ExecutionPlan, request: GenerationRequest):
        self._lock = threading.Lock()
        self._step_status = {step.id: "pending" for step in plan.steps}
        self._produced_count = {output.id: 0 for output in request.outputs}
        self._step_materials: dict[str, list[Material]] = {}
        self._step_artifact_ids: dict[str, list[str]] = {}
        self._stop_new_steps = False

    def transition(self, step_id: str, target: str) -> None:
        """Validate and record a step transition (the store write is the
        caller's, after — the domain rule must hold before anything persists).
        """
        with self._lock:
            ensure_step_transition(self._step_status[step_id], target)
            self._step_status[step_id] = target

    def status_of(self, step_id: str) -> str:
        with self._lock:
            return self._step_status.get(step_id, "pending")

    def unfinished_steps(self) -> list[str]:
        with self._lock:
            return [
                step_id
                for step_id, status in self._step_status.items()
                if status in ("pending", "ready")
            ]

    def record_products(
        self, step_id: str, materials: list[Material], artifact_ids: list[str]
    ) -> None:
        with self._lock:
            self._step_materials[step_id] = materials
            self._step_artifact_ids[step_id] = artifact_ids

    def inputs_for(self, step: PlanStep) -> tuple[list[Material], list[str]]:
        with self._lock:
            materials = [
                material
                for dep in step.depends_on
                for material in self._step_materials.get(dep, [])
            ]
            artifact_ids = [
                artifact_id
                for dep in step.depends_on
                for artifact_id in self._step_artifact_ids.get(dep, [])
            ]
        return materials, artifact_ids

    def count_artifact(self, output_id: str) -> None:
        with self._lock:
            self._produced_count[output_id] = self._produced_count.get(output_id, 0) + 1

    def any_step_failed(self) -> bool:
        """Did any step end `failed`? (ADR 0021.)

        Read from the recorded statuses rather than tracked by a flag: skipped
        steps must not count — under `fail_fast` they were never attempted —
        and cancelled ones end the job by another path entirely.
        """
        with self._lock:
            return any(status == "failed" for status in self._step_status.values())

    def produced_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._produced_count)

    def request_stop(self) -> None:
        with self._lock:
            self._stop_new_steps = True

    @property
    def stopping(self) -> bool:
        with self._lock:
            return self._stop_new_steps


def _dispatch_groups(steps: list[PlanStep], member_limit: int):
    """Split the ordered plan into dispatch groups.

    Runs of consecutive collection-member steps become one group the executor
    may run concurrently; every other step is a group of one, executed exactly
    as it always was. With a limit of 1 everything is a group of one — the
    sequential executor, unchanged. The ``depends_on`` guard is defensive: the
    planner never gives a member step dependencies, and a step that somehow
    had them must stay in the ordered sequential flow.
    """
    group: list[PlanStep] = []
    for step in steps:
        if (
            member_limit > 1
            and step.operation == MEMBER_OPERATION
            and not step.depends_on
        ):
            group.append(step)
            continue
        if group:
            yield group
            group = []
        yield [step]
    if group:
        yield group


class JobExecutor:
    def __init__(
        self, store: Store, settings: ContentSettings, providers: ProviderRegistry
    ):
        self._store = store
        self._settings = settings
        self._providers = providers
        self._events = EventPublisher(store)

    # --- public entry point ----------------------------------------------------

    def execute(self, job_row: dict) -> None:
        """Run a job already claimed as 'running'. Never raises: every outcome
        is persisted as a terminal status."""
        job_id = job_row["id"]
        try:
            self._execute(job_id, job_row)
        except Exception as exc:  # noqa: BLE001 — a worker must never die silently
            self._store.transition_job(
                job_id, "failed", error=f"executor error: {exc}", finished_at=utcnow()
            )
            self._events.publish(job_id, "job.failed", {"error": str(exc)})

    # --- internals -------------------------------------------------------------

    def _execute(self, job_id: str, job_row: dict) -> None:
        request = GenerationRequest.model_validate(job_row["request"])
        storage = JobStorage(self._settings.data_dir, job_id).ensure()
        plan = ExecutionPlan.model_validate(
            json.loads((storage.snapshots / "plan.json").read_text())
        )
        self._events.publish(job_id, "job.started", {})

        policy = request.execution.failure_policy
        max_runtime = request.constraints.resources.max_runtime_seconds
        deadline = time.monotonic() + max_runtime if max_runtime else None
        outputs_by_id = {output.id: output for output in request.outputs}
        state = _RunState(plan, request)

        # Inter-job reuse is a cache feature: inert unless the cache is enabled
        # (ADR 0009). reuse_existing=true is accepted but has no effect in V1.
        reuse_enabled = (
            self._settings.cache_enabled and request.execution.reuse_existing
        )

        member_limit = max(1, self._settings.collection_member_concurrency)
        for group in _dispatch_groups(list(plan.ordered_steps()), member_limit):
            if len(group) == 1:
                outcome = self._advance_step(
                    job_id,
                    group[0],
                    plan,
                    request,
                    storage,
                    deadline,
                    state,
                    reuse_enabled,
                )
                cancelled = outcome == "cancelled"
            else:
                cancelled = self._run_member_group(
                    job_id,
                    group,
                    plan,
                    request,
                    storage,
                    deadline,
                    state,
                    reuse_enabled,
                    member_limit,
                )
            if cancelled:
                self._finish_cancelled(job_id, plan, state, storage)
                return

        produced_count = state.produced_counts()
        required_missing = any(
            count == 0 and outputs_by_id[output_id].required
            for output_id, count in produced_count.items()
        )
        optional_missing = any(
            count == 0 and not outputs_by_id[output_id].required
            for output_id, count in produced_count.items()
        )
        any_artifact = any(count > 0 for count in produced_count.values())
        final = aggregate_final_status(
            policy,
            required_missing,
            optional_missing,
            any_artifact,
            any_step_failed=state.any_step_failed(),
        )
        self._store.transition_job(job_id, final, finished_at=utcnow())
        self._events.publish(job_id, f"job.{final}", {"outputs": produced_count})
        storage.write_snapshot("result", {"status": final, "outputs": produced_count})
        storage.purge_work()
        storage.purge_tmp()

    def _run_member_group(
        self,
        job_id: str,
        group: list[PlanStep],
        plan: ExecutionPlan,
        request: GenerationRequest,
        storage: JobStorage,
        deadline: float | None,
        state: _RunState,
        reuse_enabled: bool,
        member_limit: int,
    ) -> bool:
        """Run one group of member steps with bounded concurrency; True means
        cancellation was observed and the job must finish as cancelled.

        The bound is a politeness limit toward the provider, not a throughput
        feature: two concurrent members are two concurrent downloads from the
        same host. On cancellation, in-flight members stop through their own
        ``cancel_check`` and everything not yet started stays pending for
        ``_finish_cancelled`` to sweep — the same shape the sequential path
        leaves behind. ``fail_fast`` stops members that have not started;
        members already in flight run to completion (a valid artifact is never
        thrown away mid-download) — see ADR 0019.
        """
        outcomes: list[str] = []
        workers = min(member_limit, len(group))
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix=f"{job_id}-member"
        ) as pool:
            outcomes = list(
                pool.map(
                    lambda step: self._advance_step(
                        job_id,
                        step,
                        plan,
                        request,
                        storage,
                        deadline,
                        state,
                        reuse_enabled,
                    ),
                    group,
                )
            )
        return "cancelled" in outcomes

    def _advance_step(
        self,
        job_id: str,
        step: PlanStep,
        plan: ExecutionPlan,
        request: GenerationRequest,
        storage: JobStorage,
        deadline: float | None,
        state: _RunState,
        reuse_enabled: bool,
    ) -> str:
        """One step through its full lifecycle: gate, run, register, settle.

        Returns ``"cancelled"`` the moment cancellation is observed — the step
        (if it started) is already moved to cancelled, but the *job* is not
        finalized here: the caller does that once, after any concurrent
        siblings have wound down. Every other outcome ("succeeded", "failed",
        "skipped") is fully settled on return.
        """
        if self._store.is_cancel_requested(job_id):
            return "cancelled"

        # Reuse is checked before the dependency gate: cached work stands
        # on its own (the signature covers the whole upstream chain).
        reused = (
            self._find_reusable(job_id, step)
            if reuse_enabled and plan.bindings_for_step(step.id)
            else None
        )

        failed_deps = [
            dep for dep in step.depends_on if state.status_of(dep) != "succeeded"
        ]
        budget_exhausted = deadline is not None and time.monotonic() > deadline
        if state.stopping or budget_exhausted or (failed_deps and reused is None):
            if budget_exhausted:
                reason = "job runtime budget exhausted"
            elif failed_deps:
                reason = f"dependency did not succeed: {', '.join(failed_deps)}"
            else:
                reason = "skipped by fail_fast policy"
            self._move_step(job_id, step.id, state, "skipped")
            self._events.publish(
                job_id, "step.skipped", {"step_id": step.id, "reason": reason}
            )
            return "skipped"

        inputs, parent_artifact_ids = state.inputs_for(step)

        self._move_step(job_id, step.id, state, "ready")
        self._move_step(job_id, step.id, state, "running", started_at=utcnow())
        self._events.publish(job_id, "step.started", {"step_id": step.id})

        try:
            if reused is not None:
                produced, reused_from_job, producer_override = reused
                promote_mode = "copy"
            else:
                produced = self._run_step(job_id, step, storage, deadline, inputs)
                reused_from_job, producer_override = "", None
                promote_mode = "move"
            artifact_ids, materials = self._register_products(
                job_id,
                step,
                plan,
                produced,
                request,
                storage,
                parent_artifact_ids,
                state,
                promote_mode=promote_mode,
                producer_override=producer_override,
            )
        except StepExecutionError as exc:
            if exc.code == "cancelled" or self._store.is_cancel_requested(job_id):
                self._move_step(
                    job_id, step.id, state, "cancelled", finished_at=utcnow()
                )
                return "cancelled"
            self._move_step(
                job_id,
                step.id,
                state,
                "failed",
                error=f"{exc.code}: {exc}",
                finished_at=utcnow(),
            )
            self._events.publish(
                job_id,
                "step.failed",
                {
                    "step_id": step.id,
                    "code": exc.code,
                    "message": str(exc),
                    # Machine-readable context when the failure has some;
                    # absent rather than empty so consumers can tell the
                    # difference between "none" and "not reported".
                    **({"details": exc.details} if exc.details else {}),
                },
            )
            if request.execution.failure_policy == "fail_fast" and step.required:
                state.request_stop()
            return "failed"

        state.record_products(step.id, materials, artifact_ids)
        self._move_step(job_id, step.id, state, "succeeded", finished_at=utcnow())
        succeeded_data = {"step_id": step.id, "artifact_ids": artifact_ids}
        if reused is not None:
            succeeded_data["reused_from_job"] = reused_from_job
        self._events.publish(job_id, "step.succeeded", succeeded_data)
        return "succeeded"

    def _run_step(
        self,
        job_id: str,
        step: PlanStep,
        storage: JobStorage,
        deadline: float | None,
        inputs: list[Material],
    ) -> list[ProducedFile]:
        runner = self._providers.get(step.provider)
        timeout = float(self._settings.step_timeout_seconds)
        if deadline is not None:
            timeout = max(1.0, min(timeout, deadline - time.monotonic()))
        last_percent = {"value": -_PROGRESS_MIN_DELTA}

        def on_progress(percent: float, message: str) -> None:
            if percent - last_percent["value"] >= _PROGRESS_MIN_DELTA:
                last_percent["value"] = percent
                self._events.step_progress(
                    job_id, step.id, percent, 100.0, "percent", message
                )

        return runner.execute(
            step,
            ExecutionContext(
                settings=self._settings,
                workdir=storage.work,
                stdout_log=storage.step_log_path(step.id, "stdout"),
                stderr_log=storage.step_log_path(step.id, "stderr"),
                timeout_seconds=timeout,
                input_materials=inputs,
                cancel_check=lambda: self._store.is_cancel_requested(job_id),
                on_progress=on_progress,
            ),
        )

    def _find_reusable(
        self, job_id: str, step: PlanStep
    ) -> tuple[list[ProducedFile], str, dict] | None:
        """Products of the most recent identical step from another job
        (matched by content-addressed signature), checksum-verified on disk.
        Returns (produced, source_job_id, original_producer) or None — any
        missing/corrupt file falls back to a normal run."""
        group = self._store.find_reusable_artifact_group(step.signature, job_id)
        if not group:
            return None
        source_job_id = group[0]["job_id"]
        source_storage = JobStorage(self._settings.data_dir, source_job_id)
        produced: list[ProducedFile] = []
        seen_checksums: set[str] = set()
        for row in group:
            if row["checksum"] in seen_checksums:
                continue  # multi-binding duplicates of the same product
            seen_checksums.add(row["checksum"])
            original = source_storage.artifacts / row["filename"]
            if not original.is_file() or checksum_sha256(original) != row["checksum"]:
                return None
            attributes = dict(row["provenance"].get("attributes", {}))
            attributes["reused_from_artifact_id"] = row["id"]
            produced.append(
                ProducedFile(
                    path=original,
                    media_type=row["media_type"],
                    attributes=attributes,
                )
            )
        producer = dict(group[0]["provenance"].get("producer", {}))
        return produced, source_job_id, producer

    def _register_products(
        self,
        job_id: str,
        step: PlanStep,
        plan: ExecutionPlan,
        produced: list[ProducedFile],
        request: GenerationRequest,
        storage: JobStorage,
        parent_artifact_ids: list[str],
        state: _RunState,
        *,
        promote_mode: str = "move",
        producer_override: dict | None = None,
    ) -> tuple[list[str], list[Material]]:
        """Promote products for each binding (move once — or copy when the
        product is a reused artifact from another job — then copy for extra
        bindings); an unbound step's products become internal materials."""
        bindings = plan.bindings_for_step(step.id)
        if not bindings:
            return [], [
                Material(
                    path=item.path,
                    media_type=item.media_type,
                    attributes=item.attributes,
                    from_step=step.id,
                )
                for item in produced
            ]

        max_bytes = self._max_artifact_bytes(request)
        all_artifact_ids: list[str] = []
        materials: list[Material] = []
        for item_index, item in enumerate(produced, start=1):
            size = item.path.stat().st_size
            if max_bytes and size > max_bytes:
                if promote_mode == "move":
                    item.path.unlink(missing_ok=True)
                raise StepExecutionError(
                    "constraint_violated",
                    f"artifact exceeds max_output_bytes ({size} > {max_bytes}).",
                )
            first_target = None
            for binding in bindings:
                output = next(
                    o for o in request.outputs if o.id == binding.artifact_request_id
                )
                language = item.attributes.get("language")
                suffix = item.path.suffix
                # Per-item steps (scope each_item) carry a label so a playlist's
                # artifacts get distinct, meaningful names under one output.
                item_label = step.params.get("item_label")
                base = f"{output.id}-{item_label}" if item_label else output.id
                filename = (
                    f"{base}.{language}{suffix}" if language else f"{base}{suffix}"
                )
                # The user-facing name: the NamingPlan resolved at planning
                # time, bound with what execution knows — extension, language,
                # cardinality (ADR 0017). Mechanical; no decisions here.
                display_filename = bind_filename(
                    plan.naming.for_output(output.id),
                    output_id=output.id,
                    extension=suffix,
                    language=language or "",
                    item_label=item_label or "",
                    item_index=item_index,
                    item_count=len(produced),
                )
                if first_target is None:
                    if promote_mode == "copy":
                        target = storage.promote_artifact_copy(item.path, filename)
                    else:
                        target = storage.promote_artifact(item.path, filename)
                    first_target = target
                else:
                    target = storage.promote_artifact_copy(first_target, filename)
                artifact_id = self._register_artifact(
                    job_id,
                    step,
                    output,
                    item,
                    target,
                    size,
                    parent_artifact_ids,
                    producer_override,
                    display_filename,
                )
                state.count_artifact(output.id)
                all_artifact_ids.append(artifact_id)
                delivered, collided_with = self._deliver_artifact(
                    plan, output, target, display_filename
                )
                if delivered:
                    self._store.set_artifact_delivered(artifact_id, delivered)
                    # Delivery used to be invisible in the event stream: the
                    # library gained a file and the only trace was a
                    # `delivered_path` column nothing surfaced. It matters most
                    # when the name was taken — two different videos sharing a
                    # title leave a `…-1` the user meets weeks later, wondering
                    # which job produced which.
                    self._events.publish(
                        job_id,
                        "artifact.delivered",
                        {
                            "artifact_id": artifact_id,
                            "artifact_request_id": output.id,
                            "path": delivered,
                            "renamed_from": collided_with,
                        },
                    )
                if target is first_target:
                    materials.append(
                        Material(
                            path=target,
                            media_type=item.media_type,
                            attributes=item.attributes,
                            from_step=step.id,
                            artifact_id=artifact_id,
                        )
                    )
        return all_artifact_ids, materials

    def _deliver_artifact(
        self, plan: ExecutionPlan, output, target, display_filename: str
    ) -> tuple[str, str]:
        """Copy the artifact into the delivery library when the plan says so
        (ADR 0018), under its display name (ADR 0017) — the executor decides
        nothing here.

        Returns the delivered path relative to the delivery root (``""`` when
        no delivery happens), and the name that was wanted when delivery had
        to rename around a collision (``""`` otherwise)."""
        decision = plan.delivery_for(output.id)
        if decision is None:
            # Plan snapshotted before ADR 0018: the historical rule (deliver
            # iff the request carries folder/filename intent).
            delivery = getattr(output, "delivery", None)
            deliver = bool(delivery and (delivery.folder or delivery.filename))
            folder = delivery.folder if delivery else ""
        else:
            deliver, folder = decision.deliver, decision.folder
        if not deliver:
            return "", ""
        root = self._settings.delivery_dir or (self._settings.data_dir / "delivery")
        store = DeliveryStore(root)
        try:
            delivered = store.deliver(target, folder, display_filename)
        except OSError as exc:
            raise StepExecutionError(
                "delivery_failed", f"could not deliver artifact: {exc}"
            ) from exc
        wanted = store.expected_name(display_filename)
        return (
            delivered.relative_to(store.root).as_posix(),
            "" if delivered.name == wanted else wanted,
        )

    def _register_artifact(
        self,
        job_id: str,
        step: PlanStep,
        output,
        item: ProducedFile,
        target,
        size: int,
        parent_artifact_ids: list[str],
        producer_override: dict | None = None,
        display_filename: str = "",
    ) -> str:
        runner = self._providers.get(step.provider)
        producer = producer_override or {
            "operation": step.operation,
            "provider": step.provider,
            "tool_version": runner.tool_version,
        }
        artifact_id = new_id("art")
        self._store.register_artifact(
            {
                "id": artifact_id,
                "job_id": job_id,
                "artifact_request_id": output.id,
                "type": output.type,
                "filename": target.name,
                "display_filename": display_filename,
                "media_type": item.media_type,
                "size_bytes": size,
                "checksum": checksum_sha256(target),
                "resource_key": step.resource_key,
                "step_signature": step.signature,
                "provenance": {
                    "source_ids": [step.source_id] if step.source_id else [],
                    "parent_artifact_ids": parent_artifact_ids,
                    "producer": producer,
                    "attributes": item.attributes,
                },
            }
        )
        self._events.publish(
            job_id,
            "artifact.created",
            {
                "artifact_id": artifact_id,
                "artifact_request_id": output.id,
                "filename": target.name,
                "display_filename": display_filename,
            },
        )
        return artifact_id

    def _max_artifact_bytes(self, request: GenerationRequest) -> int | None:
        candidates = [
            value
            for value in (
                request.constraints.resources.max_output_bytes,
                self._settings.max_artifact_bytes or None,
            )
            if value
        ]
        return min(candidates) if candidates else None

    def _move_step(
        self,
        job_id: str,
        step_id: str,
        state: _RunState,
        target: str,
        **fields,
    ) -> None:
        state.transition(step_id, target)
        self._store.update_step(job_id, step_id, status=target, **fields)

    def _finish_cancelled(
        self,
        job_id: str,
        plan: ExecutionPlan,
        state: _RunState,
        storage: JobStorage,
    ) -> None:
        unfinished = set(state.unfinished_steps())
        for step in plan.steps:
            if step.id in unfinished:
                self._move_step(job_id, step.id, state, "cancelled")
        self._store.transition_job(job_id, "cancelled", finished_at=utcnow())
        self._events.publish(job_id, "job.cancelled", {})
        storage.purge_work()
        storage.purge_tmp()
