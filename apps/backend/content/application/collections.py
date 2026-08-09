"""Collection orchestration — ADR 0019.

A collection never generates artifacts. It orchestrates the canonical
single-resource pipeline for each of its members: a member is analyzed,
resolved and planned exactly like a video submitted on its own, and only then
executed. Nothing about a member is guessed.

Two responsibilities live here, and neither of them is planning:

* :func:`derive_member_request` — a pure rewrite of the collection's request
  into the request that member would have been submitted as;
* :class:`CollectionMemberRunner` — a ``StepRunner`` that *sequences* the
  canonical services for one member (analyze → ``build_plan`` → execute) and
  hands the produced files back.

The runner is a step runner on purpose. The executor keeps doing exactly what
it always did — run a resolved step through a runner and register what comes
back — so it acquires no planning responsibility, and every downstream
concern (naming, delivery, provenance, reuse) keeps working unchanged.
"""

from __future__ import annotations

from pathlib import Path

from content.analysis.service import AnalysisService
from content.config import ContentSettings
from content.domain.errors import RequestRejected
from content.domain.plan import ExecutionPlan
from content.domain.request import GenerationRequest
from content.providers.base import (
    ExecutionContext,
    Material,
    ProducedFile,
    ProviderRegistry,
    StepExecutionError,
)

MEMBER_OPERATION = "collection.member"
RUNNER_NAME = "content.collection"


def derive_member_request(request: GenerationRequest, params: dict) -> dict:
    """The request this member would have been submitted as, on its own.

    A pure rewrite, not a plan: the collection source becomes an ordinary
    ``url`` source pointing at the member (carrying the collection's ``auth``,
    since a playlist's credential is the member's credential), the
    ``each_item`` scope collapses to ``single``, and everything else — options,
    delivery intent, execution preferences — is passed through untouched. That
    is what makes a member behave exactly like the same video submitted alone.

    Returns the raw payload; the caller validates it into a
    ``GenerationRequest`` so the member goes through the same model the public
    API uses.
    """
    member_uri = params["member_uri"]
    output_id = params["member_output_id"]
    source_id = params["member_source_id"]

    original = request.model_dump(mode="json", exclude_none=True)
    source = next(
        (s for s in original.get("sources", []) if s.get("id") == source_id), None
    )
    member_source: dict = {"id": source_id, "type": "url", "uri": member_uri}
    if source and source.get("auth"):
        member_source["auth"] = source["auth"]

    outputs = []
    for output in original.get("outputs", []):
        if output.get("id") != output_id:
            continue
        member_output = dict(output)
        # The scope was the instruction to fan out; for the member itself the
        # work is an ordinary single-resource output.
        member_output.pop("scope", None)
        outputs.append(member_output)

    return {
        "schema_version": original.get("schema_version", "1.0"),
        "sources": [member_source],
        "outputs": outputs,
    }


class CollectionMemberRunner:
    """Runs one collection member through the canonical pipeline.

    Implements ``StepRunner``, so the executor dispatches to it exactly as it
    dispatches to yt-dlp or a processor. Inside, it only *sequences* services
    that already exist — it re-implements none of them.
    """

    name = RUNNER_NAME
    operations = (MEMBER_OPERATION,)
    location = "local"
    tool_version = "collection/1"

    def __init__(
        self,
        analysis_service: AnalysisService,
        providers: ProviderRegistry,
        settings: ContentSettings,
    ):
        self._analysis = analysis_service
        self._providers = providers
        self._settings = settings

    def execute(self, step, ctx: ExecutionContext) -> list[ProducedFile]:
        # Imported here: the planner imports this module for MEMBER_OPERATION,
        # and build_plan is what the planner exports.
        from content.planning.planner import build_plan

        params = step.params
        request = GenerationRequest.model_validate(params["member_request"])

        # 1. Canonical analysis of the concrete member — cached per resource,
        #    so re-running a collection re-reads instead of re-probing.
        try:
            analysis = self._analysis.analyze_sources(list(request.sources))
        except RequestRejected as exc:
            raise StepExecutionError(
                "member_analysis_failed",
                f"the member could not be analyzed: {_first_message(exc)}",
                details={"member_uri": params.get("member_uri", "")},
            ) from exc

        # 2. Canonical capability resolution + planning. A member that cannot
        #    satisfy the requested output is refused here, with the ordinary
        #    structured reason — never worked around.
        try:
            member_plan = build_plan(request, analysis, self._providers, self._settings)
        except RequestRejected as exc:
            raise StepExecutionError(
                "member_not_feasible",
                f"the member cannot produce this output: {_first_message(exc)}",
                details={
                    "member_uri": params.get("member_uri", ""),
                    "errors": [issue.model_dump() for issue in exc.result.errors],
                },
            ) from exc

        # 3. Canonical execution of the member's own steps — in a workdir of
        #    the member's own. Members of one collection produce identically
        #    named files (the member plan's step ids repeat per member), so a
        #    shared directory only works while members run strictly one after
        #    another; the outer step id is unique per member and stable across
        #    retries, which makes it the isolation key that keeps concurrent
        #    members from overwriting each other mid-flight.
        member_workdir = ctx.workdir / step.id
        member_workdir.mkdir(parents=True, exist_ok=True)
        produced = self._run_member_plan(member_plan, ctx, member_workdir)
        if not produced:
            raise StepExecutionError(
                "member_produced_nothing",
                "the member plan produced no file",
                details={"member_uri": params.get("member_uri", "")},
            )

        # Provenance the collection owes its artifacts: which concrete resource
        # this came from, which collection it belongs to, and where in it —
        # so an artifact is attributable without parsing its filename.
        member_resource_key = _resource_key_of(member_plan)
        for item in produced:
            item.attributes.setdefault("member_uri", params.get("member_uri", ""))
            item.attributes.setdefault("member_index", params.get("member_index"))
            item.attributes.setdefault(
                "collection_source_id", params.get("member_source_id", "")
            )
            if member_resource_key:
                item.attributes.setdefault("member_resource_key", member_resource_key)
        return produced

    def _run_member_plan(
        self, plan: ExecutionPlan, ctx: ExecutionContext, workdir: Path
    ) -> list[ProducedFile]:
        """Execute the member's plan in dependency order and return the files
        its *bound* steps produced.

        Deliberately narrow: no artifact registration, no delivery, no events.
        Those belong to the job, and the outer executor already does them for
        whatever this returns. Internal steps feed dependents as materials,
        exactly as they do in a single-resource job.
        """
        materials: dict[str, list[Material]] = {}
        bound: list[ProducedFile] = []
        for member_step in plan.ordered_steps():
            try:
                runner = self._providers.get(member_step.provider)
            except KeyError as exc:
                raise StepExecutionError(
                    "member_runner_missing",
                    f"no runner named '{member_step.provider}' for the member step",
                ) from exc
            inputs = [
                material
                for dep in member_step.depends_on
                for material in materials.get(dep, [])
            ]
            produced = runner.execute(
                member_step,
                ExecutionContext(
                    settings=ctx.settings,
                    workdir=workdir,
                    stdout_log=ctx.stdout_log,
                    stderr_log=ctx.stderr_log,
                    timeout_seconds=ctx.timeout_seconds,
                    input_materials=inputs,
                    cancel_check=ctx.cancel_check,
                    on_progress=ctx.on_progress,
                ),
            )
            materials[member_step.id] = [
                Material(path=Path(item.path), media_type=item.media_type)
                for item in produced
            ]
            if plan.bindings_for_step(member_step.id):
                bound.extend(produced)
        return bound


def _resource_key_of(plan: ExecutionPlan) -> str:
    """The member's resource identity, from the first step that carries one."""
    for step in plan.steps:
        if step.resource_key:
            return step.resource_key
    return ""


def _first_message(exc: RequestRejected) -> str:
    errors = getattr(exc.result, "errors", []) or []
    return errors[0].message if errors else "no reason reported"


def attach_collection_runner(
    providers: ProviderRegistry,
    analysis_service: AnalysisService,
    settings: ContentSettings,
) -> ProviderRegistry:
    """Register the orchestrator on a registry, once its collaborators exist.

    One call site for the API, the tests and any other assembly, so a
    collection behaves identically wherever the engine is wired up. Idempotent:
    re-attaching is a no-op rather than a duplicate-name error.
    """
    try:
        providers.get(RUNNER_NAME)
    except KeyError:
        providers.register(
            CollectionMemberRunner(analysis_service, providers, settings)
        )
    return providers
