"""HomeTube — Streamlit front-end for the Content engine.

A pure API client of the Content back-end (/api/v1): paste a YouTube URL,
analyze it, name the file, pick a destination folder, choose what to extract
and which subtitle languages, tune quality / sponsors / cookies, launch a job
and follow it live. No business logic here — the back-end validates, plans and
executes; this page only builds a GenerationRequest and speaks HTTP.

The layout deliberately mirrors the original HomeTube UI (URL → name →
destination folder → subtitles → collapsible sections → big Download button →
live queue) while targeting Content's versatile, declarative contract.
"""

import base64
import os
import shlex

import streamlit as st
from content_sdk import legal, notifications
from content_sdk.compat import ApiError, ContentClient
from content_sdk.status import ago, better_status, display, is_producible

API_URL = os.getenv("CONTENT_API_URL", "http://localhost:8000")
PUBLIC_API_URL = os.getenv("CONTENT_PUBLIC_API_URL", API_URL).rstrip("/")

# This app's own release, in lockstep with the whole monorepo (`make version`
# guards every declaration). Passed to the notification bar so the launch check
# can compare it against the backend's version and warn on a torn deployment.
__version__ = "0.2.0"

# HomeTube logo: gradient rounded square with a white play triangle. Embedded
# inline (base64 data URI) so no binary asset is needed and the mark stays
# byte-identical to HomeTube's docs/icons/favicon.svg.
_LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    '<defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">'
    '<stop offset="0%" style="stop-color:#8B5CF6"/>'
    '<stop offset="100%" style="stop-color:#D946EF"/></linearGradient></defs>'
    '<rect width="100" height="100" rx="20" fill="url(#g)"/>'
    '<polygon points="38,25 38,75 75,50" fill="white"/></svg>'
)
_LOGO_URI = "data:image/svg+xml;base64," + base64.b64encode(_LOGO_SVG.encode()).decode()

# SponsorBlock presets → Content's {remove, mark} category lists.
SB_PRESETS: dict[str, dict | None] = {
    "disabled": None,
    "default": {
        "remove": ["sponsor", "interaction", "selfpromo"],
        "mark": ["intro", "preview", "outro"],
    },
    "moderate": {
        "remove": ["sponsor", "interaction", "outro"],
        "mark": ["selfpromo", "intro", "preview"],
    },
    "aggressive": {
        "remove": ["sponsor", "selfpromo", "interaction", "intro", "outro", "preview"],
        "mark": [],
    },
    "minimal": {
        "remove": ["sponsor"],
        "mark": ["selfpromo", "interaction", "intro", "outro", "preview"],
    },
}

# Content preset → which Content output types to request.
PRESETS: dict[str, list[str] | None] = {
    "🎬 Video": ["video"],
    "🎵 Audio only": ["audio"],
    "💬 Subtitles only": ["subtitles"],
    "🧩 Custom…": None,
}
CUSTOM_OUTPUTS = [
    "video",
    "audio",
    "subtitles",
    "thumbnail",
    "metadata",
    "transcript",
    "summary",
]

# The dynamic UI is driven by the resolved capabilities (ADR 0013): each public
# capability maps to the output type this client requests. Ordered for display.
OUTPUT_ORDER = [
    "video",
    "audio",
    "subtitles",
    "transcript",
    "summary",
    "thumbnail",
    "metadata",
]
OUTPUT_META = {
    "video": ("🎬", "Video"),
    "audio": ("🎵", "Audio"),
    "subtitles": ("💬", "Subtitles"),
    "transcript": ("📝", "Transcript"),
    "summary": ("🧠", "Summary"),
    "thumbnail": ("🖼️", "Thumbnail"),
    "metadata": ("🧾", "Metadata"),
}
CAP_TO_OUTPUT = {
    "video.download": "video",
    "video.clip": "video",
    "audio.download": "audio",
    "subtitles.download": "subtitles",
    "thumbnail.download": "thumbnail",
    "metadata.export": "metadata",
    "transcript.generate": "transcript",
    "summary.generate": "summary",
}
# Producible = the source can yield it (attempt allowed for 'unknown').


def _reason_text(reason: dict | None) -> str:
    """A short human explanation of why a capability is unavailable."""
    if not reason:
        return "not available for this source"
    code = reason.get("code", "")
    if code == "missing_material":
        mats = ", ".join(reason.get("missing_materials", [])) or "a required material"
        return f"this source has no {mats}"
    if code == "implementation_unavailable":
        ops = ", ".join(reason.get("missing_operations", [])) or "a runner"
        return f"needs a server component ({ops})"
    if code == "policy_restricted":
        return "blocked by the server policy"
    return code or "not available"


TERMINAL = {"succeeded", "partially_succeeded", "failed", "cancelled"}

st.set_page_config(page_title="HomeTube", page_icon="🎬", layout="centered")

st.markdown(
    """
    <style>
      /* Remove Streamlit's top toolbar/decoration ribbon — it added a dark
         band that clipped the logo and served no purpose for this app. */
      header[data-testid="stHeader"] { display: none; }
      div[data-testid="stDecoration"] { display: none; }
      #MainMenu, footer { visibility: hidden; }
      .block-container { padding-top: 1.5rem; padding-bottom: 3rem;
        max-width: 820px; }
      .ht-brand { text-align:center; padding:.2rem 0 0 0; margin-bottom:3rem; }
      .ht-brand img { width:54px; vertical-align:middle; border-radius:13px;
        box-shadow:0 4px 18px rgba(139,92,246,.35); }
      .ht-brand .name { font-size:2.5rem; font-weight:800; vertical-align:middle;
        margin-left:.55rem; letter-spacing:.3px;
        background:linear-gradient(90deg,#8B5CF6,#D946EF);
        -webkit-background-clip:text; background-clip:text; color:transparent; }
      .ht-brand .sub { color:#8b93a3; font-size:.9rem; margin-top:-.15rem; }
      .ht-card { background:#171a23; border:1px solid #2a2f3a; border-radius:14px;
        padding:14px 16px; margin:.3rem 0 1rem 0; }
      .step { font-family:ui-monospace,monospace; font-size:.82rem; padding:2px 0; }
      div[data-testid="stExpander"] details { border-color:#2a2f3a;
        border-radius:12px; }
      .stButton>button[kind="primary"] { font-size:1.05rem; font-weight:700;
        padding:.55rem 0; border-radius:12px; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def get_client(base_url: str) -> ContentClient:
    return ContentClient(base_url)


client = get_client(API_URL)
st.session_state.setdefault("analysis", None)
st.session_state.setdefault("capabilities", None)
st.session_state.setdefault("analyzed_url", None)
st.session_state.setdefault("job_id", None)


# --- back-end health + config --------------------------------------------------

backend_ok = False
version = "?"
credentials: list[str] = []
credential_files: dict[str, dict] = {}
lang_prefs: dict = {}
try:
    health = client.health()
    backend_ok = health.get("status") == "ok"
    version = health.get("version", "?")
    config = client.config()
    credentials = config.get("credentials", [])
    credential_files = {
        c["id"]: c for c in config.get("credentials_info", []) if "id" in c
    }
    lang_prefs = config.get("language", {}) or {}
except Exception as exc:  # noqa: BLE001
    st.error(f"⚠️ Back-end unreachable at {API_URL} — {exc}")


def preferred_order(available: list[str], original: str = "") -> list[str]:
    """Order languages like HomeTube for the *options* list: VO first (if
    enabled), then the server's primary, then its secondaries, then the rest —
    keeping only what the source offers."""
    primary = lang_prefs.get("primary") or ""
    secondaries = list(lang_prefs.get("secondaries") or [])
    vo_first = lang_prefs.get("vo_first", True)
    order: list[str] = []
    for lang in ([original] if vo_first else []) + [primary] + secondaries + available:
        if lang and lang in available and lang not in order:
            order.append(lang)
    return order


def preferred_langs(
    available: list[str],
    original: str = "",
    *,
    include_vo: bool = True,
    include_primary: bool = True,
) -> list[str]:
    """The server-*wanted* languages the source actually offers — VO (if enabled
    and requested) + primary (unless excluded) + secondaries ∩ available, in
    that order. This is what should be pre-selected by default: only the desired
    languages, never every track the source happens to carry."""
    primary = lang_prefs.get("primary") or ""
    secondaries = list(lang_prefs.get("secondaries") or [])
    vo_first = lang_prefs.get("vo_first", True) and include_vo
    wanted = (
        ([original] if vo_first else [])
        + ([primary] if include_primary else [])
        + secondaries
    )
    out: list[str] = []
    for lang in wanted:
        if lang and lang in available and lang not in out:
            out.append(lang)
    return out


def wanted_langs(*, include_primary: bool = True) -> list[str]:
    """The server's wanted languages as *intent*, with nothing to intersect.

    `preferred_langs` keeps only what a source actually offers, which needs an
    analysis. A collection has none at form time: its entries are listed, never
    probed (ADR 0019 keeps discovery flat by design). So a playlist expresses
    the preferences as intent, and the engine intersects them against each
    member's real tracks when that member is analyzed and planned by the
    canonical single-video pipeline. VO is absent on purpose: "original" is a
    per-video fact, not a language code, so it cannot be requested for a whole
    playlist.
    """
    primary = lang_prefs.get("primary") or ""
    out: list[str] = []
    for lang in ([primary] if include_primary else []) + list(
        lang_prefs.get("secondaries") or []
    ):
        if lang and lang not in out:
            out.append(lang)
    return out


def _language_policy_caption() -> str:
    """Human-readable server language preference, for UI transparency."""
    primary = lang_prefs.get("primary") or ""
    if not primary and not lang_prefs.get("secondaries"):
        return ""
    parts = (
        (["VO"] if lang_prefs.get("vo_first", True) else [])
        + ([primary] if primary else [])
        + list(lang_prefs.get("secondaries") or [])
    )
    return "🌐 Server language preference: " + " › ".join(parts)


with st.sidebar:
    st.markdown("### 🎬 HomeTube")
    st.caption(
        f"{'🟢' if backend_ok else '🔴'} back-end v{version}"
        if backend_ok
        else "🔴 back-end offline"
    )
    if backend_ok:
        st.caption(f"[API · /docs]({PUBLIC_API_URL}/docs)")
    # AGPL §13: the source offer, from the instance (never hard-coded).
    legal.render_streamlit_footer(client)
    st.divider()
    st.caption("Recent jobs")
    try:
        for row in client.list_jobs(limit=12):
            icon = display(row["status"])[0]
            if st.button(
                f"{icon}  {row['job_id'][5:15]}",
                key=f"job-{row['job_id']}",
                use_container_width=True,
            ):
                st.session_state.job_id = row["job_id"]
                st.rerun()
    except Exception:  # noqa: BLE001
        st.caption("—")


# --- header --------------------------------------------------------------------

st.markdown(
    f"""
    <div class="ht-brand">
      <img src="{_LOGO_URI}" alt="HomeTube"/>
      <span class="name">HomeTube</span>
    </div>
    """,
    unsafe_allow_html=True,
)

# Instance notifications (a newer release, a stale yt-dlp), plus the one check
# only a client can make: this UI's version against the backend's, once per
# session — a mismatch means a torn deployment (one image updated, not the
# other). The engine decides everything else worth saying; this only renders.
# Shared with the other UIs through the SDK — never copy-pasted (D-21).
notifications.render_streamlit(client, app_version=__version__)


def _duration(seconds) -> str:
    s = int(seconds or 0)
    if not s:
        return ""
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _human_count(n) -> str:
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.1f}K".replace(".0K", "K")
    return str(n)


def source_dict(
    url: str, credential: str, provider_args: list[str] | None = None
) -> dict:
    src: dict = {"id": "main", "type": "url", "uri": url.strip()}
    if credential and credential != "none":
        src["auth"] = {"credential_id": credential}
    if provider_args:
        src["provider_args"] = provider_args
    return src


# --- URL (Enter = analyze) -----------------------------------------------------


def _error_message(body) -> str:
    """A clean human message from an API error body (never a raw dict dump)."""
    if isinstance(body, dict):
        detail = body.get("detail", body)
        if isinstance(detail, dict):
            errors = detail.get("errors") or []
            messages = [e.get("message") for e in errors if e.get("message")]
            if messages:
                return " · ".join(messages)
            if detail.get("message"):
                return detail["message"]
    return str(body)


url = st.text_input(
    "Video or Playlist URL",
    placeholder="youtube.com/watch?v=…   ·   or a playlist: …/playlist?list=…",
    key="url",
    help="Paste a single video or a whole playlist — the form adapts to what "
    "the URL is.",
)
url_clean = url.strip()

if backend_ok and url_clean and url_clean != st.session_state.analyzed_url:
    with st.spinner("🔍 Analyzing…"):
        src = source_dict(url_clean, "none")
        try:
            st.session_state.analysis = client.analyze([src])
            # Resolve what the source can actually produce — drives the UI.
            st.session_state.capabilities = client.capabilities([src])
            st.session_state.analyzed_url = url_clean
        except ApiError as exc:
            st.session_state.analysis = None
            st.session_state.capabilities = None
            st.session_state.analyzed_url = url_clean
            st.error(f"⚠️ Couldn't analyze this URL — {_error_message(exc.body)}")
        except Exception as exc:  # noqa: BLE001
            st.session_state.analysis = None
            st.session_state.capabilities = None
            st.session_state.analyzed_url = url_clean
            st.error(f"⚠️ Analysis failed — {exc}")

analysis = st.session_state.analysis if url_clean else None
resource: dict = {}
sub_manual: list[str] = []
sub_auto: list[str] = []
audio_langs_avail: list[str] = []
audio_original: str = ""
video_heights: list[int] = []
video_codecs_avail: list[str] = []
entries: list[dict] = []
is_collection = False
if analysis:
    entry = analysis["sources"][0]
    resource = entry["resource"]
    entries = entry.get("entries", []) or []
    is_collection = resource.get("resource_type") == "collection"
    # Read resource FACTS (ADR 0013): analysis describes the resource; it no
    # longer carries capabilities (those are resolved server-side on demand).
    subtitles = entry.get("subtitles", []) or []
    sub_manual = sorted({t["language"] for t in subtitles if t["origin"] == "manual"})
    sub_auto = sorted({t["language"] for t in subtitles if t["origin"] == "automatic"})
    media = entry.get("media", {}) or {}
    audio_langs_avail = list(media.get("audio_languages", []) or [])
    audio_original = media.get("original_audio_language", "") or ""
    video_heights = sorted({int(h) for h in media.get("video_heights", []) or []})
    video_codecs_avail = list(media.get("video_codecs", []) or [])
    # widget keys bound to the analyzed URL so defaults refresh on a new analysis
wk = st.session_state.analyzed_url or "-"

# --- resolved capabilities → what the UI may offer (ADR 0013) ------------------
cap_status: dict[str, str] = {}  # output_type -> best resolved status
cap_reason: dict[str, dict] = {}  # output_type -> structured reason (if blocked)
caps_payload = st.session_state.capabilities if url_clean else None
if caps_payload:
    for c in caps_payload["sources"][0].get("capabilities", []):
        out = CAP_TO_OUTPUT.get(c["id"])
        if not out:
            continue
        cap_status[out] = better_status(cap_status.get(out), c["status"])
        if c.get("reason") and out not in cap_reason:
            cap_reason[out] = c["reason"]
producible = {o for o, s in cap_status.items() if is_producible(s)}


# --- summary card (dynamic: playlist vs single video) --------------------------

if is_collection:
    st.markdown(f"### 📃 {resource.get('title') or 'Playlist'}")
    who = resource.get("channel") or resource.get("author")
    st.caption(f"{('👤 ' + who + ' · ') if who else ''}📚 {len(entries)} videos")
    with st.expander(f"Videos in this playlist ({len(entries)})", expanded=False):
        for i, e in enumerate(entries, 1):
            dur = _duration(e.get("duration_seconds"))
            st.markdown(
                f"{i}. {e.get('title') or e.get('id') or '?'}"
                + (f"  ·  {dur}" if dur else "")
            )
    st.caption("Each video is downloaded and delivered under your chosen folder.")
elif resource:
    # Clean analysis summary: the essentials (title, channel, metrics) plus a
    # concise technical line of the materials detected on the source.
    thumb, info = st.columns([1, 2.5], vertical_alignment="center")
    with thumb:
        if resource.get("thumbnail_url"):
            st.image(resource["thumbnail_url"], use_container_width=True)
        else:
            icon = "🎵" if resource.get("resource_type") == "audio" else "📺"
            st.markdown(
                f"<div style='font-size:3rem;text-align:center'>{icon}</div>",
                unsafe_allow_html=True,
            )
    with info:
        st.markdown(f"**{resource.get('title') or 'Untitled'}**")
        meta: list[str] = []
        who = resource.get("channel") or resource.get("author")
        if who:
            meta.append(f"👤 {who}")
        if resource.get("view_count") is not None:
            meta.append(f"👁 {_human_count(resource['view_count'])}")
        if resource.get("like_count"):
            meta.append(f"👍 {_human_count(resource['like_count'])}")
        dur = _duration(resource.get("duration_seconds"))
        if dur:
            meta.append(f"⏱ {dur}")
        if resource.get("published_at"):
            meta.append(f"📅 {resource['published_at']}")
        if meta:
            st.caption(" · ".join(meta))

    # Technical line: what the analysis found (materials), concise.
    tech: list[str] = []
    if video_heights:
        codecs = f" · {', '.join(video_codecs_avail)}" if video_codecs_avail else ""
        tech.append(f"🎞️ up to {max(video_heights)}p{codecs}")
    if audio_langs_avail:
        tech.append(f"🎙️ {', '.join(audio_langs_avail)}")
    sub_all = sorted(set(sub_manual) | set(sub_auto))
    if sub_all:
        auto = " (+auto)" if sub_auto else ""
        tech.append(f"💬 {', '.join(sub_manual or sub_all)}{auto}")
    if tech:
        st.caption("　·　".join(tech))


# --- name + destination folder -------------------------------------------------

if is_collection:
    name_label = "Playlist name"
    name_help = (
        "Prefix for every downloaded file — each video keeps its own number "
        "and title (e.g. “MyName-001-first-video”). The server sanitizes it."
    )
else:
    name_label = (
        "Audio name" if resource.get("resource_type") == "audio" else "Video name"
    )
    name_help = (
        "The name the engine computed for this source (ADR 0017) — edit it or "
        "leave it as proposed. Untouched, nothing is sent and the server names "
        "the files itself, arriving at exactly this name."
    )
# The engine's own proposal (naming engine, ADR 0017), prefilled and editable —
# the raw title was only ever a *placeholder* here, and it is not what the file
# would be called: the display profile turns "Artist - Song / Official Video"
# into "Artist - Song - Official Video". Showing the real answer is the point.
suggested_filename = (
    (caps_payload["sources"][0].get("suggested_filename") or "") if caps_payload else ""
)
filename = st.text_input(
    name_label,
    value=suggested_filename,
    placeholder="named by the server",
    key=f"name-{wk}",
    help=name_help,
)

folders: list[str] = []
if backend_ok:
    try:
        folders = [f for f in client.folders() if f]
    except Exception:  # noqa: BLE001
        folders = []
folder_choice = st.selectbox(
    "Destination folder",
    ["📁 Root folder (/)", *folders, "➕ New folder…"],
    help="Where the file lands under the server delivery library.",
)
if folder_choice == "➕ New folder…":
    folder = st.text_input(
        "New folder path (relative)", value="", key=f"newfolder-{wk}"
    )
elif folder_choice.startswith("📁 Root"):
    folder = ""
else:
    folder = folder_choice


# --- content type (capability-driven) ------------------------------------------

if is_collection:
    # A collection resolves per item (scope each_item): each entry is its own
    # video, so the producible outputs are Video or Audio, applied to every item.
    st.markdown("**Content** &nbsp;·&nbsp; each video is downloaded as")
    coll_label = st.radio(
        "Content",
        ["🎬 Video", "🎵 Audio only"],
        horizontal=True,
        key=f"coll-{wk}",
        label_visibility="collapsed",
    )
    active = ["video"] if coll_label.startswith("🎬") else ["audio"]
elif caps_payload:
    # Dynamic: offer only the outputs the resolver says this source can produce
    # (ADR 0013). Unavailable ones are listed with the reason. An audio-only
    # source therefore never offers Video, a source without subtitles never
    # offers Subtitles, etc.
    st.markdown("**Content** &nbsp;·&nbsp; what this source can produce")
    offer = [o for o in OUTPUT_ORDER if o in producible]
    active = []
    if not offer:
        st.warning("Nothing can be produced from this source in this installation.")
    for start in range(0, len(offer), 4):
        cols = st.columns(4)
        for i, out in enumerate(offer[start : start + 4]):
            icon, label = OUTPUT_META[out]
            default = out == "video" or (out == "audio" and "video" not in producible)
            status = cap_status.get(out, "")
            help_text = (
                "Derived from the source (transcript/summary)."
                if status == "derivable"
                else (
                    "Attempted — feasibility undetermined."
                    if status == "unknown"
                    else None
                )
            )
            if cols[i].checkbox(
                f"{icon} {label}",
                value=default,
                key=f"out-{out}-{wk}",
                help=help_text,
            ):
                active.append(out)
    blocked = [o for o in OUTPUT_ORDER if o in cap_status and o not in producible]
    if blocked:
        st.caption(
            "Not available for this source — "
            + " · ".join(
                f"{OUTPUT_META[o][1]}: {_reason_text(cap_reason.get(o))}"
                for o in blocked
            )
        )
else:
    # Pre-analysis (no source yet): the classic quick presets.
    preset_label = st.radio("Content", list(PRESETS), horizontal=True)
    active = PRESETS[preset_label]
    if active is None:  # custom
        cols = st.columns(len(CUSTOM_OUTPUTS))
        active = [
            name
            for i, name in enumerate(CUSTOM_OUTPUTS)
            if cols[i].checkbox(name, value=(name in ("video",)), key=f"c-{name}-{wk}")
        ]
want = set(active)
video_on = "video" in want
audio_on = "audio" in want


# --- audio languages -----------------------------------------------------------

audio_languages: list[str] = []
if (video_on or audio_on) and audio_langs_avail:
    # Options: every available track, preferred languages first. Default: only
    # the server-*wanted* languages the source offers (VO + primary +
    # secondaries ∩ available) — not every track.
    audio_default = preferred_langs(audio_langs_avail, audio_original) or (
        [audio_original] if audio_original in audio_langs_avail else []
    )
    audio_languages = st.multiselect(
        "Audio languages",
        preferred_order(audio_langs_avail, audio_original) or sorted(audio_langs_avail),
        default=audio_default,
        key=f"audio-{wk}",
        help="Audio tracks to include (VO first, then your server language "
        "preferences). Several = multi-audio embedded into the video.",
    )
    policy = _language_policy_caption()
    if policy:
        st.caption(policy)
    if audio_original:
        st.caption(f"🗣️ Original voice: {audio_original}")
elif (video_on or audio_on) and is_collection:
    # A playlist has no probed track list, so this asks for the server's
    # preferred languages rather than offering the source's. Without it the
    # request carried no `audio_languages` at all and every downloaded item
    # silently got a single default track — the bug this branch fixes.
    choices = wanted_langs()
    if choices:
        audio_languages = st.multiselect(
            "Audio languages",
            choices,
            default=choices,
            key=f"audio-coll-{wk}",
            help="Applied to every video in the playlist. Items are not "
            "probed beforehand, so this is a preference: a video keeps the "
            "tracks it has, and falls back to its best audio otherwise.",
        )
        # Not `_language_policy_caption()`: that one starts with "VO", and VO
        # is exactly what a playlist cannot ask for — "original" is a fact
        # about one video, not a language code. Printing it here would promise
        # an ordering the request does not carry.
        st.caption(
            "🌐 From your server preference, minus VO: a playlist is not "
            "probed, so each video keeps the tracks it has (and its best "
            "audio if it has none of these)."
        )


# --- subtitles to embed --------------------------------------------------------

sub_options = sorted(set(sub_manual) | set(sub_auto))
subs_langs: list[str] = []
subs_wanted = "subtitles" in want or video_on
# Only render the selector once the analysis actually offers subtitle tracks —
# no empty "No options to select" box before a URL is analyzed.
if subs_wanted and sub_options:
    # Default subtitles follow the server language prefs (VO doesn't apply to
    # subtitles). primary_include_subtitles=false excludes ONLY the primary —
    # the secondaries still pre-fill (original HomeTube semantics: someone
    # fluent in `fr` doesn't need `fr` subtitles but wants the `en`/`es` ones).
    include_primary = lang_prefs.get("primary_include_subtitles", True)
    pref_subs = preferred_langs(
        sub_options, include_vo=False, include_primary=include_primary
    )
    subs_default = pref_subs or (
        sub_options[:1] if ("subtitles" in want and sub_options) else []
    )
    subs_langs = st.multiselect(
        "Subtitles",
        sub_options,
        default=subs_default,
        key=f"subs-{wk}",
        help="Subtitle languages (embedded into the video, or delivered as "
        "files for the subtitles-only preset).",
    )
    if sub_auto:
        st.caption("🤖 Auto-generated captions available: " + ", ".join(sub_auto))
elif subs_wanted and is_collection:
    # Same reasoning as the audio branch above: intent, not availability. The
    # playlist's items were never probed, so without this the video output
    # carried no `embed_subtitles` and every item arrived subtitle-less.
    include_primary = lang_prefs.get("primary_include_subtitles", True)
    sub_choices = wanted_langs(include_primary=include_primary)
    if sub_choices:
        subs_langs = st.multiselect(
            "Subtitles",
            sub_choices,
            default=sub_choices,
            key=f"subs-coll-{wk}",
            help="Embedded into every video of the playlist when it has them "
            "— a video without a requested language simply keeps none.",
        )
elif subs_wanted and analysis and not sub_options:
    st.caption("💬 No subtitle tracks detected for this source.")


# All option sections below are DYNAMIC: each appears only when it applies to a
# selected output, so an audio-only source shows no video sections, etc.

# --- 📊 Advertising and Sponsors (video / audio) -------------------------------

sb_preset = "default"
sb_cut_mode = "keyframes"
if video_on or audio_on:
    with st.expander("📊 Advertising and Sponsors"):
        sb_preset = st.selectbox(
            "SponsorBlock",
            list(SB_PRESETS),
            index=1,  # "default" — sponsors removed out of the box
            help="Remove or mark sponsored segments (SponsorBlock community data).",
        )
        # The same trade-off the Cutting section names, for the cuts
        # SponsorBlock makes — and the same default, stream copy.
        if (SB_PRESETS.get(sb_preset) or {}).get("remove"):
            # Never show the bare contract values here: neither word says what
            # it costs. What the reader has to understand before choosing is
            # that one option cuts the file and the other re-encodes all of
            # it, so the labels lead with that and the help gives the measured
            # price.
            sb_cut_mode = st.radio(
                "Cut quality",
                ["keyframes", "precise"],
                format_func=lambda mode: {
                    "keyframes": "⚡ Fast cut — keeps the original video (recommended)",
                    "precise": "🐢 Exact cut — re-encodes it all (minutes per video)",
                }[mode],
                key=f"sbcut-{wk}",
                help="Fast cut removes the segments with a stream copy along "
                "existing keyframes: it finishes at download speed, keeps the "
                "codecs you asked for, and the end of the video stays clean. "
                "A boundary may shift to the nearest keyframe (usually under "
                "a second). Exact cut asks yt-dlp for frame-exact boundaries "
                "(--force-keyframes-at-cuts), which re-encodes the whole file "
                "at ffmpeg's default codecs: on a 2 min 4K clip that measured "
                "17 s of download against 8 min of CPU, and turned AV1/Opus "
                "into a larger H.264/Vorbis file.",
            )


# --- ✂️ Cutting (single video only) --------------------------------------------

cut: dict | None = None
# A cut is a video→video transform, so it needs a selected video — and a
# playlist member is a video like any other (ADR 0019): the same bounds apply
# to each member.
if video_on:
    with st.expander("✂️ Cutting"):
        cut_on = st.checkbox("Keep only a segment", value=False, key=f"cut-{wk}")
        cc1, cc2 = st.columns(2)
        cut_start = cc1.text_input("Start (HH:MM:SS)", value="0", disabled=not cut_on)
        cut_end = cc2.text_input("End (HH:MM:SS)", value="", disabled=not cut_on)
        cut_mode = st.radio(
            "Cut mode",
            ["keyframes", "precise"],
            horizontal=True,
            disabled=not cut_on,
            key=f"cutmode-{wk}",
            help="keyframes: fast, lossless stream copy — bounds snap to the "
            "nearest keyframes. precise: frame-accurate bounds via a re-encode "
            "of the segment (slower).",
        )
        if cut_on and cut_end.strip():
            cut = {
                "start": cut_start.strip() or "0",
                "end": cut_end.strip(),
                "mode": cut_mode,
            }


# --- 🎥 Video Quality (video only) ---------------------------------------------

max_height, video_codec, container = 1080, "auto", "mkv"
if video_on:
    with st.expander("🎥 Video Quality"):
        q1, q2, q3 = st.columns(3)
        # Resolutions/codecs are DRAWN FROM THE SOURCE (R5): offer only what the
        # analysis actually detected, so we never propose a 4K/av1 that isn't
        # there. Fall back to the standard ladder when facts are unavailable
        # (e.g. a playlist, whose items aren't probed).
        res_options = (
            sorted(video_heights, reverse=True)
            if video_heights
            else [2160, 1440, 1080, 720, 480, 360]
        )
        max_height = q1.selectbox(
            "Max resolution",
            res_options,
            index=0,
            help="Detected on this source." if video_heights else None,
        )
        codec_options = ["auto"] + [
            c for c in ("av1", "vp9", "h264") if c in video_codecs_avail
        ]
        video_codec = q2.selectbox("Preferred codec", codec_options)
        container = q3.selectbox("Container", ["mkv", "mp4"])


# --- 📦 Video Embedding (video only) -------------------------------------------

embed_metadata, embed_thumbnail, embed_chapters, embed_subs = True, False, True, True
if video_on:
    with st.expander("📦 Video Embedding"):
        embed_metadata = st.checkbox("Embed metadata", value=True)
        embed_thumbnail = st.checkbox("Embed thumbnail", value=False)
        embed_chapters = st.checkbox("Embed chapters", value=True)
        if subs_langs:
            embed_subs = st.checkbox(
                f"Embed subtitles into the video ({', '.join(subs_langs)})",
                value=True,
            )


# --- 🎵 Audio (audio only) -----------------------------------------------------

audio_format = "source"
if audio_on:
    with st.expander("🎵 Audio"):
        audio_format = st.selectbox(
            "Audio format",
            ["source", "opus", "mp3", "m4a"],
            help="'source' keeps the native stream; others transcode.",
        )


# --- 🧠 Summary (summary only) -------------------------------------------------

summary_len = "medium"
if "summary" in want:
    with st.expander("🧠 Summary"):
        summary_len = st.selectbox(
            "Summary length", ["short", "medium", "long"], index=1
        )


# --- 🍪 Cookie Management -------------------------------------------------------

# The expander label itself carries the cookie state, so the flag is visible
# without opening it. Declared-but-missing is a *guided setup* state, not an
# error: the credential ships declared by default because HomeTube in Docker
# all but needs cookies, and the flag tells the user the one step left.
_cookie_metas = [credential_files.get(c) for c in credentials]
if any(m and m.get("exists") for m in _cookie_metas):
    _cookie_flag = " · ✅ ready"
elif any(_cookie_metas):
    _cookie_flag = " · ⚠️ cookies file missing"
else:
    _cookie_flag = ""
with st.expander(f"🍪 Cookie Management{_cookie_flag}"):
    credential = st.selectbox(
        "Authentication",
        ["none", *credentials],
        help="Server-side cookie credentials (CONTENT_CREDENTIALS). "
        "Needed for age-restricted or private videos.",
    )
    # Kill the classic doubt — "are my cookies actually in use?" — with the
    # file's own facts: which path, whether it is there, when it was last
    # refreshed (the metadata the server reports; contents never leave it).
    for _cred_id in credentials:
        _meta = credential_files.get(_cred_id)
        if _meta and not _meta.get("exists"):
            st.caption(
                f"⚠️ `{_cred_id}` is declared but its file is not there yet — "
                f"drop your cookies export at `{_meta['path']}` (host side: "
                "the `./config` folder), then run `make docker-update`. "
                "Cookies unlock age-restricted videos and make YouTube "
                "downloads more reliable — see config/README.md."
            )
    if credential != "none":
        meta = credential_files.get(credential)
        if meta and meta.get("exists"):
            st.caption(
                f"✅ Will be used for this download: `{meta['path']}` · "
                f"updated {ago(meta.get('updated_at'))}"
            )
    if not credentials:
        st.caption(
            "No credentials configured on the server — to add YouTube "
            "cookies, see config/README.md."
        )


# --- ⚙️ Advanced (yt-dlp) ------------------------------------------------------

with st.expander("⚙️ Advanced"):
    extra_args_raw = st.text_input(
        "Extra yt-dlp arguments",
        value="",
        key=f"extra-{wk}",
        help="Power users only — forwarded to yt-dlp, e.g. "
        "--limit-rate 2M --proxy http://host:8080. Command-execution and "
        "output/cookies overrides are rejected by the server.",
    )
try:
    provider_args = shlex.split(extra_args_raw) if extra_args_raw.strip() else []
except ValueError:
    provider_args = []
    st.warning("Could not parse the extra arguments (check your quotes).")


# --- build the GenerationRequest -----------------------------------------------


def build_request() -> dict:
    sb = SB_PRESETS[sb_preset]
    if sb and sb.get("remove"):
        sb = {**sb, "cut_mode": sb_cut_mode}
    delivery = {}
    if folder:
        delivery["folder"] = folder
    # The proposal left untouched is not intent: send nothing and let the
    # engine name the artifacts, which lands on the same name by construction.
    # Only a real edit becomes a `delivery.filename` (raw — the server
    # sanitizes, the client never does: D-51).
    chosen_name = filename.strip()
    if chosen_name and chosen_name != suggested_filename:
        delivery["filename"] = chosen_name

    outputs: list[dict] = []
    for name in CUSTOM_OUTPUTS:
        if name not in want:
            continue
        if name == "subtitles" and not subs_langs:
            continue  # the subtitles output requires at least one language
        out: dict = {"id": f"{name}_main", "type": name}
        opts: dict = {}
        if name == "video":
            selection = {"max_height": max_height}
            if video_codec != "auto":
                selection["video_codec"] = {"mode": "prefer", "value": video_codec}
            if audio_languages:
                selection["audio_languages"] = audio_languages
            opts["selection"] = selection
            processing = {
                "embed_metadata": embed_metadata,
                "embed_thumbnail": embed_thumbnail,
                "embed_chapters": embed_chapters,
            }
            if subs_langs and embed_subs:
                processing["embed_subtitles"] = subs_langs
            opts["processing"] = processing
            opts["container"] = container
            if cut:
                opts["cut"] = cut
            if sb:
                opts["sponsorblock"] = sb
        elif name == "audio":
            if audio_format != "source":
                opts["format"] = audio_format
            if audio_languages:
                opts["languages"] = audio_languages
            if sb:
                opts["sponsorblock"] = sb
        elif name == "subtitles":
            opts["languages"] = subs_langs
        elif name == "summary":
            opts["length"] = summary_len
        if opts:
            out["options"] = opts
        if name in ("metadata", "thumbnail"):
            out["required"] = False
        # A playlist is produced per member: the same output, one artifact
        # family each. No output type is privileged — a member is planned by
        # the canonical pipeline, so whatever it can produce, it can produce
        # here too (ADR 0019).
        if is_collection:
            out["scope"] = "each_item"
        if delivery:
            out["delivery"] = dict(delivery)
        outputs.append(out)

    # Subtitles chosen alongside a preset: embedded into the video when possible,
    # otherwise delivered as sidecar files (audio-only, or embedding disabled).
    # A collection member takes the same path as any single video, so this no
    # longer excludes playlists (ADR 0019).
    sidecar_subs = (
        subs_langs and "subtitles" not in want and not (video_on and embed_subs)
    )
    if sidecar_subs:
        sub_out: dict = {
            "id": "subtitles_main",
            "type": "subtitles",
            "required": False,
            "options": {"languages": subs_langs},
        }
        if delivery:
            sub_out["delivery"] = dict(delivery)
        outputs.append(sub_out)

    return {
        "schema_version": "1.0",
        "sources": [source_dict(url_clean, credential, provider_args)],
        "outputs": outputs,
    }


request_body = build_request()

download_label = (
    f"🎬  Download playlist ({len(entries)} videos)"
    if is_collection
    else "🎬  Download"
)
if st.button(
    download_label,
    type="primary",
    use_container_width=True,
    disabled=not (backend_ok and url_clean and request_body["outputs"]),
):
    try:
        created = client.submit(request_body)
        st.session_state.job_id = created["job_id"]
        for w in created.get("warnings", []):
            st.warning(f"{w['code']}: {w['message']}")
        st.rerun()
    except ApiError as exc:
        st.error(f"Request refused: {exc.body}")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Submit failed: {exc}")

with st.expander("🧾 GenerationRequest (what will be sent)"):
    st.json(request_body)


# --- live job monitor (auto-refreshing fragment) -------------------------------


@st.fragment(run_every=2.0)
def render_job() -> None:
    job_id = st.session_state.job_id
    if not job_id:
        return
    try:
        job = client.job(job_id)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Job not found: {exc}")
        return

    status = job["status"]
    icon, color = display(status)
    steps = job.get("steps", [])
    done = sum(1 for s in steps if s["status"] == "succeeded")

    # One events fetch per refresh feeds both the live percentages and the
    # (filtered) events expander below. Presentation only: the engine already
    # emits a real percentage per step; the UI just stopped discarding it.
    try:
        events = client.events(job_id)
    except Exception:  # noqa: BLE001
        events = []
    percent: dict[str, float] = {}
    for e in events:
        if e["type"] == "step.progress":
            data = e.get("data") or {}
            progress = data.get("progress") or {}
            if progress.get("current") is not None:
                percent[data.get("step_id", "")] = progress["current"]

    st.divider()
    st.markdown(
        f"### {icon} <span style='color:{color}'>{status}</span> "
        f"<span style='color:#5b6472;font-size:.8rem'>· {job_id[:16]}</span>",
        unsafe_allow_html=True,
    )
    if steps:
        st.progress(done / len(steps), text=f"{done}/{len(steps)} steps")
        for s in steps:
            si = display(s["status"])[0]
            # A collection member announces itself as "3/6 · Title" (the API
            # joins that context onto the step); other steps keep their id.
            if s.get("item_title"):
                ordinal = f"{s.get('member_index')}/{s.get('member_total')}"
                name = f"{ordinal} · {s['item_title']}"
            else:
                name = s["step_id"]
            detail = s["status"]
            if s["status"] == "running" and s["step_id"] in percent:
                detail = f"downloading · {percent[s['step_id']]:.0f}%"
            err = f" · {s['error']}" if s["error"] else ""
            st.markdown(
                f"<div class='step'>{si} {name} "
                f"<span style='color:#5b6472'>{detail}{err}</span></div>",
                unsafe_allow_html=True,
            )
    if job.get("error"):
        st.error(job["error"])

    try:
        artifacts = client.artifacts(job_id)
    except Exception:  # noqa: BLE001
        artifacts = []
    if artifacts:
        st.markdown("**Artifacts**")
        for a in artifacts:
            reused = a["provenance"]["attributes"].get("reused_from_artifact_id")
            cols = st.columns([3, 2, 2])
            shown = a.get("display_filename") or a["filename"]
            cols[0].markdown(f"`{shown}`" + (" ♻" if reused else ""))
            detail = f"{a['media_type']} · {a['size_bytes'] / 1024:.1f} KiB"
            if a.get("delivered_path"):
                detail += f" · in your library: {a['delivered_path']}"
            cols[1].caption(detail)
            cols[2].link_button(
                "⬇︎ download",
                f"{PUBLIC_API_URL}/api/v1/artifacts/{a['id']}/content",
                use_container_width=True,
            )
    elif status in TERMINAL:
        st.caption("no artifacts")

    a1, a2 = st.columns(2)
    if a1.button("Cancel", disabled=status in TERMINAL, use_container_width=True):
        client.cancel(job_id)
    if a2.button("Retry", disabled=status not in TERMINAL, use_container_width=True):
        st.session_state.job_id = client.retry(job_id)["job_id"]
        st.rerun(scope="app")

    with st.expander("Events"):
        # step.progress fires every few seconds per step; dumping each one
        # buries the eight events that matter. They are summarized to a count —
        # the live percentage is already on the step lines above.
        shown = [e for e in events if e["type"] != "step.progress"]
        skipped = len(events) - len(shown)
        lines = [
            f"{e['sequence']:>3} {e['type']} {e['data'] if e['data'] else ''}"
            for e in shown
        ]
        if skipped:
            lines.append(f"    … {skipped} step.progress events (shown live above)")
        st.code("\n".join(lines) or "—")

    with st.expander("Logs (yt-dlp / ffmpeg output, per step)"):
        try:
            logs = client.logs(job_id).get("logs", {})
        except Exception:  # noqa: BLE001
            logs = {}
        if not logs:
            st.caption("no logs yet")
        for step_id, streams in logs.items():
            st.markdown(f"**{step_id}**")
            for stream_name in ("stdout", "stderr"):
                text = (streams.get(stream_name) or "").strip()
                if text:
                    tail = "\n".join(text.splitlines()[-12:])
                    st.code(tail, language=None)


render_job()
