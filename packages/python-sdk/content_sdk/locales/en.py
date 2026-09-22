"""English — the reference catalogue. Every key exists here first."""

from __future__ import annotations

TRANSLATIONS: dict[str, str] = {
    # === the layer itself ===
    "ui.language": "Language",
    # === signing in (content_sdk.signin) ===
    "signin.banner_title": "🔒 You are not signed in",
    "signin.banner_body": (
        "{app_title} needs to know who you are before it can do anything. "
        "Your work, your files and your history are yours, so nothing below "
        "will run until you sign in. It takes an email address and one click "
        "— no password."
    ),
    "signin.banner_action": "Sign in",
    "signin.sidebar_action": "🔒 Sign in",
    "signin.elsewhere_label": "Other surfaces",
    "signin.other_surface_hint": (
        "Signed in on another surface? Reload — one session covers all."
    ),
    "signin.signed_in_as": "Signed in as **{who}**{badge}",
    "signin.operator_badge": " · operator",
    "signin.sign_out": "Sign out",
    # === the source offer (content_sdk.legal) ===
    "legal.source_code": "Source code",
    # === quotas (content_sdk.quota) ===
    "quota.usage_title": "**Your usage** (rolling {days} days)",
    "quota.usage_minutes": "{used} / {allowed} min of media",
    "quota.usage_storage": "{used} / {allowed} stored",
    "quota.usage_jobs": "{used} / {allowed} jobs",
    "quota.media_minutes": (
        "This would take you past {allowed} minutes of media in the last 30 "
        "days. Your oldest minutes free themselves up as they age out — or "
        "see the two ways forward below."
    ),
    "quota.storage_bytes": (
        "You are holding {used}, and {allowed} is the ceiling here. Delete "
        "something you no longer need, or see below."
    ),
    "quota.active_jobs_one": (
        "One job at a time on this instance. Wait for the current one to "
        "finish — it keeps running, nothing is lost."
    ),
    "quota.active_jobs_many": (
        "{count} jobs at a time on this instance. Wait for the current one to "
        "finish — it keeps running, nothing is lost."
    ),
    "quota.ways_forward": "**Two ways forward, and both are fine by us.**",
    "quota.self_host": "Run it yourself. Free, no account, no quota.",
    "quota.full_setup": "Full setup: {url}",
    "quota.hosted_cta": (
        "Or stay here and let someone else run the server — [{label}]({url})"
    ),
    "quota.retention_note": (
        "Whatever you choose: what you produced here stays downloadable from "
        "your library for {days} days."
    ),
    # === notifications (content_sdk.notifications) ===
    "notifications.dismiss": "Dismiss",
    # === statuses and relative times (content_sdk.status) ===
    "status.ago_unknown": "—",
    "status.ago_seconds": "{value}s ago",
    "status.ago_minutes": "{value}m ago",
    "status.ago_hours": "{value}h ago",
    "status.ago_days": "{value}d ago",
    "status.job.created": "created",
    "status.job.validating": "validating",
    "status.job.planning": "planning",
    "status.job.queued": "queued",
    "status.job.running": "running",
    "status.job.succeeded": "succeeded",
    "status.job.partially_succeeded": "partially succeeded",
    "status.job.failed": "failed",
    "status.job.cancelled": "cancelled",
    # === HomeTube ===
    # why an output is not on offer
    "ht.reason.unavailable": "not available for this source",
    "ht.reason.missing_material": "this source has no {materials}",
    "ht.reason.material_fallback": "a required material",
    "ht.reason.implementation_unavailable": "needs a server component ({operations})",
    "ht.reason.runner_fallback": "a runner",
    "ht.reason.policy_restricted": "blocked by the server policy",
    "ht.reason.generic": "not available",
    # the engine, in the sidebar
    "ht.backend_unreachable": "⚠️ Back-end unreachable at {url} — {error}",
    "ht.backend_online": "🟢 back-end v{version}",
    "ht.backend_offline": "🔴 back-end offline",
    "ht.api_docs": "API · /docs",
    "ht.recent_jobs": "Recent jobs",
    # the URL, and what the analysis found
    "ht.url_label": "Video or Playlist URL",
    "ht.url_placeholder": (
        "youtube.com/watch?v=…   ·   or a playlist: …/playlist?list=…"
    ),
    "ht.url_help": (
        "Paste a single video or a whole playlist — the form adapts to what the URL is."
    ),
    "ht.analyzing": "🔍 Analyzing…",
    "ht.analyze_refused": "⚠️ Couldn't analyze this URL — {message}",
    "ht.analyze_failed": "⚠️ Analysis failed — {error}",
    "ht.playlist_untitled": "Playlist",
    "ht.playlist_count": "📚 {count} videos",
    "ht.playlist_entries": "Videos in this playlist ({count})",
    "ht.playlist_delivery_note": (
        "Each video is downloaded and delivered under your chosen folder."
    ),
    "ht.untitled": "Untitled",
    "ht.tech_up_to": "🎞️ up to {height}p{codecs}",
    "ht.tech_auto_suffix": " (+auto)",
    # what was asked last time
    "ht.remembered_banner": (
        "🕘 **You asked for this on {when}**: {what} into `{where}`{state}. "
        "The form below is filled in with those choices. Keeping the same "
        "folder is what lets Content see what is already there instead of "
        "downloading it again."
    ),
    "ht.remembered_anything": "a download",
    "ht.remembered_root_folder": "the root folder",
    "ht.remembered_last_run": " — last run {status}",
    # naming and destination
    "ht.playlist_name_label": "Playlist name",
    "ht.playlist_name_help": (
        "Prefix for every downloaded file — each video keeps its own number "
        "and title (e.g. “MyName-001-first-video”). The server sanitizes it."
    ),
    "ht.audio_name_label": "Audio name",
    "ht.video_name_label": "Video name",
    "ht.name_help": (
        "The name the engine computed for this source (ADR 0017) — edit it or "
        "leave it as proposed. Untouched, nothing is sent and the server names "
        "the files itself, arriving at exactly this name."
    ),
    "ht.name_placeholder": "named by the server",
    "ht.folder_label": "Destination folder",
    "ht.folder_root": "📁 Root folder (/)",
    "ht.folder_new": "➕ New folder…",
    "ht.folder_help": "Where the file lands under the server delivery library.",
    "ht.folder_new_label": "New folder path (relative)",
    # what to produce
    "ht.content_label": "Content",
    "ht.content_collection_heading": (
        "**Content** &nbsp;·&nbsp; each video is downloaded as"
    ),
    "ht.content_heading": "**Content** &nbsp;·&nbsp; what this source can produce",
    "ht.preset_video": "🎬 Video",
    "ht.preset_audio": "🎵 Audio only",
    "ht.preset_subtitles": "💬 Subtitles only",
    "ht.preset_custom": "🧩 Custom…",
    "ht.nothing_producible": (
        "Nothing can be produced from this source in this installation."
    ),
    "ht.output.video": "Video",
    "ht.output.audio": "Audio",
    "ht.output.subtitles": "Subtitles",
    "ht.output.transcript": "Transcript",
    "ht.output.summary": "Summary",
    "ht.output.thumbnail": "Thumbnail",
    "ht.output.metadata": "Metadata",
    "ht.help_derivable": "Derived from the source (transcript/summary).",
    "ht.help_unknown": "Attempted — feasibility undetermined.",
    "ht.blocked_prefix": "Not available for this source — ",
    # languages
    "ht.language_original": "original — each video's own voice",
    "ht.server_language_preference": "🌐 Server language preference: {chain}",
    "ht.language_from_server": "🌐 From your server language preference.",
    "ht.audio_languages_label": "Audio languages",
    "ht.audio_languages_help": (
        "Audio tracks to include (VO first, then your server language "
        "preferences). Several = multi-audio embedded into the video."
    ),
    "ht.audio_languages_collection_help": (
        "Applied to every video in the playlist. Items are not probed "
        "beforehand, so this is a preference: a video keeps the tracks it has, "
        "and falls back to its best audio otherwise."
    ),
    "ht.original_voice": "🗣️ Original voice: {language}",
    "ht.subtitles_label": "Subtitles",
    "ht.subtitles_help": (
        "Subtitle languages (embedded into the video, or delivered as files "
        "for the subtitles-only preset)."
    ),
    "ht.subtitles_collection_help": (
        "Embedded into every video of the playlist when it has them — a video "
        "without a requested language simply keeps none."
    ),
    "ht.subtitles_auto": "🤖 Auto-generated captions available: {languages}",
    "ht.subtitles_none": "💬 No subtitle tracks detected for this source.",
    # sponsors
    "ht.sponsors_section": "📊 Advertising and Sponsors",
    "ht.sponsorblock_label": "SponsorBlock",
    "ht.sponsorblock_help": (
        "Remove or mark sponsored segments (SponsorBlock community data)."
    ),
    "ht.cut_quality_label": "Cut quality",
    "ht.cut_quality_keyframes": "⚡ Fast cut — keeps the original video (recommended)",
    "ht.cut_quality_precise": "🐢 Exact cut — re-encodes it all (minutes per video)",
    "ht.cut_quality_help": (
        "Fast cut removes the segments with a stream copy along existing "
        "keyframes: it finishes at download speed, keeps the codecs you asked "
        "for, and the end of the video stays clean. A boundary may shift to "
        "the nearest keyframe (usually under a second). Exact cut asks yt-dlp "
        "for frame-exact boundaries (--force-keyframes-at-cuts), which "
        "re-encodes the whole file at ffmpeg's default codecs: on a 2 min 4K "
        "clip that measured 17 s of download against 8 min of CPU, and turned "
        "AV1/Opus into a larger H.264/Vorbis file."
    ),
    # cutting
    "ht.cutting_section": "✂️ Cutting",
    "ht.cut_enable": "Keep only a segment",
    "ht.cut_start": "Start (HH:MM:SS)",
    "ht.cut_end": "End (HH:MM:SS)",
    "ht.cut_mode_label": "Cut mode",
    "ht.cut_mode_help": (
        "keyframes: fast, lossless stream copy — bounds snap to the nearest "
        "keyframes. precise: frame-accurate bounds via a re-encode of the "
        "segment (slower)."
    ),
    # video quality
    "ht.quality_section": "🎥 Video Quality",
    "ht.max_resolution": "Max resolution",
    "ht.resolution_help": "Detected on this source.",
    "ht.preferred_codec": "Preferred codec",
    "ht.container": "Container",
    # embedding
    "ht.embedding_section": "📦 Video Embedding",
    "ht.embed_metadata": "Embed metadata",
    "ht.embed_thumbnail": "Embed thumbnail",
    "ht.embed_chapters": "Embed chapters",
    "ht.embed_subtitles": "Embed subtitles into the video ({languages})",
    # audio, transcript, summary
    "ht.audio_section": "🎵 Audio",
    "ht.audio_format": "Audio format",
    "ht.audio_format_help": "'source' keeps the native stream; others transcode.",
    "ht.transcript_section": "📝 Transcript",
    "ht.transcript_format": "Transcript format",
    "ht.transcript_format_help": (
        "JSON is the canonical form and carries the timings. `text` is the "
        "readable derivation — the better file to keep beside a video in your "
        "library. Asking for `text` while also asking for a summary makes the "
        "engine build its own timed transcript as well, which on a source "
        "without subtitles means transcribing twice."
    ),
    "ht.summary_section": "🧠 Summary",
    "ht.summary_length": "Summary length",
    # cookies
    "ht.cookies_section": "🍪 Cookie Management{flag}",
    "ht.cookies_ready": " · ✅ ready",
    "ht.cookies_missing": " · ⚠️ cookies file missing",
    "ht.auth_label": "Authentication",
    "ht.auth_help": (
        "Server-side cookie credentials (CONTENT_CREDENTIALS). Needed for "
        "age-restricted or private videos."
    ),
    "ht.cookies_declared_missing": (
        "⚠️ `{credential}` is declared but its file is not there yet — drop "
        "your cookies export at `{path}` (host side: the `./config` folder), "
        "then run `make docker-update`. Cookies unlock age-restricted videos "
        "and make YouTube downloads more reliable — see config/README.md."
    ),
    "ht.cookies_will_be_used": (
        "✅ Will be used for this download: `{path}` · updated {when}"
    ),
    "ht.cookies_none": (
        "No credentials configured on the server — to add YouTube cookies, "
        "see config/README.md."
    ),
    # advanced
    "ht.advanced_section": "⚙️ Advanced",
    "ht.extra_args": "Extra yt-dlp arguments",
    "ht.extra_args_help": (
        "Power users only — forwarded to yt-dlp, e.g. --limit-rate 2M --proxy "
        "http://host:8080. Only network, geo, pacing and user-agent flags are "
        "accepted; the server rejects everything else."
    ),
    "ht.extra_args_unparseable": (
        "Could not parse the extra arguments (check your quotes)."
    ),
    # submitting
    "ht.download_button": "🎬  Download",
    "ht.download_playlist_button": "🎬  Download playlist ({count} videos)",
    "ht.submit_refused": "Request refused: {message}",
    "ht.submit_failed": "Submit failed: {error}",
    "ht.request_preview": "🧾 GenerationRequest (what will be sent)",
    # following the job
    "ht.job_not_found": "Job not found: {error}",
    "ht.steps_progress": "{done}/{total} steps",
    "ht.step_downloading": "downloading · {percent}%",
    "ht.artifacts": "**Artifacts**",
    "ht.in_your_library": " · in your library: {path}",
    "ht.download_artifact": "⬇︎ download",
    "ht.no_artifacts": "no artifacts",
    "ht.cancel": "Cancel",
    "ht.retry": "Retry",
    "ht.events": "Events",
    "ht.events_skipped": "    … {count} step.progress events (shown live above)",
    "ht.logs_section": "Logs (yt-dlp / ffmpeg output, per step)",
    "ht.no_logs": "no logs yet",
}
