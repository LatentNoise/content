"""JobExecutor: runs a claimed job's ExecutionPlan step by step.

The executor is the only writer of step/job statuses during a run, and every
transition goes through the domain state machines.

Steps exchange **materials**: the products of a step's dependencies are handed
to it through ``ExecutionContext.input_materials``. A step bound to outputs
gets its products promoted as artifacts (write-then-register — the file is
moved into artifacts/ before the DB row exists, so a crash can never leave a
registered-but-missing artifact); an unbound step's products stay in work/ as
internal materials. A step whose dependency did not succeed is skipped.
"""

import json
import time

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
        produced_count: dict[str, int] = {output.id: 0 for output in request.outputs}
        step_status: dict[str, str] = {step.id: "pending" for step in plan.steps}
        step_materials: dict[str, list[Material]] = {}
        step_artifact_ids: dict[str, list[str]] = {}
        stop_new_steps = False

        # Inter-job reuse is a cache feature: inert unless the cache is enabled
        # (ADR 0009). reuse_existing=true is accepted but has no effect in V1.
        reuse_enabled = (
            self._settings.cache_enabled and request.execution.reuse_existing
        )

        for step in plan.ordered_steps():
            if self._store.is_cancel_requested(job_id):
                self._finish_cancelled(job_id, plan, step_status, storage)
                return

            # Reuse is checked before the dependency gate: cached work stands
            # on its own (the signature covers the whole upstream chain).
            reused = (
                self._find_reusable(job_id, step)
                if reuse_enabled and plan.bindings_for_step(step.id)
                else None
            )

            failed_deps = [
                dep for dep in step.depends_on if step_status.get(dep) != "succeeded"
            ]
            budget_exhausted = deadline is not None and time.monotonic() > deadline
            if stop_new_steps or budget_exhausted or (failed_deps and reused is None):
                if budget_exhausted:
                    reason = "job runtime budget exhausted"
                elif failed_deps:
                    reason = f"dependency did not succeed: {', '.join(failed_deps)}"
                else:
                    reason = "skipped by fail_fast policy"
                self._move_step(job_id, step.id, step_status, "skipped")
                self._events.publish(
                    job_id, "step.skipped", {"step_id": step.id, "reason": reason}
                )
                continue

            inputs = [
                material
                for dep in step.depends_on
                for material in step_materials.get(dep, [])
            ]
            parent_artifact_ids = [
                artifact_id
                for dep in step.depends_on
                for artifact_id in step_artifact_ids.get(dep, [])
            ]

            self._move_step(job_id, step.id, step_status, "ready")
            self._move_step(
                job_id, step.id, step_status, "running", started_at=utcnow()
            )
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
                    produced_count,
                    promote_mode=promote_mode,
                    producer_override=producer_override,
                )
            except StepExecutionError as exc:
                if exc.code == "cancelled" or self._store.is_cancel_requested(job_id):
                    self._move_step(
                        job_id, step.id, step_status, "cancelled", finished_at=utcnow()
                    )
                    self._finish_cancelled(job_id, plan, step_status, storage)
                    return
                self._move_step(
                    job_id,
                    step.id,
                    step_status,
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
                if policy == "fail_fast" and step.required:
                    stop_new_steps = True
                continue

            step_materials[step.id] = materials
            step_artifact_ids[step.id] = artifact_ids
            self._move_step(
                job_id, step.id, step_status, "succeeded", finished_at=utcnow()
            )
            succeeded_data = {"step_id": step.id, "artifact_ids": artifact_ids}
            if reused is not None:
                succeeded_data["reused_from_job"] = reused_from_job
            self._events.publish(job_id, "step.succeeded", succeeded_data)

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
            policy, required_missing, optional_missing, any_artifact
        )
        self._store.transition_job(job_id, final, finished_at=utcnow())
        self._events.publish(job_id, f"job.{final}", {"outputs": produced_count})
        storage.write_snapshot("result", {"status": final, "outputs": produced_count})
        storage.purge_work()
        storage.purge_tmp()

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
        produced_count: dict[str, int],
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
                produced_count[output.id] = produced_count.get(output.id, 0) + 1
                all_artifact_ids.append(artifact_id)
                delivered = self._deliver_artifact(
                    plan, output, target, display_filename
                )
                if delivered:
                    self._store.set_artifact_delivered(artifact_id, delivered)
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
    ) -> str:
        """Copy the artifact into the delivery library when the plan says so
        (ADR 0018), under its display name (ADR 0017) — the executor decides
        nothing here. Returns the delivered path relative to the delivery
        root, or ``""`` when no delivery happens."""
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
            return ""
        root = self._settings.delivery_dir or (self._settings.data_dir / "delivery")
        store = DeliveryStore(root)
        try:
            delivered = store.deliver(target, folder, display_filename)
        except OSError as exc:
            raise StepExecutionError(
                "delivery_failed", f"could not deliver artifact: {exc}"
            ) from exc
        return delivered.relative_to(store.root).as_posix()

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
        step_status: dict[str, str],
        target: str,
        **fields,
    ) -> None:
        ensure_step_transition(step_status[step_id], target)
        step_status[step_id] = target
        self._store.update_step(job_id, step_id, status=target, **fields)

    def _finish_cancelled(
        self,
        job_id: str,
        plan: ExecutionPlan,
        step_status: dict[str, str],
        storage: JobStorage,
    ) -> None:
        for step in plan.steps:
            if step_status[step.id] in ("pending", "ready"):
                self._move_step(job_id, step.id, step_status, "cancelled")
        self._store.transition_job(job_id, "cancelled", finished_at=utcnow())
        self._events.publish(job_id, "job.cancelled", {})
        storage.purge_work()
        storage.purge_tmp()
