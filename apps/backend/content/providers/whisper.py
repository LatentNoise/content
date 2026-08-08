"""WhisperProcessor: local speech-to-text runner for ``audio.transcribe``.

Activates the ``transcript.from_audio`` / ``summary.from_audio`` recipe
variants declared since ADR 0013: the moment this runner is installed, sources
without subtitles become transcribable — the catalog does not change, only the
implementation inventory does (R2/R7).

The engine dependency is **optional** (``pip install content-backend[stt]``,
i.e. faster-whisper): ``available()`` probes the installation, and an absent
library simply keeps the variants ``unavailable`` exactly as today. Everything
Whisper-specific stays behind this boundary — the planner and the domain know
only the ``audio.transcribe`` operation.

Model choice: ``CONTENT_WHISPER_MODEL`` (default ``small``). Transcription is
declared non-deterministic in the registry; the model is recorded in the plan
step params and the artifact provenance, like the LLM summarizers.
"""

import importlib.metadata
import importlib.util
import json

from content.domain.plan import PlanStep
from content.providers.base import (
    ExecutionContext,
    Material,
    ProducedFile,
    StepExecutionError,
)

AUDIO_SUFFIXES = (".m4a", ".mp3", ".opus", ".ogg", ".webm", ".wav", ".aac", ".flac")

DEFAULT_MODEL = "small"


class WhisperProcessor:
    name = "whisper"
    location = "local"
    operations = ("audio.transcribe",)

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model or DEFAULT_MODEL
        self.tool_version = ""
        self._available: bool | None = None

    # --- availability (installation capability) --------------------------------

    def available(self) -> bool:
        """The optional STT extra is installed. Probed once — a Python library
        does not appear or vanish mid-process."""
        if self._available is None:
            self._available = importlib.util.find_spec("faster_whisper") is not None
            if self._available:
                try:
                    version = importlib.metadata.version("faster-whisper")
                except importlib.metadata.PackageNotFoundError:
                    version = "?"
                self.tool_version = f"faster-whisper/{version}"
        return self._available

    def resolve_model(self) -> str:
        return self.model

    # --- execution --------------------------------------------------------------

    def execute(self, step: PlanStep, ctx: ExecutionContext) -> list[ProducedFile]:
        if step.operation != "audio.transcribe":
            raise StepExecutionError(
                "operation_not_supported",
                f"Processor '{self.name}' cannot execute '{step.operation}'.",
            )
        material = self._pick_audio_material(ctx.input_materials)
        if material is None:
            raise StepExecutionError(
                "no_input", "No audio material was produced by the dependency step."
            )
        requested = step.params.get("language") or None
        if requested == "auto":
            requested = None

        segments, language = self._transcribe(material, requested)
        if not segments:
            raise StepExecutionError(
                "no_output", f"No speech recognized in '{material.path.name}'."
            )

        transcript = {
            "language": language,
            "duration_seconds": segments[-1]["end"] if segments else 0.0,
            "segment_count": len(segments),
            "segments": segments,
        }
        attributes = {
            "language": language,
            "derived_from": "audio",
            "model": self.model,
        }
        if step.params.get("format") == "text":
            path = ctx.workdir / f"transcript-{step.id}.txt"
            path.write_text("\n".join(s["text"] for s in segments))
            return [
                ProducedFile(path=path, media_type="text/plain", attributes=attributes)
            ]
        path = ctx.workdir / f"transcript-{step.id}.json"
        path.write_text(json.dumps(transcript, indent=2, ensure_ascii=False))
        return [
            ProducedFile(
                path=path, media_type="application/json", attributes=attributes
            )
        ]

    def _transcribe(
        self, material: Material, language: str | None
    ) -> tuple[list[dict], str]:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover — guarded by available()
            raise StepExecutionError(
                "provider_error", "faster-whisper is not installed."
            ) from exc
        try:
            model = WhisperModel(self.model, device="cpu", compute_type="int8")
            raw_segments, info = model.transcribe(str(material.path), language=language)
            segments = [
                {
                    "start": round(float(s.start), 3),
                    "end": round(float(s.end), 3),
                    "text": s.text.strip(),
                }
                for s in raw_segments
                if s.text.strip()
            ]
        except StepExecutionError:
            raise
        except Exception as exc:  # noqa: BLE001 — normalize any backend failure
            raise StepExecutionError(
                "provider_error", f"Speech-to-text failed: {exc}"
            ) from exc
        return segments, language or getattr(info, "language", "") or ""

    @staticmethod
    def _pick_audio_material(materials: list[Material]) -> Material | None:
        """Deterministic choice among audio inputs: media_type first, then the
        known audio suffixes, filename order as tie-break."""
        candidates = [
            m
            for m in materials
            if m.media_type.startswith("audio/")
            or m.path.suffix.lower() in AUDIO_SUFFIXES
        ]
        return min(candidates, key=lambda m: m.path.name) if candidates else None
