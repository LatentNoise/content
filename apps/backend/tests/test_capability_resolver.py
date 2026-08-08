"""CapabilityResolver behaviour (ADR 0013, phase 2).

Feasibility is derived from facts × registry × implementations × policy — never
hardcoded per source type. These tests pin: correct statuses on representative
sources, explicit variant selection (R3), derived_from, and the policy
intersection (R4).
"""

from content.capabilities.catalog import capability
from content.capabilities.facts import facts_from_analysis
from content.capabilities.policy import (
    EffectivePolicy,
    RequestConstraints,
    effective_policy,
)
from content.capabilities.resolver import CapabilityResolver, select_variant
from content.domain.analysis import (
    NormalizedResource,
    SourceAnalysis,
    StreamInfo,
    SubtitleTrack,
)
from content.planning.transformations import build_registry
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ProviderRegistry
from content.providers.cloud_llm import CloudSummarizer
from content.providers.ffmpeg import FfmpegProvider
from content.providers.ytdlp import YtDlpProvider


def _providers(with_cloud_summary=True):
    processors = [TranscriptProcessor()]
    if with_cloud_summary:
        processors.append(CloudSummarizer("anthropic", "key", "model"))
    return ProviderRegistry([YtDlpProvider(), FfmpegProvider()], processors=processors)


def _resolver(with_cloud_summary=True):
    providers = _providers(with_cloud_summary)
    return CapabilityResolver(build_registry(providers), providers), providers


def _video_with_subs() -> SourceAnalysis:
    return SourceAnalysis(
        source_id="s",
        resource=NormalizedResource(
            resource_type="video",
            title="A video",
            duration_seconds=120,
            thumbnail_url="https://img/thumb.jpg",
        ),
        streams=[
            StreamInfo(type="video", height=1080),
            StreamInfo(type="audio", language="en"),
        ],
        subtitles=[SubtitleTrack(language="en", origin="manual")],
    )


def _audio_only_no_subs() -> SourceAnalysis:
    return SourceAnalysis(
        source_id="s",
        resource=NormalizedResource(resource_type="audio", title="A track"),
        streams=[StreamInfo(type="audio", language="en")],
    )


def _resolved(analysis, policy=None, with_cloud_summary=True):
    resolver, _ = _resolver(with_cloud_summary)
    facts = facts_from_analysis(analysis)
    return {c.id: c for c in resolver.resolve(facts, policy or EffectivePolicy())}


# --- rich video: everything downloadable, transcript/summary derivable ---------


def test_video_with_subtitles_offers_the_full_catalogue():
    caps = _resolved(_video_with_subs())
    assert caps["video.download"].status == "available"
    assert caps["video.clip"].status == "available"  # same-kind transform
    assert caps["audio.download"].status == "available"
    assert caps["subtitles.download"].status == "available"
    assert caps["thumbnail.download"].status == "available"
    assert caps["metadata.export"].status == "available"
    # derived outputs are 'derivable' and carry what they come from
    assert caps["transcript.generate"].status == "derivable"
    assert caps["transcript.generate"].selected_variant == "transcript.from_subtitles"
    assert caps["transcript.generate"].derived_from == ["subtitles"]
    assert caps["summary.generate"].status == "derivable"
    assert caps["summary.generate"].derived_from == ["subtitles"]


# --- audio-only, no subtitles: video/subtitles/transcript/summary unavailable --


def test_audio_only_without_subtitles_restricts_the_catalogue():
    caps = _resolved(_audio_only_no_subs())
    assert caps["audio.download"].status == "available"
    assert caps["video.download"].status == "unavailable"
    assert caps["video.download"].reason.code == "missing_material"
    assert "video" in caps["video.download"].reason.missing_materials
    assert caps["subtitles.download"].status == "unavailable"
    assert caps["thumbnail.download"].status == "unavailable"
    # transcript/summary need subtitles here (from_audio needs an STT runner)
    assert caps["transcript.generate"].status == "unavailable"
    assert caps["summary.generate"].status == "unavailable"


# --- explicit variants + the from_audio path blocked by missing STT ------------


def test_summary_selects_from_subtitles_and_from_audio_stays_unavailable():
    resolver, providers = _resolver()
    facts = facts_from_analysis(_video_with_subs())
    chosen = select_variant(
        capability("summary.generate"),
        facts,
        resolver._registry,
        providers,
        EffectivePolicy(),
    )
    assert chosen is not None and chosen.id == "summary.from_subtitles"


def test_resolver_and_planner_share_the_same_selection():
    """The resolved capability names exactly the variant select_variant returns
    (R3: no divergence between 'is it feasible' and 'how will it be built')."""
    resolver, providers = _resolver()
    facts = facts_from_analysis(_video_with_subs())
    resolved = {c.id: c for c in resolver.resolve(facts, EffectivePolicy())}
    for cap_id, cap in resolved.items():
        chosen = select_variant(
            capability(cap_id), facts, resolver._registry, providers, EffectivePolicy()
        )
        if cap.status in ("available", "derivable"):
            assert chosen is not None and chosen.id == cap.selected_variant
        else:
            assert chosen is None


# --- policy intersection (R4) --------------------------------------------------


def test_cloud_only_summary_is_restricted_when_cloud_disallowed():
    # Only a cloud summarizer installed → summary needs a cloud runner.
    strict = EffectivePolicy(allow_cloud_providers=False)
    caps = _resolved(_video_with_subs(), policy=strict)
    assert caps["summary.generate"].status == "restricted"
    assert caps["summary.generate"].reason.code == "policy_restricted"
    assert "text.summarize" in caps["summary.generate"].reason.blocked_operations
    # ...but still available when the instance allows cloud.
    caps_ok = _resolved(_video_with_subs(), policy=EffectivePolicy(True))
    assert caps_ok["summary.generate"].status == "derivable"


def test_request_constraints_can_only_restrict():
    instance = EffectivePolicy(allow_cloud_providers=True)
    # A request may tighten to False...
    tightened = effective_policy(
        instance, RequestConstraints(allow_cloud_providers=False)
    )
    assert tightened.allow_cloud_providers is False
    # ...but cannot re-enable what the instance forbids.
    locked = EffectivePolicy(allow_cloud_providers=False)
    relaxed = effective_policy(locked, RequestConstraints(allow_cloud_providers=True))
    assert relaxed.allow_cloud_providers is False
    # None = no opinion, keep the instance value.
    kept = effective_policy(instance, RequestConstraints(allow_cloud_providers=None))
    assert kept.allow_cloud_providers is True
