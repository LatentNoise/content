"""Feasibility validation (phase 2) and plan construction."""

import pytest

from content.analysis.service import AnalysisService
from content.domain.errors import RequestRejected
from content.planning.planner import build_plan
from tests.conftest import make_request, minimal_payload


@pytest.fixture
def analyze(store, providers, settings):
    service = AnalysisService(store, providers, settings)
    return lambda request: service.analyze_sources(list(request.sources))


def plan_for(payload, analyze, providers, settings):
    request = make_request(payload)
    return request, build_plan(request, analyze(request), providers, settings)


def rejected_codes(payload, analyze, providers, settings):
    request = make_request(payload)
    with pytest.raises(RequestRejected) as excinfo:
        build_plan(request, analyze(request), providers, settings)
    assert excinfo.value.result.phase == "feasibility"
    return [issue.code for issue in excinfo.value.result.errors]


def test_audio_plan_is_deterministic(analyze, providers, settings):
    _, plan_a = plan_for(minimal_payload(), analyze, providers, settings)
    _, plan_b = plan_for(minimal_payload(), analyze, providers, settings)
    assert [s.id for s in plan_a.steps] == ["acquire_audio_audio_main"]
    assert [s.model_dump(exclude={"id"}) for s in plan_a.steps] == [
        s.model_dump(exclude={"id"}) for s in plan_b.steps
    ]
    assert plan_a.output_bindings[0].artifact_request_id == "audio_main"
    assert plan_a.output_bindings[0].produced_by == "acquire_audio_audio_main"


def test_one_step_per_output_single_analysis(analyze, providers, settings):
    payload = minimal_payload(
        outputs=[
            {"id": "audio", "type": "audio"},
            {"id": "meta", "type": "metadata"},
            {"id": "thumb", "type": "thumbnail", "required": False},
        ]
    )
    _, plan = plan_for(payload, analyze, providers, settings)
    assert len(plan.steps) == 3
    assert {b.artifact_request_id for b in plan.output_bindings} == {
        "audio",
        "meta",
        "thumb",
    }


def test_reserved_output_type_rejected_at_feasibility(analyze, providers, settings):
    # `ocr` stands in for "declared but not implemented". keyframes used to
    # play this role and became executable in prompt 11 — the reserved list is
    # meant to shrink, so this fixture names a type that is still on it.
    payload = minimal_payload(outputs=[{"id": "k", "type": "ocr"}])
    assert rejected_codes(payload, analyze, providers, settings) == [
        "output_type_not_supported"
    ]


def test_valid_but_unsupported_scope(analyze, providers, settings):
    payload = minimal_payload(
        outputs=[{"id": "audio", "type": "audio", "scope": "each_source"}]
    )
    assert rejected_codes(payload, analyze, providers, settings) == [
        "scope_not_supported"
    ]


def test_audio_format_is_planned_for_url_sources(analyze, providers, settings):
    payload = minimal_payload(
        outputs=[{"id": "audio", "type": "audio", "options": {"format": "opus"}}]
    )
    _, plan = plan_for(payload, analyze, providers, settings)
    step = plan.steps[0]
    assert step.operation == "media.acquire_audio"
    assert step.params["audio_format"] == "opus"


def test_required_subtitles_without_matching_language(analyze, providers, settings):
    payload = minimal_payload(
        outputs=[{"id": "subs", "type": "subtitles", "options": {"languages": ["ja"]}}]
    )
    assert rejected_codes(payload, analyze, providers, settings) == [
        "capability_unavailable"
    ]


def test_optional_subtitles_without_matching_language_is_warning(
    analyze, providers, settings
):
    payload = minimal_payload(
        outputs=[
            {
                "id": "subs",
                "type": "subtitles",
                "required": False,
                "options": {"languages": ["ja"]},
            }
        ]
    )
    _, plan = plan_for(payload, analyze, providers, settings)
    assert [w.code for w in plan.warnings] == ["partial_output"]
    assert len(plan.steps) == 1


def test_allowed_languages_constraint(analyze, providers, settings):
    payload = minimal_payload(
        outputs=[{"id": "subs", "type": "subtitles", "options": {"languages": ["en"]}}],
        constraints={"content": {"allowed_languages": ["fr"]}},
    )
    assert rejected_codes(payload, analyze, providers, settings) == [
        "constraint_unsatisfiable"
    ]


def test_unknown_capability_plans_with_warning(providers, settings):
    """An inconclusive analysis (an uncharacterised source with no media facts)
    must not reject the request: the step is planned and attempted at runtime
    (ADR 0013 — 'unknown' is reserved for insufficient facts)."""
    from content.domain.analysis import (
        NormalizedResource,
        ResourceAnalysis,
        SourceAnalysis,
    )

    request = make_request(minimal_payload())
    analysis = ResourceAnalysis(
        analysis_id="ana_test",
        created_at="2026-07-18T00:00:00+00:00",
        sources=[
            SourceAnalysis(
                source_id="main",
                resource=NormalizedResource(resource_type="unknown"),
            )
        ],
    )
    plan = build_plan(request, analysis, providers, settings)
    assert [w.code for w in plan.warnings] == ["capability_unknown"]
    assert len(plan.steps) == 1


def test_unknown_preferred_provider_produces_warning(analyze, providers, settings):
    payload = minimal_payload(preferences={"providers": {"media": ["nonexistent"]}})
    _, plan = plan_for(payload, analyze, providers, settings)
    assert [w.code for w in plan.warnings] == ["preferred_provider_unavailable"]
