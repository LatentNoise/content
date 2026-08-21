"""Shared fixtures: hermetic settings, store, fake provider, app factory.

The fake provider implements the same interface as YtDlpProvider but produces
files locally — no network, no external tools — so the whole pipeline
(contract → analysis → plan → job → artifacts → API) is exercised for real.
"""

import ipaddress
import json
import socket

import pytest

from content.config import ContentSettings
from content.domain.analysis import (
    ChapterFact,
    CollectionEntry,
    MediaFacts,
    NormalizedResource,
    SourceAnalysis,
    SubtitleTrack,
)
from content.domain.plan import PlanStep
from content.domain.request import GenerationRequest, SourceDescriptor, UrlSource
from content.persistence.store import Store
from content.processors.transcript import TranscriptProcessor
from content.providers.base import (
    AnalysisContext,
    ExecutionContext,
    ProducedFile,
    ProviderRegistry,
    StepExecutionError,
)

# --- hermeticity, enforced ------------------------------------------------------
#
# "No network" used to be a convention: the suite asserted it in docstrings and
# was believed because it passed on a machine with egress. It is now checked.
# Every test that is not marked `external` or `release` runs with outbound
# connections and hostname resolution blocked, so a test that quietly reaches
# the Internet fails here instead of only on a locked-down runner.
#
# Loopback stays open (a local HTTP server is not the Internet) and literal IP
# addresses still "resolve" to themselves, since no resolver is involved.

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}


class NetworkBlocked(BaseException):
    """Raised when a hermetic test reaches for the network.

    Deliberately a BaseException: the executor catches `Exception` so a worker
    never dies silently, which would turn this into an ordinary failed job and
    hide the very thing we are trying to detect.
    """


def _is_local(host: object) -> bool:
    text = str(host)
    if text in _LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def _is_hostname(host: object) -> bool:
    """True when resolving `host` would actually query a name server."""
    if str(host) in _LOCAL_HOSTS:  # answered by /etc/hosts
        return False
    try:
        ipaddress.ip_address(str(host))
    except ValueError:
        return True
    return False


@pytest.fixture(autouse=True)
def _hermetic_network(request, monkeypatch):
    if request.node.get_closest_marker("external") or request.node.get_closest_marker(
        "release"
    ):
        yield  # these exist to exercise the real tools
        return

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def _check(address: object) -> None:
        # AF_UNIX addresses are plain paths — local by construction.
        if isinstance(address, tuple) and address and not _is_local(address[0]):
            raise NetworkBlocked(
                f"hermetic test opened a connection to {address!r}; "
                "stub the call, or mark the test `external`"
            )

    def connect(self, address):
        _check(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        _check(address)
        return real_connect_ex(self, address)

    def getaddrinfo(host, *args, **kwargs):
        if _is_hostname(host):
            raise NetworkBlocked(
                f"hermetic test resolved the hostname {host!r}; "
                "stub the resolver, or mark the test `external`"
            )
        return real_getaddrinfo(host, *args, **kwargs)

    # Tagged so test_hermeticity.py can assert the guard is installed here and
    # absent from `external` tests, without going near a real network.
    getaddrinfo.hermetic_guard = True

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    yield


def _language_facts(uri: str) -> dict:
    """The audio-language facts a URI models. Default: a Japanese original with
    an English dub. ``lang-fr`` makes it a French original (so a playlist can
    have members that differ), and ``noorig`` a source that declares no
    original at all — the degradation path of the ``original`` token."""
    if "noorig" in uri:
        return {"audio_languages": ["en"], "original_audio_language": ""}
    if "lang-fr" in uri:
        return {"audio_languages": ["en", "fr"], "original_audio_language": "fr"}
    return {"audio_languages": ["en", "ja"], "original_audio_language": "ja"}


class FakeProvider:
    """A deterministic provider: URLs containing "fail-audio" fail the audio
    step; subtitles produce one artifact per requested language present in
    the fake analysis (en, fr manual; de automatic)."""

    name = "ytdlp"  # planner references providers by stable name
    tool_version = "fake-1.0"
    location = "local"
    operations = (
        "media.acquire_video",
        "media.acquire_audio",
        "media.acquire_thumbnail",
        "media.acquire_subtitles",
        "metadata.export",
    )

    def __init__(self):
        self.executed_operations: list[str] = []  # reuse tests inspect this

    def supports(self, source: SourceDescriptor) -> bool:
        return isinstance(source, UrlSource)

    def resource_key(self, source: SourceDescriptor, ctx: AnalysisContext) -> str:
        assert isinstance(source, UrlSource)
        return f"{self.name}:url:{source.uri}"

    def analyze(self, source: SourceDescriptor, ctx: AnalysisContext) -> SourceAnalysis:
        assert isinstance(source, UrlSource)
        if "playlist" in source.uri:
            # "multilang" models the case ADR 0022 exists for: members whose
            # original audio language genuinely differs from each other.
            entries = (
                [
                    CollectionEntry(
                        id="ja", title="Tokyo talk", url="https://x/lang-ja"
                    ),
                    CollectionEntry(
                        id="fr", title="Paris talk", url="https://x/lang-fr"
                    ),
                ]
                if "multilang" in source.uri
                else [
                    CollectionEntry(id="v1", title="First", url="https://x/v1"),
                    CollectionEntry(id="v2", title="Second", url="https://x/v2"),
                ]
            )
            return SourceAnalysis(
                source_id=source.id,
                resource=NormalizedResource(
                    resource_type="collection",
                    title="Fake playlist",
                    canonical_url=source.uri,
                    detected_provider=self.name,
                ),
                entries=entries,
            )
        return SourceAnalysis(
            source_id=source.id,
            resource=NormalizedResource(
                resource_type="video",
                title="Fake conference",
                channel="Fake Channel",
                published_at="2026-01-15",
                duration_seconds=120.0,
                view_count=1234567,
                like_count=42000,
                canonical_url=source.uri,
                provider_id="fake123",
                thumbnail_url="https://example.com/thumb.jpg",
                detected_provider=self.name,
            ),
            # URIs containing "nosubs" model the STT-relevant world: audio
            # present, no subtitle track at all.
            subtitles=(
                []
                if "nosubs" in source.uri
                else [
                    SubtitleTrack(language="en", origin="manual"),
                    SubtitleTrack(language="fr", origin="manual"),
                    SubtitleTrack(language="de", origin="automatic"),
                ]
            ),
            media=MediaFacts(
                has_video=True,
                has_audio=True,
                video_heights=[360, 720, 1080],
                video_codecs=["h264", "vp9"],
                audio_codecs=["aac", "opus"],
                **_language_facts(source.uri),
            ),
            # URIs containing "chapters" model a source that DECLARES chapters.
            chapters=(
                [
                    ChapterFact(start=0.0, end=40.0, title="Intro"),
                    ChapterFact(start=40.0, end=100.0, title="Main part"),
                    ChapterFact(start=100.0, end=120.0, title="Outro"),
                ]
                if "chapters" in source.uri
                else []
            ),
        )

    def execute(self, step: PlanStep, ctx: ExecutionContext) -> list[ProducedFile]:
        self.executed_operations.append(step.operation)
        uri = step.params.get("uri", "")
        if step.operation == "media.acquire_video":
            if "fail-video" in uri:
                raise StepExecutionError("provider_error", "simulated video failure")
            ext = step.params.get("container") or "mp4"
            path = ctx.workdir / f"video-{step.id}.{ext}"
            path.write_bytes(b"fake-video-bytes")
            return [ProducedFile(path=path, media_type=f"video/{ext}")]
        if step.operation == "media.acquire_audio":
            if "fail-audio" in uri:
                raise StepExecutionError("provider_error", "simulated audio failure")
            path = ctx.workdir / f"audio-{step.id}.m4a"
            path.write_bytes(b"fake-audio-bytes")
            return [ProducedFile(path=path, media_type="audio/mp4")]
        if step.operation == "metadata.export":
            path = ctx.workdir / f"metadata-{step.id}.json"
            path.write_text("{}")
            return [ProducedFile(path=path, media_type="application/json")]
        if step.operation == "media.acquire_thumbnail":
            if "fail-thumbnail" in uri:
                raise StepExecutionError("no_output", "simulated missing thumbnail")
            path = ctx.workdir / f"thumbnail-{step.id}.jpg"
            path.write_bytes(b"fake-jpeg")
            return [ProducedFile(path=path, media_type="image/jpeg")]
        if step.operation == "media.acquire_subtitles":
            if "fail-subs" in uri:
                raise StepExecutionError(
                    "provider_error", "simulated subtitles failure"
                )
            produced = []
            for language in step.params["languages"]:
                if language not in ("en", "fr"):
                    continue
                path = ctx.workdir / f"subs-{step.id}.{language}.srt"
                path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n")
                produced.append(
                    ProducedFile(
                        path=path,
                        media_type="application/x-subrip",
                        attributes={"language": language, "origin": "manual"},
                    )
                )
            return produced
        raise StepExecutionError("operation_not_supported", step.operation)


class FakeFileProvider:
    """Fake counterpart of FfmpegProvider for hermetic file-source planning
    tests: an h264/aac 1080p file with no embedded subtitles."""

    name = "ffmpeg"
    tool_version = "fake-ffmpeg-1.0"
    location = "local"
    operations = (
        "media.acquire_video",
        "media.acquire_audio",
        "media.acquire_thumbnail",
        "media.acquire_subtitles",
        "metadata.export",
        "video.cut",
    )

    def supports(self, source: SourceDescriptor) -> bool:
        return getattr(source, "type", "") == "file"

    def resource_key(self, source: SourceDescriptor, ctx: AnalysisContext) -> str:
        return f"{self.name}:file:{source.path}"

    def analyze(self, source: SourceDescriptor, ctx: AnalysisContext) -> SourceAnalysis:
        return SourceAnalysis(
            source_id=source.id,
            resource=NormalizedResource(
                resource_type="video",
                title="Local file",
                duration_seconds=60.0,
                detected_provider=self.name,
            ),
            media=MediaFacts(
                has_video=True,
                has_audio=True,
                video_heights=[1080],
                video_codecs=["h264"],
                audio_codecs=["aac"],
            ),
        )

    def execute(self, step: PlanStep, ctx: ExecutionContext) -> list[ProducedFile]:
        if step.operation == "media.acquire_video":
            ext = step.params.get("container") or "mp4"
            path = ctx.workdir / f"video-{step.id}.{ext}"
            path.write_bytes(b"fake-file-video")
            return [ProducedFile(path=path, media_type=f"video/{ext}")]
        if step.operation == "video.cut":
            # Read the input (dependency material for a URL, or the file source),
            # write a shorter fake cut.
            src = ctx.input_materials[0].path if ctx.input_materials else None
            ext = src.suffix.lstrip(".") if src else "mp4"
            path = ctx.workdir / f"cut-{step.id}.{ext}"
            path.write_bytes(b"fake-cut")
            return [ProducedFile(path=path, media_type=f"video/{ext}")]
        raise StepExecutionError("operation_not_supported", step.operation)


class FakeSummarizer:
    """Deterministic local LLM stand-in: summarizes by echoing the first
    transcript line; translates by tagging each item with the target language
    (which exercises the REAL cue/protocol logic in processors/translate.py).
    Unavailable when constructed with available=False."""

    name = "fake-llm"
    tool_version = "fake-llm-1.0"
    location = "local"
    operations = ("text.summarize", "text.translate", "chapters.derive")

    def __init__(self, is_available: bool = True):
        self._available = is_available

    def available(self) -> bool:
        return self._available

    def resolve_model(self) -> str:
        return "fake-model"

    @staticmethod
    def _fake_generate(prompt: str) -> str:
        """Deterministic 'translation': numbered-list prompts get the same list
        back with each item tagged; plain-text prompts get a tagged echo."""
        import re

        numbered = [
            m.group(2)
            for m in (
                re.match(r"^\s*(\d+)\.\s(.*)$", line) for line in prompt.splitlines()
            )
            if m
        ]
        if numbered:
            return "\n".join(f"{i + 1}. [T] {text}" for i, text in enumerate(numbered))
        body = prompt.split("\n\n", 1)[1] if "\n\n" in prompt else prompt
        return f"[T] {body}"

    def execute(self, step: PlanStep, ctx: ExecutionContext) -> list[ProducedFile]:
        from content.processors.chapters import execute_derive
        from content.processors.summarize import transcript_text_from_material
        from content.processors.translate import execute_translation

        if step.operation == "text.translate":
            return execute_translation(step, ctx, self._fake_generate, "fake-model")
        if step.operation == "chapters.derive":
            # A compliant "LLM": two valid chapters. Exercises the REAL parse
            # + strict validation in processors/chapters.py.
            def generate(prompt: str) -> str:
                return (
                    '[{"start": 0, "end": 5, "title": "Part 1"},'
                    ' {"start": 5, "end": 9, "title": "Part 2"}]'
                )

            return execute_derive(step, ctx, generate, "fake-model")

        material = next(
            (m for m in ctx.input_materials if m.path.suffix in (".json", ".txt")), None
        )
        if material is None:
            raise StepExecutionError("no_input", "no transcript material")
        first_line = transcript_text_from_material(material).splitlines()[0]
        suffix = (
            ".md" if step.params.get("format", "markdown") == "markdown" else ".txt"
        )
        path = ctx.workdir / f"summary-{step.id}{suffix}"
        path.write_text(f"# Summary\n\n{first_line}\n")
        return [
            ProducedFile(
                path=path,
                media_type="text/markdown" if suffix == ".md" else "text/plain",
                attributes={"model": step.params.get("model", "")},
            )
        ]


class FakeCloudSummarizer(FakeSummarizer):
    """Same, but cloud-located — for privacy-constraint tests."""

    name = "fake-cloud-llm"
    location = "cloud"


class FakeStt:
    """Fake speech-to-text runner (audio.transcribe): emits the canonical
    transcript JSON from any audio material — the hermetic stand-in for the
    optional Whisper processor."""

    name = "whisper"
    tool_version = "fake-whisper-1.0"
    location = "local"
    operations = ("audio.transcribe",)

    def __init__(self, is_available: bool = True):
        self._available = is_available
        self.executed: list[str] = []

    def available(self) -> bool:
        return self._available

    def resolve_model(self) -> str:
        return "fake-stt-model"

    def execute(self, step: PlanStep, ctx: ExecutionContext) -> list[ProducedFile]:
        self.executed.append(step.operation)
        material = next(
            (
                m
                for m in ctx.input_materials
                if m.media_type.startswith("audio/")
                or m.path.suffix in (".m4a", ".opus", ".mp3")
            ),
            None,
        )
        if material is None:
            raise StepExecutionError("no_input", "no audio material")
        transcript = {
            "language": step.params.get("language") or "en",
            "duration_seconds": 2.0,
            "segment_count": 1,
            "segments": [{"start": 0.0, "end": 2.0, "text": "hello from audio"}],
        }
        path = ctx.workdir / f"transcript-{step.id}.json"
        path.write_text(json.dumps(transcript))
        return [
            ProducedFile(
                path=path,
                media_type="application/json",
                attributes={
                    "language": transcript["language"],
                    "derived_from": "audio",
                    "model": step.params.get("model", ""),
                },
            )
        ]


@pytest.fixture
def settings(tmp_path) -> ContentSettings:
    return ContentSettings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "content.db",
        max_concurrent_jobs=1,
        step_timeout_seconds=30,
    )


@pytest.fixture
def store(settings) -> Store:
    return Store(settings.db_path)


@pytest.fixture
def providers(store, settings) -> ProviderRegistry:
    """The installed runners for a test engine.

    The collection orchestrator is attached the same way the API attaches it
    (ADR 0019): it needs the analysis service and the registry it joins, so a
    collection behaves in tests exactly as it does in the running engine.
    """
    from content.analysis.service import AnalysisService
    from content.application.collections import attach_collection_runner

    registry = ProviderRegistry(
        [FakeProvider()],
        processors=[TranscriptProcessor(), FakeSummarizer()],
    )
    attach_collection_runner(
        registry, AnalysisService(store, registry, settings), settings
    )
    return registry


def make_request(payload: dict) -> GenerationRequest:
    return GenerationRequest.model_validate(payload)


def minimal_payload(**overrides) -> dict:
    payload = {
        "schema_version": "1.0",
        "sources": [{"id": "main", "type": "url", "uri": "https://example.com/video"}],
        "outputs": [{"id": "audio_main", "type": "audio"}],
    }
    payload.update(overrides)
    return payload


def resolved_capabilities(entry, providers) -> dict:
    """Public capabilities for one analyzed source, `{capability_id: resolved}`.

    The route the engine really takes since ADR 0013: analysis produces *facts*,
    and the CapabilityResolver crosses them with the transformation registry and
    the installed implementations. `SourceAnalysis.capabilities` /
    `.capability_for()` were removed with that change; tests that still called
    them kept passing for months because `make validate` deselects `-m external`
    (D-33).
    """
    from content.capabilities.facts import facts_from_analysis
    from content.capabilities.policy import EffectivePolicy
    from content.capabilities.resolver import CapabilityResolver
    from content.planning.transformations import build_registry

    resolver = CapabilityResolver(build_registry(providers), providers)
    resolved = resolver.resolve(facts_from_analysis(entry), EffectivePolicy())
    return {capability.id: capability for capability in resolved}
