"""The Artifact Naming Engine (ADR 0017).

Every produced Artifact gets a meaningful, user-facing ``display_filename``,
computed from semantic information the engine owns — the analyzed resource
title, the declared outputs, the provenance chain — never from implementation
details. Two phases:

- ``resolve_naming_plan`` runs at *planning* time: it fixes each output's
  display base and qualifier deterministically and the result is recorded in
  the ExecutionPlan (visible in the plan snapshot).
- ``bind_filename`` runs at *registration* time: only execution knows the final
  extension, the concrete language, and the cardinality. Binding is mechanical
  template instantiation — the executor takes no naming decisions.

Pure module: no FastAPI, no providers, no filesystem.
"""

from pydantic import BaseModel, Field

from content.domain.analysis import ResourceAnalysis
from content.domain.validation import resolve_inputs
from content.naming.sanitize import display_name, item_slug

# The output granted the unqualified base name, first type present wins; every
# other output carries its semantic qualifier. A single-output request is
# always primary regardless of type. Types absent from this list (metadata,
# thumbnail, subtitles…) are never bare in a multi-output request: their names
# should say what the file is.
PRIMARY_PRECEDENCE = (
    "video",
    "audio",
    "markdown",
    "document_text",
    "transcript",
    "summary",
    "pdf",
)

# Types that are a *presentation* of their upstream output rather than a new
# semantic thing: they inherit the qualifier of the declared output they
# derive from (a PDF of the summary is a summary; a French translation of the
# subtitles is subtitles — the language suffix and the extension carry the
# rest). Semantic transformations (summary from transcript) keep their own
# name.
INHERIT_QUALIFIER_TYPES = frozenset({"pdf", "translation"})


class OutputNaming(BaseModel):
    """The resolved naming intent for one declared output."""

    output_id: str
    # Display base name, already through the display profile. Empty means the
    # binder falls back to the output id (pre-naming plans, degraded analyses).
    base: str = ""
    # Semantic qualifier ("audio", "summary", …); empty for the primary output.
    qualifier: str = ""
    # each_item scopes: item label (as stamped in step params) -> display base
    # for that item (a playlist entry is its own resource with its own title).
    item_bases: dict[str, str] = Field(default_factory=dict)


class NamingPlan(BaseModel):
    outputs: list[OutputNaming] = Field(default_factory=list)

    def for_output(self, output_id: str) -> OutputNaming | None:
        for entry in self.outputs:
            if entry.output_id == output_id:
                return entry
        return None


def _source_filename(source) -> str:
    """The stem of a client-supplied file name, when the source has one."""
    path = getattr(source, "path", "")
    if path:
        stem = path.replace("\\", "/").rsplit("/", 1)[-1]
        return stem.rsplit(".", 1)[0] if "." in stem else stem
    return ""


def _root_output(output, outputs_by_id: dict, seen: frozenset = frozenset()):
    """Follow ``from_outputs`` to the declared output the chain derives from.
    First parent only — D3 keeps derivation chains linear in V1."""
    if output.from_outputs and output.id not in seen:
        parent = outputs_by_id.get(output.from_outputs[0])
        if parent is not None:
            return _root_output(parent, outputs_by_id, seen | {output.id})
    return output


def _base_for(output, outputs_by_id, resolved, sources_by_id, analysis) -> str:
    """Deterministic fallback chain: analyzed resource title → source filename
    → provider resource id → (empty: the binder uses the output id)."""
    root = _root_output(output, outputs_by_id)
    source_ids, _ = resolved.get(root.id, ([], []))
    if not source_ids:
        return ""
    source = sources_by_id.get(source_ids[0])
    source_analysis = analysis.for_source(source_ids[0]) if analysis else None
    resource = source_analysis.resource if source_analysis else None
    candidates = (
        resource.title if resource else "",
        _source_filename(source) if source else "",
        resource.provider_id if resource else "",
    )
    for candidate in candidates:
        cleaned = display_name(candidate)
        if cleaned:
            return cleaned
    return ""


def _qualifier_for(output, primary_id, outputs_by_id, seen: frozenset = frozenset()):
    """The primary output is bare; a presentation output (pdf, translation)
    inherits the qualifier of the declared output it derives from (a PDF of
    the summary is a summary); everything else is named for what it is."""
    if output.id == primary_id:
        return ""
    if (
        output.type in INHERIT_QUALIFIER_TYPES
        and output.from_outputs
        and output.id not in seen
    ):
        parent = outputs_by_id.get(output.from_outputs[0])
        if parent is not None:
            return _qualifier_for(parent, primary_id, outputs_by_id, seen | {output.id})
    return output.type


def _primary_output_id(outputs) -> str | None:
    if len(outputs) == 1:
        return outputs[0].id
    for output_type in PRIMARY_PRECEDENCE:
        for output in outputs:
            if output.type == output_type:
                return output.id
    return None


def suggest_base_name(resource) -> str:
    """The base name the engine would give this resource's artifacts (title,
    then provider id, through the display profile) — what a UI should offer
    the user as the editable proposal. ``""`` when nothing usable exists."""
    return display_name(resource.title) or display_name(resource.provider_id)


def resolve_naming_plan(request, analysis: ResourceAnalysis | None) -> NamingPlan:
    """Planning phase: fix every output's display base and qualifier. Pure and
    deterministic — identical request + analysis give an identical plan."""
    resolved, _ = resolve_inputs(request)
    outputs_by_id = {output.id: output for output in request.outputs}
    sources_by_id = {source.id: source for source in request.sources}
    primary_id = _primary_output_id(request.outputs)

    entries: list[OutputNaming] = []
    for output in request.outputs:
        # An explicit client filename is naming intent (ADR 0018 §3): it
        # replaces the resolved *base*, sanitized like any name. Qualifiers
        # stay — clients send one filename for the whole request, and its
        # sidecars must still say what they are.
        client_base = display_name(getattr(output.delivery, "filename", ""))
        base = client_base or _base_for(
            output, outputs_by_id, resolved, sources_by_id, analysis
        )
        qualifier = _qualifier_for(output, primary_id, outputs_by_id)

        item_bases: dict[str, str] = {}
        if output.scope == "each_item":
            source_ids, _ = resolved.get(output.id, ([], []))
            source_analysis = (
                analysis.for_source(source_ids[0]) if analysis and source_ids else None
            )
            if source_analysis is not None:
                # Mirrors _plan_each_item exactly: same enumeration, same
                # skip rule, same slug — so labels always match step params.
                for position, entry in enumerate(source_analysis.entries, start=1):
                    if not entry.url:
                        continue
                    label = item_slug(entry.title or entry.id, position)
                    title = display_name(entry.title)
                    if title:
                        item_bases[label] = title

        entries.append(
            OutputNaming(
                output_id=output.id,
                base=base,
                qualifier=qualifier,
                item_bases=item_bases,
            )
        )

    # Residual collisions: two declared outputs of the same type over the same
    # base would be indistinguishable (or worse, tautological — a second video
    # labeled "video"). The first declared keeps its resolved qualifier; the
    # others are qualified by their own output id — the name the user themself
    # chose for the duplicate.
    groups: dict[tuple, list[OutputNaming]] = {}
    for entry in entries:
        output_type = outputs_by_id[entry.output_id].type
        groups.setdefault((entry.base, output_type), []).append(entry)
    for group in groups.values():
        for entry in group[1:]:
            entry.qualifier = entry.output_id

    return NamingPlan(outputs=entries)


def bind_filename(
    naming: OutputNaming | None,
    *,
    output_id: str,
    extension: str,
    language: str = "",
    item_label: str = "",
    item_index: int = 1,
    item_count: int = 1,
) -> str:
    """Registration phase: instantiate the naming template with what execution
    learned — extension, language, cardinality. ``naming=None`` (a plan from
    before ADR 0017) degrades to the output id, the previous behaviour."""
    base = (naming.base if naming else "") or output_id
    qualifier = naming.qualifier if naming else ""
    if item_label:
        item_base = naming.item_bases.get(item_label, "") if naming else ""
        base = item_base or f"{base} - {item_label}"
    parts = [base]
    if qualifier:
        parts.append(qualifier)
    if language:
        parts.append(language)
    if item_count > 1 and not language:
        # Numbering only when cardinality is the sole distinguisher (several
        # keyframes); language-addressed siblings are already distinct.
        parts.append(f"{item_index:02d}")
    stem = display_name(" - ".join(parts)) or "artifact"
    return f"{stem}{extension}"
