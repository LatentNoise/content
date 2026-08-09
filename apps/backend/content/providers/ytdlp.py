"""YtDlpProvider: analysis and media acquisition for URL sources.

Adapts HomeTube's proven yt-dlp behaviors (probe via ``-J``, audio/subtitles/
thumbnail command shapes, progress regexes, error classification) behind the
Content provider interface. Raw yt-dlp JSON never leaves this module as public
metadata — it is normalized into the domain models (contract decision D5).
"""

import hashlib
import ipaddress
import json
import re
import shutil
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from content.domain import errors as codes
from content.domain.analysis import (
    AnalysisError,
    ChapterFact,
    CollectionEntry,
    MediaFacts,
    NormalizedResource,
    SourceAnalysis,
    StreamInfo,
    SubtitleTrack,
)
from content.domain.errors import ValidationIssue
from content.domain.plan import PlanStep
from content.domain.request import SourceDescriptor, UrlSource
from content.execution.process import run_process
from content.providers.base import (
    AnalysisContext,
    ExecutionContext,
    ProducedFile,
    StepExecutionError,
)
from content.providers.codecs import normalize_audio_codec, normalize_video_codec

# Proven progress regexes (HomeTube app/constants.py).
DOWNLOAD_PROGRESS_PATTERN = re.compile(
    r"\[download\]\s+(\d{1,3}\.\d+)%\s+of\s+~?\s*([\d.]+\w+)\s+at\s+"
    r"([\d.]+\w+/s)\s+ETA\s+(\d{2}:\d{2})"
)

# Proven error classifiers (HomeTube app/logs_utils.py, reduced).
_AUTH_PATTERNS = (
    "sign in to confirm",
    "login required",
    "video is private",
    "age restricted",
    "requires authentication",
)

AUDIO_EXTS = (".m4a", ".opus", ".webm", ".mp3", ".ogg", ".aac", ".flac", ".wav")
VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
SUBTITLE_EXTS = (".srt", ".vtt")

# .webm is ambiguous (audio-only or video); video acquisition overrides it.
VIDEO_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}

# yt-dlp format-filter syntax per normalized codec name (provider dialect —
# stays out of the plan, ADR 0005).
_VIDEO_CODEC_FILTERS = {
    "h264": "[vcodec~='^(avc|h264)']",
    "av1": "[vcodec~='^av01']",
    "vp9": "[vcodec~='^vp0?9']",
}
_AUDIO_CODEC_FILTERS = {
    "aac": "[acodec~='^(mp4a|aac)']",
    "opus": "[acodec~='^opus']",
}


# Codec fallback order — best efficiency/quality first (HomeTube priority).
_CODEC_PRIORITY = ("av1", "vp9", "h264")

# YouTube player clients tried in order (HomeTube fallback); "" = default.
# Rotated only for YouTube URLs (the arg is ignored by other extractors).
_PLAYER_CLIENTS = ("", "ios", "web")


def _client_args(client: str) -> list[str]:
    return ["--extractor-args", f"youtube:player_client={client}"] if client else []


def _is_youtube(uri: str) -> bool:
    host = (urlsplit(uri).hostname or "").lower()
    return host == "youtu.be" or host.endswith(("youtube.com", ".youtu.be"))


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    return [x for x in items if not (x in seen or seen.add(x))]


def build_video_profiles(
    selection: dict, available_codecs: list[str] | None = None
) -> list[str]:
    """Ordered list of yt-dlp format selectors, tried in order until one
    downloads (adapts HomeTube's multi-profile logic without exposing format
    ids). A preferred codec comes first; a `require` codec yields a single
    strict profile (no codec downgrade); otherwise remaining codecs follow by
    priority, then a generic best-effort selector (which also matches direct
    combined-stream URLs)."""
    height = selection.get("max_height")
    # `<=?`, not `<=`: yt-dlp drops formats whose height it does not know when
    # the operator is strict, so a *ceiling* turned into a hard requirement that
    # the height be reported at all. A direct media URL (and any extractor that
    # omits height) then matched no profile and the job died with
    # `format_unavailable` — including at HomeTube's permissive 2160 default,
    # which the user reads as "no limit". Unknown height now means "attempt it",
    # while a known height above the ceiling is still excluded.
    hf = f"[height<=?{height}]" if height else ""
    video_pref = selection.get("video_codec")
    audio_pref = selection.get("audio_codec")
    af = (
        _AUDIO_CODEC_FILTERS[audio_pref["value"]]
        if audio_pref and audio_pref.get("available")
        else ""
    )
    available = set(available_codecs or ())
    audio_langs = selection.get("audio_languages") or []

    def audio_sel() -> str:
        # No language pref → best audio. One or more → one `+ba` per language
        # (with --audio-multistreams for several), each honouring the codec
        # filter; falls back to the generic profile if none match.
        if not audio_langs:
            return f"+ba{af}"
        return "".join(f"+ba{af}[language^={lang}]" for lang in audio_langs)

    def profile(video_filter: str) -> str:
        return f"bv*{hf}{video_filter}{audio_sel()}"

    profiles: list[str] = []
    preferred = None
    if video_pref and video_pref.get("available"):
        preferred = video_pref["value"]
        profiles.append(profile(_VIDEO_CODEC_FILTERS[preferred]))
        if video_pref.get("mode") == "require":
            return _dedup(profiles)  # strict — no codec downgrade

    for codec in _CODEC_PRIORITY:
        if codec == preferred or (available and codec not in available):
            continue
        profiles.append(profile(_VIDEO_CODEC_FILTERS[codec]))

    profiles.append(f"bv*{hf}+ba{af}/b{hf}")
    return _dedup(profiles)


_MEDIA_TYPES = {
    ".m4a": "audio/mp4",
    ".opus": "audio/opus",
    ".webm": "audio/webm",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".srt": "application/x-subrip",
    ".vtt": "text/vtt",
    ".json": "application/json",
    ".mp4": "video/mp4",
}


def media_type_for(path: Path) -> str:
    return _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def check_url_allowed(uri: str, allow_private_networks: bool) -> None:
    """SSRF guard on the submitted URL: http(s) only, and no private/loopback
    hosts unless explicitly allowed. Redirect targets are not re-validated in
    V1 (documented limitation, docs/architecture.md §8)."""
    parts = urlsplit(uri)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise AnalysisError(
            ValidationIssue(
                code=codes.URL_NOT_ALLOWED,
                message=f"Only http(s) URLs are accepted, got '{uri}'.",
            )
        )
    if allow_private_networks:
        return
    try:
        infos = socket.getaddrinfo(parts.hostname, None)
    except OSError as exc:
        raise AnalysisError(
            ValidationIssue(
                code=codes.ANALYSIS_FAILED,
                message=f"Cannot resolve host '{parts.hostname}': {exc}.",
            )
        )
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if address.is_private or address.is_loopback or address.is_link_local:
            raise AnalysisError(
                ValidationIssue(
                    code=codes.URL_NOT_ALLOWED,
                    message=(
                        f"Host '{parts.hostname}' resolves to a private address; "
                        "set CONTENT_ALLOW_PRIVATE_NETWORKS=true to allow it."
                    ),
                )
            )


def classify_failure(stderr_text: str) -> str:
    lowered = stderr_text.lower()
    if any(pattern in lowered for pattern in _AUTH_PATTERNS):
        return "authentication_required"
    if "requested format is not available" in lowered:
        return "format_unavailable"
    if "sign in" in lowered and "bot" in lowered:
        return "bot_detection"
    return "provider_error"


def sponsorblock_args(params: dict) -> list[str]:
    """yt-dlp SponsorBlock flags from step params (empty when disabled).

    ``remove`` deletes segments, ``mark`` only records chapters.

    The keyframe flag is what decides whether the result is watchable.
    ``--no-force-keyframes-at-cuts`` stream-copies: fast, but yt-dlp can only
    cut on keyframes, so the frames it was told to discard are spliced back in
    with timestamps that run backwards. Measured on a real 93 s download whose
    sponsor segment sat at the end: 2506 video frames where 2331 fit, 173 of
    them non-monotonic, all crammed into the last 3.2 seconds — the video
    stutters there while the audio, which cuts anywhere, plays on.
    ``--force-keyframes-at-cuts`` re-encodes and produces a clean stream, so
    it is the default (``cut_mode: precise``).
    """
    sb = params.get("sponsorblock")
    if not sb:
        return []
    args: list[str] = []
    remove = sb.get("remove") or []
    mark = sb.get("mark") or []
    if remove:
        keyframes = (
            "--no-force-keyframes-at-cuts"
            if sb.get("cut_mode") == "keyframes"
            else "--force-keyframes-at-cuts"
        )
        args += ["--sponsorblock-remove", ",".join(remove), keyframes]
    if mark:
        args += ["--sponsorblock-mark", ",".join(mark)]
    return args


def embedding_args(params: dict) -> list[str]:
    """yt-dlp post-processing flags that embed extra streams/metadata into the
    output container (metadata, thumbnail, chapters, subtitles)."""
    args: list[str] = []
    if params.get("embed_metadata"):
        args += ["--embed-metadata"]
    if params.get("embed_thumbnail"):
        args += ["--embed-thumbnail", "--convert-thumbnails", "jpg"]
    if params.get("embed_chapters"):
        args += ["--embed-chapters"]
    languages = params.get("embed_subtitles") or []
    if languages:
        args += ["--embed-subs", "--sub-langs", ",".join(languages)]
    return args


def audio_format_args(params: dict) -> list[str]:
    """yt-dlp flags to extract/transcode audio to an explicit container
    (empty when the native stream is kept — ``format: "source"``)."""
    fmt = params.get("audio_format")
    if not fmt:
        return []
    return ["--extract-audio", "--audio-format", fmt]


def extra_args(settings, params: dict | None = None) -> list[str]:
    """Operator-trusted server args (CONTENT_YTDLP_EXTRA_ARGS) plus the request's
    per-source ``provider_args`` escape hatch — appended to a yt-dlp command."""
    server = list(getattr(settings, "ytdlp_extra_args", ()) or ())
    source = list((params or {}).get("provider_args") or [])
    return server + source


def audio_language_selector(languages: list[str] | None) -> str:
    """yt-dlp ``-f`` value for an audio-only download honouring language
    preference: the first available language wins, always falling back to best
    audio so a track is never missed."""
    if not languages:
        return "bestaudio/best"
    preferred = "/".join(f"ba[language^={lang}]" for lang in languages)
    return f"{preferred}/bestaudio/best"


def prepare_cookies(credential_id: str | None, settings, workdir: Path) -> Path | None:
    """Copy the credential's cookie file into *workdir* and return the copy.

    yt-dlp **rewrites** the cookie jar to the ``--cookies`` file on exit, so the
    configured file — which may be a read-only mount — must never be handed to
    it directly. A missing/unconfigured credential yields None (yt-dlp then runs
    without cookies and fails with a classified auth error if needed).
    """
    if not credential_id:
        return None
    src = settings.credentials.get(credential_id)
    if not src or not Path(src).is_file():
        return None
    Path(workdir).mkdir(parents=True, exist_ok=True)
    dest = Path(workdir) / "cookies.txt"
    shutil.copy2(str(src), str(dest))
    return dest


def cookies_args(cookie_file: Path | None) -> list[str]:
    return ["--cookies", str(cookie_file)] if cookie_file else []


class YtDlpProvider:
    name = "ytdlp"
    # A specific media extractor: URLs are offered to it before any generic
    # reader, so a YouTube link can never be claimed by the web-page provider.
    analysis_priority = 10

    location = "local"
    operations = (
        "media.acquire_video",
        "media.acquire_audio",
        "media.acquire_thumbnail",
        "media.acquire_subtitles",
        "metadata.export",
    )

    def __init__(self, binary: str = "yt-dlp"):
        self.binary = binary
        self.tool_version = self._detect_version()

    def _detect_version(self) -> str:
        executable = shutil.which(self.binary)
        if not executable:
            return ""
        try:
            result = subprocess.run(
                [executable, "--version"], capture_output=True, text=True, timeout=10
            )
            return (
                result.stdout.strip().splitlines()[0] if result.returncode == 0 else ""
            )
        except (OSError, subprocess.TimeoutExpired, IndexError):
            return ""

    # --- analysis --------------------------------------------------------------

    def supports(self, source: SourceDescriptor) -> bool:
        return isinstance(source, UrlSource)

    def resource_key(self, source: SourceDescriptor, ctx: AnalysisContext) -> str:
        assert isinstance(source, UrlSource)
        # tool_version in the key (T5): a yt-dlp upgrade invalidates the cache.
        # credential_id in the key: an authenticated probe may reveal different
        # material than an anonymous one, so it must cache separately.
        credential_id = source.auth.credential_id if source.auth else ""
        digest = hashlib.sha256(
            f"{source.uri}:{self.tool_version}:{credential_id or ''}".encode()
        ).hexdigest()
        return f"{self.name}:url:{digest}"

    def analyze(self, source: SourceDescriptor, ctx: AnalysisContext) -> SourceAnalysis:
        assert isinstance(source, UrlSource)
        check_url_allowed(source.uri, ctx.settings.allow_private_networks)
        credential_id = source.auth.credential_id if source.auth else None
        # --flat-playlist lists a playlist's members without probing each one;
        # a single video (nothing to flatten) still comes back with full info.
        raw = self._fetch_info(source.uri, ctx, credential_id)
        if raw.get("_type") == "playlist":
            return self._normalize_collection(source.id, source.uri, raw)
        return self._normalize(source.id, raw)

    def _fetch_info(
        self, uri: str, ctx: AnalysisContext, credential_id: str | None = None
    ) -> dict:
        ctx.workdir.mkdir(parents=True, exist_ok=True)
        stdout_log = ctx.workdir / "analyze.stdout.log"
        stderr_log = ctx.workdir / "analyze.stderr.log"
        cookie_file = prepare_cookies(credential_id, ctx.settings, ctx.workdir)
        args = [
            self.binary,
            "-J",
            "--flat-playlist",
            *cookies_args(cookie_file),
            *extra_args(ctx.settings),
            uri,
        ]
        try:
            result = run_process(
                args,
                cwd=ctx.workdir,
                timeout_seconds=ctx.settings.analysis_timeout_seconds,
                stdout_log=stdout_log,
                stderr_log=stderr_log,
            )
        finally:
            # Don't leave a cookies copy in the persistent analysis cache dir.
            if cookie_file is not None:
                cookie_file.unlink(missing_ok=True)
        if not result.ok:
            stderr_text = stderr_log.read_text() if stderr_log.exists() else ""
            raise AnalysisError(
                ValidationIssue(
                    code=codes.ANALYSIS_FAILED,
                    message="Resource analysis failed.",
                    details={
                        "reason": classify_failure(stderr_text),
                        "provider": self.name,
                    },
                )
            )
        try:
            return json.loads(stdout_log.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise AnalysisError(
                ValidationIssue(
                    code=codes.ANALYSIS_FAILED,
                    message=f"Provider returned unreadable metadata: {exc}.",
                    details={"provider": self.name},
                )
            )

    @staticmethod
    def _stream_status(formats: list[dict], codec_key: str) -> str:
        """Three-valued presence: yt-dlp uses "none" for definitely-absent and
        null for *unknown* (generic/direct-URL extraction). Unknown maps to the
        capability status "unknown" — attempted at runtime, never rejected."""
        states = set()
        for entry in formats:
            codec = entry.get(codec_key)
            if codec and codec != "none":
                states.add("yes")
            elif codec == "none":
                states.add("no")
            else:
                states.add("maybe")
        if "yes" in states:
            return "available"
        if "maybe" in states or not formats:
            return "unknown"
        return "unavailable"

    def _normalize_collection(
        self, source_id: str, uri: str, raw: dict
    ) -> SourceAnalysis:
        """A playlist and its flat member listing → a `collection` resource."""
        entries: list[CollectionEntry] = []
        for item in raw.get("entries") or []:
            if not item:
                continue
            entries.append(
                CollectionEntry(
                    id=str(item.get("id") or ""),
                    title=item.get("title") or "",
                    url=item.get("url") or item.get("webpage_url") or "",
                    uploader=item.get("uploader") or item.get("channel") or "",
                    duration_seconds=item.get("duration"),
                )
            )
        resource = NormalizedResource(
            resource_type="collection",
            title=raw.get("title") or "",
            channel=raw.get("channel") or raw.get("uploader") or "",
            canonical_url=raw.get("webpage_url") or raw.get("original_url") or uri,
            provider_id=str(raw.get("id") or ""),
            detected_provider=self.name,
        )
        return SourceAnalysis(
            source_id=source_id,
            resource=resource,
            entries=entries,
        )

    def _normalize(self, source_id: str, raw: dict) -> SourceAnalysis:
        formats = raw.get("formats") or []
        video_status = self._stream_status(formats, "vcodec")
        audio_status = self._stream_status(formats, "acodec")
        has_audio = audio_status == "available"
        has_video = video_status == "available"

        subtitles = [
            SubtitleTrack(language=lang, origin="manual")
            for lang in sorted(raw.get("subtitles") or {})
            if lang != "live_chat"
        ] + [
            SubtitleTrack(language=lang, origin="automatic")
            for lang in sorted(raw.get("automatic_captions") or {})
        ]

        # Chapters declared by the source — facts, straight from yt-dlp.
        chapters = [
            ChapterFact(
                start=float(c.get("start_time") or 0.0),
                end=float(c.get("end_time") or 0.0),
                title=str(c.get("title") or ""),
            )
            for c in (raw.get("chapters") or [])
            if c.get("end_time") is not None
        ]

        streams: list[StreamInfo] = []
        for entry in formats:
            if entry.get("vcodec") not in (None, "none"):
                streams.append(
                    StreamInfo(
                        type="video",
                        codec=entry.get("vcodec") or "",
                        width=entry.get("width"),
                        height=entry.get("height"),
                    )
                )
            elif entry.get("acodec") not in (None, "none"):
                streams.append(
                    StreamInfo(
                        type="audio",
                        codec=entry.get("acodec") or "",
                        language=entry.get("language") or "",
                    )
                )

        resource = NormalizedResource(
            resource_type="video"
            if has_video
            else ("audio" if has_audio else "unknown"),
            title=raw.get("title") or "",
            description=(raw.get("description") or "")[:2000],
            author=raw.get("uploader") or "",
            channel=raw.get("channel") or raw.get("uploader") or "",
            published_at=_iso_date(raw.get("upload_date") or ""),
            duration_seconds=raw.get("duration"),
            languages=sorted(
                {f.get("language") for f in formats if f.get("language")} - {None}
            ),
            view_count=raw.get("view_count"),
            like_count=raw.get("like_count"),
            thumbnail_url=raw.get("thumbnail") or "",
            canonical_url=raw.get("webpage_url") or raw.get("original_url") or "",
            provider_id=str(raw.get("id") or ""),
            detected_provider=self.name,
        )

        # Available audio-track languages, and the original voice (VO): the
        # track yt-dlp marks "original"/"default" in its format note.
        audio_languages = sorted(
            {
                f.get("language")
                for f in formats
                if f.get("vcodec") in (None, "none")
                and f.get("acodec") not in (None, "none")
                and f.get("language")
            }
        )
        original_audio_language = ""
        for f in formats:
            note = (f.get("format_note") or "").lower()
            if ("original" in note or "default" in note) and f.get("language"):
                original_audio_language = f.get("language")
                break

        media = MediaFacts(
            has_video=has_video,
            has_audio=has_audio,
            video_heights=sorted({f.get("height") for f in formats if f.get("height")}),
            video_codecs=sorted(
                {
                    codec
                    for codec in (
                        normalize_video_codec(f.get("vcodec")) for f in formats
                    )
                    if codec
                }
            ),
            audio_codecs=sorted(
                {
                    codec
                    for codec in (
                        normalize_audio_codec(f.get("acodec")) for f in formats
                    )
                    if codec
                }
            ),
            audio_languages=audio_languages,
            original_audio_language=original_audio_language,
        )

        return SourceAnalysis(
            source_id=source_id,
            resource=resource,
            streams=streams[:50],
            subtitles=subtitles,
            media=media,
            chapters=chapters,
        )

    # --- execution -------------------------------------------------------------

    def execute(self, step: PlanStep, ctx: ExecutionContext) -> list[ProducedFile]:
        operations = {
            "media.acquire_video": self._acquire_video,
            "media.acquire_audio": self._acquire_audio,
            "media.acquire_thumbnail": self._acquire_thumbnail,
            "media.acquire_subtitles": self._acquire_subtitles,
            "metadata.export": self._export_metadata,
        }
        if step.operation not in operations:
            raise StepExecutionError(
                "operation_not_supported",
                f"Provider '{self.name}' cannot execute '{step.operation}'.",
            )
        return operations[step.operation](step, ctx)

    def _run(self, args: list[str], ctx: ExecutionContext) -> None:
        def on_line(line: str) -> None:
            match = DOWNLOAD_PROGRESS_PATTERN.search(line)
            if match:
                ctx.on_progress(float(match.group(1)), "downloading")

        result = run_process(
            args,
            cwd=ctx.workdir,
            timeout_seconds=ctx.timeout_seconds,
            stdout_log=ctx.stdout_log,
            stderr_log=ctx.stderr_log,
            cancel_check=ctx.cancel_check,
            on_line=on_line,
        )
        if result.cancelled:
            raise StepExecutionError("cancelled", "Step cancelled.")
        if result.timed_out:
            raise StepExecutionError("timeout", "Step timed out.")
        if result.returncode != 0:
            stderr_text = ctx.stderr_log.read_text() if ctx.stderr_log.exists() else ""
            raise StepExecutionError(
                classify_failure(stderr_text),
                f"{self.binary} exited with code {result.returncode}.",
            )

    def _acquire_video(
        self, step: PlanStep, ctx: ExecutionContext
    ) -> list[ProducedFile]:
        base = f"video-{step.id}"
        uri = step.params["uri"]
        profiles = build_video_profiles(
            step.params["selection"], step.params.get("available_video_codecs")
        )
        clients = _PLAYER_CLIENTS if _is_youtube(uri) else ("",)
        cookie_file = prepare_cookies(
            step.params.get("credential_id"), ctx.settings, ctx.workdir
        )

        def args_for(selector: str, client: str) -> list[str]:
            args = [
                self.binary,
                "--newline",
                "--no-playlist",
                "-o",
                f"{base}.%(ext)s",
                "--paths",
                f"home:{ctx.workdir}",
                "-f",
                selector,
                "--force-overwrites",
                "--retries",
                "10",
            ]
            if step.params.get("container"):
                args += ["--merge-output-format", step.params["container"]]
            if (
                len((step.params.get("selection") or {}).get("audio_languages") or [])
                > 1
            ):
                args += ["--audio-multistreams"]  # embed several audio tracks
            args += embedding_args(step.params)
            args += _client_args(client)
            args += cookies_args(cookie_file)
            args += sponsorblock_args(step.params)
            args += extra_args(ctx.settings, step.params)
            args.append(uri)
            return args

        # Try each codec profile against each player client until one downloads
        # a file. Cancellation/timeout abort immediately; any other failure
        # moves to the next attempt (HomeTube resilience).
        last_error: StepExecutionError | None = None
        for profile_index, selector in enumerate(profiles):
            for client in clients:
                try:
                    self._run(args_for(selector, client), ctx)
                except StepExecutionError as exc:
                    if exc.code in ("cancelled", "timeout"):
                        raise
                    last_error = exc
                    continue
                produced = _find_by_ext(ctx.workdir, base, VIDEO_EXTS)
                if produced is None:
                    last_error = StepExecutionError(
                        "no_output", "yt-dlp reported success but no video file."
                    )
                    continue
                attributes: dict = {
                    "profile_index": profile_index,
                    "player_client": client or "default",
                }
                if profile_index > 0 or client:
                    attributes["selection"] = "fallback"
                return [
                    ProducedFile(
                        path=produced,
                        media_type=VIDEO_MEDIA_TYPES.get(
                            produced.suffix.lower(), media_type_for(produced)
                        ),
                        attributes=attributes,
                    )
                ]

        raise last_error or StepExecutionError(
            "no_output", "Video acquisition failed for every profile."
        )

    def _acquire_audio(
        self, step: PlanStep, ctx: ExecutionContext
    ) -> list[ProducedFile]:
        base = f"audio-{step.id}"
        cookie_file = prepare_cookies(
            step.params.get("credential_id"), ctx.settings, ctx.workdir
        )
        args = [
            self.binary,
            "--newline",
            "--no-playlist",
            "-o",
            f"{base}.%(ext)s",
            "--paths",
            f"home:{ctx.workdir}",
            "-f",
            audio_language_selector(step.params.get("audio_languages")),
            "--force-overwrites",
            "--retries",
            "10",
            *audio_format_args(step.params),
            *cookies_args(cookie_file),
            *sponsorblock_args(step.params),
            *extra_args(ctx.settings, step.params),
            step.params["uri"],
        ]
        self._run(args, ctx)
        produced = _find_by_ext(ctx.workdir, base, AUDIO_EXTS)
        if not produced:
            raise StepExecutionError(
                "no_output", "Audio acquisition succeeded but produced no audio file."
            )
        return [ProducedFile(path=produced, media_type=media_type_for(produced))]

    def _acquire_thumbnail(
        self, step: PlanStep, ctx: ExecutionContext
    ) -> list[ProducedFile]:
        base = f"thumbnail-{step.id}"
        args = [
            self.binary,
            "--no-playlist",
            "--skip-download",
            "--write-thumbnail",
            "-o",
            f"{base}.%(ext)s",
            "--paths",
            f"home:{ctx.workdir}",
        ]
        if step.params.get("format") == "jpeg":
            args += ["--convert-thumbnails", "jpg"]
        args += cookies_args(
            prepare_cookies(step.params.get("credential_id"), ctx.settings, ctx.workdir)
        )
        args.append(step.params["uri"])
        self._run(args, ctx)
        produced = _find_by_ext(ctx.workdir, base, IMAGE_EXTS)
        if not produced:
            raise StepExecutionError("no_output", "No thumbnail was available.")
        return [ProducedFile(path=produced, media_type=media_type_for(produced))]

    def _acquire_subtitles(
        self, step: PlanStep, ctx: ExecutionContext
    ) -> list[ProducedFile]:
        base = f"subtitles-{step.id}"
        languages = ",".join(step.params["languages"])
        mode = step.params.get("source", "prefer_manual")
        cookie_file = prepare_cookies(
            step.params.get("credential_id"), ctx.settings, ctx.workdir
        )

        def command(manual: bool, automatic: bool) -> list[str]:
            args = [self.binary, "--no-playlist", "--skip-download"]
            if manual:
                args.append("--write-subs")
            if automatic:
                args.append("--write-auto-subs")
            args += [
                "--sub-langs",
                languages,
                "--convert-subs",
                step.params.get("format", "srt"),
                "-o",
                f"{base}.%(ext)s",
                "--paths",
                f"home:{ctx.workdir}",
                "--force-overwrites",
                *cookies_args(cookie_file),
                step.params["uri"],
            ]
            return args

        # prefer_manual: try manual tracks first, fall back to automatic ones.
        passes = {
            "manual_only": [(True, False)],
            "automatic_only": [(False, True)],
            "any": [(True, True)],
            "prefer_manual": [(True, False), (False, True)],
        }[mode]
        origin_by_pass = {(True, False): "manual", (False, True): "automatic"}

        produced: list[ProducedFile] = []
        for flags in passes:
            self._run(command(*flags), ctx)
            for path in sorted(ctx.workdir.glob(f"{base}*")):
                if path.suffix.lower() not in SUBTITLE_EXTS:
                    continue
                attributes = {"language": _subtitle_language(path, base)}
                if flags in origin_by_pass:
                    attributes["origin"] = origin_by_pass[flags]
                produced.append(
                    ProducedFile(
                        path=path,
                        media_type=media_type_for(path),
                        attributes=attributes,
                    )
                )
            if produced:
                break
        return produced  # 0..N artifacts by contract (decision D7)

    def _export_metadata(
        self, step: PlanStep, ctx: ExecutionContext
    ) -> list[ProducedFile]:
        # No process: the normalized resource resolved at planning time is the
        # public metadata model (D5).
        path = ctx.workdir / f"metadata-{step.id}.json"
        path.write_text(
            json.dumps(step.params["resource"], indent=2, ensure_ascii=False)
        )
        return [ProducedFile(path=path, media_type="application/json")]


def _find_by_ext(workdir: Path, base: str, extensions: tuple[str, ...]) -> Path | None:
    for path in sorted(workdir.glob(f"{base}*")):
        if path.suffix.lower() in extensions:
            return path
    return None


def _subtitle_language(path: Path, base: str) -> str:
    # yt-dlp names subtitle files "<base>.<lang>.<ext>".
    middle = path.name[len(base) :].strip(".")
    parts = middle.split(".")
    return parts[0] if len(parts) >= 2 else ""


def _iso_date(upload_date: str) -> str:
    if len(upload_date) == 8 and upload_date.isdigit():
        return f"{upload_date[0:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
    return ""
