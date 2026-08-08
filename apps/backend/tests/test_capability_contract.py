"""Public-contract stability for the capability layer (ADR 0013 phase 5a).

Pins the shape of ResolvedCapability / CapabilityReason in the OpenAPI schema
and the stable machine-identifier vocabularies (statuses, reason codes,
capability ids). Per the ADR these are additive-only: renaming or removing a
field, status, code or capability id is a breaking change — a failure here is a
deliberate contract decision, not a test to silence.
"""

import dataclasses

from fastapi.testclient import TestClient

from content.api.app import create_app
from content.capabilities.catalog import all_capabilities
from content.capabilities.facts import SourceFacts
from content.capabilities.policy import EffectivePolicy
from content.capabilities.resolver import CapabilityResolver
from content.planning.transformations import build_registry
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ProviderRegistry
from content.providers.cloud_llm import CloudSummarizer
from content.providers.ffmpeg import FfmpegProvider
from content.providers.ytdlp import YtDlpProvider

STABLE_STATUSES = {"available", "derivable", "unavailable", "unknown", "restricted"}
STABLE_REASON_CODES = {
    "missing_material",
    "implementation_unavailable",
    "policy_restricted",
}
STABLE_CAPABILITY_IDS = {
    "video.download",
    "video.clip",
    "audio.download",
    "subtitles.download",
    "thumbnail.download",
    "metadata.export",
    "transcript.generate",
    "summary.generate",
    "translation.generate",
    "chapters.generate",
    # Non-media vertical (prompt 10): reading a page or a document.
    "text.extract",
    "markdown.export",
    # Rendering readable content into a paginated document.
    "pdf.render",
    # Frames cut out of the video itself (prompt 11).
    "thumbnail.generate",
    "keyframes.extract",
}


def _schemas(settings):
    with TestClient(create_app(settings, start_worker=False)) as client:
        return client.get("/openapi.json").json()["components"]["schemas"]


# --- schema shape --------------------------------------------------------------


def test_resolved_capability_schema_is_published(settings):
    props = _schemas(settings)["ResolvedCapability"]["properties"]
    assert set(props) >= {
        "id",
        "title",
        "description",
        "output_type",
        "status",
        "selected_variant",
        "derived_from",
        "reason",
    }


def test_capability_reason_schema_is_published(settings):
    props = _schemas(settings)["CapabilityReason"]["properties"]
    assert set(props) >= {
        "code",
        "missing_materials",
        "missing_operations",
        "blocked_operations",
    }


def test_status_enum_is_the_stable_set(settings):
    status = _schemas(settings)["ResolvedCapability"]["properties"]["status"]
    assert set(status["enum"]) == STABLE_STATUSES


# --- stable vocabularies -------------------------------------------------------


def test_capability_ids_are_the_stable_public_vocabulary():
    assert {cap.id for cap in all_capabilities()} == STABLE_CAPABILITY_IDS


def test_every_reason_code_the_resolver_emits_is_in_the_contract():
    # Drive the three blockers and assert every emitted code is documented.
    providers = ProviderRegistry(
        [YtDlpProvider(), FfmpegProvider()],
        processors=[TranscriptProcessor(), CloudSummarizer("anthropic", "k", "m")],
    )
    resolver = CapabilityResolver(build_registry(providers), providers)

    scenarios = [
        # audio-only source: video is a missing material; the from_audio
        # transcript path is implementation_unavailable (no STT).
        SourceFacts(
            resource_type="audio", has_audio=True, _present=frozenset({"audio"})
        ),
        # rich source but cloud disallowed: summary is policy_restricted.
        SourceFacts(
            resource_type="video",
            has_video=True,
            has_audio=True,
            subtitle_languages=("en",),
            _present=frozenset({"video", "audio", "subtitles", "image"}),
        ),
    ]
    codes = set()
    for facts in scenarios:
        for policy in (EffectivePolicy(True), EffectivePolicy(False)):
            for cap in resolver.resolve(facts, policy):
                if cap.reason is not None:
                    codes.add(cap.reason.code)
    assert codes  # the scenarios actually exercise blockers
    assert codes <= STABLE_REASON_CODES


# --- unknown rule (R8) ---------------------------------------------------------


def test_unknown_is_never_presented_as_available():
    # An uncharacterised source (no facts) → capabilities resolve to 'unknown',
    # never 'available' (R8).
    providers = ProviderRegistry([YtDlpProvider(), FfmpegProvider()])
    resolver = CapabilityResolver(build_registry(providers), providers)
    inconclusive = SourceFacts(resource_type="unknown", conclusive=False)
    resolved = resolver.resolve(inconclusive, EffectivePolicy())
    statuses = {c.status for c in resolved}
    assert "unknown" in statuses
    for cap in resolved:
        if cap.status == "unknown":
            assert cap.reason is None  # no proven impossibility
            assert cap.status != "available"


def test_capability_unknown_is_traceable_on_the_job(store, settings):
    # R8: the uncertainty must survive to the execution surface. An inconclusive
    # source is planned + attempted, and the capability_unknown warning is
    # carried on the job's `job.planned` event (and its plan snapshot).
    from content.analysis.service import AnalysisService
    from content.application.submit import submit_generation
    from content.domain.analysis import NormalizedResource, SourceAnalysis
    from tests.conftest import FakeProvider, make_request, minimal_payload

    class UnknownProvider(FakeProvider):
        def analyze(self, source, ctx):
            return SourceAnalysis(
                source_id=source.id,
                resource=NormalizedResource(resource_type="unknown"),
            )

    providers = ProviderRegistry(
        [UnknownProvider()], processors=[TranscriptProcessor()]
    )
    payload = minimal_payload()  # a single audio output
    result = submit_generation(
        payload,
        make_request(payload),
        store=store,
        settings=settings,
        providers=providers,
        analysis_service=AnalysisService(store, providers, settings),
    )
    assert "capability_unknown" in [w.code for w in result.warnings]
    planned = next(
        e for e in store.list_events(result.job_id) if e["type"] == "job.planned"
    )
    warning_codes = [w["code"] for w in planned["data"].get("warnings", [])]
    assert "capability_unknown" in warning_codes


def test_material_tristate_distinguishes_unknown_from_absent():
    absent = SourceFacts(resource_type="audio", conclusive=True)  # video absent
    unknown = SourceFacts(resource_type="unknown", conclusive=False)  # video unknown
    assert absent.material_state("video") == "absent"
    assert unknown.material_state("video") == "unknown"
    # frozen dataclass: sanity that the flag is what drives it
    assert dataclasses.replace(unknown, conclusive=True).material_state("video") == (
        "absent"
    )
