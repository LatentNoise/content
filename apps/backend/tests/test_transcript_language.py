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
from content.identity import LOCAL_OWNER
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


# --- the dub menu, and the regioned tags in it (D-60) ---------------------------
#
# The second way rule 1 could be silently wrong, found on a live multi-dub
# video. Providers report dubbed audio as regioned BCP-47 tags (`en-US`) while
# the subtitle tracks of that same video are keyed by bare codes (`en`), so
# string equality misses every dub — and then whichever dub happened to be
# tagged bare won. On an English talk dubbed into five languages that was `hi`:
# the engine fetched auto-translated Hindi captions and called them the
# transcript of an English video.

# yt-dlp's view of such a video: five dubbed audio tracks, one of them bare,
# plus YouTube's usual wall of auto-translated captions.
MULTI_DUB = sorted(["en-US", "es-US", "fr-FR", "hi", "pt-BR"])
DUB_TRACKS = {
    "manual": ["en"],
    "automatic": ["aa", "de", "en", "es", "fr", "hi", "pt", "zu"],
}


def test_a_dubbed_english_video_is_not_transcribed_through_hindi():
    """D-60 itself, stated plainly: the bug's own case."""
    assert resolve("auto", DUB_TRACKS, MULTI_DUB, (), "en-US") == "en"


def test_the_marked_original_voice_decides_among_the_dubs():
    """The provider says which track is the source's own voice, and that is the
    one question rule 1 is asking. ADR 0022 already trusts this field for the
    `original` audio token, so the transcript rule reading it too is one
    signal used consistently rather than a new one."""
    assert resolve("auto", DUB_TRACKS, MULTI_DUB, (), "fr-FR") == "fr"
    assert resolve("auto", DUB_TRACKS, MULTI_DUB, (), "pt-BR") == "pt"


def test_a_dub_menu_without_a_marked_original_falls_through_to_the_manual_track():
    """Several reported languages and no marked voice track: nothing here knows
    what was said, so the hand-written track stands in — rule 2's whole
    purpose. Guessing from the menu is what produced Hindi."""
    assert resolve("auto", DUB_TRACKS, MULTI_DUB, ()) == "en"


def test_the_alphabet_no_longer_decides_which_dub_wins():
    """The class behind the instance. Comparing on the primary subtag alone
    would still have walked the menu in order, so a video whose dubs sort with
    German first would have come back German instead of Hindi — a different
    wrong answer, not a fix."""
    menu = sorted(["de-DE", "en-US", "ja-JP"])
    assert menu[0] == "de-DE"
    assert resolve("auto", DUB_TRACKS, menu, (), "en-US") == "en"


def test_a_single_regioned_language_still_finds_its_bare_track():
    """One reported language is genuine knowledge of what was spoken, and it
    must survive the spelling difference: `pt-BR` said, `pt` on offer."""
    assert resolve("auto", DUB_TRACKS, ["pt-BR"], ()) == "pt"


def test_an_exactly_matching_track_is_preferred_over_its_bare_form():
    """When the source really does offer the regional track, that is the more
    precise answer and the subtag match must not step on it."""
    details = {"manual": ["pt", "pt-BR"], "automatic": []}
    assert resolve("auto", details, ["pt-BR"], ()) == "pt-BR"


def test_the_returned_name_is_the_track_s_own_spelling():
    """The result is handed to the acquisition step as the language to fetch,
    so it has to be a name the provider knows. `en-US` would fetch nothing."""
    chosen = resolve("auto", DUB_TRACKS, MULTI_DUB, (), "en-US")
    assert chosen in set(DUB_TRACKS["manual"]) | set(DUB_TRACKS["automatic"])


def test_a_spoken_language_with_no_track_at_all_falls_through():
    """A Japanese original on a video that carries no Japanese captions: rule 1
    has nothing to offer and must not invent a track."""
    details = {"manual": ["en"], "automatic": ["de", "en"]}
    assert resolve("auto", details, ["ja-JP"], (), "ja-JP") == "en"


# --- and the same rule reaching a real plan -------------------------------------


@pytest.fixture
def plan_with(store, providers, settings):
    """Build a real plan with the operator's configured languages."""

    def _plan(
        primary: str = "",
        secondaries: tuple[str, ...] = (),
        uri: str = "https://example.com/video",
    ):
        configured = dataclasses.replace(
            settings, language_primary=primary, languages_secondaries=secondaries
        )
        service = AnalysisService(store, providers, configured)
        payload = minimal_payload(
            sources=[{"id": "main", "type": "url", "uri": uri}],
            outputs=[{"id": "transcript", "type": "transcript"}],
        )
        request = make_request(payload)
        analysis = service.analyze_sources(LOCAL_OWNER, list(request.sources))
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


def test_the_marked_original_voice_reaches_the_acquisition_step(plan_with):
    """The wiring D-60 needed, proved where it matters rather than on the rule
    in isolation: the `lang-fr` source declares a French original voice, and an
    English installation must still transcribe the French that was spoken. The
    alternative is an English auto-translation wearing a transcript's name —
    the very thing this module's opening docstring refuses."""
    assert _subtitle_languages(plan_with(primary="en", uri="https://x/lang-fr")) == [
        "fr"
    ]
