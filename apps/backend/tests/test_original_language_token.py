"""`original` inside an audio language list (ADR 0022).

"The source's own voice" is a per-resource fact, so a caller cannot resolve it
in advance for a playlist — members are deliberately not analyzed at
submission (ADR 0019). The contract therefore carries a reserved word, and the
planner expands it against the analysis of *the resource being planned*.

The invariant these tests exist to hold is INV-018: the collection contributes
fan-out and nothing else. The token is resolved by the single-resource path,
so a playlist member and the same video submitted alone reach the same answer.
"""

import pytest
from pydantic import ValidationError

from content.analysis.service import AnalysisService
from content.domain.languages import ORIGINAL, expand_original
from content.domain.request import GenerationRequest
from content.planning.planner import build_plan
from tests.conftest import make_request, minimal_payload


@pytest.fixture
def plan(store, providers, settings):
    service = AnalysisService(store, providers, settings)

    def _plan(payload):
        request = make_request(payload)
        return build_plan(
            request, service.analyze_sources(list(request.sources)), providers, settings
        )

    return _plan


def video_payload(languages: list[str], uri: str = "https://x/talk") -> dict:
    return minimal_payload(
        sources=[{"id": "s1", "type": "url", "uri": uri}],
        outputs=[
            {
                "id": "vid",
                "type": "video",
                "options": {"selection": {"audio_languages": languages}},
            }
        ],
    )


def selection(plan_result) -> list[str]:
    step = next(s for s in plan_result.steps if s.operation == "media.acquire_video")
    return step.params["selection"]["audio_languages"]


def warnings_on(plan_result, ending: str) -> list:
    return [w for w in plan_result.warnings if w.path.endswith(ending)]


# --- the pure expansion ---------------------------------------------------------


def test_expansion_preserves_order_and_drops_duplicates():
    assert expand_original([ORIGINAL, "fr"], "ja") == ["ja", "fr"]
    assert expand_original(["fr", ORIGINAL], "ja") == ["fr", "ja"]
    assert expand_original([ORIGINAL, "ja"], "ja") == ["ja"], "no duplicate track"
    assert expand_original([ORIGINAL], "") == [], "unknown original drops out"
    assert expand_original(["fr", "en"], "ja") == ["fr", "en"], "untouched"


# --- a single resource ----------------------------------------------------------


def test_original_resolves_to_the_sources_own_language(plan):
    assert selection(plan(video_payload([ORIGINAL]))) == ["ja"]


def test_original_composes_with_the_rest_of_the_preference_list(plan):
    """It is one more entry in an ordered list — the reason the ADR chose a
    token over a `prefer_original` flag, which cannot express position."""
    assert selection(plan(video_payload([ORIGINAL, "en"]))) == ["ja", "en"]
    assert selection(plan(video_payload(["en", ORIGINAL]))) == ["en", "ja"]


def test_original_does_not_duplicate_a_language_already_asked_for(plan):
    assert selection(plan(video_payload([ORIGINAL, "ja"]))) == ["ja"]


def test_an_ordinary_language_list_is_untouched(plan):
    result = plan(video_payload(["ja", "en"]))
    assert selection(result) == ["ja", "en"]
    assert warnings_on(result, "audio_languages") == []


def test_the_token_never_reaches_the_provider(plan):
    """A provider must never receive a word it cannot resolve: whatever the
    analysis said, what lands in the step params are language codes."""
    for languages in ([ORIGINAL], [ORIGINAL, "en"], ["en", ORIGINAL]):
        assert ORIGINAL not in selection(plan(video_payload(languages)))


def test_an_audio_output_resolves_it_too(plan):
    """The audio path has its own resolution call — and its own details keys.
    A token that worked for video and not for audio would be a contract with
    two meanings."""
    result = plan(
        minimal_payload(
            outputs=[
                {"id": "aud", "type": "audio", "options": {"languages": [ORIGINAL]}}
            ]
        )
    )
    step = next(s for s in result.steps if s.operation == "media.acquire_audio")
    assert step.params["audio_languages"] == ["ja"]


# --- degradation ----------------------------------------------------------------


def test_no_declared_original_falls_through_to_the_next_preference(plan):
    result = plan(video_payload([ORIGINAL, "en"], uri="https://x/noorig-talk"))
    assert selection(result) == ["en"]
    warning = warnings_on(result, "audio_languages")[0]
    assert warning.code == "partial_output"
    assert ORIGINAL in warning.message


def test_no_declared_original_and_nothing_else_leaves_the_engine_default(plan):
    """An empty list means "the engine's default track" — the request stays
    satisfiable, but silence would be the bug, so it is warned about."""
    result = plan(video_payload([ORIGINAL], uri="https://x/noorig-talk"))
    assert selection(result) == []
    assert warnings_on(result, "audio_languages")[0].code == "partial_output"


# --- a collection ---------------------------------------------------------------


def _multilang_playlist(languages: list[str]) -> dict:
    return minimal_payload(
        sources=[{"id": "s1", "type": "url", "uri": "https://x/playlist-multilang"}],
        outputs=[
            {
                "id": "vid",
                "type": "video",
                "scope": "each_item",
                "options": {"selection": {"audio_languages": languages}},
            }
        ],
    )


def test_each_member_resolves_the_token_against_its_own_analysis(
    plan, store, providers, settings
):
    """The request people actually want: a channel's back catalogue, each
    video in its own language, asked for once."""
    service = AnalysisService(store, providers, settings)
    result = plan(_multilang_playlist([ORIGINAL]))
    members = [s for s in result.steps if s.operation == "collection.member"]
    assert len(members) == 2

    resolved = []
    for member in members:
        derived = GenerationRequest.model_validate(member.params["member_request"])
        # The token travels untouched to the member — the collection resolves
        # nothing, which is INV-018.
        assert derived.outputs[0].options.selection.audio_languages == [ORIGINAL]
        member_plan = build_plan(
            derived,
            service.analyze_sources(list(derived.sources)),
            providers,
            settings,
        )
        resolved.append(selection(member_plan))

    assert resolved == [["ja"], ["fr"]], "each member got its own voice"


def test_a_member_and_the_same_video_alone_resolve_identically(
    plan, store, providers, settings
):
    """The ADR's load-bearing claim: the token is expanded by the single-
    resource path, so a lone video gains the ability identically."""
    service = AnalysisService(store, providers, settings)
    result = plan(_multilang_playlist([ORIGINAL, "en"]))
    member = [s for s in result.steps if s.operation == "collection.member"][1]
    derived = GenerationRequest.model_validate(member.params["member_request"])
    member_plan = build_plan(
        derived, service.analyze_sources(list(derived.sources)), providers, settings
    )

    alone = plan(video_payload([ORIGINAL, "en"], uri=member.params["member_uri"]))

    assert selection(member_plan) == selection(alone) == ["fr", "en"]


# --- where it has no meaning ----------------------------------------------------


def test_a_subtitles_output_refuses_the_token():
    """ "The original" is undefined for a translated subtitle track. Invalid is
    a different answer from unsupported, so this is a refusal, not a warning."""
    with pytest.raises(ValidationError) as excinfo:
        make_request(
            minimal_payload(
                outputs=[
                    {
                        "id": "subs",
                        "type": "subtitles",
                        "options": {"languages": [ORIGINAL]},
                    }
                ]
            )
        )
    assert ORIGINAL in str(excinfo.value)


def test_embedded_subtitles_refuse_the_token():
    with pytest.raises(ValidationError):
        make_request(
            minimal_payload(
                outputs=[
                    {
                        "id": "vid",
                        "type": "video",
                        "options": {"processing": {"embed_subtitles": [ORIGINAL]}},
                    }
                ]
            )
        )


def test_a_translation_refuses_the_token():
    with pytest.raises(ValidationError):
        make_request(
            minimal_payload(
                outputs=[
                    {
                        "id": "subs",
                        "type": "subtitles",
                        "options": {"languages": ["en"]},
                    },
                    {
                        "id": "tr",
                        "type": "translation",
                        "from_outputs": ["subs"],
                        "options": {"target_language": ORIGINAL},
                    },
                ]
            )
        )
