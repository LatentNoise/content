"""Chapters building blocks: extraction (facts) and derivation (LLM).

Two operations share this module:

- ``chapters.export`` — deterministic: the source-declared chapters (analysis
  facts) travel in the step params and are serialized here. Implemented by the
  pure :class:`ChaptersProcessor` (always available).
- ``chapters.derive`` — non-deterministic: an LLM runner supplies a
  ``generate(prompt) -> str`` callable; the answer is parsed and **strictly
  validated** (schema, increasing bounds, within duration) — an LLM that
  wanders produces a clean step failure, never an invalid artifact.

One canonical artifact shape (JSON ``{"chapters": [{start, end, title}]}``)
plus an ``ffmetadata`` serialization of the same data — two projections, one
truth.
"""

from __future__ import annotations

import json
import re

from content import __version__
from content.domain.plan import PlanStep
from content.processors.summarize import strip_thinking, transcript_text_from_material
from content.providers.base import (
    ExecutionContext,
    ProducedFile,
    StepExecutionError,
)

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)

# Bounds tolerance (seconds): sources routinely declare the last chapter a hair
# past the probed duration.
_DURATION_SLACK = 2.0


def validate_chapters(data: object, duration: float | None) -> list[dict]:
    """Strict validation → canonical [{start, end, title}]. Raises ValueError."""
    if not isinstance(data, list) or not data:
        raise ValueError("chapters must be a non-empty list")
    out: list[dict] = []
    previous_start = -1.0
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"chapter {i} is not an object")
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"chapter {i} has invalid bounds") from exc
        title = str(item.get("title", "")).strip()
        if start < 0 or end <= start:
            raise ValueError(f"chapter {i} bounds are not increasing")
        if start <= previous_start:
            raise ValueError(f"chapter {i} does not start after chapter {i - 1}")
        if duration and end > duration + _DURATION_SLACK:
            raise ValueError(f"chapter {i} ends past the media duration")
        previous_start = start
        out.append({"start": round(start, 3), "end": round(end, 3), "title": title})
    return out


def serialize_chapters(chapters: list[dict], fmt: str) -> tuple[str, str, str]:
    """(content, file suffix, media type) for the canonical data."""
    if fmt == "ffmetadata":
        lines = [";FFMETADATA1"]
        for c in chapters:
            lines += [
                "[CHAPTER]",
                "TIMEBASE=1/1000",
                f"START={int(c['start'] * 1000)}",
                f"END={int(c['end'] * 1000)}",
                f"title={c['title']}",
            ]
        return "\n".join(lines) + "\n", ".ffmetadata.txt", "text/plain"
    payload = {"chapter_count": len(chapters), "chapters": chapters}
    return (
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        ".json",
        "application/json",
    )


def build_chapters_prompt(text: str, duration: float | None) -> str:
    bound = f" The media lasts {duration:.0f} seconds." if duration else ""
    return (
        "Segment the following transcript into chapters.{bound} Answer with a "
        'JSON array ONLY: [{{"start": seconds, "end": seconds, "title": '
        '"..."}}, ...] — increasing, non-overlapping bounds covering the '
        "content; 3 to 12 chapters; concise factual titles in the transcript's "
        "language. No commentary.\n\n{text}"
    ).format(bound=bound, text=text)


def _produced(
    step: PlanStep, ctx: ExecutionContext, chapters: list[dict], attributes: dict
) -> list[ProducedFile]:
    content_text, suffix, media_type = serialize_chapters(
        chapters, step.params.get("format", "json")
    )
    path = ctx.workdir / f"chapters-{step.id}{suffix}"
    path.write_text(content_text)
    return [
        ProducedFile(
            path=path,
            media_type=media_type,
            attributes={"chapter_count": len(chapters), **attributes},
        )
    ]


class ChaptersProcessor:
    """Pure processor for ``chapters.export``: serializes the source-declared
    chapter facts carried by the plan step. No I/O beyond the workdir."""

    name = "content.chapters"
    tool_version = __version__
    location = "local"
    operations = ("chapters.export",)

    def execute(self, step: PlanStep, ctx: ExecutionContext) -> list[ProducedFile]:
        if step.operation != "chapters.export":
            raise StepExecutionError(
                "operation_not_supported",
                f"Processor '{self.name}' cannot execute '{step.operation}'.",
            )
        raw = step.params.get("chapters") or []
        try:
            chapters = validate_chapters(raw, step.params.get("duration"))
        except ValueError as exc:
            raise StepExecutionError(
                "no_input", f"Source chapters are invalid: {exc}"
            ) from exc
        return _produced(step, ctx, chapters, {"derived_from": "source"})


def execute_derive(
    step: PlanStep, ctx: ExecutionContext, generate, model: str
) -> list[ProducedFile]:
    """Shared ``chapters.derive`` execution for the LLM runners."""
    material = next(
        (m for m in ctx.input_materials if m.path.suffix.lower() in (".json", ".txt")),
        None,
    )
    if material is None:
        raise StepExecutionError("no_input", "No transcript material to chapter.")
    text = transcript_text_from_material(material)
    if not text:
        raise StepExecutionError("no_input", "The transcript is empty.")
    duration = step.params.get("duration")

    answer = strip_thinking(generate(build_chapters_prompt(text, duration)))
    match = _JSON_ARRAY_RE.search(answer)
    if match is None:
        raise StepExecutionError(
            "provider_error", "The model did not answer with a JSON array."
        )
    try:
        chapters = validate_chapters(json.loads(match.group(0)), duration)
    except (json.JSONDecodeError, ValueError) as exc:
        raise StepExecutionError(
            "provider_error", f"The model's chapters are invalid: {exc}"
        ) from exc
    return _produced(
        step, ctx, chapters, {"derived_from": "transcript", "model": model}
    )
