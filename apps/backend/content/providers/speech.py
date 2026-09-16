"""SpeechProcessor: speech-to-text through an OpenAI-compatible speech service.

The other implementation of ``audio.transcribe``. ``whisper.py`` runs
faster-whisper inside the engine, which the published image cannot do: it is
Alpine (musl), ``ctranslate2`` and ``av`` publish no musl wheels, and the
``INSTALL_STT=true`` build ends in ``ResolutionImpossible`` (run on
2026-09-16). So transcription runs *next to* the engine, in a speech service —
speaches by default, bundled by the chart (``speech.enabled``) and by the
``speech`` compose profile — the way Ollama became a service for the language
half on 2026-09-15. The same container also speaks text-to-speech; the engine
does not use that half yet.

The seam is the OpenAI audio API (``POST /v1/audio/transcriptions``, multipart,
``response_format=verbose_json``), not a bespoke one: the same client serves the
bundled service, any other server that speaks it, and later a person's own key.

Precedence. The planner takes the first *available* audio.transcribe runner in
sorted-name order (``planning/planner.py`` ``_stt_runner``). "speech" sorts
before "whisper", so a configured speech service wins over a local install —
an address someone typed is never overridden. ``tests/test_speech.py`` pins it,
because an order that holds by the spelling of two names holds only while
nobody renames one.

Privacy. Audio is personal data, and the speech-to-text planning path does not
apply ``constraints.privacy.allow_cloud_providers`` — it only ever had a local
runner, so it never needed to. This runner therefore serves only endpoints
whose host resolves to private, loopback or link-local addresses: a cluster
Service, a box on the LAN. A public endpoint is reported unavailable, with the
reason, instead of shipping people's recordings to a third party without a
word. Lifting that belongs with teaching the planner the constraint.
"""

import ipaddress
import json
import socket
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit

from content.domain.plan import PlanStep
from content.providers.base import (
    ExecutionContext,
    Material,
    ProducedFile,
    StepExecutionError,
)
from content.providers.whisper import WhisperProcessor, write_transcript

DEFAULT_MODEL = "Systran/faster-whisper-small"

# Same cadence as the Ollama probe: long enough that a planning burst does not
# hammer the service, short enough that a service coming up is noticed quickly.
_AVAILABILITY_TTL_SECONDS = 30.0

_MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".webm": "audio/webm",
}


def _resolve(host: str) -> list[str]:
    """Every address *host* resolves to. A seam of its own so tests can answer
    for the resolver instead of querying a real one."""
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def host_is_private(url: str) -> bool:
    """True when every address the URL's host resolves to is private, loopback
    or link-local. An unresolvable host is not private: it is unknown, and
    unknown is not a place to send someone's audio."""
    host = urlsplit(url).hostname
    if not host:
        return False
    try:
        addresses = [ipaddress.ip_address(a) for a in _resolve(host)]
    except (OSError, ValueError):
        return False
    return bool(addresses) and all(
        a.is_private or a.is_loopback or a.is_link_local for a in addresses
    )


class SpeechProcessor:
    name = "speech"
    location = "local"
    operations = ("audio.transcribe",)

    def __init__(self, base_url: str = "", model: str = "", api_key: str = ""):
        self.base_url = (base_url or "").rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.api_key = api_key
        self.tool_version = ""
        # Why the service is unavailable, in a sentence — read by the admin
        # console, and the difference between "not configured" and "configured
        # but refusing to send audio to a public host".
        self.unavailable_reason = ""
        self._probe_cache: tuple[float, bool] | None = None

    # --- availability (installation capability) --------------------------------

    def available(self) -> bool:
        if not self.base_url:
            self.unavailable_reason = "CONTENT_SPEECH_URL is not set."
            return False
        now = time.monotonic()
        if self._probe_cache and now - self._probe_cache[0] < _AVAILABILITY_TTL_SECONDS:
            return self._probe_cache[1]
        reachable = self._probe()
        self._probe_cache = (now, reachable)
        return reachable

    def _probe(self) -> bool:
        if not host_is_private(self.base_url):
            self.unavailable_reason = (
                f"{self.base_url} is not on a private network; audio is only sent "
                "to a speech service inside the installation."
            )
            return False
        try:
            listing = self._get("/v1/models", timeout=2.0)
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
            self.unavailable_reason = (
                f"The speech service at {self.base_url} does not answer."
            )
            return False
        installed = {
            m.get("id") for m in listing.get("data", []) if isinstance(m, dict)
        }
        if self.model not in installed:
            # Honest while a first start is still downloading the weights: the
            # service answers, the model is not there yet.
            self.unavailable_reason = (
                f"The speech service answers, but the model '{self.model}' is not "
                "installed on it (yet)."
            )
            return False
        self.unavailable_reason = ""
        self.tool_version = "openai-audio-api"
        return True

    def resolve_model(self) -> str:
        return self.model

    # --- execution --------------------------------------------------------------

    def execute(self, step: PlanStep, ctx: ExecutionContext) -> list[ProducedFile]:
        if step.operation != "audio.transcribe":
            raise StepExecutionError(
                "operation_not_supported",
                f"Processor '{self.name}' cannot execute '{step.operation}'.",
            )
        material = WhisperProcessor._pick_audio_material(ctx.input_materials)
        if material is None:
            raise StepExecutionError(
                "no_input", "No audio material was produced by the dependency step."
            )
        requested = step.params.get("language") or None
        if requested == "auto":
            requested = None

        payload = self._transcribe(material, requested, ctx.timeout_seconds)
        segments = [
            {
                "start": round(float(s.get("start", 0.0)), 3),
                "end": round(float(s.get("end", 0.0)), 3),
                "text": str(s.get("text", "")).strip(),
            }
            for s in payload.get("segments") or []
            if str(s.get("text", "")).strip()
        ]
        if not segments:
            raise StepExecutionError(
                "no_output", f"No speech recognized in '{material.path.name}'."
            )
        language = requested or str(payload.get("language") or "")
        return write_transcript(step, ctx, segments, language, self.model)

    # --- HTTP -------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _get(self, route: str, timeout: float) -> dict:
        request = urllib.request.Request(self.base_url + route, headers=self._headers())
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())

    def _transcribe(
        self, material: Material, language: str | None, timeout: float
    ) -> dict:
        fields = {"model": self.model, "response_format": "verbose_json"}
        if language:
            fields["language"] = language
        media_type = (
            material.media_type
            if material.media_type.startswith("audio/")
            else _MEDIA_TYPES.get(
                material.path.suffix.lower(), "application/octet-stream"
            )
        )
        body, content_type = _multipart(
            fields, "file", material.path.name, material.path.read_bytes(), media_type
        )
        request = urllib.request.Request(
            self.base_url + "/v1/audio/transcriptions",
            data=body,
            headers={**self._headers(), "Content-Type": content_type},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise StepExecutionError(
                "provider_error",
                f"The speech service refused the transcription ({exc.code}): {detail}",
            ) from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise StepExecutionError(
                "provider_error", f"The speech service could not be reached: {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise StepExecutionError(
                "provider_error",
                "The speech service answered with something that is not JSON.",
            ) from exc


def _multipart(
    fields: dict[str, str], file_field: str, filename: str, data: bytes, media_type: str
) -> tuple[bytes, str]:
    """A multipart/form-data body, by hand: the engine has no HTTP client
    dependency, and one upload does not justify adding one."""
    boundary = f"content-{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    safe_name = filename.replace('"', "")
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
        f'filename="{safe_name}"\r\nContent-Type: {media_type}\r\n\r\n'.encode()
        + data
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"
