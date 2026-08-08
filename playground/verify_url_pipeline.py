#!/usr/bin/env python3
"""Playground: traverse the URL → every output pipeline, feature by feature.

A reusable, dependency-free (stdlib only) end-to-end harness against a running
Content back-end. It submits real jobs for a YouTube URL, waits for each, and
checks status + artifacts + delivered files, printing a clear PASS/FAIL report.

Usage:
    python3 playground/verify_url_pipeline.py \
        [--api http://localhost:8010] [--cred youtube] \
        [--url https://www.youtube.com/watch?v=jNQXAC9IVRw] \
        [--playlist https://www.youtube.com/playlist?list=...] \
        [--keep]            # keep delivered files (default: clean up)

Covers (cuts excluded, per current scope): video (+quality/container),
audio (+format transcode), subtitles, thumbnail, metadata, transcript, summary,
embed chapters/subtitles, SponsorBlock, delivery folder+name, playlist analysis,
and cache reuse.
"""

import argparse
import json
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Delivery sub-folder, cleaned at the end. Must survive backend sanitization
# unchanged (no leading/trailing '._'), so the host path matches what is written.
FOLDER = "pgverify"
OUTPUT_DIR = Path(__file__).resolve().parent / "output" / FOLDER

RESET, GREEN, RED, DIM, BOLD = "\033[0m", "\033[32m", "\033[31m", "\033[2m", "\033[1m"


class Ctx:
    api = "http://localhost:8010"
    cred = "youtube"
    url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    playlist = (
        "https://www.youtube.com/playlist?list=PLbpi6ZahtOH6Blw3RGYpWkSByi_T7Rygb"
    )


def _req(method: str, path: str, body=None, params=None):
    url = f"{Ctx.api}/api/v1{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=140) as resp:
            return resp.status, json.loads(resp.read() or "null")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read())
        except Exception:  # noqa: BLE001
            return exc.code, exc.reason


def _auth() -> dict:
    # Only attach a credential when one is configured; "none"/empty means the
    # source is public and no server-side credential should be referenced.
    return {"auth": {"credential_id": Ctx.cred}} if Ctx.cred not in ("", "none") else {}


def source() -> dict:
    return {"id": "main", "type": "url", "uri": Ctx.url, **_auth()}


def submit_and_wait(outputs, timeout=180, reuse=True):
    body = {
        "schema_version": "1.0",
        "sources": [source()],
        "outputs": outputs,
        "execution": {"reuse_existing": reuse},
    }
    status, created = _req("POST", "/jobs", body)
    if status >= 400:
        return {"error": f"submit {status}: {created}"}
    job_id = created["job_id"]
    warnings = [w["code"] for w in created.get("warnings", [])]
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, job = _req("GET", f"/jobs/{job_id}")
        if job.get("status") in (
            "succeeded",
            "partially_succeeded",
            "failed",
            "cancelled",
        ):
            _, arts = _req("GET", f"/jobs/{job_id}/artifacts")
            _, events = _req("GET", f"/jobs/{job_id}/events")
            return {
                "job_id": job_id,
                "status": job["status"],
                "error": job.get("error"),
                "artifacts": arts,
                "events": events,
                "warnings": warnings,
            }
        time.sleep(2)
    return {"job_id": job_id, "error": "timeout"}


def delivered(name: str) -> list[Path]:
    if not OUTPUT_DIR.exists():
        return []
    return sorted(OUTPUT_DIR.glob(f"{name}*"))


# --- cases ---------------------------------------------------------------------


def _delivery(name: str) -> dict:
    return {"folder": FOLDER, "filename": name}


def case_video(name, opts, fname):
    out = {
        "id": "video_main",
        "type": "video",
        "options": opts,
        "delivery": _delivery(fname),
    }
    r = submit_and_wait([out])
    files = delivered(fname)
    ok = r.get("status") == "succeeded" and files and files[0].stat().st_size > 0
    detail = f"{r.get('status')} · {files[0].name if files else 'no file'}"
    return ok, detail, r


def run(cases):
    results = []
    for label, fn in cases:
        sys.stdout.write(f"  … {label:<42}")
        sys.stdout.flush()
        try:
            ok, detail, _ = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"exception: {exc}"
        mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"\r  [{mark}] {label:<42} {DIM}{detail}{RESET}")
        results.append((label, ok, detail))
    return results


def build_cases():
    def video_default():
        return case_video(
            "video default",
            {"selection": {"max_height": 360}, "container": "mkv"},
            "v_def",
        )

    def video_quality():
        return case_video(
            "video 360p mp4 h264",
            {
                "selection": {
                    "max_height": 360,
                    "video_codec": {"mode": "prefer", "value": "h264"},
                },
                "container": "mp4",
            },
            "v_h264",
        )

    def video_embed():
        return case_video(
            "video embed chapters+subs(en)",
            {
                "selection": {"max_height": 360},
                "container": "mkv",
                "processing": {"embed_chapters": True, "embed_subtitles": ["en"]},
            },
            "v_embed",
        )

    def video_sponsorblock():
        return case_video(
            "video + sponsorblock default",
            {
                "selection": {"max_height": 360},
                "container": "mkv",
                "sponsorblock": {"remove": ["sponsor"], "mark": ["intro"]},
            },
            "v_sb",
        )

    def video_cut():
        # keyframes cut: expect a delivered clip shorter than the source.
        return case_video(
            "video cut [2,7] keyframes",
            {
                "selection": {"max_height": 360},
                "container": "mkv",
                "cut": {"start": "2", "end": "7", "mode": "keyframes"},
            },
            "v_cut",
        )

    def audio_source():
        r = submit_and_wait(
            [{"id": "audio_main", "type": "audio", "delivery": _delivery("a_src")}]
        )
        files = delivered("a_src")
        return (
            r.get("status") == "succeeded" and bool(files),
            f"{r.get('status')} · {files[0].name if files else '-'}",
            r,
        )

    def audio_opus():
        r = submit_and_wait(
            [
                {
                    "id": "audio_main",
                    "type": "audio",
                    "options": {"format": "opus"},
                    "delivery": _delivery("a_opus"),
                }
            ]
        )
        files = delivered("a_opus")
        ok = r.get("status") == "succeeded" and files and files[0].suffix == ".opus"
        return ok, f"{r.get('status')} · {files[0].name if files else '-'}", r

    def subtitles():
        r = submit_and_wait(
            [
                {
                    "id": "subs",
                    "type": "subtitles",
                    "options": {"languages": ["en"]},
                    "delivery": _delivery("subs"),
                }
            ]
        )
        files = delivered("subs")
        return (
            r.get("status") == "succeeded" and bool(files),
            f"{r.get('status')} · {len(r.get('artifacts', []))} artifact(s)",
            r,
        )

    def thumbnail():
        r = submit_and_wait(
            [
                {
                    "id": "thumb",
                    "type": "thumbnail",
                    "required": False,
                    "delivery": _delivery("thumb"),
                }
            ]
        )
        return (
            r.get("status") == "succeeded" and bool(r.get("artifacts")),
            f"{r.get('status')} · {len(r.get('artifacts', []))} artifact(s)",
            r,
        )

    def metadata():
        r = submit_and_wait(
            [
                {
                    "id": "meta",
                    "type": "metadata",
                    "required": False,
                    "delivery": _delivery("meta"),
                }
            ]
        )
        return (
            r.get("status") == "succeeded" and bool(r.get("artifacts")),
            f"{r.get('status')}",
            r,
        )

    def transcript():
        r = submit_and_wait(
            [
                {
                    "id": "tr",
                    "type": "transcript",
                    "options": {"language": "auto", "format": "text"},
                    "delivery": _delivery("tr"),
                }
            ]
        )
        return (
            r.get("status") == "succeeded" and bool(r.get("artifacts")),
            f"{r.get('status')} · {len(r.get('artifacts', []))} artifact(s)",
            r,
        )

    def summary():
        r = submit_and_wait(
            [
                {
                    "id": "sum",
                    "type": "summary",
                    "options": {"length": "short", "format": "markdown"},
                    "delivery": _delivery("sum"),
                }
            ],
            timeout=240,
        )
        st = r.get("status")
        ok = st == "succeeded" and bool(r.get("artifacts"))
        note = "" if ok else " (needs Ollama)"
        return ok, f"{st}{note}", r

    def cache_reuse():
        # Same audio twice: the 2nd must reuse the 1st acquisition.
        submit_and_wait([{"id": "audio_main", "type": "audio"}])
        r2 = submit_and_wait([{"id": "audio_main", "type": "audio"}])
        reused = any(
            "reused_from_job" in (e.get("data") or {}) for e in r2.get("events", [])
        )
        return reused, f"reused={reused}", r2

    def playlist_analyze():
        st, body = _req(
            "POST",
            "/analyses",
            {"sources": [{"id": "p", "type": "url", "uri": Ctx.playlist, **_auth()}]},
        )
        if st >= 400:
            return False, f"analyse {st}", body
        s = body["sources"][0]
        n = len(s.get("entries", []))
        ok = s["resource"]["resource_type"] == "collection" and n > 0
        return ok, f"collection · {n} entries", body

    return [
        ("video default", video_default),
        ("video quality (h264/mp4)", video_quality),
        ("video embed chapters+subs", video_embed),
        ("video + sponsorblock", video_sponsorblock),
        ("video cut (keyframes)", video_cut),
        ("audio (source)", audio_source),
        ("audio format opus", audio_opus),
        ("subtitles (en)", subtitles),
        ("thumbnail", thumbnail),
        ("metadata", metadata),
        ("transcript (from subs)", transcript),
        ("summary (LLM)", summary),
        ("cache reuse", cache_reuse),
        ("playlist analysis", playlist_analyze),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--api", default=Ctx.api)
    ap.add_argument("--cred", default=Ctx.cred)
    ap.add_argument("--url", default=Ctx.url)
    ap.add_argument("--playlist", default=Ctx.playlist)
    ap.add_argument("--keep", action="store_true", help="keep delivered files")
    args = ap.parse_args()
    Ctx.api, Ctx.cred, Ctx.url, Ctx.playlist = (
        args.api,
        args.cred,
        args.url,
        args.playlist,
    )

    st, health = _req("GET", "/health")
    if st != 200:
        print(f"{RED}back-end not reachable at {Ctx.api}{RESET}")
        return 2
    print(
        f"{BOLD}Content URL→output verification{RESET}  "
        f"{DIM}api={Ctx.api} v{health.get('version')} url={Ctx.url}{RESET}\n"
    )

    results = run(build_cases())

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n{BOLD}{passed}/{total} passed{RESET}")
    failed = [f"{label} ({detail})" for label, ok, detail in results if not ok]
    if failed:
        print(f"{RED}Failing:{RESET} " + "; ".join(failed))

    if not args.keep and OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR, ignore_errors=True)
        print(f"{DIM}cleaned {OUTPUT_DIR}{RESET}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
