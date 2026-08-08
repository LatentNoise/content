"""Phase-4 bascule guards (ADR 0013): the resolver reproduces the old
provider-emitted statuses for the download capabilities (equivalence), and
encodes the ONE intentional difference — a transcript is no longer over-promised
from audio when no speech-to-text runner is active.
"""

from content.capabilities.facts import facts_from_analysis
from content.capabilities.policy import EffectivePolicy
from content.capabilities.resolver import CapabilityResolver
from content.domain.analysis import (
    MediaFacts,
    NormalizedResource,
    SourceAnalysis,
    StreamInfo,
    SubtitleTrack,
)
from content.planning.transformations import build_registry
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ProviderRegistry
from content.providers.ffmpeg import FfmpegProvider
from content.providers.ytdlp import YtDlpProvider


def _providers():
    return ProviderRegistry(
        [YtDlpProvider(), FfmpegProvider()], processors=[TranscriptProcessor()]
    )


def _resolve(analysis):
    providers = _providers()
    resolver = CapabilityResolver(build_registry(providers), providers)
    facts = facts_from_analysis(analysis)
    return {c.id: c for c in resolver.resolve(facts, EffectivePolicy())}


def _audio_no_subtitles():
    """A source with video+audio but NO subtitles — the case the old path
    over-promised a transcript for."""
    return SourceAnalysis(
        source_id="s",
        resource=NormalizedResource(resource_type="video", thumbnail_url="http://t"),
        streams=[
            StreamInfo(type="video", height=1080),
            StreamInfo(type="audio", language="en"),
        ],
        media=MediaFacts(has_video=True, has_audio=True, video_heights=[1080]),
    )


def _with_subtitles():
    a = _audio_no_subtitles()
    return a.model_copy(
        update={"subtitles": [SubtitleTrack(language="en", origin="manual")]}
    )


# --- equivalence ---------------------------------------------------------------


def test_downloads_stay_available_like_the_old_capabilities():
    caps = _resolve(_audio_no_subtitles())
    for cap_id in (
        "video.download",
        "audio.download",
        "thumbnail.download",
        "metadata.export",
    ):
        assert caps[cap_id].status == "available", cap_id


def test_transcript_and_summary_derivable_when_subtitles_present():
    caps = _resolve(_with_subtitles())
    assert caps["transcript.generate"].status == "derivable"
    assert caps["transcript.generate"].derived_from == ["subtitles"]


# --- the intentional difference ------------------------------------------------


def test_transcript_from_audio_is_not_overpromised_without_stt():
    # Old behaviour announced transcript as derivable from audio; the new
    # resolver reports it unavailable, with a structured, actionable reason.
    transcript = _resolve(_audio_no_subtitles())["transcript.generate"]
    assert transcript.status == "unavailable"
    assert transcript.reason.code == "implementation_unavailable"
    assert transcript.reason.missing_operations == ["audio.transcribe"]
