"""Content Admin — the operations console for the Content back-end.

The back-end is the heart of the project; this app is its cockpit. It is
strictly an **observability + control** client of the public API — it does NOT
create downloads (that is HomeTube / Content Studio). Here you watch and pilot
the engine: the effective configuration (every CONTENT_* variable in force),
jobs (past and in-flight) with their steps, events, logs and artifacts; where
things land (delivery) and what the cache holds; and the public contract, with
a raw request tester.

Replaces the old, incoherent `/ui` page that used to offer a download form.
"""

import json
import os
from datetime import datetime, timezone

import streamlit as st
from content_sdk import legal, notifications
from content_sdk.compat import ApiError, ContentClient
from content_sdk.status import capability_display, display

API_URL = os.getenv("CONTENT_API_URL", "http://localhost:8000")
PUBLIC_API_URL = os.getenv("CONTENT_PUBLIC_API_URL", API_URL).rstrip("/")

# This app's own release, in lockstep with the whole monorepo (`make version`
# guards every declaration). Passed to the notification bar so the launch check
# can compare it against the backend's version and warn on a torn deployment.
__version__ = "0.1.0"

TERMINAL = {"succeeded", "partially_succeeded", "failed", "cancelled"}
CATEGORY_ORDER = [
    "storage",
    "execution",
    "providers",
    "credentials",
    "language",
    "security",
]
CATEGORY_ICON = {
    "storage": "💾",
    "execution": "⚙️",
    "providers": "🔌",
    "credentials": "🔑",
    "language": "🌐",
    "security": "🛡️",
}


def _human_bytes(n: int) -> str:
    size = float(n or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def _ago(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = (datetime.now(timezone.utc) - ts).total_seconds()
    except ValueError:
        return iso
    if delta < 60:
        return f"{delta:.0f}s ago"
    if delta < 3600:
        return f"{delta / 60:.0f}m ago"
    if delta < 86400:
        return f"{delta / 3600:.1f}h ago"
    return f"{delta / 86400:.1f}d ago"


st.set_page_config(page_title="Content Admin", page_icon="🛠️", layout="wide")
st.markdown(
    """
    <style>
      .block-container { padding-top: 2rem; max-width: 1240px; }
      .ca-brand .name { font-size: 2.3rem; font-weight: 800; letter-spacing:.3px;
        background: linear-gradient(90deg,#8B5CF6,#D946EF);
        -webkit-background-clip:text; background-clip:text; color:transparent; }
      .ca-brand .sub { color:#8b93a3; font-size:.9rem; margin-top:-.2rem; }
      .ca-card { background:#171a23; border:1px solid #2a2f3a; border-radius:14px;
        padding:14px 16px; margin:.2rem 0 1rem 0; }
      .ca-card h4 { margin:0 0 .5rem 0; font-size:.95rem; color:#c9cfda; }
      .step { font-family: ui-monospace, monospace; font-size:.82rem; padding:1px 0; }
      .pill { display:inline-block; font-size:.72rem; padding:2px 9px;
        border-radius:99px; border:1px solid #2a2f3a; color:#8b93a3;
        margin:2px 4px 0 0; }
      .env-row { display:grid; grid-template-columns: 300px 1fr; gap:10px;
        padding:7px 0; border-bottom:1px solid #21252f; align-items:baseline; }
      .env-name { font-family: ui-monospace, monospace; font-size:.82rem;
        color:#c9cfda; }
      .env-val { font-family: ui-monospace, monospace; font-size:.82rem;
        color:#e6e9ef; word-break:break-all; }
      .env-desc { color:#7b8494; font-size:.76rem; margin-top:2px; }
      .b-env { background:rgba(139,92,246,.16); border:1px solid #6d4bd0;
        color:#c4b1f5; }
      .b-def { color:#8b93a3; }
      .badge { display:inline-block; font-size:.68rem; padding:1px 8px;
        border-radius:99px; border:1px solid #2a2f3a; margin-left:6px;
        vertical-align:middle; }
      div[data-testid="stExpander"] details { border-color:#2a2f3a;
        border-radius:12px; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def get_client(base_url: str) -> ContentClient:
    return ContentClient(base_url)


client = get_client(API_URL)

backend_ok = False
version = "?"
try:
    version = client.health().get("version", "?")
    backend_ok = True
except Exception as exc:  # noqa: BLE001
    st.error(f"⚠️ Back-end unreachable at {API_URL} — {exc}")

st.markdown(
    "<div class='ca-brand'><div class='name'>🛠️ Content Admin</div>"
    "<div class='sub'>backend cockpit — observe &amp; pilot the engine</div></div>",
    unsafe_allow_html=True,
)
if not backend_ok:
    st.stop()

# Instance notifications (a newer release, a stale yt-dlp), plus the one check
# only a client can make: this UI's version against the backend's, once per
# session — a mismatch means a torn deployment (one image updated, not the
# other). The engine decides everything else worth saying; this only renders.
# Shared with the other UIs through the SDK — never copy-pasted (D-21).
notifications.render_streamlit(client, app_version=__version__)

with st.sidebar:
    st.caption(f"🟢 back-end v{version}")
    st.caption(f"API base: `{PUBLIC_API_URL}`")
    # AGPL §13: the source offer, from the instance (never hard-coded).
    legal.render_streamlit_footer(client)
    st.caption(
        f"[Swagger /docs]({PUBLIC_API_URL}/docs) · "
        f"[OpenAPI]({PUBLIC_API_URL}/openapi.json)"
    )
    st.divider()
    st.caption("The Jobs panel updates live on its own.")
    if st.button("🔄 Refresh now", use_container_width=True):
        st.rerun()

# One system() read powers the Overview and Environment tabs.
try:
    system = client.system()
except Exception as exc:  # noqa: BLE001
    system = {}
    st.error(f"/system failed: {exc}")


@st.fragment(run_every=2.0)
def render_job_detail(job_id: str) -> None:
    """Live job detail — auto-refreshes every 2s (scoped to this panel, so the
    rest of the console stays still). Steps, events, logs, artifacts + actions."""
    try:
        job = client.job(job_id)
    except Exception as exc:  # noqa: BLE001
        st.error(f"job failed: {exc}")
        return
    icon, color = display(job["status"])
    live = "  🔴 live" if job["status"] not in TERMINAL else ""
    st.markdown(
        f"### {icon} <span style='color:{color}'>{job['status']}</span>"
        f"<span style='color:#5b6472;font-size:.7rem'>{live}</span>",
        unsafe_allow_html=True,
    )
    st.caption(
        f"`{job_id}` · created {_ago(job['created_at'])} · "
        f"finished {_ago(job.get('finished_at'))} · policy {job['failure_policy']}"
        + (f" · retry_of {job['retry_of']}" if job.get("retry_of") else "")
    )
    if job.get("error"):
        st.error(job["error"])

    a1, a2 = st.columns(2)
    if a1.button(
        "Cancel", disabled=job["status"] in TERMINAL, use_container_width=True
    ):
        client.cancel(job_id)
        st.rerun()
    if a2.button(
        "Retry", disabled=job["status"] not in TERMINAL, use_container_width=True
    ):
        client.retry(job_id)
        st.rerun()

    steps = job.get("steps", [])
    done = sum(1 for s in steps if s["status"] == "succeeded")
    if steps:
        st.progress(done / len(steps), text=f"{done}/{len(steps)} steps")
    for s in steps:
        si = display(s["status"])[0]
        err = f" · {s['error']}" if s.get("error") else ""
        st.markdown(
            f"<div class='step'>{si} {s['step_id']} "
            f"<span style='color:#5b6472'>{s['status']}{err}</span></div>",
            unsafe_allow_html=True,
        )

    with st.expander("Submitted request (GenerationRequest)"):
        st.json(job.get("request") or {})
    with st.expander("Artifacts"):
        try:
            arts = client.artifacts(job_id)
        except Exception:  # noqa: BLE001
            arts = []
        for art in arts:
            prov = art.get("provenance", {})
            st.markdown(
                f"`{art['filename']}` · {art['media_type']} · "
                f"{_human_bytes(art['size_bytes'])}"
            )
            st.caption(
                f"producer: {prov.get('producer', {}).get('operation', '?')}"
                f" · {art.get('checksum', '')[:20]}"
            )
            st.link_button(
                "⬇︎ download",
                f"{PUBLIC_API_URL}/api/v1/artifacts/{art['id']}/content",
            )
        if not arts:
            st.caption("no artifacts")
    with st.expander("Events (timeline)"):
        try:
            events = client.events(job_id)
        except Exception:  # noqa: BLE001
            events = []
        st.code(
            "\n".join(
                f"{e['sequence']:>3} {e['type']} {e.get('data') or ''}" for e in events
            )
            or "—"
        )
    with st.expander("Logs (per step)"):
        try:
            logs = client.logs(job_id).get("logs", {})
        except Exception:  # noqa: BLE001
            logs = {}
        if not logs:
            st.caption("no logs")
        for step_id, streams in logs.items():
            st.markdown(f"**{step_id}**")
            for stream, text in streams.items():
                st.caption(stream)
                st.code(text or "—")


tab_over, tab_caps, tab_env, tab_jobs, tab_storage, tab_contract = st.tabs(
    [
        "📊 Overview",
        "🧩 Capabilities",
        "⚙️ Environment",
        "📋 Jobs",
        "💾 Storage & Cache",
        "📜 Contract & API",
    ]
)


# --- Overview ------------------------------------------------------------------

with tab_over:
    if system:
        c = st.columns(4)
        c[0].metric("Version", system["version"])
        c[1].metric("Cache", "on" if system["cache_enabled"] else "off")
        c[2].metric("Max concurrent", system["max_concurrent_jobs"])
        c[3].metric("Analysis TTL", f"{system['analysis_ttl_hours']:.0f} h")

        # Live pulse of the queue, computed from the job list.
        try:
            recent = client.list_jobs(limit=100)
        except Exception:  # noqa: BLE001
            recent = []
        counts: dict[str, int] = {}
        for job in recent:
            counts[job["status"]] = counts.get(job["status"], 0) + 1
        active = sum(v for k, v in counts.items() if k not in TERMINAL)
        st.markdown(
            f"<h4 style='margin:.6rem 0 .2rem'>Jobs pulse "
            f"<span class='badge b-env'>{active} active</span></h4>",
            unsafe_allow_html=True,
        )
        pulse = "".join(
            f"<span class='pill'>{display(k)[0]} {k}: {v}</span>"
            for k, v in sorted(counts.items())
        )
        st.markdown(
            pulse or "<span class='env-desc'>no jobs yet</span>",
            unsafe_allow_html=True,
        )

        st.subheader("Runners — providers & processors")
        st.dataframe(
            [
                {
                    "name": r["name"],
                    "kind": r["kind"],
                    "available": "✅" if r["available"] else "—",
                    "location": r["location"],
                    "tool_version": r["tool_version"],
                    "operations": ", ".join(r["operations"]),
                }
                for r in system["runners"]
            ],
            use_container_width=True,
            hide_index=True,
        )

        cols = st.columns(2)
        with cols[0]:
            lang = system["language"]
            st.markdown(
                "<div class='ca-card'><h4>🌐 Language preferences</h4>"
                f"primary <code>{lang['primary'] or '—'}</code> · "
                f"secondaries <code>{', '.join(lang['secondaries']) or '—'}</code><br>"
                f"VO first <code>{lang['vo_first']}</code> · "
                f"primary subs <code>{lang['primary_include_subtitles']}</code></div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                "<div class='ca-card'><h4>🔑 Credentials (ids only)</h4>"
                f"<code>{', '.join(system['credentials']) or '—'}</code></div>",
                unsafe_allow_html=True,
            )
        with cols[1]:
            paths = "".join(
                f"<span class='pill'>{k}</span> <code>{v}</code><br>"
                for k, v in system["paths"].items()
            )
            st.markdown(
                f"<div class='ca-card'><h4>📁 Storage paths</h4>{paths}</div>",
                unsafe_allow_html=True,
            )


# --- Capabilities (architecture inspector) ------------------------------------


def _reason_str(reason: dict | None) -> str:
    if not reason:
        return ""
    code = reason.get("code", "")
    extra = (
        reason.get("missing_materials")
        or reason.get("missing_operations")
        or reason.get("blocked_operations")
        or []
    )
    return f"{code}" + (f" ({', '.join(extra)})" if extra else "")


with tab_caps:
    st.caption(
        "The capability layer (ADR 0013): resolve what a source can produce, and "
        "inspect the catalog → operations → implementations that decide it."
    )
    st.subheader("Resolve a source")
    st.caption("Paste a URL (or file path) — the engine answers what it can produce.")
    rc1, rc2 = st.columns([3, 1])
    resolve_uri = rc1.text_input(
        "source", "https://www.youtube.com/watch?v=…", label_visibility="collapsed"
    )
    stype = rc2.selectbox("type", ["url", "file"], label_visibility="collapsed")
    if st.button("Resolve capabilities", type="primary"):
        src = {"id": "s", "type": stype}
        src["uri" if stype == "url" else "path"] = resolve_uri.strip()
        try:
            res = client.capabilities([src])["sources"][0]
            st.markdown(
                f"**{res.get('title') or res['source_id']}** · "
                f"`{res.get('resource_type', '?')}`"
            )
            for c in res["capabilities"]:
                icon, color = capability_display(c["status"])
                derived = (
                    f" ← {', '.join(c['derived_from'])}"
                    if c.get("derived_from")
                    else ""
                )
                reason = _reason_str(c.get("reason"))
                st.markdown(
                    f"<div class='step'>{icon} <b>{c['id']}</b> "
                    f"<span style='color:{color}'>{c['status']}</span>{derived}"
                    f"<span style='color:#7b8494'>"
                    f"{('  ·  ' + reason) if reason else ''}</span></div>",
                    unsafe_allow_html=True,
                )
        except ApiError as exc:
            st.error(f"resolve failed: {exc.body}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"resolve failed: {exc}")

    st.divider()
    st.subheader("Architecture")
    try:
        arch = client.catalog()
    except Exception as exc:  # noqa: BLE001
        arch = {}
        st.error(f"/catalog failed: {exc}")
    if arch:
        st.markdown("**Public capabilities → recipe variants → operations**")
        st.dataframe(
            [
                {
                    "capability": c["id"],
                    "output": c["output_type"],
                    "variants": " · ".join(
                        f"{v['id']} [{' → '.join(v['operations'])}]"
                        for v in c["variants"]
                    ),
                }
                for c in arch["capabilities"]
            ],
            use_container_width=True,
            hide_index=True,
        )
        st.markdown("**Operations → implementations (installed & available)**")
        st.dataframe(
            [
                {
                    "operation": o["operation"],
                    "kinds": f"{','.join(o['input_kinds'])} → {','.join(o['output_kinds'])}",
                    "implementations": ", ".join(
                        f"{i['runner']} v{i['version']} {'✅' if i['available'] else '—'}"
                        for i in o["implementations"]
                    )
                    or "⛔️ none installed",
                }
                for o in arch["operations"]
            ],
            use_container_width=True,
            hide_index=True,
        )


# --- Environment ---------------------------------------------------------------

with tab_env:
    env_vars = system.get("environment", []) if system else []
    if not env_vars:
        st.info("No environment inventory available from /system.")
    else:
        overridden = sum(1 for v in env_vars if v["is_set"])
        top = st.columns([1, 1, 2])
        top[0].metric("Variables", len(env_vars))
        top[1].metric("Set in env", f"{overridden}/{len(env_vars)}")
        only_set = top[2].toggle("Only variables set in the environment", value=False)
        st.caption(
            "Every `CONTENT_*` variable the engine reads, with its **effective** "
            "value. 🟣 = set in the environment · default otherwise. "
            "🔒 secrets are never shown — only presence & length."
        )

        shown = [v for v in env_vars if v["is_set"]] if only_set else env_vars
        by_cat: dict[str, list[dict]] = {}
        for v in shown:
            by_cat.setdefault(v["category"], []).append(v)

        for cat in CATEGORY_ORDER:
            rows = by_cat.get(cat)
            if not rows:
                continue
            icon = CATEGORY_ICON.get(cat, "•")
            st.markdown(f"#### {icon} {cat}")
            html = []
            for v in rows:
                src = (
                    "<span class='badge b-env'>env</span>"
                    if v["is_set"]
                    else "<span class='badge b-def'>default</span>"
                )
                lock = "🔒 " if v["secret"] else ""
                html.append(
                    "<div class='env-row'>"
                    f"<div><span class='env-name'>{lock}{v['name']}</span>{src}</div>"
                    f"<div><span class='env-val'>{v['value']}</span>"
                    f"<div class='env-desc'>{v['description']}</div></div></div>"
                )
            st.markdown("".join(html), unsafe_allow_html=True)


# --- Jobs ----------------------------------------------------------------------

with tab_jobs:
    left, right = st.columns([2, 3])
    with left:
        limit = st.slider("How many jobs", 5, 100, 25, key="joblimit")
        try:
            jobs = client.list_jobs(limit=limit)
        except Exception as exc:  # noqa: BLE001
            jobs = []
            st.error(f"jobs failed: {exc}")
        statuses = sorted({j["status"] for j in jobs})
        flt = st.multiselect("Filter status", statuses, default=statuses)
        shown = [j for j in jobs if j["status"] in flt]
        st.caption(f"{len(shown)}/{len(jobs)} jobs")
        job_ids = [j["job_id"] for j in shown]
        labels = {
            j["job_id"]: (
                f"{display(j['status'])[0]} {j['job_id'][4:16]} · "
                f"{j['status']} · {_ago(j.get('created_at'))}"
            )
            for j in shown
        }
        selected = (
            st.radio(
                "Select a job",
                job_ids,
                format_func=lambda i: labels.get(i, i),
                label_visibility="collapsed",
            )
            if job_ids
            else None
        )

    with right:
        if selected:
            render_job_detail(selected)
        else:
            st.info("Select a job on the left.")


# --- Storage & Cache -----------------------------------------------------------

with tab_storage:
    try:
        report = client.storage()
    except Exception as exc:  # noqa: BLE001
        report = {}
        st.error(f"/storage failed: {exc}")
    if report:
        c = st.columns(4)
        c[0].metric(
            "Jobs",
            _human_bytes(report["jobs"]["bytes"]),
            f"{report['jobs']['count']} jobs · {report['jobs']['files']} files",
        )
        c[1].metric(
            "Delivery",
            _human_bytes(report["delivery"]["bytes"]),
            f"{report['delivery']['folders']} folders",
        )
        c[2].metric(
            "Tmp",
            _human_bytes(report["tmp"]["bytes"]),
            f"{report['tmp']['files']} files",
        )
        c[3].metric(
            "Cache",
            _human_bytes(report["cache"]["bytes"]),
            f"{'on' if report['cache']['enabled'] else 'off'} · "
            f"{report['cache']['cached_analyses']} analyses",
        )
        st.subheader("Paths")
        for family, data in report.items():
            st.markdown(
                f"<span class='pill'>{family}</span> `{data['path']}` — "
                f"{_human_bytes(data['bytes'])}, {data.get('files', 0)} files",
                unsafe_allow_html=True,
            )
        st.caption(
            "Lifecycles (docs/storage.md): tmp = disposable · work = job "
            "intermediates · artifacts = persistent results · cache = reusable."
        )

    st.divider()
    st.subheader("Analysis cache")
    try:
        cache = client.cache()
    except Exception as exc:  # noqa: BLE001
        cache = {}
        st.error(f"/cache failed: {exc}")
    if cache:
        analyses = cache.get("analyses", [])
        cc = st.columns([2, 1])
        cc[0].caption(
            f"{'🟢 enabled' if cache.get('enabled') else '⚪️ disabled'} · "
            f"TTL {cache.get('ttl_hours', 0):.0f} h · {len(analyses)} cached"
        )
        if cc[1].button(
            "🗑️ Purge cache", use_container_width=True, disabled=not analyses
        ):
            res = client.purge_cache()
            st.success(
                f"Purged {res['purged_analyses']} analyses / "
                f"{res['purged_files']} files."
            )
            st.rerun()
        if analyses:
            st.dataframe(
                [
                    {
                        "title": a.get("title") or "—",
                        "type": a.get("resource_type") or "—",
                        "cached": _ago(a.get("created_at")),
                        "resource_key": a.get("resource_key", "")[:40],
                    }
                    for a in analyses
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("No cached analyses.")


# --- Contract & API ------------------------------------------------------------

with tab_contract:
    st.subheader("Public contract")
    st.markdown(
        f"Interactive docs: [Swagger UI]({PUBLIC_API_URL}/docs) · "
        f"[ReDoc]({PUBLIC_API_URL}/redoc) · "
        f"[openapi.json]({PUBLIC_API_URL}/openapi.json)"
    )
    with st.expander("GenerationRequest schema (from OpenAPI)"):
        try:
            schema = client.openapi()
            gen = (
                schema.get("components", {})
                .get("schemas", {})
                .get("GenerationRequest", {})
            )
            st.json(gen or {"note": "GenerationRequest schema not found"})
        except Exception as exc:  # noqa: BLE001
            st.error(f"openapi fetch failed: {exc}")

    st.subheader("API tester")
    st.caption("Send a raw request to the backend (path relative to /api/v1).")
    st.session_state.setdefault("api_path", "/jobs")
    quick = st.columns(4)
    for i, ep in enumerate(["/health", "/system", "/storage", "/jobs"]):
        if quick[i].button(ep, use_container_width=True):
            st.session_state["api_path"] = ep
    tc = st.columns([1, 3])
    method = tc[0].selectbox("method", ["GET", "POST"])
    path = tc[1].text_input("path", key="api_path", help="e.g. /health, /system")
    body_raw = st.text_area(
        "JSON body (POST)",
        "",
        height=140,
        placeholder='{"sources": [...], "outputs": [...]}',
    )
    if st.button("Send request", type="primary"):
        body = None
        if method == "POST" and body_raw.strip():
            try:
                body = json.loads(body_raw)
            except ValueError as exc:
                st.error(f"invalid JSON body: {exc}")
                body = ...  # sentinel to skip the call
        if body is not ...:
            try:
                status, parsed = client.call_raw(method, path, body)
                (st.success if 200 <= status < 300 else st.error)(f"HTTP {status}")
                st.json(parsed)
            except ApiError as exc:
                st.error(f"{exc}")
            except Exception as exc:  # noqa: BLE001
                st.error(f"request failed: {exc}")
