"""CapabilityResolver — computes what a source can produce (ADR 0013).

Pure and deterministic. For each capability in the catalog it evaluates its
variants **in order** and keeps the first concretely feasible one (R2): a variant
is feasible when the source supplies its required materials AND every operation
has at least one available implementation allowed by the effective policy. The
same ``select_variant`` the resolver uses is what the planner must call, so a
capability announced available is built by the exact variant that was checked
(R3 — no divergence).

Nothing here is hardcoded per source type: feasibility is derived from
``SourceFacts`` × the transformation registry × the installed implementations ×
the effective policy.
"""

from dataclasses import dataclass

from content.capabilities.catalog import (
    CapabilityDef,
    RecipeVariant,
    all_capabilities,
)
from content.capabilities.facts import SourceFacts
from content.capabilities.policy import EffectivePolicy
from content.domain.capability import CapabilityReason, ResolvedCapability
from content.planning import transformations as T
from content.planning.transformations import TransformationRegistry
from content.providers.base import ProviderRegistry

# Material kind → the output type a source of that material corresponds to.
_MATERIAL_OUTPUT = {
    T.VIDEO: "video",
    T.AUDIO: "audio",
    T.SUBTITLES: "subtitles",
    T.IMAGE: "thumbnail",
}


@dataclass(frozen=True)
class VariantVerdict:
    variant: RecipeVariant
    missing_materials: tuple[str, ...] = ()  # the source definitely lacks these
    unknown_materials: tuple[str, ...] = ()  # facts insufficient to decide
    missing_operations: tuple[str, ...] = ()  # no active implementation
    blocked_operations: tuple[str, ...] = ()  # blocked by the effective policy

    @property
    def feasible(self) -> bool:
        return not (
            self.missing_materials
            or self.unknown_materials
            or self.missing_operations
            or self.blocked_operations
        )

    @property
    def inconclusive(self) -> bool:
        # Operations are runnable and no material is definitely absent, but a
        # material's presence could not be decided → attemptable ('unknown').
        return bool(self.unknown_materials) and not (
            self.missing_materials or self.missing_operations or self.blocked_operations
        )

    @property
    def materials_present(self) -> bool:
        return not (self.missing_materials or self.unknown_materials)


def _material_name(kind: str) -> str:
    return _MATERIAL_OUTPUT.get(kind, kind)


def _dedup(items) -> list[str]:
    out: list[str] = []
    for item in items:
        if item not in out:
            out.append(item)
    return out


def _evaluate_variant(
    variant: RecipeVariant,
    facts: SourceFacts,
    registry: TransformationRegistry,
    providers: ProviderRegistry,
    policy: EffectivePolicy,
) -> VariantVerdict:
    missing: list[str] = []
    unknown: list[str] = []
    for kind in variant.requires_materials:
        state = facts.material_state(kind)
        if state == "absent":
            missing.append(_material_name(kind))
        elif state == "unknown":
            unknown.append(_material_name(kind))
    if missing:
        # A definitely-absent material is the precondition — report it alone.
        return VariantVerdict(variant, missing_materials=tuple(missing))
    missing_ops: list[str] = []
    blocked_ops: list[str] = []
    for op in variant.operations:
        runners = providers.available_runners_for_operation(op)
        if registry.definition(op) is None or not runners:
            missing_ops.append(op)
        elif not any(policy.allows_runner(r) for r in runners):
            blocked_ops.append(op)
    return VariantVerdict(
        variant,
        unknown_materials=tuple(unknown),
        missing_operations=tuple(missing_ops),
        blocked_operations=tuple(blocked_ops),
    )


def _status_for(cap: CapabilityDef, variant: RecipeVariant) -> str:
    """available when the output is the source material itself (download / same
    kind), derivable when it is produced from a different material."""
    if not variant.requires_materials:
        return "available"
    source_types = {_material_name(k) for k in variant.requires_materials}
    return "available" if cap.output_type in source_types else "derivable"


def classify_capability(
    cap: CapabilityDef,
    facts: SourceFacts,
    registry: TransformationRegistry,
    providers: ProviderRegistry,
    policy: EffectivePolicy,
) -> tuple[str, RecipeVariant | None, CapabilityReason | None]:
    """The single deterministic judgement shared by the public resolver and the
    planner (R3): (status, selected_variant, reason). Precedence — a feasible
    variant wins; else an inconclusive one yields 'unknown' (attemptable); else
    the most actionable blocker: policy → 'restricted', missing runner →
    'implementation_unavailable', absent material → 'missing_material'."""
    verdicts = [
        _evaluate_variant(v, facts, registry, providers, policy) for v in cap.variants
    ]
    feasible = next((v for v in verdicts if v.feasible), None)
    if feasible is not None:
        return _status_for(cap, feasible.variant), feasible.variant, None
    inconclusive = next((v for v in verdicts if v.inconclusive), None)
    if inconclusive is not None:
        return "unknown", inconclusive.variant, None

    policy_only = [v for v in verdicts if v.materials_present and v.blocked_operations]
    impl_reachable = [
        v for v in verdicts if v.materials_present and v.missing_operations
    ]
    if policy_only:
        return (
            "restricted",
            None,
            CapabilityReason(
                code="policy_restricted",
                blocked_operations=_dedup(
                    op for v in policy_only for op in v.blocked_operations
                ),
            ),
        )
    if impl_reachable:
        return (
            "unavailable",
            None,
            CapabilityReason(
                code="implementation_unavailable",
                missing_operations=_dedup(
                    op for v in impl_reachable for op in v.missing_operations
                ),
            ),
        )
    return (
        "unavailable",
        None,
        CapabilityReason(
            code="missing_material",
            missing_materials=_dedup(
                mat for v in verdicts for mat in v.missing_materials
            ),
        ),
    )


def select_variant(
    cap: CapabilityDef,
    facts: SourceFacts,
    registry: TransformationRegistry,
    providers: ProviderRegistry,
    policy: EffectivePolicy,
) -> RecipeVariant | None:
    """The concrete variant to build (R3): the feasible one, or the inconclusive
    one to attempt; None when the capability is blocked."""
    return classify_capability(cap, facts, registry, providers, policy)[1]


def _derivation_for(
    variant: RecipeVariant, registry: TransformationRegistry
) -> list[str]:
    """The material chain the variant walks, source first, artifact last —
    read from the registry's own ``output_kinds`` declarations (R1: one
    declaration), so ``pdf.render.via_summary`` answers
    ["subtitles", "transcript", "summary", "pdf"] without any per-capability
    hand-writing. Consecutive duplicates collapse (an acquisition *of* the
    required material adds nothing to the story)."""
    chain = [_material_name(kind) for kind in variant.requires_materials]
    for op in variant.operations:
        definition = registry.definition(op)
        if definition is None:
            continue
        for kind in definition.output_kinds:
            name = _material_name(kind)
            if not chain or chain[-1] != name:
                chain.append(name)
    return chain


def _resolve_one(
    cap: CapabilityDef,
    facts: SourceFacts,
    registry: TransformationRegistry,
    providers: ProviderRegistry,
    policy: EffectivePolicy,
) -> ResolvedCapability:
    status, variant, reason = classify_capability(
        cap, facts, registry, providers, policy
    )
    derived_from = (
        [_material_name(k) for k in variant.requires_materials]
        if variant is not None and status == "derivable"
        else []
    )
    return ResolvedCapability(
        id=cap.id,
        title=cap.title,
        description=cap.description,
        output_type=cap.output_type,
        status=status,
        selected_variant=variant.id if variant is not None else None,
        derived_from=derived_from,
        derivation=_derivation_for(variant, registry) if variant is not None else [],
        reason=reason,
    )


class CapabilityResolver:
    """Resolves the whole catalog against one analyzed source."""

    def __init__(self, registry: TransformationRegistry, providers: ProviderRegistry):
        self._registry = registry
        self._providers = providers

    def resolve(
        self, facts: SourceFacts, policy: EffectivePolicy
    ) -> list[ResolvedCapability]:
        return [
            _resolve_one(cap, facts, self._registry, self._providers, policy)
            for cap in all_capabilities()
        ]
