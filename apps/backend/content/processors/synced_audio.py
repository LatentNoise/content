"""Write a timed transcript into an audio file, so a player shows the words.

The materials already exist and already agree: `audio` is the track, and a
canonical `transcript` is a list of `{start, end, text}` taken from the same
recording. Pairing them is the whole feature — and the pairing is what neither
half can recover alone, which is why this is a step rather than an option on
either output.

**Two artifacts, deliberately.** The words go into the MP3 as ID3 `SYLT`, which
is the standard's answer for synchronised lyrics and is read by disappointingly
few players; `USLT` carries the same text unsynchronised, for the many that
show only static lyrics. So an `.lrc` file ships beside the audio as well,
because that is the format phone players actually open. One output, two
artifacts — which the contract has always allowed, and this is what it is for.

Timings are the transcript's own. Nothing is re-aligned, re-cut or guessed at:
if the transcript drifts from the audio, this shows the drift rather than
hiding it, and the fix belongs upstream where the transcript was made.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import shutil
from pathlib import Path

from content.domain.plan import PlanStep
from content.planning import transformations as T
from content.providers.base import (
    ExecutionContext,
    ProducedFile,
    StepExecutionError,
)

# Materials this step reads. A transcript asked for as `.txt` has lost its
# timings, and there is nothing to synchronise — the planner refuses that
# earlier, with the remedy.
TIMED_SUFFIXES = (".json", ".srt", ".vtt")

# ID3 wants a three-letter ISO 639-2 code and silently mangles anything else.
# Only the languages Content's own UI offers are mapped; anything unknown
# becomes `und`, which is the standard's own word for "not stated" and is
# honest in a way that guessing `eng` would not be.
_ISO_639_2 = {
    "fr": "fra",
    "en": "eng",
    "es": "spa",
    "de": "deu",
    "it": "ita",
    "pt": "por",
    "nl": "nld",
}
UNDETERMINED = "und"


def iso_639_2(language: str) -> str:
    """`fr`, `fr-FR`, `FR_fr` -> `fra`; anything unmapped -> `und`."""
    base = (language or "").strip().lower().replace("_", "-").split("-")[0]
    if len(base) == 3 and base.isalpha():
        return base  # already a 639-2 tag
    return _ISO_639_2.get(base, UNDETERMINED)


def segments_from(material_path: Path) -> list[dict]:
    """The timed lines, out of whichever timed format the dependency produced."""
    text = material_path.read_text(errors="replace")
    if material_path.suffix.lower() == ".json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return []
        segments = payload.get("segments") if isinstance(payload, dict) else None
        if not isinstance(segments, list):
            return []
        return [
            {
                "start": float(s.get("start", 0.0)),
                "end": float(s.get("end", 0.0)),
                "text": str(s.get("text", "")).strip(),
            }
            for s in segments
            if isinstance(s, dict) and str(s.get("text", "")).strip()
        ]
    # SRT/VTT: reuse the parser the transcript itself was built with, so the two
    # can never disagree about what a cue is.
    from content.processors.subtitle_parsing import parse_subtitles

    return [s for s in parse_subtitles(text) if s.get("text", "").strip()]


def lrc_timestamp(seconds: float) -> str:
    """`[mm:ss.xx]`, the only shape LRC readers agree on.

    Hundredths, not milliseconds: the format's own resolution, and a player
    meeting three digits there tends to show the tag instead of hiding it.
    """
    seconds = max(0.0, float(seconds))
    minutes, rest = divmod(seconds, 60)
    return f"[{int(minutes):02d}:{rest:05.2f}]"


def build_lrc(segments: list[dict], *, title: str = "", language: str = "") -> str:
    """The sidecar. Header tags first, then one timed line per segment."""
    lines = []
    if title:
        lines.append(f"[ti:{title}]")
    if language and language != UNDETERMINED:
        lines.append(f"[la:{language}]")
    lines.append("[re:Content]")
    lines.extend(f"{lrc_timestamp(s['start'])}{s['text']}" for s in segments)
    return "\n".join(lines) + "\n"


def embed_lyrics(
    audio_path: Path, segments: list[dict], *, language: str, title: str = ""
) -> None:
    """Write SYLT and USLT into the file, in place.

    `format=2` is "absolute time, milliseconds" and `type=1` is "lyrics" — the
    two values that make a SYLT frame mean what this is. Written as ID3v2.3
    rather than 2.4: players that read SYLT at all are old enough that 2.3 is
    the version they read.
    """
    from mutagen.id3 import ID3, SYLT, USLT, Encoding, ID3NoHeaderError

    try:
        tags = ID3(audio_path)
    except ID3NoHeaderError:
        tags = ID3()

    # Replace rather than append: running this twice must not leave a file with
    # two sets of lyrics disagreeing about the same recording.
    tags.delall("SYLT")
    tags.delall("USLT")
    tags.add(
        SYLT(
            encoding=Encoding.UTF8,
            lang=language,
            format=2,
            type=1,
            desc=title,
            text=[(s["text"], int(round(s["start"] * 1000))) for s in segments],
        )
    )
    tags.add(
        USLT(
            encoding=Encoding.UTF8,
            lang=language,
            desc=title,
            text="\n".join(s["text"] for s in segments),
        )
    )
    tags.save(audio_path, v2_version=3)


class SyncedAudioProcessor:
    """StepRunner for `audio.sync_text`."""

    name = "content.synced_audio"
    kind = "processor"
    location = "local"
    operations = (T.AUDIO_SYNC_TEXT,)

    def __init__(self) -> None:
        self.tool_version = ""
        self._available: bool | None = None

    def available(self) -> bool:
        """ID3 writing needs mutagen. Probed once — a library does not appear
        or vanish mid-process."""
        if self._available is None:
            self._available = importlib.util.find_spec("mutagen") is not None
            if self._available:
                try:
                    version = importlib.metadata.version("mutagen")
                except importlib.metadata.PackageNotFoundError:
                    version = "?"
                self.tool_version = f"mutagen/{version}"
        return self._available

    def execute(self, step: PlanStep, ctx: ExecutionContext) -> list[ProducedFile]:
        if step.operation != T.AUDIO_SYNC_TEXT:
            raise StepExecutionError(
                "operation_not_supported",
                f"Processor '{self.name}' cannot execute '{step.operation}'.",
            )
        if not self.available():
            raise StepExecutionError(
                "provider_error",
                "Writing ID3 lyrics needs the optional extra "
                "(pip install 'content-backend[lyrics]').",
            )

        audio, timed = self._pick_materials(ctx.input_materials)
        if audio is None:
            raise StepExecutionError(
                "no_input", "No audio material was produced by the dependency steps."
            )
        if timed is None:
            raise StepExecutionError(
                "no_input",
                "No timed text material was produced by the dependency steps.",
            )

        segments = segments_from(timed.path)
        if not segments:
            # An audio file carrying an empty lyrics frame is worse than one
            # carrying none: the player shows a blank pane and the user thinks
            # the feature is broken rather than the transcript empty.
            raise StepExecutionError(
                "no_input",
                f"'{timed.path.name}' carries no timed lines to synchronise.",
            )

        language = str(step.params.get("language") or "auto")
        if language == "auto":
            language = str(timed.attributes.get("language", "")) or ""
        tag = iso_639_2(language)
        title = str(step.params.get("title") or "")

        target = ctx.workdir / f"{step.id}.mp3"
        shutil.copyfile(audio.path, target)
        ctx.on_progress(40.0, f"Writing {len(segments)} timed lines")
        embed_lyrics(target, segments, language=tag, title=title)

        produced = [
            ProducedFile(
                path=target,
                media_type="audio/mpeg",
                attributes={
                    "synced_lines": str(len(segments)),
                    "language": tag,
                    "lyrics_frames": "SYLT+USLT",
                    "duration_seconds": f"{segments[-1]['end']:.3f}",
                },
            )
        ]
        if step.params.get("lrc_sidecar", True):
            sidecar = ctx.workdir / f"{step.id}.lrc"
            sidecar.write_text(
                build_lrc(segments, title=title, language=tag), encoding="utf-8"
            )
            produced.append(
                ProducedFile(
                    path=sidecar,
                    media_type="application/octet-stream",
                    attributes={"synced_lines": str(len(segments)), "language": tag},
                )
            )
        ctx.on_progress(100.0, "Synchronised")
        return produced

    @staticmethod
    def _pick_materials(materials):
        """The audio and the timed text, whichever order the planner bound them.

        Chosen by suffix rather than by position: the plan lists dependencies in
        declaration order, and a caller writing `from_outputs: [t, a]` means the
        same thing as `[a, t]`.
        """
        audio = timed = None
        for material in materials:
            suffix = material.path.suffix.lower()
            if suffix in TIMED_SUFFIXES and timed is None:
                timed = material
            elif suffix == ".mp3" and audio is None:
                audio = material
        return audio, timed
