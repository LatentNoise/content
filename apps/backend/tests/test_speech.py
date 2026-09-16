"""The speech service runner: transcription next to the engine, never elsewhere.

Three things are worth pinning here, and none of them is "the HTTP call works"
— the external test covers that against a real server. What these pin is the
behaviour a green run would not reveal: that audio is never sent to a public
host, that a service still downloading its model reads as unavailable rather
than broken, and that a configured service wins over a local install for a
reason that is only a spelling.
"""

import email.parser
import email.policy
import io
import json
import urllib.error

import pytest

from content.domain.plan import PlanStep
from content.providers import speech as speech_module
from content.providers.base import (
    ExecutionContext,
    Material,
    ProviderRegistry,
    StepExecutionError,
)
from content.providers.speech import SpeechProcessor, host_is_private
from content.providers.whisper import WhisperProcessor

PRIVATE_URL = "http://content-speech:8000"
MODEL = "Systran/faster-whisper-small"


@pytest.fixture
def resolver(monkeypatch):
    """Answer for the resolver: hostname -> addresses. Unknown names fail."""
    table: dict[str, list[str]] = {"content-speech": ["10.43.12.7"]}

    def fake_resolve(host):
        if host in table:
            return table[host]
        try:
            import ipaddress

            ipaddress.ip_address(host)
            return [host]
        except ValueError as exc:
            raise OSError(f"cannot resolve {host}") from exc

    monkeypatch.setattr(speech_module, "_resolve", fake_resolve)
    return table


@pytest.fixture
def http(monkeypatch):
    """Replace the HTTP layer, keeping every layer above it real."""
    calls: list[dict] = []
    routes: dict[str, object] = {}

    class _Resp:
        def __init__(self, payload):
            self._body = json.dumps(payload).encode()

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        calls.append(
            {
                "url": url,
                "method": request.get_method(),
                "headers": {k.lower(): v for k, v in request.header_items()},
                "data": request.data,
            }
        )
        for suffix, outcome in routes.items():
            if url.endswith(suffix):
                if isinstance(outcome, Exception):
                    raise outcome
                return _Resp(outcome)
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(speech_module.urllib.request, "urlopen", fake_urlopen)
    return routes, calls


def _ctx(settings, tmp_path, audio):
    return ExecutionContext(
        settings=settings,
        workdir=tmp_path,
        stdout_log=tmp_path / "out.log",
        stderr_log=tmp_path / "err.log",
        timeout_seconds=60,
        input_materials=[Material(path=audio, media_type="audio/mpeg")],
    )


def _step(**params):
    return PlanStep(
        id="s1", operation="audio.transcribe", provider="speech", params=params
    )


def _form(call) -> dict:
    """Parse the multipart body the runner sent."""
    content_type = call["headers"]["content-type"]
    raw = f"Content-Type: {content_type}\r\n\r\n".encode() + call["data"]
    message = email.parser.BytesParser(policy=email.policy.default).parse(
        io.BytesIO(raw)
    )
    form = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        payload = part.get_payload(decode=True)
        form[name] = (filename, payload) if filename else payload.decode()
    return form


# --- availability ------------------------------------------------------------


def test_not_configured_is_unavailable_and_says_why(http):
    runner = SpeechProcessor("")
    assert not runner.available()
    assert "CONTENT_SPEECH_URL" in runner.unavailable_reason
    assert http[1] == []


def test_a_public_host_is_refused_before_a_single_byte_is_sent(resolver, http):
    """The privacy line. The STT planning path does not apply
    allow_cloud_providers, so this runner must not be the thing that ships
    someone's recording to a third party."""
    resolver["speech.example.com"] = ["93.184.216.34"]
    runner = SpeechProcessor("https://speech.example.com")
    assert not runner.available()
    assert "private network" in runner.unavailable_reason
    assert http[1] == [], "not even the probe may reach a public host"


def test_a_host_resolving_to_public_and_private_addresses_is_not_private(resolver):
    resolver["split-horizon"] = ["10.0.0.5", "93.184.216.34"]
    assert not host_is_private("http://split-horizon:8000")


@pytest.mark.parametrize(
    "url",
    [
        "http://content-speech:8000",  # a cluster Service
        "http://192.168.21.85:18000",  # a box on the LAN
        "http://127.0.0.1:8000",
        "http://[::1]:8000",
    ],
)
def test_private_loopback_and_cluster_hosts_are_private(resolver, url):
    assert host_is_private(url)


def test_an_unresolvable_host_is_not_private(resolver):
    assert not host_is_private("http://nowhere-at-all:8000")


def test_a_silent_service_is_unavailable(resolver, http):
    runner = SpeechProcessor(PRIVATE_URL, MODEL)
    assert not runner.available()
    assert "does not answer" in runner.unavailable_reason


def test_a_service_still_downloading_its_model_is_unavailable_not_broken(
    resolver, http
):
    routes, _ = http
    routes["/v1/models"] = {"data": [{"id": "speaches-ai/Kokoro-82M-v1.0-ONNX"}]}
    runner = SpeechProcessor(PRIVATE_URL, MODEL)
    assert not runner.available()
    assert MODEL in runner.unavailable_reason


def test_available_once_the_model_is_installed_and_the_probe_is_cached(resolver, http):
    routes, calls = http
    routes["/v1/models"] = {"data": [{"id": MODEL}]}
    runner = SpeechProcessor(PRIVATE_URL, MODEL)
    assert runner.available()
    assert runner.available()
    assert len(calls) == 1, "a planning burst must not probe the service each time"
    assert runner.unavailable_reason == ""


def test_the_key_is_sent_on_the_probe_and_on_the_transcription(
    resolver, http, settings, tmp_path
):
    routes, calls = http
    routes["/v1/models"] = {"data": [{"id": MODEL}]}
    routes["/v1/audio/transcriptions"] = {
        "language": "fr",
        "segments": [{"start": 0.0, "end": 1.0, "text": "bonjour"}],
    }
    runner = SpeechProcessor(PRIVATE_URL, MODEL, api_key="sk-local")
    assert runner.available()
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"ID3fake")
    runner.execute(_step(), _ctx(settings, tmp_path, audio))
    assert all(c["headers"]["authorization"] == "Bearer sk-local" for c in calls)


# --- execution ---------------------------------------------------------------


def test_transcribes_through_the_openai_audio_api(resolver, http, settings, tmp_path):
    routes, calls = http
    routes["/v1/audio/transcriptions"] = {
        "task": "transcribe",
        "language": "french",
        "duration": 3.2,
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.4049, "text": " Bonjour à tous."},
            {"id": 1, "start": 1.4, "end": 2.0, "text": "   "},
            {"id": 2, "start": 2.0, "end": 3.2, "text": "Voici Content."},
        ],
    }
    audio = tmp_path / "episode.mp3"
    audio.write_bytes(b"ID3fake-audio-bytes")
    runner = SpeechProcessor(PRIVATE_URL, MODEL)

    produced = runner.execute(_step(language="fr"), _ctx(settings, tmp_path, audio))

    call = calls[0]
    assert call["url"] == "http://content-speech:8000/v1/audio/transcriptions"
    assert call["method"] == "POST"
    form = _form(call)
    assert form["model"] == MODEL
    assert form["response_format"] == "verbose_json"
    assert form["language"] == "fr"
    assert form["file"] == ("episode.mp3", b"ID3fake-audio-bytes")

    data = json.loads(produced[0].path.read_text())
    assert [s["text"] for s in data["segments"]] == [
        "Bonjour à tous.",
        "Voici Content.",
    ]
    assert data["segments"][0]["end"] == 1.405
    assert data["segment_count"] == 2
    assert produced[0].attributes == {
        "language": "fr",
        "derived_from": "audio",
        "model": MODEL,
    }


def test_same_artifact_shape_as_the_local_runner(resolver, http, settings, tmp_path):
    """Two implementations of one operation must produce one artifact: a surface
    reading a field that only one of them writes is the drift this prevents."""
    routes, _ = http
    routes["/v1/audio/transcriptions"] = {
        "language": "en",
        "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}],
    }
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    remote = SpeechProcessor(PRIVATE_URL, MODEL).execute(
        _step(), _ctx(settings, tmp_path, audio)
    )
    remote_keys = set(json.loads(remote[0].path.read_text()))

    local = WhisperProcessor("small")
    local._transcribe = lambda material, language: (
        [{"start": 0.0, "end": 1.0, "text": "hello"}],
        "en",
    )
    (tmp_path / "local").mkdir()
    local_out = local.execute(_step(), _ctx(settings, tmp_path / "local", audio))
    assert remote_keys == set(json.loads(local_out[0].path.read_text()))


def test_auto_language_sends_none_and_keeps_the_detected_one(
    resolver, http, settings, tmp_path
):
    routes, calls = http
    routes["/v1/audio/transcriptions"] = {
        "language": "en",
        "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}],
    }
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"x")
    produced = SpeechProcessor(PRIVATE_URL, MODEL).execute(
        _step(language="auto"), _ctx(settings, tmp_path, audio)
    )
    assert "language" not in _form(calls[0])
    assert produced[0].attributes["language"] == "en"


def test_text_format_writes_plain_text(resolver, http, settings, tmp_path):
    routes, _ = http
    routes["/v1/audio/transcriptions"] = {
        "segments": [
            {"start": 0.0, "end": 1.0, "text": "one"},
            {"start": 1.0, "end": 2.0, "text": "two"},
        ]
    }
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    produced = SpeechProcessor(PRIVATE_URL, MODEL).execute(
        _step(format="text"), _ctx(settings, tmp_path, audio)
    )
    assert produced[0].media_type == "text/plain"
    assert produced[0].path.read_text() == "one\ntwo"


def test_no_speech_is_no_output(resolver, http, settings, tmp_path):
    routes, _ = http
    routes["/v1/audio/transcriptions"] = {"language": "en", "segments": []}
    audio = tmp_path / "silence.mp3"
    audio.write_bytes(b"x")
    with pytest.raises(StepExecutionError) as err:
        SpeechProcessor(PRIVATE_URL, MODEL).execute(
            _step(), _ctx(settings, tmp_path, audio)
        )
    assert err.value.code == "no_output"


def test_a_refusal_carries_the_status_and_what_the_service_said(
    resolver, http, settings, tmp_path
):
    routes, _ = http
    routes["/v1/audio/transcriptions"] = urllib.error.HTTPError(
        PRIVATE_URL, 404, "Not Found", {}, io.BytesIO(b'{"detail":"Model not found"}')
    )
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    with pytest.raises(StepExecutionError) as err:
        SpeechProcessor(PRIVATE_URL, MODEL).execute(
            _step(), _ctx(settings, tmp_path, audio)
        )
    assert err.value.code == "provider_error"
    assert "404" in str(err.value) and "Model not found" in str(err.value)


def test_no_audio_material_is_no_input(resolver, http, settings, tmp_path):
    ctx = _ctx(settings, tmp_path, tmp_path / "x.mp3")
    ctx.input_materials = [
        Material(path=tmp_path / "notes.txt", media_type="text/plain")
    ]
    with pytest.raises(StepExecutionError) as err:
        SpeechProcessor(PRIVATE_URL, MODEL).execute(_step(), ctx)
    assert err.value.code == "no_input"


# --- precedence --------------------------------------------------------------


def test_a_configured_speech_service_wins_over_a_local_whisper(monkeypatch):
    """The planner takes the first available audio.transcribe runner in sorted
    name order. The speech service must win — an address someone typed is never
    overridden — and it does only because "speech" < "whisper". Rename either
    runner and this test is what says the rule broke."""
    from content.planning.planner import _stt_runner

    local = WhisperProcessor("small")
    remote = SpeechProcessor(PRIVATE_URL, MODEL)
    monkeypatch.setattr(local, "available", lambda: True)
    monkeypatch.setattr(remote, "available", lambda: True)
    registry = ProviderRegistry([], processors=[local, remote])

    assert _stt_runner(registry) is remote


def test_the_local_whisper_still_serves_when_no_speech_service_is_set(monkeypatch):
    from content.planning.planner import _stt_runner

    local = WhisperProcessor("small")
    monkeypatch.setattr(local, "available", lambda: True)
    registry = ProviderRegistry([], processors=[local, SpeechProcessor("")])

    assert _stt_runner(registry) is local


# --- configuration -----------------------------------------------------------


def test_settings_read_the_speech_service_from_the_environment(monkeypatch, tmp_path):
    from content.config import settings_from_env

    monkeypatch.setenv("CONTENT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTENT_SPEECH_URL", " http://content-speech:8000 ")
    monkeypatch.setenv("CONTENT_SPEECH_API_KEY", "sk-local")
    monkeypatch.delenv("CONTENT_SPEECH_STT_MODEL", raising=False)
    settings = settings_from_env()
    assert settings.speech_url == "http://content-speech:8000"
    assert settings.speech_api_key == "sk-local"
    assert settings.speech_stt_model == MODEL
