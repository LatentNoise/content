"""TranscriptProcessor: derive a canonical transcript from subtitle materials.

The first processor (ADR 0005: a processor transforms materials, it accesses
no source). Pure Python — no external tool — so a transcript from existing
subtitles works on every installation. Speech-to-text is a future, optional
runner behind the same ``speech.transcribe`` operation.

The step consumes the subtitle files produced by its dependency (an
acquisition step or a bound `subtitles` output) through
``ctx.input_materials`` and emits the canonical JSON transcript (D8), or its
plain-text derivation when ``format: "text"`` was requested.
"""

import json

from content import __version__
from content.domain.plan import PlanStep
from content.processors.subtitle_parsing import parse_subtitles, segments_to_text
from content.providers.base import (
    ExecutionContext,
    Material,
    ProducedFile,
    StepExecutionError,
)

SUBTITLE_SUFFIXES = (".srt", ".vtt")


class TranscriptProcessor:
    name = "content.transcript"
    tool_version = __version__
    location = "local"
    operations = ("subtitles.to_transcript",)

    def execute(self, step: PlanStep, ctx: ExecutionContext) -> list[ProducedFile]:
        if step.operation != "subtitles.to_transcript":
            raise StepExecutionError(
                "operation_not_supported",
                f"Processor '{self.name}' cannot execute '{step.operation}'.",
            )
        material = self._pick_subtitle_material(
            ctx.input_materials, step.params.get("language")
        )
        if material is None:
            raise StepExecutionError(
                "no_input",
                "No subtitle material was produced by the dependency step.",
            )

        segments = parse_subtitles(material.path.read_text(errors="replace"))
        if not segments:
            raise StepExecutionError(
                "no_output", f"No usable cues found in '{material.path.name}'."
            )

        language = (
            material.attributes.get("language") or step.params.get("language") or ""
        )
        transcript = {
            "language": language,
            "duration_seconds": segments[-1]["end"] if segments else 0.0,
            "segment_count": len(segments),
            "segments": segments,
        }
        attributes = {
            "language": language,
            "derived_from": "subtitles",
            **(
                {"origin": material.attributes["origin"]}
                if material.attributes.get("origin")
                else {}
            ),
        }

        if step.params.get("format") == "text":
            path = ctx.workdir / f"transcript-{step.id}.txt"
            path.write_text(segments_to_text(segments))
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

    @staticmethod
    def _pick_subtitle_material(
        materials: list[Material], language: str | None
    ) -> Material | None:
        """Deterministic choice among subtitle inputs: requested language
        first, manual before automatic, then filename order."""
        candidates = [
            m for m in materials if m.path.suffix.lower() in SUBTITLE_SUFFIXES
        ]

        def rank(material: Material) -> tuple:
            attrs = material.attributes
            return (
                0 if language and attrs.get("language") == language else 1,
                0 if attrs.get("origin") == "manual" else 1,
                material.path.name,
            )

        return min(candidates, key=rank) if candidates else None
