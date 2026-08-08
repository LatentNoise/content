"""FfmpegProvider: analysis and extraction for local file sources.

ffprobe analyzes the file; ffmpeg extracts materials. Security first: every
path goes through :func:`check_path_allowed` against the configured roots
(`CONTENT_ALLOWED_INPUT_ROOTS`) before being touched — a server without roots
refuses file sources entirely (docs/contract.md §2).

Operations implement the same abstract verbs as the URL provider (ADR 0005):
``media.acquire_audio`` is a stream copy of the first audio track (no
transcoding — matching ``options.format: "source"``), ``media.acquire_thumbnail``
extracts a representative frame, ``media.acquire_subtitles`` extracts embedded
text subtitle tracks.
"""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from content.domain import errors as codes
from content.domain.analysis import (
    AnalysisError,
    MediaFacts,
    NormalizedResource,
    SourceAnalysis,
    StreamInfo,
    SubtitleTrack,
)
from content.domain.errors import ValidationIssue
from content.domain.plan import PlanStep
from content.domain.request import FileSource, SourceDescriptor
from content.execution.process import run_process
from content.providers.base import (
    AnalysisContext,
    ExecutionContext,
    ProducedFile,
    StepExecutionError,
)
from content.providers.codecs import normalize_audio_codec, normalize_video_codec
from content.providers.ytdlp import media_type_for

# Audio codec -> container that accepts it under stream copy. Unknown codecs
# fall back to Matroska audio, which accepts everything.
_AUDIO_COPY_EXT = {
    "aac": ".m4a",
    "alac": ".m4a",
    "mp3": ".mp3",
    "opus": ".opus",
    "vorbis": ".ogg",
    "flac": ".flac",
    "pcm_s16le": ".wav",
    "pcm_s24le": ".wav",
}
_AUDIO_COPY_FALLBACK_EXT = ".mka"

# Embedded subtitle codecs ffmpeg can convert to srt/vtt (text-based only;
# bitmap tracks like pgs are not extractable this way).
_TEXT_SUBTITLE_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt"}


def _precise_encoders(suffix: str) -> list[str]:
    """Encoder args for the precise (re-encode) cut, following the source
    container so the mux always succeeds: webm only accepts VP8/VP9/AV1 +
    Vorbis/Opus, everything else takes H.264/AAC. Subtitle tracks stay copied
    (text streams cut fine without re-encoding)."""
    if suffix == ".webm":
        return [
            "-c:v",
            "libvpx-vp9",
            "-crf",
            "32",
            "-b:v",
            "0",
            "-c:a",
            "libopus",
            "-c:s",
            "copy",
        ]
    return [
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-preset",
        "medium",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-c:s",
        "copy",
    ]


_CONTAINER_MIME = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
}


def check_path_allowed(path_str: str, allowed_roots: tuple[Path, ...]) -> Path:
    """Resolve *path_str* (symlinks, ..) and require it under an allowed root.

    Raises AnalysisError with a normalized issue — the API must never expose
    arbitrary filesystem access (docs/architecture.md §8).
    """
    if not allowed_roots:
        raise AnalysisError(
            ValidationIssue(
                code=codes.SOURCE_TYPE_NOT_SUPPORTED,
                message=(
                    "File sources are disabled: no allowed input roots are "
                    "configured (CONTENT_ALLOWED_INPUT_ROOTS)."
                ),
            )
        )
    resolved = Path(path_str).resolve()
    if not any(resolved.is_relative_to(root) for root in allowed_roots):
        raise AnalysisError(
            ValidationIssue(
                code=codes.PATH_NOT_ALLOWED,
                message=f"Path '{path_str}' is outside the allowed input roots.",
            )
        )
    if not resolved.is_file():
        raise AnalysisError(
            ValidationIssue(
                code=codes.ANALYSIS_FAILED,
                message=f"Path '{path_str}' does not exist or is not a file.",
            )
        )
    return resolved


def _timestamp_slug(seconds: float) -> str:
    """`00h01m30s500` — sortable, filename-safe, and readable at a glance.

    The artifact is named by the instant it shows because a sheet of frames is
    only useful if you can tell which is which without opening them.
    """
    total = max(seconds, 0.0)
    hours, rest = divmod(int(total), 3600)
    minutes, secs = divmod(rest, 60)
    millis = int(round((total - int(total)) * 1000))
    return f"{hours:02d}h{minutes:02d}m{secs:02d}s{millis:03d}"


class FfmpegProvider:
    name = "ffmpeg"
    # Local media files: no overlap with the URL providers.
    analysis_priority = 20

    location = "local"
    operations = (
        "media.acquire_video",
        "media.acquire_audio",
        "media.acquire_thumbnail",
        "media.acquire_subtitles",
        "metadata.export",
        "video.cut",
        "video.extract_frames",
    )

    def __init__(self, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe"):
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.tool_version = self._detect_version()
        self._image_formats: tuple[str, ...] | None = None

    def _detect_version(self) -> str:
        try:
            result = subprocess.run(
                [self.ffmpeg, "-version"], capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout.splitlines()[0].split("Copyright")[0].strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
        return ""

    # --- analysis --------------------------------------------------------------

    def supports(self, source: SourceDescriptor) -> bool:
        return isinstance(source, FileSource)

    def resource_key(self, source: SourceDescriptor, ctx: AnalysisContext) -> str:
        assert isinstance(source, FileSource)
        resolved = check_path_allowed(source.path, ctx.settings.allowed_input_roots)
        stat = resolved.stat()
        digest = hashlib.sha256(
            f"{resolved}:{stat.st_mtime_ns}:{stat.st_size}:{self.tool_version}".encode()
        ).hexdigest()
        return f"{self.name}:file:{digest}"

    def analyze(self, source: SourceDescriptor, ctx: AnalysisContext) -> SourceAnalysis:
        assert isinstance(source, FileSource)
        resolved = check_path_allowed(source.path, ctx.settings.allowed_input_roots)
        raw = self._probe(resolved, ctx)
        return self._normalize(source.id, resolved, raw)

    def _probe(self, path: Path, ctx: AnalysisContext) -> dict:
        ctx.workdir.mkdir(parents=True, exist_ok=True)
        stdout_log = ctx.workdir / "probe.stdout.log"
        stderr_log = ctx.workdir / "probe.stderr.log"
        result = run_process(
            [
                self.ffprobe,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            cwd=ctx.workdir,
            timeout_seconds=ctx.settings.analysis_timeout_seconds,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
        )
        if not result.ok:
            raise AnalysisError(
                ValidationIssue(
                    code=codes.ANALYSIS_FAILED,
                    message="ffprobe could not analyze the file.",
                    details={"provider": self.name},
                )
            )
        try:
            return json.loads(stdout_log.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise AnalysisError(
                ValidationIssue(
                    code=codes.ANALYSIS_FAILED,
                    message=f"ffprobe returned unreadable metadata: {exc}.",
                    details={"provider": self.name},
                )
            )

    def _normalize(self, source_id: str, path: Path, raw: dict) -> SourceAnalysis:
        raw_streams = raw.get("streams") or []
        fmt = raw.get("format") or {}
        tags = fmt.get("tags") or {}

        streams: list[StreamInfo] = []
        subtitles: list[SubtitleTrack] = []
        for entry in raw_streams:
            codec_type = entry.get("codec_type")
            if codec_type == "video":
                streams.append(
                    StreamInfo(
                        type="video",
                        codec=entry.get("codec_name") or "",
                        width=entry.get("width"),
                        height=entry.get("height"),
                    )
                )
            elif codec_type == "audio":
                streams.append(
                    StreamInfo(
                        type="audio",
                        codec=entry.get("codec_name") or "",
                        language=(entry.get("tags") or {}).get("language") or "",
                    )
                )
            elif codec_type == "subtitle":
                if entry.get("codec_name") in _TEXT_SUBTITLE_CODECS:
                    subtitles.append(
                        SubtitleTrack(
                            language=(entry.get("tags") or {}).get("language") or "und",
                            origin="manual",
                        )
                    )

        has_video = any(s.type == "video" for s in streams)
        has_audio = any(s.type == "audio" for s in streams)
        duration = None
        try:
            duration = float(fmt.get("duration")) if fmt.get("duration") else None
        except (TypeError, ValueError):
            pass

        resource = NormalizedResource(
            resource_type="video"
            if has_video
            else ("audio" if has_audio else "unknown"),
            title=tags.get("title") or path.stem,
            author=tags.get("artist") or "",
            duration_seconds=duration,
            languages=sorted({s.language for s in streams if s.language}),
            mime_type=_CONTAINER_MIME.get(path.suffix.lower(), ""),
            size_bytes=path.stat().st_size,
            canonical_url=path.as_uri(),
            provider_id=str(path),
            detected_provider=self.name,
        )

        media = MediaFacts(
            has_video=has_video,
            has_audio=has_audio,
            video_heights=sorted(
                {s.height for s in streams if s.type == "video" and s.height}
            ),
            video_codecs=sorted(
                {
                    codec
                    for codec in (
                        normalize_video_codec(s.codec)
                        for s in streams
                        if s.type == "video"
                    )
                    if codec
                }
            ),
            audio_codecs=sorted(
                {
                    codec
                    for codec in (
                        normalize_audio_codec(s.codec)
                        for s in streams
                        if s.type == "audio"
                    )
                    if codec
                }
            ),
            audio_languages=sorted(
                {s.language for s in streams if s.type == "audio" and s.language}
            ),
        )

        return SourceAnalysis(
            source_id=source_id,
            resource=resource,
            streams=streams[:50],
            subtitles=subtitles,
            media=media,
        )

    # --- execution -------------------------------------------------------------

    def execute(self, step: PlanStep, ctx: ExecutionContext) -> list[ProducedFile]:
        operations = {
            "media.acquire_video": self._copy_or_remux_video,
            "media.acquire_audio": self._extract_audio,
            "media.acquire_thumbnail": self._extract_thumbnail,
            "media.acquire_subtitles": self._extract_subtitles,
            "metadata.export": self._export_metadata,
            "video.cut": self._cut_video,
            "video.extract_frames": self._extract_frames,
        }
        if step.operation not in operations:
            raise StepExecutionError(
                "operation_not_supported",
                f"Provider '{self.name}' cannot execute '{step.operation}'.",
            )
        return operations[step.operation](step, ctx)

    def _input_path(self, step: PlanStep, ctx: ExecutionContext) -> Path:
        try:
            return check_path_allowed(
                step.params["path"], ctx.settings.allowed_input_roots
            )
        except AnalysisError as exc:
            # The file changed/vanished between planning and execution.
            raise StepExecutionError(exc.issue.code, exc.issue.message)

    def _run(self, args: list[str], ctx: ExecutionContext) -> None:
        result = run_process(
            args,
            cwd=ctx.workdir,
            timeout_seconds=ctx.timeout_seconds,
            stdout_log=ctx.stdout_log,
            stderr_log=ctx.stderr_log,
            cancel_check=ctx.cancel_check,
        )
        if result.cancelled:
            raise StepExecutionError("cancelled", "Step cancelled.")
        if result.timed_out:
            raise StepExecutionError("timeout", "Step timed out.")
        if result.returncode != 0:
            raise StepExecutionError(
                "provider_error",
                f"{self.ffmpeg} exited with code {result.returncode}.",
            )

    def _probe_for_step(self, path: Path, ctx: ExecutionContext) -> dict:
        analysis_ctx = AnalysisContext(settings=ctx.settings, workdir=ctx.workdir)
        return self._probe(path, analysis_ctx)

    def _copy_or_remux_video(
        self, step: PlanStep, ctx: ExecutionContext
    ) -> list[ProducedFile]:
        """Stream-copy the file, changing the container when requested.
        Never re-encodes (transcoding is not implemented in V1)."""
        source = self._input_path(step, ctx)
        container = step.params.get("container")  # None -> keep source container
        target_ext = f".{container}" if container else source.suffix
        target = ctx.workdir / f"video-{step.id}{target_ext}"
        if target_ext == source.suffix:
            shutil.copy2(source, target)
        else:
            # Video + audio streams only; subtitles are their own output type.
            self._run(
                [
                    self.ffmpeg,
                    "-y",
                    "-i",
                    str(source),
                    "-map",
                    "0:v",
                    "-map",
                    "0:a?",
                    "-c",
                    "copy",
                    str(target),
                ],
                ctx,
            )
        if not target.is_file():
            raise StepExecutionError("no_output", "Video remux produced no file.")
        media_type = {
            ".mkv": "video/x-matroska",
            ".webm": "video/webm",
            ".mov": "video/quicktime",
        }.get(target_ext.lower(), media_type_for(target))
        return [ProducedFile(path=target, media_type=media_type)]

    def _cut_video(self, step: PlanStep, ctx: ExecutionContext) -> list[ProducedFile]:
        """Keep only [start, start+duration] of the video.

        Two modes, chosen by the request (params["cut"]["mode"]):

        - ``keyframes`` — stream copy: fast and container-lossless, but the cut
          snaps to the nearest keyframes (bounds are approximate);
        - ``precise`` — re-encode of the segment: frame-accurate bounds at the
          cost of a transcode. The encoder follows the source container (webm
          → VP9/Opus, else H.264/AAC) so the remux always succeeds. V1
          re-encodes the whole segment; smart-cut (re-encode only the edge
          GOPs) is a documented future optimization.

        Source-agnostic: the input is the video material of a dependency (a URL
        that was acquired first) or, for a file source, the input file directly."""
        source = (
            ctx.input_materials[0].path
            if ctx.input_materials
            else self._input_path(step, ctx)
        )
        cut = step.params.get("cut") or {}
        mode = cut.get("mode", "keyframes")
        target = ctx.workdir / f"cut-{step.id}{source.suffix}"
        if mode == "precise":
            # Re-encoding makes the input seek frame-accurate: ffmpeg decodes
            # from the previous keyframe and discards frames before the point.
            codec_args = _precise_encoders(source.suffix.lower())
        else:
            # -ss before -i = fast input seek (snaps to the nearest keyframe,
            # hence "keyframes" mode); -t after -i = duration from that point.
            codec_args = ["-c", "copy"]
        self._run(
            [
                self.ffmpeg,
                "-y",
                "-ss",
                str(cut.get("start", 0)),
                "-i",
                str(source),
                "-t",
                str(cut.get("duration", 0)),
                "-map",
                "0",
                *codec_args,
                "-avoid_negative_ts",
                "make_zero",
                str(target),
            ],
            ctx,
        )
        if not target.is_file():
            raise StepExecutionError("no_output", "Video cut produced no file.")
        media_type = {
            ".mkv": "video/x-matroska",
            ".webm": "video/webm",
            ".mov": "video/quicktime",
        }.get(source.suffix.lower(), media_type_for(target))
        return [
            ProducedFile(
                path=target,
                media_type=media_type,
                attributes={
                    "cut_mode": mode,
                    "start": cut.get("start", 0),
                    "duration": cut.get("duration", 0),
                    # keyframes: bounds are the *requested* ones; the actual cut
                    # snaps to keyframes. precise: bounds are frame-accurate.
                    "bounds": "exact" if mode == "precise" else "keyframe-snapped",
                },
            )
        ]

    def _extract_audio(
        self, step: PlanStep, ctx: ExecutionContext
    ) -> list[ProducedFile]:
        source = self._input_path(step, ctx)
        raw = self._probe_for_step(source, ctx)
        audio_streams = [
            s for s in (raw.get("streams") or []) if s.get("codec_type") == "audio"
        ]
        if not audio_streams:
            raise StepExecutionError("no_output", "The file has no audio track.")
        codec = audio_streams[0].get("codec_name") or ""
        ext = _AUDIO_COPY_EXT.get(codec, _AUDIO_COPY_FALLBACK_EXT)
        target = ctx.workdir / f"audio-{step.id}{ext}"
        self._run(
            [
                self.ffmpeg,
                "-y",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-vn",
                "-sn",
                "-dn",
                "-c:a",
                "copy",
                str(target),
            ],
            ctx,
        )
        if not target.is_file():
            raise StepExecutionError("no_output", "Audio extraction produced no file.")
        return [ProducedFile(path=target, media_type=media_type_for(target))]

    # Still-frame extraction. One routine serves the thumbnail acquisition and
    # the two frame capabilities: the seek/filter/short-clip fallback below is
    # the part that took work to get right, and a second copy of it would drift.
    # (media type, suffix, encoder ffmpeg must have). jpg and png are in every
    # build; webp is not — it needs libwebp compiled in, and asking for it on a
    # build without it fails at execution with nothing useful to say.
    FRAME_FORMATS = {
        "jpg": ("image/jpeg", ".jpg", "mjpeg"),
        "png": ("image/png", ".png", "png"),
        "webp": ("image/webp", ".webp", "libwebp"),
    }
    DEFAULT_THUMBNAIL_SEEK = 3.0

    def image_formats(self) -> tuple[str, ...]:
        """Frame formats this ffmpeg build can really encode.

        Probed rather than declared: webp needs libwebp compiled in and a great
        many builds (Homebrew's among them) ship without it. Advertising a
        format the encoder lacks turns into "No frame could be extracted" at
        execution time, which tells the caller nothing. Probed once — a build
        does not grow encoders while the process runs.
        """
        if self._image_formats is None:
            try:
                result = subprocess.run(  # noqa: S603 - fixed argv, no shell
                    [self.ffmpeg, "-hide_banner", "-encoders"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                listing = result.stdout if result.returncode == 0 else ""
            except (OSError, subprocess.SubprocessError):
                listing = ""
            if not listing:
                # Cannot tell: offer the two formats every build has rather than
                # claiming none and making thumbnails impossible.
                self._image_formats = ("jpg", "png")
            else:
                self._image_formats = tuple(
                    name
                    for name, (_type, _suffix, encoder) in self.FRAME_FORMATS.items()
                    if f" {encoder} " in listing
                )
        return self._image_formats

    def _grab_frame(
        self,
        source: Path,
        target: Path,
        ctx: ExecutionContext,
        *,
        at: float,
        width: int | None = None,
        smart: bool = False,
    ) -> bool:
        """One frame at *at* seconds. Returns False when nothing came out.

        ``smart`` applies ffmpeg's `thumbnail` filter, which picks the most
        representative frame in the window after the seek — right for a poster
        image, wrong for a keyframe sheet where the caller asked for a specific
        instant and expects to get it.
        """
        args = [self.ffmpeg, "-y", "-ss", f"{max(at, 0):.3f}", "-i", str(source)]
        filters = ["thumbnail"] if smart else []
        if width:
            filters.append(f"scale='min(iw,{int(width)})':-2")
        if filters:
            args += ["-vf", ",".join(filters)]
        args += ["-frames:v", "1", "-q:v", "2", str(target)]
        try:
            self._run(args, ctx)
        except StepExecutionError as exc:
            if exc.code != "provider_error":
                raise  # cancellation/timeout must not be retried
            target.unlink(missing_ok=True)
        return target.is_file()

    def _extract_thumbnail(
        self, step: PlanStep, ctx: ExecutionContext
    ) -> list[ProducedFile]:
        source = self._input_path(step, ctx)
        target = ctx.workdir / f"thumbnail-{step.id}.jpg"
        width = step.params.get("max_width")
        at = float(step.params.get("at") or self.DEFAULT_THUMBNAIL_SEEK)
        if not self._grab_frame(source, target, ctx, at=at, width=width, smart=True):
            # Clips shorter than the seek offset: retry from the very start.
            self._grab_frame(source, target, ctx, at=0, width=width, smart=True)
        if not target.is_file():
            raise StepExecutionError("no_output", "No frame could be extracted.")
        return [ProducedFile(path=target, media_type="image/jpeg")]

    def _extract_frames(
        self, step: PlanStep, ctx: ExecutionContext
    ) -> list[ProducedFile]:
        """`video.extract_frames`: one image per requested instant.

        The instants arrive already resolved and bounded by the planner — the
        provider does no arithmetic on durations, so what runs is exactly what
        the plan says (and what /capabilities advertised).
        """
        source = self._materialized_input(step, ctx)
        timestamps = [float(value) for value in step.params.get("timestamps") or [0.0]]
        image_format = str(step.params.get("format") or "jpg").lower()
        if image_format not in self.FRAME_FORMATS:
            raise StepExecutionError(
                "provider_error", f"Unsupported frame format '{image_format}'."
            )
        media_type, suffix, _encoder = self.FRAME_FORMATS[image_format]
        width = step.params.get("width")
        # A specific instant is a request, not a hint: the `thumbnail` filter
        # would silently return a different frame.
        smart = bool(step.params.get("smart"))

        produced: list[ProducedFile] = []
        for index, requested in enumerate(timestamps):
            actual = requested
            target = ctx.workdir / (
                f"frame-{step.id}-{_timestamp_slug(requested)}{suffix}"
            )
            ok = self._grab_frame(
                source, target, ctx, at=requested, width=width, smart=smart
            )
            if not ok and requested > 0:
                # Past the end (a duration that shifted, a bound that rounded
                # up): fall back to the start rather than failing the sheet.
                ok = self._grab_frame(source, target, ctx, at=0, width=width)
                if ok:
                    # Name and record the frame we actually got. Keeping the
                    # requested instant here would label a picture of second 0
                    # as second 999 — provenance has to describe the bytes.
                    actual = 0.0
                    renamed = ctx.workdir / (
                        f"frame-{step.id}-{_timestamp_slug(actual)}{suffix}"
                    )
                    if renamed != target:
                        target.replace(renamed)
                        target = renamed
            if not ok:
                continue
            attributes = {
                "at_seconds": round(actual, 3),
                "index": index,
                "format": image_format,
                **({"width": int(width)} if width else {}),
            }
            if actual != requested:
                attributes["requested_at_seconds"] = round(requested, 3)
                attributes["clamped"] = True
            produced.append(
                ProducedFile(path=target, media_type=media_type, attributes=attributes)
            )
        if not produced:
            raise StepExecutionError("no_output", "No frame could be extracted.")
        return produced

    def _materialized_input(self, step: PlanStep, ctx: ExecutionContext) -> Path:
        """The video to read frames from: whatever a dependency step produced,
        else the file named in the params. This is the whole reason a URL source
        can generate frames — the recipe acquires the video first."""
        for material in ctx.input_materials:
            if material.media_type.startswith(
                "video/"
            ) or material.path.suffix.lower() in (
                ".mp4",
                ".mkv",
                ".webm",
                ".mov",
                ".avi",
                ".m4v",
            ):
                return material.path
        if ctx.input_materials:
            return ctx.input_materials[0].path
        return self._input_path(step, ctx)

    def _extract_subtitles(
        self, step: PlanStep, ctx: ExecutionContext
    ) -> list[ProducedFile]:
        source = self._input_path(step, ctx)
        raw = self._probe_for_step(source, ctx)
        wanted = set(step.params["languages"])
        out_format = step.params.get("format", "srt")
        produced: list[ProducedFile] = []
        subtitle_index = -1
        for entry in raw.get("streams") or []:
            if entry.get("codec_type") != "subtitle":
                continue
            subtitle_index += 1
            if entry.get("codec_name") not in _TEXT_SUBTITLE_CODECS:
                continue
            language = (entry.get("tags") or {}).get("language") or "und"
            if language not in wanted:
                continue
            target = ctx.workdir / f"subs-{step.id}.{language}.{out_format}"
            self._run(
                [
                    self.ffmpeg,
                    "-y",
                    "-i",
                    str(source),
                    "-map",
                    f"0:s:{subtitle_index}",
                    "-c:s",
                    "webvtt" if out_format == "vtt" else "srt",
                    str(target),
                ],
                ctx,
            )
            if target.is_file():
                produced.append(
                    ProducedFile(
                        path=target,
                        media_type=media_type_for(target),
                        attributes={"language": language, "origin": "manual"},
                    )
                )
        return produced  # 0..N by contract (D7)

    def _export_metadata(
        self, step: PlanStep, ctx: ExecutionContext
    ) -> list[ProducedFile]:
        path = ctx.workdir / f"metadata-{step.id}.json"
        path.write_text(
            json.dumps(step.params["resource"], indent=2, ensure_ascii=False)
        )
        return [ProducedFile(path=path, media_type="application/json")]
