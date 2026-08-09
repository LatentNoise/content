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


def test_sponsorblock_args_remove():
    """Precise is the default: cuts get forced keyframes, so the result plays.

    Stream-copying instead (the old unconditional behaviour) makes yt-dlp cut
    on the nearest keyframe and splice the discarded frames back in with
    backwards timestamps — a real download measured 2506 video frames where
    2331 fit, 173 non-monotonic, all in the last 3.2 seconds. That is the
    "the end of the video stutters while the audio keeps going" bug.
    """
    args = sponsorblock_args({"sponsorblock": {"remove": ["sponsor", "intro"]}})
    assert args == [
        "--sponsorblock-remove",
        "sponsor,intro",
        "--force-keyframes-at-cuts",
    ]


def test_sponsorblock_args_remove_fast_mode_is_opt_in():
    """The fast path stays reachable for anyone who prefers speed over a clean
    tail — but it has to be asked for by name."""
    args = sponsorblock_args(
        {"sponsorblock": {"remove": ["sponsor"], "cut_mode": "keyframes"}}
    )
    assert args == [
        "--sponsorblock-remove",
        "sponsor",
        "--no-force-keyframes-at-cuts",
    ]


def test_sponsorblock_cut_mode_defaults_to_precise_in_the_contract():
    """The default lives in the contract, not only in the argument builder."""
    from content.domain.request import SponsorBlockOptions

    assert SponsorBlockOptions().cut_mode == "precise"
    assert SponsorBlockOptions(cut_mode="keyframes").cut_mode == "keyframes"


def test_sponsorblock_args_mark():
    args = sponsorblock_args({"sponsorblock": {"mark": ["outro"]}})
    assert args == ["--sponsorblock-mark", "outro"]


def test_sponsorblock_args_remove_and_mark():
    args = sponsorblock_args(
        {"sponsorblock": {"remove": ["sponsor"], "mark": ["intro"]}}
    )
    assert "--sponsorblock-remove" in args and "--sponsorblock-mark" in args


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
        "cut_mode": "precise",
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
        "cut_mode": "precise",
    }


def test_no_sponsorblock_leaves_params_clean(plan):
    result = plan(minimal_payload(outputs=[{"id": "audio_main", "type": "audio"}]))
    step = next(s for s in result.steps if s.operation == "media.acquire_audio")
    assert "sponsorblock" not in step.params
