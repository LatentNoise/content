"""Structural validation beyond what Pydantic enforces field by field.

Deterministic rules only (docs/contract.md §3 D3 and §6 phase 1): id
uniqueness, reference resolution, cycle detection, input-resolution rules.
Feasibility (phase 2) lives in content.planning.
"""

from graphlib import CycleError, TopologicalSorter

from content.domain import errors as codes
from content.domain.errors import ValidationIssue, ValidationResult
from content.domain.request import (
    SUPPORTED_SCHEMA_MAJOR,
    GenerationRequest,
)
from content.domain.reserved import check_reserved

# V1 media output types consume exactly one source and no upstream outputs.
_SINGLE_SOURCE_TYPES = ("video", "audio", "metadata", "thumbnail", "subtitles")


class ResolvedInputs(dict):
    """output id -> (source_ids, output_ids) once resolution rules applied."""


def resolve_inputs(
    request: GenerationRequest,
) -> tuple[ResolvedInputs, list[ValidationIssue]]:
    """Apply the deterministic input-resolution rules (D3).

    When an output declares neither from_sources nor from_outputs, it consumes
    the request's single source, or the unique ``role: primary`` source.
    Anything else is an explicit error — no further inference.
    """
    issues: list[ValidationIssue] = []
    resolved = ResolvedInputs()
    primaries = [s.id for s in request.sources if s.role == "primary"]

    for index, output in enumerate(request.outputs):
        source_ids = list(output.from_sources)
        output_ids = list(output.from_outputs)
        if not source_ids and not output_ids:
            if len(request.sources) == 1:
                source_ids = [request.sources[0].id]
            elif len(primaries) == 1:
                source_ids = primaries.copy()
            else:
                issues.append(
                    ValidationIssue(
                        code=codes.AMBIGUOUS_INPUTS,
                        path=f"outputs[{index}]",
                        message=(
                            f"Output '{output.id}' declares no inputs and the request "
                            "has neither a single source nor a unique primary source; "
                            "set from_sources or from_outputs explicitly."
                        ),
                    )
                )
        resolved[output.id] = (source_ids, output_ids)
    return resolved, issues


def validate_structure(request: GenerationRequest) -> ValidationResult:
    issues: list[ValidationIssue] = []

    major = int(request.schema_version.split(".", 1)[0])
    if major != SUPPORTED_SCHEMA_MAJOR:
        issues.append(
            ValidationIssue(
                code=codes.UNSUPPORTED_SCHEMA_VERSION,
                path="schema_version",
                message=f"Schema major version {major} is not supported.",
                details={"supported_major": SUPPORTED_SCHEMA_MAJOR},
            )
        )

    source_ids = [s.id for s in request.sources]
    output_ids = [o.id for o in request.outputs]
    for kind, ids in (("sources", source_ids), ("outputs", output_ids)):
        seen: set[str] = set()
        for index, local_id in enumerate(ids):
            if local_id in seen:
                issues.append(
                    ValidationIssue(
                        code=codes.DUPLICATE_ID,
                        path=f"{kind}[{index}].id",
                        message=f"Duplicate id '{local_id}' in {kind}.",
                        details={"id": local_id},
                    )
                )
            seen.add(local_id)

    known_sources = set(source_ids)
    known_outputs = set(output_ids)
    for index, output in enumerate(request.outputs):
        for ref_index, ref in enumerate(output.from_sources):
            if ref not in known_sources:
                issues.append(
                    ValidationIssue(
                        code=codes.UNKNOWN_SOURCE_REFERENCE,
                        path=f"outputs[{index}].from_sources[{ref_index}]",
                        message=f"Source '{ref}' does not exist.",
                        details={"source_id": ref},
                    )
                )
        for ref_index, ref in enumerate(output.from_outputs):
            if ref not in known_outputs:
                issues.append(
                    ValidationIssue(
                        code=codes.UNKNOWN_OUTPUT_REFERENCE,
                        path=f"outputs[{index}].from_outputs[{ref_index}]",
                        message=f"Output '{ref}' does not exist.",
                        details={"output_id": ref},
                    )
                )
            elif ref == output.id:
                issues.append(
                    ValidationIssue(
                        code=codes.DEPENDENCY_CYCLE,
                        path=f"outputs[{index}].from_outputs[{ref_index}]",
                        message=f"Output '{output.id}' depends on itself.",
                    )
                )

    graph = {
        o.id: [ref for ref in o.from_outputs if ref in known_outputs and ref != o.id]
        for o in request.outputs
    }
    try:
        TopologicalSorter(graph).prepare()
    except CycleError as exc:
        cycle = [str(node) for node in exc.args[1]]
        issues.append(
            ValidationIssue(
                code=codes.DEPENDENCY_CYCLE,
                path="outputs",
                message=f"Outputs form a dependency cycle: {' -> '.join(cycle)}.",
                details={"cycle": cycle},
            )
        )

    resolved, resolution_issues = resolve_inputs(request)
    issues.extend(resolution_issues)

    for index, output in enumerate(request.outputs):
        if output.type in ("transcript", "summary", "translation", "chapters"):
            # These derive from exactly one input: a source, or one upstream
            # output (transcript <- subtitles/audio; summary <- transcript;
            # translation <- subtitles/transcript). Multi-input aggregation
            # belongs to scope all_sources, not implemented.
            source_ids_for_output, output_ids_for_output = resolved.get(
                output.id, ([], [])
            )
            if len(source_ids_for_output) + len(output_ids_for_output) > 1:
                issues.append(
                    ValidationIssue(
                        code=codes.TOO_MANY_INPUTS,
                        path=f"outputs[{index}]",
                        message=(
                            f"A {output.type} consumes exactly one input (one "
                            "source or one upstream output)."
                        ),
                    )
                )
            continue
        if output.type not in _SINGLE_SOURCE_TYPES:
            continue
        source_ids_for_output, output_ids_for_output = resolved.get(output.id, ([], []))
        if output_ids_for_output:
            issues.append(
                ValidationIssue(
                    code=codes.TOO_MANY_INPUTS,
                    path=f"outputs[{index}].from_outputs",
                    message=(
                        f"Output type '{output.type}' consumes a source directly; "
                        "from_outputs is not applicable."
                    ),
                )
            )
        if len(source_ids_for_output) > 1:
            issues.append(
                ValidationIssue(
                    code=codes.TOO_MANY_INPUTS,
                    path=f"outputs[{index}].from_sources",
                    message=(
                        f"Output type '{output.type}' with scope 'single' consumes "
                        "exactly one source."
                    ),
                )
            )

    # Accepted-but-unimplemented fields (content/domain/reserved.py). Refused
    # here rather than ignored: a field that does nothing teaches clients to
    # send it, and a restriction that is ignored is a broken guarantee (D-01).
    refusals, _ = check_reserved(request)
    issues.extend(refusals)

    if issues:
        return ValidationResult.failure(issues)
    return ValidationResult.success()
