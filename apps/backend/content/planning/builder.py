"""PlanBuilder: accumulates PlanSteps and output bindings deterministically.

The generalized primitives are ``ensure_step`` (create-or-mutualize a step for a
logical *operation* run by a named *implementation*) and ``bind_output`` (attach
an output to a step). Identical needs mutualize by content-addressed signature.

``bound_step`` / ``acquisition_step`` remain as transitional wrappers over the
new API so existing recipes keep their exact step ids and signatures while they
are migrated one at a time.
"""

import hashlib
import json
from graphlib import TopologicalSorter

from content.domain.plan import OutputBinding, PlanStep
from content.planning.transformations import TransformationRegistry, default_registry


class PlanBuilder:
    def __init__(
        self,
        outputs_required: dict[str, bool],
        registry: TransformationRegistry | None = None,
    ):
        self.steps: list[PlanStep] = []
        self.bindings: list[OutputBinding] = []
        self._outputs_required = outputs_required
        self._registry = registry or default_registry()
        self._step_by_id: dict[str, PlanStep] = {}
        self._id_by_key: dict[str, str] = {}
        self._step_of_output: dict[str, str] = {}

    def _signature(
        self,
        operation: str,
        implementation: str,
        implementation_version: int,
        resource_key: str,
        params: dict,
        depends_on: list[str],
    ) -> str:
        """Content-addressed identity of the work: dependency *signatures* (not
        ids) go into the hash, so it is independent of the client's output ids —
        the anchor of both mutualization and reuse_existing. Includes the
        Content-controlled ``implementation_version`` (not the exact tool
        version, which lives in provenance). The JSON key stays ``provider`` for
        the runner name during the provider→implementation migration."""
        dependency_signatures = sorted(
            self._step_by_id[dep].signature for dep in depends_on
        )
        canonical = json.dumps(
            {
                "op": operation,
                "provider": implementation,
                "impl_version": implementation_version,
                "resource": resource_key,
                "params": params,
                "deps": dependency_signatures,
            },
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def _register(self, step: PlanStep) -> PlanStep:
        self.steps.append(step)
        self._step_by_id[step.id] = step
        self._id_by_key[step.signature] = step.id
        return step

    # --- generalized primitives ------------------------------------------------

    def ensure_step(
        self,
        *,
        operation: str,
        implementation: str,
        params: dict,
        inputs: list[str] | None = None,
        resource_key: str = "",
        source_id: str | None = None,
        id_suffix: str = "",
        unique_id: bool = True,
    ) -> str:
        """Create the step, or return the id of an identical existing one
        (mutualization by signature). ``inputs`` are the ids of the steps whose
        materials this step consumes. ``id_suffix``/``unique_id`` only shape the
        human-readable id, never the signature."""
        deps = sorted(inputs or [])
        # Validate against the central registry (raises UnknownTransformation on
        # an unknown/incompatible operation or implementation) and version it.
        impl = self._registry.implementation(operation, implementation)
        signature = self._signature(
            operation, implementation, impl.version, resource_key, params, deps
        )
        step_id = self._id_by_key.get(signature)
        if step_id is not None:
            return step_id
        short = operation.split(".")[-1]
        base = f"{short}_{id_suffix}" if id_suffix else short
        new_id = f"{base}_{signature[:8]}" if unique_id else base
        self._register(
            PlanStep(
                id=new_id,
                operation=operation,
                provider=implementation,
                source_id=source_id,
                depends_on=deps,
                required=False,  # set by propagation
                params=params,
                signature=signature,
                resource_key=resource_key,
            )
        )
        return new_id

    def bind_output(self, output_id: str, step_id: str) -> str:
        self.bindings.append(
            OutputBinding(artifact_request_id=output_id, produced_by=step_id)
        )
        self._step_of_output[output_id] = step_id
        return step_id

    # --- transitional wrappers (kept until each recipe is migrated) ------------

    def bound_step(
        self,
        output_id: str,
        *,
        operation: str,
        provider: str,
        source_id: str | None,
        params: dict,
        depends_on: list[str] | None = None,
        resource_key: str = "",
        per_item: bool = False,
    ) -> str:
        """A step that produces the given output (ensure_step + bind_output).
        ``per_item`` disambiguates the id so several steps can back one output
        (scope each_item over a collection)."""
        step_id = self.ensure_step(
            operation=operation,
            implementation=provider,
            params=params,
            inputs=depends_on,
            resource_key=resource_key,
            source_id=source_id,
            id_suffix=output_id,
            unique_id=per_item,
        )
        return self.bind_output(output_id, step_id)

    def acquisition_step(
        self,
        *,
        operation: str,
        provider: str,
        source_id: str,
        params: dict,
        depends_on: list[str] | None = None,
        resource_key: str = "",
    ) -> str:
        """An internal (unbound) step producing materials for dependents."""
        return self.ensure_step(
            operation=operation,
            implementation=provider,
            params=params,
            inputs=depends_on,
            resource_key=resource_key,
            source_id=source_id,
            id_suffix=source_id,
            unique_id=True,
        )

    # --- queries / finalization ------------------------------------------------

    def step_of_output(self, output_id: str) -> str | None:
        return self._step_of_output.get(output_id)

    def step_resource_key(self, step_id: str) -> str:
        return self._step_by_id[step_id].resource_key

    def finalize_required(self) -> None:
        """Bound steps inherit their outputs' `required`; then requiredness
        propagates to transitive dependencies (reverse topological pass)."""
        required_by_step: dict[str, bool] = {step.id: False for step in self.steps}
        for binding in self.bindings:
            if self._outputs_required.get(binding.artifact_request_id, False):
                required_by_step[binding.produced_by] = True
        graph = {step.id: list(step.depends_on) for step in self.steps}
        order = list(TopologicalSorter(graph).static_order())
        for step_id in reversed(order):
            if required_by_step[step_id]:
                for dependency in self._step_by_id[step_id].depends_on:
                    required_by_step[dependency] = True
        for step in self.steps:
            step.required = required_by_step[step.id]
