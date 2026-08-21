"""Audio acquisition survives a refused first attempt.

A real job asked one YouTube URL for both video and audio. The video output
succeeded; the audio output — asking for the *same* opus stream seconds later
— was refused with `HTTP Error 403: Forbidden`, and the job failed. Replaying
the identical command minutes later succeeded, so the refusal was transient:
a media URL is signed and short-lived, and every yt-dlp invocation
re-extracts it.

The video path had carried a fallback ladder since the beginning (codec
profiles × player clients) and recovered without anyone noticing. The audio
path had a single shot. These tests hold both to the same standard, with the
`ios`/`web` clients deliberately *last*: they usually cannot serve audio-only
formats at all ("only images are available" without a PO token), so reaching
for them before a plain retry would replace a recoverable blip with a
different failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from content.domain.plan import PlanStep
from content.providers.base import ExecutionContext, StepExecutionError
from content.providers.ytdlp import _AUDIO_ATTEMPTS, YtDlpProvider

YOUTUBE = "https://www.youtube.com/watch?v=pXRviuL6vMY"


def _step(uri: str = YOUTUBE) -> PlanStep:
    return PlanStep(
        id="audio_main",
        operation="media.acquire_audio",
        provider="ytdlp",
        params={"uri": uri, "audio_languages": []},
    )


def _ctx(tmp_path: Path, settings) -> ExecutionContext:
    return ExecutionContext(
        settings=settings,
        workdir=tmp_path,
        stdout_log=tmp_path / "out.log",
        stderr_log=tmp_path / "err.log",
        timeout_seconds=60,
    )


class _Recorder:
    """Stands in for yt-dlp: fails the first `failures` attempts, then writes
    the file the provider looks for."""

    def __init__(
        self, tmp_path: Path, failures: int, error_code: str = "provider_error"
    ):
        self.tmp_path = tmp_path
        self.failures = failures
        self.error_code = error_code
        self.clients: list[str] = []

    def __call__(self, args, ctx):
        # The client is what `--extractor-args youtube:player_client=X` carries.
        client = ""
        for index, value in enumerate(args):
            if value == "--extractor-args" and index + 1 < len(args):
                client = args[index + 1].split("=", 1)[-1]
        self.clients.append(client)
        if len(self.clients) <= self.failures:
            raise StepExecutionError(self.error_code, "yt-dlp exited with code 1.")
        (self.tmp_path / "audio-audio_main.m4a").write_bytes(b"audio")


def test_the_default_client_is_retried_before_any_alternative():
    """The observed failure: a transient refusal of the default client. The
    recovery is the same command again, not a different client."""
    assert _AUDIO_ATTEMPTS[:2] == ("", ""), "the default must be tried twice first"
    assert _AUDIO_ATTEMPTS[2:] == ("ios", "web"), "alternatives come after"


def test_a_transient_refusal_is_retried_and_the_output_survives(
    tmp_path, settings, monkeypatch
):
    provider = YtDlpProvider()
    recorder = _Recorder(tmp_path, failures=1)
    monkeypatch.setattr(
        YtDlpProvider, "_run", lambda self, args, ctx, *_: recorder(args, ctx)
    )
    monkeypatch.setattr(
        YtDlpProvider, "_apply_fast_sponsorblock_cut", lambda self, s, c, p: None
    )

    produced = provider._acquire_audio(_step(), _ctx(tmp_path, settings))

    assert len(produced) == 1
    assert recorder.clients == ["", ""], "retried the default, did not jump to ios"
    assert produced[0].attributes["attempts"] == 2, "provenance records the retry"


def test_alternatives_are_reached_when_the_default_keeps_refusing(
    tmp_path, settings, monkeypatch
):
    provider = YtDlpProvider()
    recorder = _Recorder(tmp_path, failures=2)
    monkeypatch.setattr(
        YtDlpProvider, "_run", lambda self, args, ctx, *_: recorder(args, ctx)
    )
    monkeypatch.setattr(
        YtDlpProvider, "_apply_fast_sponsorblock_cut", lambda self, s, c, p: None
    )

    produced = provider._acquire_audio(_step(), _ctx(tmp_path, settings))

    assert recorder.clients == ["", "", "ios"]
    assert produced[0].attributes["selection"] == "fallback"
    assert produced[0].attributes["player_client"] == "ios"


def test_every_attempt_failing_reports_the_provider_error(
    tmp_path, settings, monkeypatch
):
    provider = YtDlpProvider()
    recorder = _Recorder(tmp_path, failures=99)
    monkeypatch.setattr(
        YtDlpProvider, "_run", lambda self, args, ctx, *_: recorder(args, ctx)
    )

    with pytest.raises(StepExecutionError) as excinfo:
        provider._acquire_audio(_step(), _ctx(tmp_path, settings))

    assert excinfo.value.code == "provider_error", "the real reason, not 'no_output'"
    assert len(recorder.clients) == len(_AUDIO_ATTEMPTS)


def test_cancellation_is_a_decision_not_a_hiccup(tmp_path, settings, monkeypatch):
    """A cancelled or timed-out step must not be retried into oblivion."""
    provider = YtDlpProvider()
    recorder = _Recorder(tmp_path, failures=99, error_code="cancelled")
    monkeypatch.setattr(
        YtDlpProvider, "_run", lambda self, args, ctx, *_: recorder(args, ctx)
    )

    with pytest.raises(StepExecutionError) as excinfo:
        provider._acquire_audio(_step(), _ctx(tmp_path, settings))

    assert excinfo.value.code == "cancelled"
    assert recorder.clients == [""], "stopped at the first attempt"


def test_a_non_youtube_source_gets_one_attempt(tmp_path, settings, monkeypatch):
    """The client rotation is a YouTube remedy; elsewhere the arg means
    nothing and repeating a refused download would only waste time."""
    provider = YtDlpProvider()
    recorder = _Recorder(tmp_path, failures=99)
    monkeypatch.setattr(
        YtDlpProvider, "_run", lambda self, args, ctx, *_: recorder(args, ctx)
    )

    with pytest.raises(StepExecutionError):
        provider._acquire_audio(
            _step("https://example.com/a.mp3"), _ctx(tmp_path, settings)
        )

    assert recorder.clients == [""]
