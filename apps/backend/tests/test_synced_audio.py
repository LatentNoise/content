"""Audio that carries its own words, timed to them.

Nothing here needs ffmpeg or a real encoder: ID3 is a container prepended to a
file, so the tags round-trip through any bytes on disk. What is being tested is
the pairing and the timings, and those are ours; whether libmp3lame works is
not.
"""

from __future__ import annotations

import json

import pytest

from content.domain.request import GenerationRequest
from content.domain.validation import validate_structure
from content.processors.synced_audio import (
    UNDETERMINED,
    SyncedAudioProcessor,
    build_lrc,
    embed_lyrics,
    iso_639_2,
    lrc_timestamp,
    segments_from,
)
from content.providers.base import Material, StepExecutionError

SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": "Bonjour et bienvenue."},
    {"start": 2.0, "end": 4.5, "text": "Content transforme des sources."},
    {"start": 4.5, "end": 6.0, "text": "Le texte suit l'audio."},
]


# --- the language tag ID3 insists on --------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [("fr", "fra"), ("fr-FR", "fra"), ("FR_fr", "fra"), ("en", "eng"), ("por", "por")],
)
def test_a_language_becomes_its_three_letter_tag(given, expected):
    assert iso_639_2(given) == expected


def test_an_unmapped_language_says_so_rather_than_guessing():
    """`und` is the standard's own word for "not stated". Guessing `eng`
    because most things are English is how a French recording ends up labelled
    English forever."""
    assert iso_639_2("kl") == UNDETERMINED
    assert iso_639_2("") == UNDETERMINED


# --- the sidecar ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.0, "[00:00.00]"),
        (4.5, "[00:04.50]"),
        (61.25, "[01:01.25]"),
        (-3, "[00:00.00]"),
    ],
)
def test_the_lrc_timestamp_is_minutes_seconds_hundredths(seconds, expected):
    """The only shape LRC readers agree on. Milliseconds there and a player
    tends to print the tag instead of hiding it."""
    assert lrc_timestamp(seconds) == expected


def test_the_lrc_carries_a_line_per_segment():
    lrc = build_lrc(SEGMENTS, title="Une conférence", language="fra")
    lines = lrc.splitlines()
    assert lines[0] == "[ti:Une conférence]"
    assert "[la:fra]" in lines
    assert "[00:02.00]Content transforme des sources." in lines
    assert len([line for line in lines if line.startswith("[0")]) == len(SEGMENTS)


def test_an_undetermined_language_is_left_out_of_the_lrc():
    """A `[la:und]` header tells a reader nothing and some players display it."""
    assert "[la:" not in build_lrc(SEGMENTS, language=UNDETERMINED)


# --- reading the timings back out of whatever produced them ----------------------


def test_a_canonical_transcript_keeps_its_timings(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"segments": SEGMENTS}))
    assert segments_from(path) == SEGMENTS


def test_srt_is_read_with_the_parser_the_transcript_was_built_with(tmp_path):
    path = tmp_path / "t.srt"
    path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nBonjour et bienvenue.\n\n"
        "2\n00:00:02,000 --> 00:00:04,500\nContent transforme des sources.\n"
    )
    got = segments_from(path)
    assert [s["text"] for s in got] == [
        "Bonjour et bienvenue.",
        "Content transforme des sources.",
    ]
    assert got[1]["start"] == 2.0


def test_blank_lines_are_not_synchronised(tmp_path):
    """A cue with no words is a timestamp pointing at nothing, and a player
    shows it as the lyrics going blank mid-sentence."""
    path = tmp_path / "t.json"
    path.write_text(
        json.dumps({"segments": [*SEGMENTS, {"start": 7.0, "end": 8.0, "text": "  "}]})
    )
    assert len(segments_from(path)) == len(SEGMENTS)


def test_malformed_json_yields_nothing_rather_than_half_a_transcript(tmp_path):
    path = tmp_path / "t.json"
    path.write_text("{not json")
    assert segments_from(path) == []


# --- what a player actually reads -------------------------------------------------


def test_the_tags_round_trip_through_a_real_file(tmp_path):
    from mutagen.id3 import ID3

    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 2048)
    embed_lyrics(audio, SEGMENTS, language="fra", title="Une conférence")

    tags = ID3(audio)
    sylt = tags.getall("SYLT")[0]
    assert sylt.lang == "fra"
    assert sylt.format == 2, "absolute time in milliseconds"
    assert sylt.type == 1, "lyrics"
    assert sylt.text[1] == ("Content transforme des sources.", 2000)
    assert "Le texte suit" in tags.getall("USLT")[0].text


def test_running_twice_leaves_one_set_of_lyrics(tmp_path):
    """Two SYLT frames disagreeing about the same recording is worse than none,
    and a retried job is an ordinary event."""
    from mutagen.id3 import ID3

    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 2048)
    embed_lyrics(audio, SEGMENTS, language="fra")
    embed_lyrics(audio, SEGMENTS[:1], language="fra")

    tags = ID3(audio)
    assert len(tags.getall("SYLT")) == 1
    assert len(tags.getall("SYLT")[0].text) == 1


# --- the step ---------------------------------------------------------------------


class _Ctx:
    def __init__(self, workdir, materials):
        self.workdir = workdir
        self.input_materials = materials
        self.timeout_seconds = 60.0

    def on_progress(self, percent, message):
        pass

    def on_warning(self, code, message, details):
        pass


def _step(**params):
    from content.domain.plan import PlanStep

    return PlanStep(
        id="sync_sy",
        operation="audio.sync_text",
        provider="content.synced_audio",
        params={"language": "auto", "lrc_sidecar": True, **params},
        depends_on=[],
    )


def _materials(tmp_path):
    audio = tmp_path / "talk.mp3"
    audio.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 2048)
    timed = tmp_path / "talk.json"
    timed.write_text(json.dumps({"segments": SEGMENTS}))
    return [
        Material(path=audio, media_type="audio/mpeg"),
        Material(
            path=timed, media_type="application/json", attributes={"language": "fr"}
        ),
    ]


def test_one_output_produces_the_audio_and_its_sidecar(tmp_path):
    """The contract has always allowed an output to produce several artifacts.
    This is what it is for: the file a player opens, and the file it reads the
    words from."""
    produced = SyncedAudioProcessor().execute(
        _step(), _Ctx(tmp_path, _materials(tmp_path))
    )
    kinds = sorted(p.path.suffix for p in produced)
    assert kinds == [".lrc", ".mp3"]
    mp3 = next(p for p in produced if p.path.suffix == ".mp3")
    assert mp3.media_type == "audio/mpeg"
    assert mp3.attributes["synced_lines"] == "3"
    assert mp3.attributes["language"] == "fra"


def test_the_sidecar_can_be_declined(tmp_path):
    produced = SyncedAudioProcessor().execute(
        _step(lrc_sidecar=False), _Ctx(tmp_path, _materials(tmp_path))
    )
    assert [p.path.suffix for p in produced] == [".mp3"]


def test_the_language_comes_from_the_transcript_when_not_given(tmp_path):
    produced = SyncedAudioProcessor().execute(
        _step(), _Ctx(tmp_path, _materials(tmp_path))
    )
    assert produced[0].attributes["language"] == "fra"


def test_the_order_of_from_outputs_does_not_matter(tmp_path):
    """`from_outputs: [t, a]` means what `[a, t]` means. Picking by suffix
    rather than by position is what makes that true."""
    materials = list(reversed(_materials(tmp_path)))
    produced = SyncedAudioProcessor().execute(_step(), _Ctx(tmp_path, materials))
    assert produced[0].attributes["synced_lines"] == "3"


def test_a_transcript_with_no_lines_fails_instead_of_shipping_silence(tmp_path):
    """An audio file carrying an empty lyrics frame is worse than one carrying
    none: the player shows a blank pane and the reader blames the feature."""
    audio = tmp_path / "talk.mp3"
    audio.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 2048)
    timed = tmp_path / "talk.json"
    timed.write_text(json.dumps({"segments": []}))
    materials = [
        Material(path=audio, media_type="audio/mpeg"),
        Material(path=timed, media_type="application/json"),
    ]
    with pytest.raises(StepExecutionError) as raised:
        SyncedAudioProcessor().execute(_step(), _Ctx(tmp_path, materials))
    assert raised.value.code == "no_input"


def test_a_missing_half_is_named(tmp_path):
    audio = tmp_path / "talk.mp3"
    audio.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 2048)
    with pytest.raises(StepExecutionError) as raised:
        SyncedAudioProcessor().execute(
            _step(), _Ctx(tmp_path, [Material(path=audio, media_type="audio/mpeg")])
        )
    assert raised.value.code == "no_input"
    assert "timed text" in str(raised.value)


# --- the contract -----------------------------------------------------------------


def _request(**output):
    return GenerationRequest.model_validate(
        {
            "schema_version": "1.0",
            "sources": [{"id": "s", "type": "url", "uri": "https://example.com/talk"}],
            "outputs": [
                {"id": "a", "type": "audio", "options": {"format": "mp3"}},
                {"id": "t", "type": "transcript"},
                {
                    "id": "sy",
                    "type": "synced_audio",
                    "from_outputs": ["a", "t"],
                    **output,
                },
            ],
        }
    )


def test_pairing_two_outputs_is_a_well_formed_request():
    """The only output type here that consumes two upstream outputs. The arity
    rules bind `single`-scope media types and the four one-input derivations;
    this is neither, and the pairing is the product."""
    assert validate_structure(_request()).valid
