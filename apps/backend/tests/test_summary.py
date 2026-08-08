"""Summary output: prompt building, runner selection (installation
capabilities, privacy constraint, provider preferences), chain synthesis and
mutualization, end-to-end execution with the fake LLM."""

import pytest

from content.analysis.service import AnalysisService
from content.application.submit import submit_generation
from content.domain.errors import RequestRejected
from content.execution.executor import JobExecutor
from content.planning.planner import build_plan
from content.processors.summarize import build_summary_prompt, strip_thinking
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ProviderRegistry
from tests.conftest import (
    FakeCloudSummarizer,
    FakeProvider,
    FakeSummarizer,
    make_request,
    minimal_payload,
)


def summary_payload(output_extra=None, **overrides):
    output = {"id": "summary", "type": "summary", **(output_extra or {})}
    return minimal_payload(outputs=[output], **overrides)


def registry_with(*processors):
    return ProviderRegistry([FakeProvider()], processors=list(processors))


def plan_with(payload, registry, store, settings):
    service = AnalysisService(store, registry, settings)
    request = make_request(payload)
    analysis = service.analyze_sources(list(request.sources))
    return build_plan(request, analysis, registry, settings)


# --- prompt (pure) --------------------------------------------------------------


def test_prompt_carries_options():
    prompt = build_summary_prompt(
        text="Hello",
        language="fr",
        length="short",
        style="bullet_points",
        output_format="text",
    )
    assert "language 'fr'" in prompt
    assert "3 to 5 sentences" in prompt
    assert "bullet points" in prompt
    assert "plain text" in prompt
    assert '"""\nHello\n"""' in prompt


def test_strip_thinking_removes_reasoning_blocks():
    raw = "<think>chain of thought</think>\n# Summary\nDone."
    assert strip_thinking(raw) == "# Summary\nDone."


# --- planning -------------------------------------------------------------------


def test_summary_synthesizes_full_chain(providers, store, settings):
    plan = plan_with(summary_payload(), providers, store, settings)
    operations = [step.operation for step in plan.ordered_steps()]
    assert operations == [
        "media.acquire_subtitles",
        "subtitles.to_transcript",
        "text.summarize",
    ]
    summary_step = plan.steps[-1]
    assert summary_step.provider == "fake-llm"
    assert summary_step.params["model"] == "fake-model"
    # only the summary output is bound; the chain is internal
    assert [b.artifact_request_id for b in plan.output_bindings] == ["summary"]
    # requiredness propagated down the whole chain
    assert all(step.required for step in plan.steps)


def test_summary_reuses_bound_transcript_step(providers, store, settings):
    payload = minimal_payload(
        outputs=[
            {"id": "transcript", "type": "transcript"},
            {"id": "summary", "type": "summary", "from_outputs": ["transcript"]},
        ]
    )
    plan = plan_with(payload, providers, store, settings)
    assert len(plan.steps) == 3  # acquisition + transcript + summary, no dupes
    transcript_step = next(
        s for s in plan.steps if s.operation == "subtitles.to_transcript"
    )
    summary_step = next(s for s in plan.steps if s.operation == "text.summarize")
    assert summary_step.depends_on == [transcript_step.id]


def test_implicit_summary_and_transcript_mutualize_when_identical(
    providers, store, settings
):
    # transcript output in canonical json + implicit summary chain: the hidden
    # transcript need is identical to the bound one -> single step, two roles.
    payload = minimal_payload(
        outputs=[
            {"id": "transcript", "type": "transcript", "options": {"format": "json"}},
            {"id": "summary", "type": "summary"},
        ]
    )
    plan = plan_with(payload, providers, store, settings)
    transcript_steps = [
        s for s in plan.steps if s.operation == "subtitles.to_transcript"
    ]
    assert len(transcript_steps) == 1
    assert len(plan.bindings_for_step(transcript_steps[0].id)) == 1  # bound once


def test_no_summarizer_installed(store, settings):
    # No text.summarize runner → the shared feasibility gate rejects the summary
    # (the structured reason lives on the resolver; see the capability tests).
    registry = registry_with(TranscriptProcessor())
    with pytest.raises(RequestRejected) as excinfo:
        plan_with(summary_payload(), registry, store, settings)
    assert excinfo.value.result.errors[0].code == "capability_unavailable"


def test_summarizer_installed_but_down(store, settings):
    registry = registry_with(TranscriptProcessor(), FakeSummarizer(is_available=False))
    with pytest.raises(RequestRejected) as excinfo:
        plan_with(summary_payload(), registry, store, settings)
    assert excinfo.value.result.errors[0].code == "capability_unavailable"


def test_privacy_constraint_excludes_cloud_summarizer(store, settings):
    registry = registry_with(TranscriptProcessor(), FakeCloudSummarizer())
    payload = summary_payload(constraints={"privacy": {"allow_cloud_providers": False}})
    with pytest.raises(RequestRejected) as excinfo:
        plan_with(payload, registry, store, settings)
    assert excinfo.value.result.errors[0].code == "constraint_unsatisfiable"

    # allowed -> planned on the cloud runner
    plan = plan_with(
        summary_payload(constraints={"privacy": {"allow_cloud_providers": True}}),
        registry,
        store,
        settings,
    )
    assert plan.steps[-1].provider == "fake-cloud-llm"


def test_llm_preference_order_is_honored(store, settings):
    registry = registry_with(
        TranscriptProcessor(), FakeSummarizer(), FakeCloudSummarizer()
    )
    payload = summary_payload(preferences={"providers": {"llm": ["fake-cloud-llm"]}})
    plan = plan_with(payload, registry, store, settings)
    summary_step = next(s for s in plan.steps if s.operation == "text.summarize")
    assert summary_step.provider == "fake-cloud-llm"
    assert plan.warnings == []


def test_fallback_when_preferred_llm_down_warns(store, settings):
    registry = registry_with(
        TranscriptProcessor(),
        FakeSummarizer(),
        # preferred but down:
        type("DownLlm", (FakeSummarizer,), {"name": "down-llm"})(is_available=False),
    )
    payload = summary_payload(preferences={"providers": {"llm": ["down-llm"]}})
    plan = plan_with(payload, registry, store, settings)
    summary_step = next(s for s in plan.steps if s.operation == "text.summarize")
    assert summary_step.provider == "fake-llm"
    assert "preferred_provider_unavailable" in [w.code for w in plan.warnings]


def test_summary_from_non_transcript_output_is_invalid(providers, store, settings):
    payload = minimal_payload(
        outputs=[
            {"id": "audio", "type": "audio"},
            {"id": "summary", "type": "summary", "from_outputs": ["audio"]},
        ]
    )
    with pytest.raises(RequestRejected) as excinfo:
        plan_with(payload, providers, store, settings)
    assert excinfo.value.result.errors[0].code == "invalid_option"


def _resolve_summary(registry, store, settings):
    from content.capabilities.facts import facts_from_analysis
    from content.capabilities.policy import EffectivePolicy
    from content.capabilities.resolver import CapabilityResolver
    from content.planning.transformations import build_registry

    service = AnalysisService(store, registry, settings)
    analysis = service.analyze_sources(list(make_request(minimal_payload()).sources))
    facts = facts_from_analysis(analysis.sources[0])
    resolver = CapabilityResolver(build_registry(registry), registry)
    caps = {c.id: c for c in resolver.resolve(facts, EffectivePolicy())}
    return caps["summary.generate"]


def test_resolver_exposes_summary_capability(providers, store, settings):
    # Summary is resolved (not provider-emitted, ADR 0013): derivable, and it
    # advertises what it comes from.
    summary = _resolve_summary(providers, store, settings)
    assert summary.status == "derivable"
    assert summary.derived_from == ["subtitles"]


def test_resolver_summary_unavailable_without_runner(store, settings):
    summary = _resolve_summary(registry_with(TranscriptProcessor()), store, settings)
    assert summary.status == "unavailable"
    assert summary.reason.code == "implementation_unavailable"
    assert "text.summarize" in summary.reason.missing_operations


# --- execution ------------------------------------------------------------------


def test_summary_job_end_to_end(providers, store, settings):
    service = AnalysisService(store, providers, settings)
    payload = summary_payload()
    request = make_request(payload)
    result = submit_generation(
        payload,
        request,
        store=store,
        settings=settings,
        providers=providers,
        analysis_service=service,
    )
    claimed = store.claim_next_queued()
    JobExecutor(store, settings, providers).execute(claimed)

    assert store.get_job(result.job_id)["status"] == "succeeded"
    artifacts = store.list_artifacts(result.job_id)
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact["type"] == "summary"
    assert artifact["filename"] == "summary.md"
    assert artifact["media_type"] == "text/markdown"
    assert artifact["provenance"]["producer"]["provider"] == "fake-llm"
    assert artifact["provenance"]["attributes"]["model"] == "fake-model"

    path = (
        settings.data_dir / "jobs" / result.job_id / "artifacts" / artifact["filename"]
    )
    assert path.read_text().startswith("# Summary")

    steps = {s["step_id"]: s["status"] for s in store.list_steps(result.job_id)}
    assert len(steps) == 3 and set(steps.values()) == {"succeeded"}
