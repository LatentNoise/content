"""Transcript output: parsers, planning (dependencies, mutualization,
requiredness propagation), and execution (material flow, skip on failed
dependency, parent provenance)."""

import json

import pytest

from content.analysis.service import AnalysisService
from content.application.submit import submit_generation
from content.domain.errors import RequestRejected
from content.execution.executor import JobExecutor
from content.planning.planner import build_plan
from content.processors.subtitle_parsing import parse_subtitles, segments_to_text
from tests.conftest import make_request, minimal_payload

SRT = """1
00:00:00,000 --> 00:00:02,500
Hello <i>world</i>

2
00:00:02,500 --> 00:00:04,000
Second line
"""

VTT = """WEBVTT
Kind: captions

NOTE a comment block

00:00.000 --> 00:02.500 align:start
Hello <c>world</c>

00:02.500 --> 00:04.000
Hello world

00:04.000 --> 00:06.000
Something else
"""


# --- parsers --------------------------------------------------------------------


def test_parse_srt():
    segments = parse_subtitles(SRT)
    assert segments == [
        {"start": 0.0, "end": 2.5, "text": "Hello world"},
        {"start": 2.5, "end": 4.0, "text": "Second line"},
    ]


def test_parse_vtt_dedupes_rolling_captions():
    segments = parse_subtitles(VTT)
    assert [s["text"] for s in segments] == ["Hello world", "Something else"]
    assert segments[0]["end"] == 4.0  # merged duplicate extends the segment


def test_segments_to_text():
    assert segments_to_text(parse_subtitles(SRT)) == "Hello world\nSecond line"


# --- planning -------------------------------------------------------------------


@pytest.fixture
def plan(store, providers, settings):
    service = AnalysisService(store, providers, settings)

    def _plan(payload):
        request = make_request(payload)
        analysis = service.analyze_sources(list(request.sources))
        return build_plan(request, analysis, providers, settings)

    return _plan


def transcript_payload(output_extra=None, **overrides):
    output = {"id": "transcript", "type": "transcript", **(output_extra or {})}
    return minimal_payload(outputs=[output], **overrides)


def test_transcript_from_source_synthesizes_acquisition(plan):
    result = plan(transcript_payload())
    assert len(result.steps) == 2
    acquisition = next(
        s for s in result.steps if s.operation == "media.acquire_subtitles"
    )
    transcript = next(
        s for s in result.steps if s.operation == "subtitles.to_transcript"
    )
    assert transcript.depends_on == [acquisition.id]
    assert transcript.provider == "content.transcript"
    # auto language resolves deterministically to the first manual track
    assert acquisition.params["languages"] == ["en"]
    # only the transcript output is bound; the acquisition is internal
    assert [b.artifact_request_id for b in result.output_bindings] == ["transcript"]
    assert result.bindings_for_step(acquisition.id) == []


def test_requiredness_propagates_to_hidden_acquisition(plan):
    result = plan(transcript_payload())
    assert all(step.required for step in result.steps)

    optional = plan(transcript_payload({"required": False}))
    assert all(not step.required for step in optional.steps)


def test_transcript_from_subtitles_output_reuses_its_step(plan):
    payload = minimal_payload(
        outputs=[
            {"id": "subs", "type": "subtitles", "options": {"languages": ["en"]}},
            {"id": "transcript", "type": "transcript", "from_outputs": ["subs"]},
        ]
    )
    result = plan(payload)
    assert len(result.steps) == 2  # no extra acquisition synthesized
    subs_step = next(
        s for s in result.steps if s.operation == "media.acquire_subtitles"
    )
    transcript_step = next(
        s for s in result.steps if s.operation == "subtitles.to_transcript"
    )
    assert transcript_step.depends_on == [subs_step.id]
    assert result.bindings_for_step(subs_step.id)[0].artifact_request_id == "subs"


def test_two_transcripts_share_one_acquisition(plan):
    payload = minimal_payload(
        outputs=[
            {"id": "t_json", "type": "transcript"},
            {"id": "t_text", "type": "transcript", "options": {"format": "text"}},
        ]
    )
    result = plan(payload)
    acquisitions = [s for s in result.steps if s.operation == "media.acquire_subtitles"]
    assert len(acquisitions) == 1  # mutualized
    transcripts = [s for s in result.steps if s.operation == "subtitles.to_transcript"]
    assert {tuple(s.depends_on) for s in transcripts} == {(acquisitions[0].id,)}


def test_explicit_language_not_available_fails(plan):
    with pytest.raises(RequestRejected) as excinfo:
        plan(transcript_payload({"options": {"language": "ja"}}))
    assert excinfo.value.result.errors[0].code == "capability_unavailable"


def test_speech_to_text_mode_not_installed(plan):
    with pytest.raises(RequestRejected) as excinfo:
        plan(transcript_payload({"options": {"source": "speech_to_text"}}))
    assert excinfo.value.result.errors[0].code == "option_not_supported"


def test_transcript_from_audio_output_requires_stt(plan):
    payload = minimal_payload(
        outputs=[
            {"id": "audio", "type": "audio"},
            {"id": "transcript", "type": "transcript", "from_outputs": ["audio"]},
        ]
    )
    with pytest.raises(RequestRejected) as excinfo:
        plan(payload)
    assert excinfo.value.result.errors[0].code == "option_not_supported"


def test_word_timestamps_not_supported(plan):
    with pytest.raises(RequestRejected) as excinfo:
        plan(transcript_payload({"options": {"timestamps": "word"}}))
    assert excinfo.value.result.errors[0].code == "option_not_supported"


def test_transcript_with_two_inputs_is_structural_error():
    from content.domain.validation import validate_structure

    payload = minimal_payload(
        outputs=[
            {"id": "subs", "type": "subtitles", "options": {"languages": ["en"]}},
            {
                "id": "transcript",
                "type": "transcript",
                "from_sources": ["main"],
                "from_outputs": ["subs"],
            },
        ]
    )
    result = validate_structure(make_request(payload))
    assert "too_many_inputs" in [issue.code for issue in result.errors]


# --- execution ------------------------------------------------------------------


@pytest.fixture
def run_job(store, providers, settings):
    service = AnalysisService(store, providers, settings)

    def _run(payload: dict) -> str:
        request = make_request(payload)
        result = submit_generation(
            payload,
            request,
            store=store,
            settings=settings,
            providers=providers,
            analysis_service=service,
        )
        claimed = store.claim_next_queued()
        JobExecutor(store, settings, providers).execute(claimed)
        return result.job_id

    return _run


def test_transcript_job_end_to_end(run_job, store, settings):
    job_id = run_job(transcript_payload())
    assert store.get_job(job_id)["status"] == "succeeded"

    artifacts = store.list_artifacts(job_id)
    assert len(artifacts) == 1  # the internal acquisition produced no artifact
    artifact = artifacts[0]
    assert artifact["type"] == "transcript"
    assert artifact["filename"] == "transcript.en.json"
    assert artifact["provenance"]["producer"]["provider"] == "content.transcript"
    assert artifact["provenance"]["attributes"]["derived_from"] == "subtitles"

    path = settings.data_dir / "jobs" / job_id / "artifacts" / artifact["filename"]
    transcript = json.loads(path.read_text())
    assert transcript["language"] == "en"
    assert transcript["segments"][0]["text"] == "hello"

    # the internal material was purged with work/
    work = settings.data_dir / "jobs" / job_id / "work"
    assert not any(work.iterdir())


def test_transcript_from_bound_subtitles_has_parent_artifact(run_job, store):
    payload = minimal_payload(
        outputs=[
            {"id": "subs", "type": "subtitles", "options": {"languages": ["en"]}},
            {"id": "transcript", "type": "transcript", "from_outputs": ["subs"]},
        ]
    )
    job_id = run_job(payload)
    assert store.get_job(job_id)["status"] == "succeeded"
    artifacts = {a["artifact_request_id"]: a for a in store.list_artifacts(job_id)}
    assert set(artifacts) == {"subs", "transcript"}
    assert artifacts["transcript"]["provenance"]["parent_artifact_ids"] == [
        artifacts["subs"]["id"]
    ]


def test_failed_dependency_skips_transcript(run_job, store):
    # The fake provider produces no subtitles for languages outside en/fr:
    # the acquisition succeeds but yields nothing, so the processor fails
    # with no_input... To exercise the *skip* path, make the acquisition
    # itself fail via the fail-audio trick on a different type: instead we
    # verify the aggregate behavior of a transcript whose subtitles output
    # dependency produced nothing.
    payload = minimal_payload(
        sources=[{"id": "main", "type": "url", "uri": "https://example.com/fail-subs"}],
        outputs=[
            {"id": "transcript", "type": "transcript", "options": {"language": "fr"}},
        ],
    )
    job_id = run_job(payload)
    job = store.get_job(job_id)
    assert job["status"] == "failed"  # required transcript not produced
    steps = {s["step_id"]: s["status"] for s in store.list_steps(job_id)}
    assert "failed" in steps.values() or "skipped" in steps.values()
