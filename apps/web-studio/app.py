"""Content Studio — the general-purpose Streamlit front-end for Content.

A pure API client of the Content back-end (/api/v1) that exposes the whole
public contract rather than a single use case: several sources (url / file /
text), every output type (video, audio, subtitles, thumbnail, metadata,
transcript, summary) with their options, preferences and constraints. The form
is dynamic — outputs and their controls appear from what you ask for. No
business logic here: the back-end validates, plans and executes.

Its sibling ``frontend/`` (HomeTube) is a specialized skin of the same engine;
both speak the same GenerationRequest contract.
"""

import os

import streamlit as st
from content_sdk import legal, notifications
from content_sdk.compat import ApiError, ContentClient
from content_sdk.status import better_status, display, is_producible

API_URL = os.getenv("CONTENT_API_URL", "http://localhost:8000")
PUBLIC_API_URL = os.getenv("CONTENT_PUBLIC_API_URL", API_URL).rstrip("/")

# This app's own release, in lockstep with the whole monorepo (`make version`
# guards every declaration). Passed to the notification bar so the launch check
# can compare it against the backend's version and warn on a torn deployment.
__version__ = "0.3.3"

# Fallback ordering only. The real list comes from the server's resolved
# capabilities, each of which carries its own `output_type` — see
# `_output_types_from`. A hardcoded allowlist here silently hid every capability
# added since it was written (text.extract, markdown.export, pdf.render), which
# is exactly the drift ADR 0013 R6 exists to prevent: the catalog is the only
# public list, and clients must not keep a second one.
OUTPUT_TYPES = [
    "video",
    "audio",
    "subtitles",
    "thumbnail",
    "keyframes",
    "metadata",
    "transcript",
    "summary",
    "translation",
    "chapters",
    "document_text",
    "markdown",
    "pdf",
]
SOURCE_TYPES = ["url", "file", "text"]

SB_PRESETS: dict[str, dict | None] = {
    "disabled": None,
    "default": {
        "remove": ["sponsor", "interaction", "selfpromo"],
        "mark": ["intro", "preview", "outro"],
    },
    "aggressive": {
        "remove": ["sponsor", "selfpromo", "interaction", "intro", "outro", "preview"],
        "mark": [],
    },
    "minimal": {"remove": ["sponsor"], "mark": []},
}

TERMINAL = {"succeeded", "partially_succeeded", "failed", "cancelled"}
CAP_COLOR = {
    "available": "#3fca6b",
    "derivable": "#3fca6b",
    "unknown": "#e8b64c",
    "unavailable": "#5b6472",
    "restricted": "#e85d5d",
}


# The dynamic UI is driven by the resolved capabilities (ADR 0013). Each
# resolved capability states its own `output_type`, so Studio reads that instead
# of keeping a second copy of the catalog — several capabilities legitimately
# share one output type (video.download / video.clip, thumbnail.download /
# thumbnail.generate) and the best status among them wins.
def _capability_output(capability: dict) -> str:
    return str(capability.get("output_type") or "")


def _reason_text(reason: dict | None) -> str:
    if not reason:
        return "not available for this source"
    code = reason.get("code", "")
    if code == "missing_material":
        return f"source has no {', '.join(reason.get('missing_materials', [])) or 'material'}"
    if code == "implementation_unavailable":
        return f"needs a runner ({', '.join(reason.get('missing_operations', []))})"
    if code == "policy_restricted":
        return "blocked by policy"
    return code or "not available"


st.set_page_config(page_title="Content Studio", page_icon="🧩", layout="wide")

st.markdown(
    """
    <style>
      .block-container { padding-top: 2rem; max-width: 1100px; }
      .cs-brand .name { font-size: 2.3rem; font-weight: 800; letter-spacing:.3px;
        background: linear-gradient(90deg,#6366f1,#a855f7,#d946ef);
        -webkit-background-clip:text; background-clip:text; color:transparent; }
      .cs-brand .sub { color:#8b93a3; font-size:.9rem; margin-top:-.2rem; }
      .pill { display:inline-block; font-size:.72rem; padding:2px 9px;
        border-radius:99px; border:1px solid #2a2f3a; color:#8b93a3;
        margin:2px 4px 2px 0; }
      .step { font-family: ui-monospace, monospace; font-size:.82rem; padding:2px 0; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def get_client(base_url: str) -> ContentClient:
    return ContentClient(base_url)


client = get_client(API_URL)
st.session_state.setdefault("analysis", None)
st.session_state.setdefault("job_id", None)


# --- back-end health + config --------------------------------------------------

backend_ok = False
version = "?"
credentials: list[str] = []
try:
    health = client.health()
    backend_ok = health.get("status") == "ok"
    version = health.get("version", "?")
    credentials = client.config().get("credentials", [])
except Exception as exc:  # noqa: BLE001
    st.error(f"⚠️ Back-end unreachable at {API_URL} — {exc}")

with st.sidebar:
    st.markdown("### 🧩 Content Studio")
    st.caption(f"🟢 back-end v{version}" if backend_ok else "🔴 back-end offline")
    # AGPL §13: the source offer, from the instance (never hard-coded).
    legal.render_streamlit_footer(client)
    if backend_ok:
        st.caption(f"[API · /docs]({PUBLIC_API_URL}/docs)")
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


st.markdown(
    "<div class='cs-brand'><div class='name'>Content Studio</div>"
    "<div class='sub'>every source, every output — the full contract</div></div>",
    unsafe_allow_html=True,
)

# Instance notifications (a newer release, a stale yt-dlp), plus the one check
# only a client can make: this UI's version against the backend's, once per
# session — a mismatch means a torn deployment (one image updated, not the
# other). The engine decides everything else worth saying; this only renders.
# Shared with the other UIs through the SDK — never copy-pasted (D-21).
notifications.render_streamlit(client, app_version=__version__)


# --- sources -------------------------------------------------------------------

st.subheader("1 · Sources")
n_sources = st.number_input("How many sources?", 1, 8, 1, key="n_sources")


def source_editor(i: int) -> dict:
    sid = f"s{i + 1}"
    cols = st.columns([1, 3])
    stype = cols[0].selectbox("Type", SOURCE_TYPES, key=f"stype-{i}")
    src: dict = {"id": sid, "type": stype}
    if stype == "url":
        uri = cols[1].text_input("URL", key=f"uri-{i}", placeholder="https://…")
        src["uri"] = uri.strip()
        if credentials:
            cred = cols[1].selectbox("Auth", ["none", *credentials], key=f"cred-{i}")
            if cred != "none":
                src["auth"] = {"credential_id": cred}
    elif stype == "file":
        path = cols[1].text_input("Path (under an allowed input root)", key=f"path-{i}")
        src["path"] = path.strip()
    else:  # text
        content = cols[1].text_area("Text content", key=f"text-{i}", height=100)
        src["content"] = content
    return src


sources = [source_editor(i) for i in range(int(n_sources))]
valid_sources = [
    s
    for s in sources
    if (s["type"] == "url" and s.get("uri"))
    or (s["type"] == "file" and s.get("path"))
    or (s["type"] == "text" and s.get("content"))
]
source_ids = [s["id"] for s in valid_sources]

if backend_ok and valid_sources and st.button("🔍 Analyze sources", type="secondary"):
    with st.spinner("Analyzing…"):
        try:
            st.session_state.analysis = client.analyze(valid_sources)
            # Resolved capabilities are the server's answer to "what can I do
            # with this source?" (ADR 0013) — the feed this UI renders from.
            st.session_state.capabilities = client.capabilities(valid_sources)
        except ApiError as exc:
            st.session_state.analysis = None
            st.session_state.capabilities = None
            st.error(f"Analysis refused: {exc.body}")
        except Exception as exc:  # noqa: BLE001
            st.session_state.analysis = None
            st.session_state.capabilities = None
            st.error(f"Analysis failed: {exc}")

analysis = st.session_state.analysis
resolved = st.session_state.get("capabilities") or {}
# Per source: the output types it can produce (+ status/reason) and its facts —
# so the outputs section below can be capability-driven, not just capability-lit.
producible_by_source: dict[str, set[str]] = {}
out_status_by_source: dict[str, dict[str, str]] = {}
out_reason_by_source: dict[str, dict[str, dict]] = {}
media_by_source: dict[str, dict] = {}
if analysis:
    resolved_by_source = {s["source_id"]: s for s in resolved.get("sources", [])}
    for entry in analysis.get("sources", []):
        sid = entry["source_id"]
        res = entry["resource"]
        media = entry.get("media", {}) or {}
        subs = entry.get("subtitles", []) or []
        status: dict[str, str] = {}
        reason: dict[str, dict] = {}
        for c in resolved_by_source.get(sid, {}).get("capabilities", []):
            out = _capability_output(c)
            if not out:
                continue
            status[out] = better_status(status.get(out), c["status"])
            if c.get("reason") and out not in reason:
                reason[out] = c["reason"]
        out_status_by_source[sid] = status
        out_reason_by_source[sid] = reason
        media_by_source[sid] = media
        producible_by_source[sid] = {o for o, s in status.items() if is_producible(s)}

        # Clean summary: title, metrics, and a concise materials line.
        with st.container(border=True):
            meta = [f"`{res.get('resource_type', '?')}`"]
            if res.get("view_count") is not None:
                meta.append(f"👁 {res['view_count']:,}")
            if res.get("duration_seconds"):
                meta.append(f"⏱ {int(res['duration_seconds'])}s")
            st.markdown(
                f"**{sid}** · {res.get('title') or '(untitled)'} — " + " · ".join(meta)
            )
            tech = []
            if media.get("video_heights"):
                codecs = ", ".join(media.get("video_codecs", []))
                tech.append(
                    f"🎞️ up to {max(media['video_heights'])}p"
                    + (f" · {codecs}" if codecs else "")
                )
            if media.get("audio_languages"):
                tech.append(f"🎙️ {', '.join(media['audio_languages'])}")
            if subs:
                tech.append(f"💬 {', '.join(sorted({t['language'] for t in subs}))}")
            if tech:
                st.caption("　·　".join(tech))
            pills = "".join(
                f"<span class='pill' style='border-color:{CAP_COLOR.get(s, '#2a2f3a')};"
                f"color:{CAP_COLOR.get(s, '#8b93a3')}'>{o}: {s}</span>"
                for o, s in sorted(status.items())
            )
            st.markdown(pills or "", unsafe_allow_html=True)


# --- outputs -------------------------------------------------------------------

st.subheader("2 · Outputs")
st.caption("Enable the outputs you want; each is produced from a source.")


def output_options(otype: str, idx: int, media: dict | None = None) -> dict:
    media = media or {}
    opts: dict = {}
    if otype == "video":
        c = st.columns(4)
        # Resolutions/codecs are drawn from the chosen source's facts (R5), with
        # the standard ladder as a fallback when unknown.
        heights = sorted(set(media.get("video_heights") or []), reverse=True) or [
            2160,
            1440,
            1080,
            720,
            480,
            360,
        ]
        opts["selection"] = {
            "max_height": c[0].selectbox(
                "max height", heights, index=0, key=f"vh-{idx}"
            )
        }
        avail = media.get("video_codecs") or []
        codec_opts = ["auto"] + (
            [x for x in ("av1", "vp9", "h264") if x in avail]
            if avail
            else ["av1", "vp9", "h264"]
        )
        codec = c[1].selectbox("codec", codec_opts, key=f"vc-{idx}")
        if codec != "auto":
            opts["selection"]["video_codec"] = {"mode": "prefer", "value": codec}
        opts["container"] = c[2].selectbox(
            "container", ["source", "mkv", "mp4"], key=f"vct-{idx}"
        )
        sb = c[3].selectbox("sponsorblock", list(SB_PRESETS), key=f"vsb-{idx}")
        if SB_PRESETS[sb]:
            opts["sponsorblock"] = SB_PRESETS[sb]
    elif otype == "audio":
        c = st.columns(2)
        fmt = c[0].selectbox(
            "format", ["source", "opus", "mp3", "m4a"], key=f"af-{idx}"
        )
        if fmt != "source":
            opts["format"] = fmt
        sb = c[1].selectbox("sponsorblock", list(SB_PRESETS), key=f"asb-{idx}")
        if SB_PRESETS[sb]:
            opts["sponsorblock"] = SB_PRESETS[sb]
    elif otype == "subtitles":
        langs = st.text_input("languages (comma-sep)", "en", key=f"sl-{idx}")
        opts["languages"] = [x.strip() for x in langs.split(",") if x.strip()] or ["en"]
        opts["format"] = st.selectbox("format", ["srt", "vtt"], key=f"sf-{idx}")
    elif otype == "thumbnail":
        opts["format"] = st.selectbox("format", ["source", "jpeg"], key=f"tf-{idx}")
    elif otype == "transcript":
        c = st.columns(2)
        opts["language"] = c[0].text_input("language", "auto", key=f"tl-{idx}")
        opts["format"] = c[1].selectbox(
            "format", ["text", "json", "srt", "vtt"], key=f"tfm-{idx}"
        )
    elif otype == "summary":
        c = st.columns(2)
        opts["length"] = c[0].selectbox(
            "length", ["short", "medium", "long"], index=1, key=f"sul-{idx}"
        )
        opts["format"] = c[1].selectbox(
            "format", ["markdown", "text"], key=f"suf-{idx}"
        )
    elif otype == "chapters":
        opts["format"] = st.selectbox(
            "format", ["json", "ffmetadata"], key=f"chf-{idx}"
        )
    elif otype == "translation":
        c = st.columns(2)
        opts["target_language"] = c[0].text_input(
            "target language", "fr", key=f"trt-{idx}"
        )
        opts["source_language"] = c[1].text_input(
            "source language", "auto", key=f"trs-{idx}"
        )
    return opts


analyzed = bool(analysis)
outputs: list[dict] = []
# Every known output type is listed, including the ones this source cannot
# produce: they render blocked *with a reason*, which is more useful than
# vanishing (capability-driven, not merely capability-lit). Anything the server
# offers that this build has never heard of is appended, so a new capability
# appears without a Studio release.
_offered = {o for types in producible_by_source.values() for o in types}
_ordered = OUTPUT_TYPES + sorted(_offered - set(OUTPUT_TYPES))
for idx, otype in enumerate(_ordered):
    # Capability-driven (ADR 0013): which analyzed sources can produce this?
    producers = [s for s in source_ids if otype in producible_by_source.get(s, set())]
    blocked = analyzed and bool(source_ids) and not producers
    with st.container(border=True):
        head = st.columns([1, 3])
        enabled = head[0].checkbox(f"**{otype}**", key=f"en-{otype}", disabled=blocked)
        if blocked:
            reasons = {
                _reason_text(out_reason_by_source.get(s, {}).get(otype))
                for s in source_ids
            }
            head[1].caption(f"⛔️ no source can produce this — {'; '.join(reasons)}")
            continue
        if not enabled:
            continue
        pick_from = producers if (analyzed and producers) else source_ids
        chosen = pick_from[0] if pick_from else None
        with head[1]:
            if len(pick_from) > 1:
                chosen = st.selectbox("from source", pick_from, key=f"src-{otype}")
            if analyzed and chosen:
                stt = out_status_by_source.get(chosen, {}).get(otype)
                if stt and stt != "available":
                    st.caption(f"↳ {stt} on {chosen}")
        out: dict = {"id": f"{otype}_1", "type": otype}
        if chosen and len(source_ids) > 1:
            out["from_sources"] = [chosen]
        opts = output_options(otype, idx, media_by_source.get(chosen or "", {}))
        if opts:
            out["options"] = opts
        if otype in ("metadata", "thumbnail"):
            out["required"] = False
        outputs.append(out)


# --- preferences & constraints -------------------------------------------------

with st.expander("Preferences & constraints"):
    c = st.columns(3)
    pref_lang = c[0].text_input("preferences.language", "", key="pref-lang")
    optimize = c[1].selectbox(
        "optimize_for", ["balanced", "quality", "speed", "size"], key="pref-opt"
    )
    allow_cloud = c[2].checkbox("allow cloud providers", value=True, key="con-cloud")
    reuse = st.checkbox("reuse_existing (cache)", value=True, key="exec-reuse")

preferences: dict = {"optimize_for": optimize}
if pref_lang.strip():
    preferences["language"] = pref_lang.strip()
constraints = {"privacy": {"allow_cloud_providers": allow_cloud}}


def build_request() -> dict:
    return {
        "schema_version": "1.0",
        "sources": valid_sources,
        "outputs": outputs,
        "preferences": preferences,
        "constraints": constraints,
        "execution": {"reuse_existing": reuse},
    }


request_body = build_request()

st.subheader("3 · Launch")
with st.expander("GenerationRequest preview"):
    st.json(request_body)

if st.button(
    "🚀 Submit job",
    type="primary",
    use_container_width=True,
    disabled=not (backend_ok and valid_sources and outputs),
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


# --- live job monitor ----------------------------------------------------------


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
            # Collection members announce themselves as "3/6 · Title" (the
            # API joins that context onto the step); others keep their id.
            if s.get("item_title"):
                ordinal = f"{s.get('member_index')}/{s.get('member_total')}"
                name = f"{ordinal} · {s['item_title']}"
            else:
                name = s["step_id"]
            err = f" · {s['error']}" if s["error"] else ""
            st.markdown(
                f"<div class='step'>{si} {name} "
                f"<span style='color:#5b6472'>{s['status']}{err}</span></div>",
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
            cols = st.columns([3, 2, 2])
            cols[0].markdown(f"`{a['filename']}`")
            cols[1].caption(f"{a['media_type']} · {a['size_bytes'] / 1024:.1f} KiB")
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


render_job()
