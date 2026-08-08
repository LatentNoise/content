"""Translation building blocks for the ``text.translate`` operation.

Pure helpers shared by the LLM runners (Ollama, cloud): the runner supplies a
``generate(prompt) -> str`` callable; everything else — cue handling, prompt
protocol, response validation, output writing — lives here so every runner
translates identically.

Two input shapes:

- **subtitles** (SRT/VTT): the structure is NEVER handed to the LLM in bulk.
  Cue texts are extracted, sent as a numbered list (chunked), and re-attached
  to their original timing lines — timings survive byte-identical. A count
  mismatch in the model's answer is a hard error, not a silent drop.
- **transcript / plain text**: translated chunk by chunk (paragraph-aligned).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from content.domain.plan import PlanStep
from content.processors.summarize import strip_thinking, transcript_text_from_material
from content.providers.base import (
    ExecutionContext,
    Material,
    ProducedFile,
    StepExecutionError,
)

SUBTITLE_SUFFIXES = (".srt", ".vtt")
_TIMING_RE = re.compile(r"-->")
_NUMBERED_LINE_RE = re.compile(r"^\s*(\d+)\s*[.)]\s*(.*)$")

# Cues per LLM round-trip: large enough to keep context, small enough that a
# numbered-list answer stays reliable.
CUE_CHUNK_SIZE = 40
TEXT_CHUNK_CHARS = 3000


@dataclass
class _Cue:
    """One subtitle cue: the verbatim non-text header lines (index + timing)
    and the text lines to translate."""

    header: list[str]
    text: str


def split_subtitle_cues(raw: str) -> tuple[str, list[_Cue]]:
    """Parse SRT/VTT into (verbatim preamble, cues). The preamble (e.g. the
    WEBVTT header) and every timing line are preserved byte-identical."""
    lines = raw.splitlines()
    preamble: list[str] = []
    index = 0
    # VTT preamble: everything before the first block containing a timing line.
    if lines and lines[0].strip().upper().startswith("WEBVTT"):
        while index < len(lines) and lines[index].strip():
            preamble.append(lines[index])
            index += 1
        while index < len(lines) and not lines[index].strip():
            preamble.append(lines[index])
            index += 1
    cues: list[_Cue] = []
    block: list[str] = []

    def flush(block: list[str]) -> None:
        if not any(_TIMING_RE.search(line) for line in block):
            return
        timing_at = next(i for i, line in enumerate(block) if _TIMING_RE.search(line))
        header = block[: timing_at + 1]
        text = "\n".join(block[timing_at + 1 :]).strip()
        cues.append(_Cue(header=header, text=text))

    for line in lines[index:]:
        if line.strip():
            block.append(line)
        elif block:
            flush(block)
            block = []
    if block:
        flush(block)
    return "\n".join(preamble), cues


def reassemble_subtitles(preamble: str, cues: list[_Cue], texts: list[str]) -> str:
    """Rebuild the subtitle file with translated texts under original timings."""
    blocks = []
    for cue, text in zip(cues, texts):
        blocks.append("\n".join([*cue.header, text.strip()]))
    body = "\n\n".join(blocks) + "\n"
    return (preamble + "\n" + body) if preamble else body


def build_cue_prompt(items: list[str], target: str, source: str) -> str:
    src = f" from {source}" if source and source != "auto" else ""
    numbered = "\n".join(
        f"{i + 1}. {t.replace(chr(10), ' / ')}" for i, t in enumerate(items)
    )
    return (
        f"Translate the following subtitle lines{src} into {target}.\n"
        "Rules: answer with the SAME numbered list, one translation per line, "
        "same count, no extra commentary. Keep ' / ' separators as line breaks. "
        "Preserve meaning and tone; keep names and technical terms.\n\n"
        f"{numbered}"
    )


def parse_numbered_response(response: str, expected: int) -> list[str]:
    """Parse '1. …' lines back; raise ValueError when the count differs (the
    caller turns this into a clean step failure — never a silent drop)."""
    found: dict[int, str] = {}
    for line in strip_thinking(response).splitlines():
        match = _NUMBERED_LINE_RE.match(line)
        if match:
            found[int(match.group(1))] = match.group(2).strip()
    if len(found) != expected or set(found) != set(range(1, expected + 1)):
        raise ValueError(
            f"translation answer has {len(found)} items, expected {expected}"
        )
    return [found[i].replace(" / ", "\n") for i in range(1, expected + 1)]


def translate_cue_texts(texts, generate, target: str, source: str) -> list[str]:
    """Translate cue texts in chunks via the numbered-list protocol. Empty cues
    pass through untouched."""
    out: list[str] = list(texts)
    todo = [i for i, t in enumerate(texts) if t.strip()]
    for start in range(0, len(todo), CUE_CHUNK_SIZE):
        chunk = todo[start : start + CUE_CHUNK_SIZE]
        prompt = build_cue_prompt([texts[i] for i in chunk], target, source)
        translated = parse_numbered_response(generate(prompt), len(chunk))
        for position, text in zip(chunk, translated):
            out[position] = text
    return out


def translate_plain_text(text: str, generate, target: str, source: str) -> str:
    """Translate plain text chunk by chunk, split on paragraph boundaries."""
    src = f" from {source}" if source and source != "auto" else ""
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        if current and len(current) + len(paragraph) > TEXT_CHUNK_CHARS:
            chunks.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        chunks.append(current)
    translated = []
    for chunk in chunks:
        prompt = (
            f"Translate the following text{src} into {target}. Answer with the "
            "translation only — no commentary, keep the paragraph structure.\n\n"
            f"{chunk}"
        )
        translated.append(strip_thinking(generate(prompt)).strip())
    return "\n\n".join(translated)


def pick_translatable_material(materials: list[Material]) -> Material | None:
    """Deterministic input choice: subtitles first, then transcript files."""
    ranked = sorted(
        (
            m
            for m in materials
            if m.path.suffix.lower() in (*SUBTITLE_SUFFIXES, ".json", ".txt")
        ),
        key=lambda m: (
            0 if m.path.suffix.lower() in SUBTITLE_SUFFIXES else 1,
            m.path.name,
        ),
    )
    return ranked[0] if ranked else None


def execute_translation(
    step: PlanStep, ctx: ExecutionContext, generate, model: str
) -> list[ProducedFile]:
    """Shared text.translate execution for every LLM runner. ``generate`` is
    the runner's prompt→completion callable (model already bound)."""
    material = pick_translatable_material(ctx.input_materials)
    if material is None:
        raise StepExecutionError(
            "no_input", "No subtitle or transcript material to translate."
        )
    target = step.params.get("target_language", "")
    source = step.params.get("source_language", "auto")
    if not target:
        raise StepExecutionError("provider_error", "No target language given.")

    if material.path.suffix.lower() in SUBTITLE_SUFFIXES:
        raw = material.path.read_text(errors="replace")
        preamble, cues = split_subtitle_cues(raw)
        if not cues:
            raise StepExecutionError(
                "no_input", f"No cues found in '{material.path.name}'."
            )
        try:
            texts = translate_cue_texts(
                [c.text for c in cues], generate, target, source
            )
        except ValueError as exc:
            raise StepExecutionError("provider_error", str(exc)) from exc
        result = reassemble_subtitles(preamble, cues, texts)
        suffix = material.path.suffix.lower()
        path = ctx.workdir / f"translation-{step.id}.{target}{suffix}"
        path.write_text(result)
        media_type = "application/x-subrip" if suffix == ".srt" else "text/vtt"
    else:
        text = transcript_text_from_material(material)
        if not text:
            raise StepExecutionError("no_input", "The transcript is empty.")
        translated = translate_plain_text(text, generate, target, source)
        if not translated.strip():
            raise StepExecutionError(
                "no_output", "The model returned an empty translation."
            )
        path = ctx.workdir / f"translation-{step.id}.{target}.txt"
        path.write_text(translated.strip() + "\n")
        media_type = "text/plain"

    return [
        ProducedFile(
            path=path,
            media_type=media_type,
            attributes={
                "target_language": target,
                "source_language": material.attributes.get("language", source),
                "model": model,
                "derived_from": (
                    "subtitles"
                    if material.path.suffix.lower() in SUBTITLE_SUFFIXES
                    else "transcript"
                ),
            },
        )
    ]
