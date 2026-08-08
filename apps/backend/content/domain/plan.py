"""ExecutionPlan: the resolved technical strategy for one GenerationRequest.

Internal object (never part of the public contract), persisted as an immutable
snapshot. Steps reference providers by stable name so the plan is serializable
and execution can resume after a crash.
"""

from graphlib import TopologicalSorter

from pydantic import BaseModel, Field

from content.domain.errors import ValidationIssue
from content.naming.engine import NamingPlan


class PlanStep(BaseModel):
    id: str
    operation: str  # e.g. "media.acquire_audio" — stable verb, provider-independent
    provider: str  # stable provider name from the registry
    source_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    # True when a required output (transitively) depends on this step.
    required: bool = True
    # Normalized, provider-facing parameters resolved by the planner.
    params: dict = Field(default_factory=dict)
    # Content-addressed identity of the work (operation + provider + resource +
    # params + dependency signatures) — independent of client-chosen ids.
    # Anchor of reuse_existing.
    signature: str = ""
    # Identity of the source resource this step (transitively) works on.
    resource_key: str = ""


class OutputBinding(BaseModel):
    artifact_request_id: str
    produced_by: str  # PlanStep id


class OutputDelivery(BaseModel):
    """The effective delivery decision for one output (ADR 0018), resolved by
    the planner from the request's intent and the server policy. The executor
    follows it without re-deciding."""

    output_id: str
    deliver: bool = False
    folder: str = ""


class ExecutionPlan(BaseModel):
    plan_id: str
    schema_version: str
    analysis_id: str
    steps: list[PlanStep] = Field(default_factory=list)
    output_bindings: list[OutputBinding] = Field(default_factory=list)
    # Resolved display naming per output (ADR 0017). Default keeps plans from
    # before the naming engine loadable; binding then falls back to output ids.
    naming: NamingPlan = Field(default_factory=NamingPlan)
    # Resolved delivery per output (ADR 0018). Empty for plans snapshotted
    # before the policy existed; the executor then applies the historical
    # field-presence rule.
    delivery: list[OutputDelivery] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)

    def ordered_steps(self) -> list[PlanStep]:
        """Steps in a deterministic topological order."""
        by_id = {step.id: step for step in self.steps}
        graph = {step.id: sorted(step.depends_on) for step in self.steps}
        sorter = TopologicalSorter(graph)
        return [by_id[step_id] for step_id in sorter.static_order()]

    def bindings_for_step(self, step_id: str) -> list[OutputBinding]:
        """All outputs a step produces. Mutualization can bind one step to
        several outputs; a step with no binding produces internal materials
        that are consumed by dependent steps and never promoted."""
        return [b for b in self.output_bindings if b.produced_by == step_id]

    def delivery_for(self, output_id: str) -> OutputDelivery | None:
        for entry in self.delivery:
            if entry.output_id == output_id:
                return entry
        return None
