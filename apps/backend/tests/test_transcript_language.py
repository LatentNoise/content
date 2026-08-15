"""Which language a transcript resolves to when the request says nothing (D-58).

The rule under test, for `language: "auto"`: the resource's own language, then
the operator's configured languages, then English, and only then — as a genuine
last resort — the first track alphabetically.

The bug this pins down was found on a live 0.4.0 install: YouTube publishes
auto-*translated* subtitles in around a hundred languages, so "the first manual
or automatic track" resolved to `aa` (Afar) for an ordinary English video. The
engine downloaded Afar subtitles and called the result a transcript — silently
wrong, which is worse than failing. The alphabetical tie-break is kept, because
determinism still matters, but it now runs last instead of second.
"""

from __future__ import annotations

import dataclasses

import pytest

from content.analysis.service import AnalysisService
from content.planning.planner import (
    _resolve_transcript_language as resolve,
)
from content.planning.planner import (
    build_plan,
)
from tests.conftest import make_request, minimal_payload

# What YouTube really offers on an English video: one human track, and machine
# translations into everything — 'aa' first alphabetically, 'zu' last.
YOUTUBE_LIKE = {
    "manual": ["en"],
    "automatic": ["aa", "ab", "af", "ar", "de", "en", "es", "fr", "ja", "zu"],
}


# --- the rule itself ------------------------------------------------------------


def test_the_resource_language_wins():
    assert resolve("auto", YOUTUBE_LIKE, ["fr"], ("es",)) == "fr"


def test_a_manual_track_stands_in_for_the_unknown_source_language():
    """The real YouTube case: analysis reports no resource language, the video
    has one hand-written English track, and the installation prefers French.
    The transcript must be the English that was actually spoken — a French
    machine translation would be a translation wearing a transcript's name."""
    assert resolve("auto", YOUTUBE_LIKE, [], ("fr", "es")) == "en"


def test_a_preference_chooses_among_several_manual_tracks():
    """Where preferences legitimately decide: the source really does offer
    both, so picking the configured one adds information rather than inventing
    it."""
    details = {"manual": ["en", "fr"], "automatic": ["aa", "zu"]}
    assert resolve("auto", details, [], ("fr",)) == "fr"


def test_preferences_apply_to_automatic_tracks_when_there_is_no_manual_one():
    details = {"manual": [], "automatic": ["aa", "en", "fr", "zu"]}
    assert resolve("auto", details, [], ("fr",)) == "fr"


def test_english_is_the_fallback_when_nothing_else_matches():
    details = {"manual": [], "automatic": ["aa", "en", "zu"]}
    assert resolve("auto", details, [], ("pt",)) == "en"


def test_alphabetical_order_is_the_last_resort_not_the_second():
    """No resource language, no preference match, and no English at all."""
    details = {"manual": [], "automatic": ["aa", "sw", "zu"]}
    assert resolve("auto", details, [], ("fr",)) == "aa"


def test_an_explicit_request_always_wins():
    assert resolve("de", YOUTUBE_LIKE, ["en"], ("fr",)) == "de"


def test_afar_is_never_chosen_while_a_sane_option_exists():
    """The regression itself, stated plainly."""
    for resource, preferred in (
        ([], ()),
        ([], ("fr",)),
        (["en"], ()),
        (["ja"], ("fr",)),
    ):
        chosen = resolve("auto", YOUTUBE_LIKE, list(resource), preferred)
        assert chosen != "aa", f"resource={resource} preferred={preferred} → {chosen}"


# --- and the same rule reaching a real plan -------------------------------------


@pytest.fixture
def plan_with(store, providers, settings):
    """Build a real plan with the operator's configured languages."""

    def _plan(primary: str = "", secondaries: tuple[str, ...] = ()):
        configured = dataclasses.replace(
            settings, language_primary=primary, languages_secondaries=secondaries
        )
        service = AnalysisService(store, providers, configured)
        payload = minimal_payload(outputs=[{"id": "transcript", "type": "transcript"}])
        request = make_request(payload)
        analysis = service.analyze_sources(list(request.sources))
        return build_plan(request, analysis, providers, configured)

    return _plan


def _subtitle_languages(plan):
    step = next(s for s in plan.steps if s.operation == "media.acquire_subtitles")
    return step.params["languages"]


def test_the_configured_primary_reaches_the_acquisition_step(plan_with):
    """The fake source offers manual en + fr: a French installation must get
    French subtitles for its transcript, not whatever sorts first."""
    assert _subtitle_languages(plan_with(primary="fr")) == ["fr"]


def test_english_installation_gets_english(plan_with):
    assert _subtitle_languages(plan_with(primary="en")) == ["en"]


def test_an_unconfigured_installation_still_avoids_a_surprise(plan_with):
    """No preference configured at all — English rather than alphabetical."""
    assert _subtitle_languages(plan_with()) == ["en"]
