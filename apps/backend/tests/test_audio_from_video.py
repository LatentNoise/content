"""One request, one download: audio is copied out of the video (D-57).

Asking a URL for both a video and an audio output downloaded the same stream
twice — merged into the video, then again on its own. The waste was the
smaller half: a second request is a second chance to be refused, and one was,
losing a job's audio to a transient 403 while the video beside it already held
the identical stream.

The audio output is now a `-c:a copy` extraction from that acquisition. What
these tests mostly guard is the *refusals*: derivation is only correct where
the extracted track is provably the one the second download would have
produced, and silently returning a different audio file would be a worse bug
than fetching twice.
"""

from __future__ import annotations

import pytest

from content.analysis.service import AnalysisService
from content.planning.planner import build_plan
from tests.conftest import make_request, minimal_payload


@pytest.fixture
def registry():
    """The engine's real shape for this feature: a URL provider that
    downloads and an ffmpeg runner that can copy the audio out."""
    from content.providers.base import ProviderRegistry
    from tests.conftest import FakeFileProvider, FakeProvider

    return ProviderRegistry([FakeProvider(), FakeFileProvider()])


@pytest.fixture
def plan(store, registry, settings):
    providers = registry
    service = AnalysisService(store, providers, settings)

    def _plan(outputs, sources=None):
        payload = minimal_payload(outputs=outputs)
        if sources is not None:
            payload["sources"] = sources
        request = make_request(payload)
        analysis = service.analyze_sources(list(request.sources))
        return build_plan(request, analysis, providers, settings)

    return _plan


def _by_operation(plan, operation):
    return [s for s in plan.steps if s.operation == operation]


def test_audio_is_copied_out_of_the_video_instead_of_downloaded_again(plan):
    result = plan(
        [
            {"id": "video_main", "type": "video"},
            {"id": "audio_main", "type": "audio"},
        ]
    )
    acquisitions = _by_operation(result, "media.acquire_video")
    audio_steps = _by_operation(result, "media.acquire_audio")

    assert len(acquisitions) == 1, "the video is downloaded exactly once"
    assert len(audio_steps) == 1
    audio = audio_steps[0]
    assert audio.provider == "ffmpeg", "extracted locally, not fetched again"
    assert audio.depends_on == [acquisitions[0].id], "reads the acquired video"
    # Both outputs still deliver.
    assert {b.artifact_request_id for b in result.output_bindings} == {
        "video_main",
        "audio_main",
    }


def test_audio_alone_is_still_downloaded(plan):
    """Nothing to copy from: the ordinary acquisition must remain."""
    result = plan([{"id": "audio_main", "type": "audio"}])
    audio_steps = _by_operation(result, "media.acquire_audio")

    assert len(audio_steps) == 1
    assert audio_steps[0].provider == "ytdlp"
    assert not audio_steps[0].depends_on
    assert not _by_operation(result, "media.acquire_video")


def test_a_different_audio_format_keeps_its_own_download(plan):
    """`format: opus` means transcoding at acquisition; a stream copy cannot
    produce it, so the second download is genuinely needed."""
    result = plan(
        [
            {"id": "video_main", "type": "video"},
            {"id": "audio_main", "type": "audio", "options": {"format": "opus"}},
        ]
    )
    audio = _by_operation(result, "media.acquire_audio")[0]
    assert audio.provider == "ytdlp", "a transcode is not an extraction"
    assert audio.params.get("audio_format") == "opus"


def test_different_sponsorblock_settings_keep_their_own_download(plan):
    """SponsorBlock is applied during acquisition, so it is baked into the
    file: cutting the video but not the audio makes the tracks differ."""
    result = plan(
        [
            {
                "id": "video_main",
                "type": "video",
                "options": {"sponsorblock": {"remove": ["sponsor"]}},
            },
            {"id": "audio_main", "type": "audio"},
        ]
    )
    audio = _by_operation(result, "media.acquire_audio")[0]
    assert audio.provider == "ytdlp"
    assert not audio.depends_on


def test_matching_sponsorblock_settings_still_share_the_download(plan):
    same = {"remove": ["sponsor"], "mark": ["intro"]}
    result = plan(
        [
            {"id": "video_main", "type": "video", "options": {"sponsorblock": same}},
            {"id": "audio_main", "type": "audio", "options": {"sponsorblock": same}},
        ]
    )
    audio = _by_operation(result, "media.acquire_audio")[0]
    assert audio.provider == "ffmpeg"
    assert len(_by_operation(result, "media.acquire_video")) == 1


def test_different_audio_languages_keep_their_own_download(plan):
    """The video carries the tracks *it* selected; asking the audio output for
    a different set means a genuinely different download."""
    result = plan(
        [
            {
                "id": "video_main",
                "type": "video",
                "options": {"selection": {"audio_languages": ["en"]}},
            },
            {
                "id": "audio_main",
                "type": "audio",
                "options": {"languages": ["fr"]},
            },
        ]
    )
    audio = _by_operation(result, "media.acquire_audio")[0]
    assert audio.provider == "ytdlp"


def test_the_audio_is_taken_before_any_cut_is_applied(plan):
    """A cut is a transform *after* acquisition. The audio output never asked
    to be cut, so it must read the acquisition, not the cut result."""
    result = plan(
        [
            {
                "id": "video_main",
                "type": "video",
                "options": {"cut": {"start": "0", "end": "5"}},
            },
            {"id": "audio_main", "type": "audio"},
        ]
    )
    acquisition = _by_operation(result, "media.acquire_video")[0]
    cut = _by_operation(result, "video.cut")[0]
    audio = _by_operation(result, "media.acquire_audio")[0]

    assert audio.depends_on == [acquisition.id], "reads the uncut acquisition"
    assert cut.id not in audio.depends_on


def test_a_file_source_is_unaffected(plan):
    """A file's audio was always extracted; nothing was ever fetched twice."""
    result = plan(
        [
            {"id": "video_main", "type": "video"},
            {"id": "audio_main", "type": "audio"},
        ],
        sources=[{"id": "main", "type": "file", "path": "/input/movie.mp4"}],
    )
    audio = _by_operation(result, "media.acquire_audio")[0]
    assert audio.provider == "ffmpeg"


def test_without_the_extractor_the_download_is_kept(store, providers, settings):
    """An optimization must never be the reason a request stops working.

    The default fixture registry has no ffmpeg runner — the shape of an
    installation that cannot copy a track out of a container. Planning must
    fall back to the ordinary download rather than emit a step nothing can
    execute.
    """
    service = AnalysisService(store, providers, settings)
    payload = minimal_payload(
        outputs=[
            {"id": "video_main", "type": "video"},
            {"id": "audio_main", "type": "audio"},
        ]
    )
    request = make_request(payload)
    result = build_plan(
        request, service.analyze_sources(list(request.sources)), providers, settings
    )

    audio = _by_operation(result, "media.acquire_audio")[0]
    assert audio.provider == "ytdlp", "no extractor: download as before"
    assert not audio.depends_on
