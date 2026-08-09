"""SponsorBlock options on video/audio outputs."""

import pytest
from pydantic import ValidationError

from content.analysis.service import AnalysisService
from content.planning.planner import build_plan
from content.providers.ytdlp import sponsorblock_args
from tests.conftest import make_request, minimal_payload

# --- contract -------------------------------------------------------------------


def test_valid_categories_accepted():
    make_request(
        minimal_payload(
            outputs=[
                {
                    "id": "v",
                    "type": "video",
                    "options": {"sponsorblock": {"remove": ["sponsor", "outro"]}},
                }
            ]
        )
    )


def test_invalid_category_rejected():
    with pytest.raises(ValidationError):
        make_request(
            minimal_payload(
                outputs=[
                    {
                        "id": "v",
                        "type": "video",
                        "options": {"sponsorblock": {"remove": ["not_a_category"]}},
                    }
                ]
            )
        )


# --- yt-dlp argument builder (pure) --------------------------------------------


def test_sponsorblock_args_remove_never_lets_ytdlp_cut_by_default():
    """Fast mode: yt-dlp marks, Content cuts (INV-019).

    Neither of yt-dlp's own removal modes is acceptable as a default: its
    stream copy leaves a phantom keep-chunk on end-reaching segments (the
    "stuttering tail" defect — 141 frames replayed with colliding PTS on a
    measured file), and ``--force-keyframes-at-cuts`` re-encodes the whole
    file at ffmpeg's default codecs (measured: 17 s of network, 8 min 33 s of
    CPU, AV1/Opus returned as H.264/Vorbis, 46% larger). So the download call
    must only mark the segments — remove set included, so their bounds are
    known — and print them for the keyframe-snapped stream-copy cut that
    Content runs itself.
    """
    args = sponsorblock_args({"sponsorblock": {"remove": ["sponsor", "intro"]}})
    assert args == [
        "--sponsorblock-mark",
        "sponsor,intro",
        "--print",
        "after_move:SBCUT:%(sponsorblock_chapters)j",
        "--no-simulate",
        "--no-quiet",
    ]
    assert "--sponsorblock-remove" not in args
    assert "--force-keyframes-at-cuts" not in args


def test_sponsorblock_args_remove_precise_mode_is_opt_in():
    """The re-encoding path stays reachable for anyone who wants the cut to
    land exactly where they asked — but it has to be asked for by name."""
    args = sponsorblock_args(
        {"sponsorblock": {"remove": ["sponsor"], "cut_mode": "precise"}}
    )
    assert args == [
        "--sponsorblock-remove",
        "sponsor",
        "--force-keyframes-at-cuts",
    ]


def test_sponsorblock_cut_mode_defaults_to_keyframes_in_the_contract():
    """The default lives in the contract, not only in the argument builder."""
    from content.domain.request import SponsorBlockOptions

    assert SponsorBlockOptions().cut_mode == "keyframes"
    assert SponsorBlockOptions(cut_mode="precise").cut_mode == "precise"


def test_sponsorblock_args_mark():
    args = sponsorblock_args({"sponsorblock": {"mark": ["outro"]}})
    assert args == ["--sponsorblock-mark", "outro"]


def test_sponsorblock_args_remove_and_mark():
    """Fast mode marks the union (mark set first), so the cut's boundaries are
    in the file's own chapters until Content removes them."""
    args = sponsorblock_args(
        {"sponsorblock": {"remove": ["sponsor"], "mark": ["intro"]}}
    )
    marked = args[args.index("--sponsorblock-mark") + 1]
    assert marked == "intro,sponsor"
    assert "--sponsorblock-remove" not in args
    assert "--print" in args


def test_sponsorblock_args_empty():
    assert sponsorblock_args({}) == []
    assert sponsorblock_args({"sponsorblock": {"remove": [], "mark": []}}) == []


# --- planner threading ----------------------------------------------------------


@pytest.fixture
def plan(store, providers, settings):
    service = AnalysisService(store, providers, settings)

    def _plan(payload):
        request = make_request(payload)
        analysis = service.analyze_sources(list(request.sources))
        return build_plan(request, analysis, providers, settings)

    return _plan


def test_video_sponsorblock_threaded_into_step(plan):
    result = plan(
        minimal_payload(
            outputs=[
                {
                    "id": "video_main",
                    "type": "video",
                    "options": {"sponsorblock": {"remove": ["sponsor"]}},
                }
            ]
        )
    )
    step = next(s for s in result.steps if s.operation == "media.acquire_video")
    assert step.params["sponsorblock"] == {
        "remove": ["sponsor"],
        "mark": [],
        # Threaded through to the provider: it decides the keyframe flag.
        "cut_mode": "keyframes",
    }


def test_audio_sponsorblock_threaded_into_step(plan):
    result = plan(
        minimal_payload(
            outputs=[
                {
                    "id": "audio_main",
                    "type": "audio",
                    "options": {"sponsorblock": {"mark": ["intro"]}},
                }
            ]
        )
    )
    step = next(s for s in result.steps if s.operation == "media.acquire_audio")
    assert step.params["sponsorblock"] == {
        "remove": [],
        "mark": ["intro"],
        "cut_mode": "keyframes",
    }


def test_no_sponsorblock_leaves_params_clean(plan):
    result = plan(minimal_payload(outputs=[{"id": "audio_main", "type": "audio"}]))
    step = next(s for s in result.steps if s.operation == "media.acquire_audio")
    assert "sponsorblock" not in step.params
