"""A real round trip through a real speech service: its text-to-speech speaks a
sentence, and the engine's actual SpeechProcessor transcribes it back.

External, and skipped unless a speech service is running. Start one with the
`speech` compose profile (it publishes the port on 127.0.0.1:18000), then:

    CONTENT_SPEECH_TEST_URL=http://127.0.0.1:18000 \
      pytest -m external tests/test_speech_external.py

Why a round trip rather than a checked-in clip: it proves both halves of the
container the chart bundles in one go, and it needs no fixture a licence would
have to cover. First run on 2026-09-16: 4.65 s of Kokoro speech, transcribed
back word for word in 4.6 s on a laptop CPU, models cold.
"""

import json
import os
import urllib.request

import pytest

from content.domain.plan import PlanStep
from content.providers.base import ExecutionContext, Material
from content.providers.speech import SpeechProcessor

URL = os.getenv("CONTENT_SPEECH_TEST_URL", "")
STT_MODEL = "Systran/faster-whisper-small"
TTS_MODEL = "speaches-ai/Kokoro-82M-v1.0-ONNX"
SENTENCE = "Content turns any video into a transcript, a summary, and now into speech."

pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(not URL, reason="CONTENT_SPEECH_TEST_URL is not set"),
]


def _speak(text: str) -> bytes:
    body = json.dumps(
        {
            "model": TTS_MODEL,
            "input": text,
            "voice": "af_heart",
            "response_format": "wav",
        }
    ).encode()
    request = urllib.request.Request(
        URL.rstrip("/") + "/v1/audio/speech",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read()


def test_the_speech_service_speaks_and_the_engine_transcribes_it_back(
    settings, tmp_path
):
    audio = tmp_path / "spoken.wav"
    audio.write_bytes(_speak(SENTENCE))
    assert audio.read_bytes()[:4] == b"RIFF", "text-to-speech returned no WAV"

    runner = SpeechProcessor(URL, STT_MODEL)
    assert runner.available(), runner.unavailable_reason

    produced = runner.execute(
        PlanStep(
            id="s1",
            operation="audio.transcribe",
            provider="speech",
            params={"format": "json", "language": "en"},
        ),
        ExecutionContext(
            settings=settings,
            workdir=tmp_path,
            stdout_log=tmp_path / "out.log",
            stderr_log=tmp_path / "err.log",
            timeout_seconds=300,
            input_materials=[Material(path=audio, media_type="audio/wav")],
        ),
    )
    data = json.loads(produced[0].path.read_text())
    heard = " ".join(s["text"] for s in data["segments"]).lower()
    for word in ("content", "transcript", "summary", "speech"):
        assert word in heard, f"'{word}' missing from what came back: {heard!r}"
    assert produced[0].attributes["derived_from"] == "audio"
