"""ResolvedCapability — the resolver's public output (ADR 0013).

What a client renders: a public capability with its computed status, the concrete
variant selected for it, what it is derived from, and (when not available) a
machine-stable reason. Option domains are attached in a later slice, once the
analysis surfaces the needed resource facts.
"""

from pydantic import BaseModel, Field

from content.domain.analysis import CapabilityStatus


class CapabilityReason(BaseModel):
    """Why a capability is not available — structured so a client can tell an
    incompatible source from a feature this installation lacks (ADR 0013).

    Codes:
    - ``missing_material``: the source itself lacks a needed material
      (``missing_materials``) — the source is incompatible.
    - ``implementation_unavailable``: the source could support it, but an
      operation has no active runner (``missing_operations``) — install it.
    - ``policy_restricted``: an operation is blocked by the effective policy
      (``blocked_operations``) — e.g. cloud disabled.
    """

    code: str
    missing_materials: list[str] = Field(default_factory=list)
    missing_operations: list[str] = Field(default_factory=list)
    blocked_operations: list[str] = Field(default_factory=list)


class ResolvedCapability(BaseModel):
    id: str
    title: str
    description: str
    output_type: str
    status: CapabilityStatus
    # The concrete variant chosen for this capability (R3: the planner builds
    # exactly this one). None when no variant is feasible.
    selected_variant: str | None = None
    # For derivable capabilities, the source material(s) it is built from
    # (e.g. ["subtitles"] for summary.from_subtitles).
    derived_from: list[str] = Field(default_factory=list)
    # Structured reason when status is not available/derivable.
    reason: CapabilityReason | None = None
