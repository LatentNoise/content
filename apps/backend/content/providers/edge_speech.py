"""Read text aloud, using Microsoft Edge's voice service.

`text.speak` is the transformation; this is one implementation of it. The
boundary matters more than the implementation does: a synthesiser is exactly
the kind of thing that gets replaced — a local one, a paid one, a better one —
and nothing outside this file knows which is installed.

**This runner is `location = "cloud"`, and that is not a detail.** It sends the
text to a Microsoft endpoint. That classification is what makes
`constraints.privacy.allow_cloud_providers: false` refuse it, and an engine
whose whole selling point is self-hosting must not quietly narrate a private
transcript to a third party. A local synthesiser would declare `local` and be
chosen ahead of this one under the same policy; none is installed yet.

The dependency is optional (`[tts]`) and probed once, like the speech-to-text
runner: absent package, `available()` is False, and the capability resolves as
`unavailable` with a reason instead of vanishing.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import json

from content.domain.plan import PlanStep
from content.planning import transformations as T
from content.providers.base import (
    ExecutionContext,
    Material,
    ProducedFile,
    StepExecutionError,
)

# What arrives from a text-bearing dependency. Same list as the PDF renderer's,
# and for the same reason: `.json` is a transcript or chapters output asked for
# in its canonical serialization, and it is still text.
TEXT_SUFFIXES = (".md", ".markdown", ".txt", ".text", ".json")

# A default voice per language, so `voice: ""` is a usable request rather than
# an error. Deliberately short: these are the languages Content's own UI offers,
# and anything else falls through to asking the service for its catalogue
# rather than guessing wrong. Neural voices, because the older ones are the
# reason people think synthesis sounds like 2005.
# Verified against `edge_tts.list_voices()` on 2026-08-25 rather than written
# from memory — the first draft of this map invented `fr-FR-DenisePotentialNeural`,
# which sounds exactly like a real voice name and is not one.
DEFAULT_VOICES = {
    "fr": "fr-FR-DeniseNeural",
    "en": "en-US-AriaNeural",
    "es": "es-ES-ElviraNeural",
    "de": "de-DE-KatjaNeural",
    "it": "it-IT-ElsaNeural",
    "pt": "pt-BR-FranciscaNeural",
    "nl": "nl-NL-ColetteNeural",
}
FALLBACK_VOICE = "en-US-AriaNeural"

# The service answers mp3. Anything else would be a transcode, which is
# ffmpeg's job and a step of its own; `reserved.py` refuses the other formats
# by name rather than letting a client discover the silence.
NATIVE_FORMAT = "mp3"

# Long text is split by the library itself, but a synthesiser reading an
# eight-hour transcript is almost never what was meant, and the run costs real
# minutes before anyone finds out. Warn — the same channel the truncated-summary
# warning uses — and carry on: refusing would be worse for the person who did
# mean it.
_LONG_TEXT_CHARS = 200_000


def text_from_material(material: Material) -> str:
    """The words to read, out of whatever the dependency produced.

    A canonical transcript is JSON with timed segments; spoken aloud, the
    timings are noise, so the segment texts are joined and nothing else.
    """
    content = material.path.read_text(errors="replace")
    if material.path.suffix.lower() == ".json":
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return content.strip()
        segments = payload.get("segments") if isinstance(payload, dict) else None
        if isinstance(segments, list):
            return "\n".join(
                str(segment.get("text", ""))
                for segment in segments
                if isinstance(segment, dict)
            ).strip()
        return content.strip()
    return content.strip()


def rate_for(speed: float) -> str:
    """`1.15` -> `"+15%"`, which is the only shape the library accepts.

    Rounded to whole percent: the service ignores finer granularity, and a
    string like `+14.999%` is rejected outright.
    """
    percent = round((float(speed) - 1.0) * 100)
    return f"{percent:+d}%"


def voice_for(language: str, requested: str) -> str:
    """An explicit voice wins; otherwise the language decides."""
    if requested:
        return requested
    base = (language or "").strip().lower().replace("_", "-").split("-")[0]
    return DEFAULT_VOICES.get(base, FALLBACK_VOICE)


class EdgeSpeechRunner:
    name = "edge_tts"
    # See the module docstring. This value is the privacy guarantee.
    location = "cloud"
    operations = (T.TEXT_SPEAK,)

    def __init__(self) -> None:
        self.tool_version = ""
        self._available: bool | None = None

    # --- availability (installation capability) --------------------------------

    def available(self) -> bool:
        """The optional TTS extra is installed. Probed once — a Python library
        does not appear or vanish mid-process."""
        if self._available is None:
            self._available = importlib.util.find_spec("edge_tts") is not None
            if self._available:
                try:
                    version = importlib.metadata.version("edge-tts")
                except importlib.metadata.PackageNotFoundError:
                    version = "?"
                self.tool_version = f"edge-tts/{version}"
        return self._available

    # --- execution --------------------------------------------------------------

    def execute(self, step: PlanStep, ctx: ExecutionContext) -> list[ProducedFile]:
        if step.operation != T.TEXT_SPEAK:
            raise StepExecutionError(
                "operation_not_supported",
                f"Runner '{self.name}' cannot execute '{step.operation}'.",
            )
        if not self.available():
            raise StepExecutionError(
                "provider_error",
                "The text-to-speech extra is not installed "
                "(pip install 'content-backend[tts]').",
            )

        material = self._pick_material(ctx.input_materials)
        if material is None:
            raise StepExecutionError(
                "no_input", "No text material was produced by the dependency step."
            )
        text = text_from_material(material)
        if not text:
            # An empty artifact that plays silence is the failure mode nobody
            # notices until they listen to it.
            raise StepExecutionError("no_input", "The material to read aloud is empty.")
        if len(text) > _LONG_TEXT_CHARS:
            ctx.on_warning(
                "long_synthesis",
                f"Reading {len(text)} characters aloud: this will take a while "
                "and produce a very long file.",
                {"provider": self.name, "characters": len(text)},
            )

        language = str(step.params.get("language") or "auto")
        voice = voice_for(language, str(step.params.get("voice") or ""))
        rate = rate_for(step.params.get("speed", 1.0))
        target = ctx.workdir / f"{step.id}.{NATIVE_FORMAT}"

        ctx.on_progress(5.0, f"Synthesising with {voice}")
        self._synthesise(text, voice, rate, target)
        if not target.exists() or target.stat().st_size == 0:
            raise StepExecutionError(
                "provider_error",
                f"The voice service returned nothing for voice '{voice}'.",
            )
        ctx.on_progress(100.0, "Spoken")

        return [
            ProducedFile(
                path=target,
                media_type="audio/mpeg",
                attributes={
                    "voice": voice,
                    "rate": rate,
                    "language": language,
                    "derived_from": material.path.suffix.lstrip(".") or "text",
                    "characters": str(len(text)),
                },
            )
        ]

    # --- the one call that leaves the machine -----------------------------------

    def _synthesise(self, text: str, voice: str, rate: str, target) -> None:
        import edge_tts

        async def run() -> None:
            await edge_tts.Communicate(text, voice, rate=rate).save(str(target))

        try:
            # A step runs in a worker thread, which has no event loop of its
            # own, so this owns one for the duration of the call.
            asyncio.run(run())
        except Exception as exc:  # noqa: BLE001 — the library raises broadly
            raise StepExecutionError(
                "provider_error",
                f"Speech synthesis failed ({type(exc).__name__}): {exc}",
            ) from exc

    def _pick_material(self, materials: list[Material]) -> Material | None:
        for material in materials:
            if material.path.suffix.lower() in TEXT_SUFFIXES:
                return material
        # The planner only binds this step behind a text-bearing one, so an
        # unexpected suffix is likelier a new text format than a mismatch.
        return materials[0] if materials else None
