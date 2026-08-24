"""Reading text aloud: the contract, the planning, and the one call that leaves.

`speech` is a new output type whose input is the same thing a PDF's is —
readable prose — so most of what is tested here is that it behaves like its
sibling rather than like a new special case.

Nothing in this file touches the network. The synthesiser is a boundary, and
the point of a boundary is that everything around it can be tested without it.
"""

from __future__ import annotations

import json

import pytest

from content.domain.request import GenerationRequest
from content.domain.validation import validate_structure
from content.providers.base import Material
from content.providers.edge_speech import (
    DEFAULT_VOICES,
    FALLBACK_VOICE,
    EdgeSpeechRunner,
    rate_for,
    text_from_material,
    voice_for,
)


def _request(**output) -> GenerationRequest:
    return GenerationRequest.model_validate(
        {
            "schema_version": "1.0",
            "sources": [{"id": "a", "type": "url", "uri": "https://example.com/talk"}],
            "outputs": [{"id": "sp", "type": "speech", **output}],
        }
    )


# --- the public contract ---------------------------------------------------------


def test_speech_is_a_first_class_output_type():
    request = _request()
    assert validate_structure(request).valid
    assert request.outputs[0].options.format == "mp3"
    assert request.outputs[0].options.speed == 1.0


def test_an_unusable_speaking_rate_is_refused_by_the_schema():
    """Past these bounds a synthesiser stops being intelligible. A client
    asking for 10x wants an error, not forty minutes of noise."""
    with pytest.raises(Exception):
        _request(options={"speed": 10})


@pytest.mark.parametrize("fmt", ["wav", "opus"])
def test_an_unimplemented_format_is_refused_by_name(fmt):
    """The service answers mp3; anything else is a transcode nobody has wired.

    Declared in the contract because the intent is real, refused because
    returning an mp3 labelled `wav` is the promise D-01 forbids — the client
    gets a file, the file plays, and it is not what was asked for.
    """
    result = validate_structure(_request(options={"format": fmt}))
    assert not result.valid
    assert [e.code for e in result.errors] == ["option_not_supported"]
    assert "mp3" in result.errors[0].message


# --- choosing a voice and a rate --------------------------------------------------


def test_the_language_picks_a_voice_when_none_is_named():
    assert voice_for("fr", "") == DEFAULT_VOICES["fr"]
    assert voice_for("fr-FR", "") == DEFAULT_VOICES["fr"]
    assert voice_for("FR_fr", "") == DEFAULT_VOICES["fr"]


def test_an_explicit_voice_always_wins():
    assert voice_for("fr", "en-GB-SoniaNeural") == "en-GB-SoniaNeural"


def test_an_unknown_language_falls_back_rather_than_guessing():
    """Inventing `xx-XX-SomethingNeural` would fail at the service with a
    message nobody can act on. The first draft of the default map invented a
    French voice that does not exist, which is exactly how this goes wrong."""
    assert voice_for("kl", "") == FALLBACK_VOICE
    assert voice_for("", "") == FALLBACK_VOICE


def test_every_default_voice_is_shaped_like_a_real_one():
    """Cannot check existence offline; can check nobody typed a bare name.
    Verified against `edge_tts.list_voices()` when the map was written."""
    for language, voice in {**DEFAULT_VOICES, "_": FALLBACK_VOICE}.items():
        assert voice.endswith("Neural"), voice
        assert voice.count("-") >= 2, voice


@pytest.mark.parametrize(
    ("speed", "expected"),
    [(1.0, "+0%"), (1.15, "+15%"), (0.5, "-50%"), (2.0, "+100%"), (1.001, "+0%")],
)
def test_the_rate_is_whole_percent(speed, expected):
    """`+14.999%` is rejected outright by the library, so the conversion
    rounds rather than formats."""
    assert rate_for(speed) == expected


# --- reading the material ---------------------------------------------------------


def test_a_canonical_transcript_is_read_without_its_timings(tmp_path):
    """Spoken aloud, the timings are noise."""
    path = tmp_path / "t.json"
    path.write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 0.0, "end": 1.0, "text": "Bonjour."},
                    {"start": 1.0, "end": 2.0, "text": "Ça va ?"},
                ]
            }
        )
    )
    assert text_from_material(Material(path=path, media_type="application/json")) == (
        "Bonjour.\nÇa va ?"
    )


def test_malformed_json_is_read_as_the_text_it_is(tmp_path):
    path = tmp_path / "t.json"
    path.write_text("not json at all")
    assert (
        text_from_material(Material(path=path, media_type="application/json"))
        == "not json at all"
    )


def test_markdown_is_read_verbatim(tmp_path):
    path = tmp_path / "s.md"
    path.write_text("# Titre\n\nUn paragraphe.\n")
    assert text_from_material(Material(path=path, media_type="text/markdown")) == (
        "# Titre\n\nUn paragraphe."
    )


# --- the runner, without the network ----------------------------------------------


class _Ctx:
    def __init__(self, workdir, materials):
        self.workdir = workdir
        self.input_materials = materials
        self.warnings = []
        self.timeout_seconds = 60.0

    def on_progress(self, percent, message):
        pass

    def on_warning(self, code, message, details):
        self.warnings.append((code, message, details))


def _step(**params):
    from content.domain.plan import PlanStep

    return PlanStep(
        id="speak_sp",
        operation="text.speak",
        provider="edge_tts",
        params=params,
        depends_on=[],
    )


def test_empty_material_fails_instead_of_producing_silence(tmp_path):
    """An artifact that plays nothing is the failure nobody notices until they
    listen to it."""
    from content.providers.base import StepExecutionError

    path = tmp_path / "s.md"
    path.write_text("   \n\n ")
    runner = EdgeSpeechRunner()
    if not runner.available():
        pytest.skip("the tts extra is not installed here")
    with pytest.raises(StepExecutionError) as raised:
        runner.execute(
            _step(), _Ctx(tmp_path, [Material(path=path, media_type="text/markdown")])
        )
    assert raised.value.code == "no_input"


def test_a_very_long_text_warns_before_spending_the_minutes(tmp_path, monkeypatch):
    """Same channel as the truncated-summary warning: the step still runs, and
    it says out loud what it is about to cost."""

    path = tmp_path / "s.md"
    path.write_text("a " * 120_000)
    runner = EdgeSpeechRunner()
    monkeypatch.setattr(runner, "available", lambda: True)
    monkeypatch.setattr(
        runner,
        "_synthesise",
        lambda text, voice, rate, target: target.write_bytes(b"x"),
    )
    ctx = _Ctx(tmp_path, [Material(path=path, media_type="text/markdown")])

    produced = runner.execute(_step(voice="fr-FR-DeniseNeural"), ctx)

    assert [w[0] for w in ctx.warnings] == ["long_synthesis"]
    assert produced[0].media_type == "audio/mpeg"


def test_what_was_said_and_how_travels_with_the_artifact(tmp_path, monkeypatch):
    """`voice` is the one thing that cannot be recovered from the file, and it
    is the first thing asked when a recording sounds wrong."""
    path = tmp_path / "s.md"
    path.write_text("Bonjour tout le monde.")
    runner = EdgeSpeechRunner()
    monkeypatch.setattr(runner, "available", lambda: True)
    monkeypatch.setattr(
        runner,
        "_synthesise",
        lambda text, voice, rate, target: target.write_bytes(b"x"),
    )

    produced = runner.execute(
        _step(language="fr", speed=1.2),
        _Ctx(tmp_path, [Material(path=path, media_type="text/markdown")]),
    )

    attributes = produced[0].attributes
    assert attributes["voice"] == DEFAULT_VOICES["fr"]
    assert attributes["rate"] == "+20%"
    assert attributes["language"] == "fr"


def test_a_runner_asked_for_the_wrong_operation_refuses(tmp_path):
    from content.providers.base import StepExecutionError

    runner = EdgeSpeechRunner()
    step = _step()
    step.operation = "text.summarize"
    with pytest.raises(StepExecutionError) as raised:
        runner.execute(step, _Ctx(tmp_path, []))
    assert raised.value.code == "operation_not_supported"


# --- the privacy classification, which is the whole guarantee ---------------------


def test_the_runner_declares_itself_cloud():
    """This value is what makes `allow_cloud_providers: false` refuse it. An
    engine whose selling point is self-hosting must not quietly narrate a
    private transcript to a third party, and the only thing standing between
    those two facts is this attribute."""
    assert EdgeSpeechRunner().location == "cloud"


def test_a_private_policy_rejects_it():
    from content.capabilities.policy import EffectivePolicy

    runner = EdgeSpeechRunner()
    assert not EffectivePolicy(allow_cloud_providers=False).allows_runner(runner)
    assert EffectivePolicy(allow_cloud_providers=True).allows_runner(runner)
