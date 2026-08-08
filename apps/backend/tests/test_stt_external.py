"""Real speech-to-text through faster-whisper: a short spoken clip (macOS
`say` + ffmpeg) transcribed by the actual WhisperProcessor. External, local-only
— skipped when the optional [stt] extra, `say` or ffmpeg are unavailable."""

import importlib.util
import json
import shutil
import subprocess

import pytest

from content.domain.plan import PlanStep
from content.providers.base import ExecutionContext, Material
from content.providers.whisper import WhisperProcessor

HAVE_WHISPER = importlib.util.find_spec("faster_whisper") is not None
HAVE_SAY = shutil.which("say") is not None
HAVE_FFMPEG = shutil.which("ffmpeg") is not None

pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(not HAVE_WHISPER, reason="faster-whisper not installed"),
    pytest.mark.skipif(
        not (HAVE_SAY and HAVE_FFMPEG), reason="needs macOS `say` + ffmpeg"
    ),
]


def test_real_whisper_transcribes_spoken_audio(settings, tmp_path):
    aiff = tmp_path / "hello.aiff"
    subprocess.run(
        ["say", "-o", str(aiff), "hello world, this is a content engine test"],
        check=True,
    )
    wav = tmp_path / "hello.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(aiff), str(wav)], check=True, capture_output=True
    )

    processor = WhisperProcessor("tiny")
    assert processor.available()
    step = PlanStep(
        id="s1",
        operation="audio.transcribe",
        provider="whisper",
        params={"format": "json", "language": "en"},
    )
    ctx = ExecutionContext(
        settings=settings,
        workdir=tmp_path,
        stdout_log=tmp_path / "out.log",
        stderr_log=tmp_path / "err.log",
        timeout_seconds=300,
        input_materials=[Material(path=wav, media_type="audio/wav")],
    )
    produced = processor.execute(step, ctx)
    data = json.loads(produced[0].path.read_text())
    text = " ".join(s["text"].lower() for s in data["segments"])
    assert "hello" in text
    assert data["segment_count"] >= 1
    assert produced[0].attributes["derived_from"] == "audio"
