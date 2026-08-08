#!/usr/bin/env python3
"""Playground: verify HomeTube's playlist support end to end.

Checks the dynamic URL handling (a single video vs a playlist), the collection
analysis (entries), and per-item execution (scope each_item) against a running
back-end — bounded: it submits an each_item audio job, waits for the FIRST item
to be delivered, asserts the per-item naming, then cancels (so it never
downloads the whole playlist).

Usage:
    python3 playground/verify_playlist_pipeline.py \
        [--api http://localhost:8010] [--cred youtube] \
        [--video https://youtu.be/…] [--playlist https://…/playlist?list=…]
"""

import argparse
import contextlib
import json
import shutil
import time
import urllib.request
from pathlib import Path

FOLDER = "pgplaylist"
OUTPUT_DIR = Path(__file__).resolve().parent / "output" / FOLDER
RESET, GREEN, RED, DIM, BOLD = "\033[0m", "\033[32m", "\033[31m", "\033[2m", "\033[1m"


class Ctx:
    api = "http://localhost:8010"
    cred = "youtube"
    video = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    playlist = (
        "https://www.youtube.com/playlist?list=PLbpi6ZahtOH6Blw3RGYpWkSByi_T7Rygb"
    )


def _req(method, path, body=None):
    req = urllib.request.Request(
        f"{Ctx.api}/api/v1{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=140) as resp:
        return json.loads(resp.read() or "null")


def _src(uri):
    src = {"id": "main", "type": "url", "uri": uri}
    # Only reference a server-side credential when one is configured.
    if Ctx.cred not in ("", "none"):
        src["auth"] = {"credential_id": Ctx.cred}
    return src


RESULTS = []


def check(name, ok, detail=""):
    mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{mark}] {name:<44} {DIM}{detail}{RESET}")
    RESULTS.append(ok)


def run():
    # 1. Dynamic URL handling: a single video analyses as a video…
    v = _req("POST", "/analyses", {"sources": [_src(Ctx.video)]})["sources"][0]
    check(
        "single video → resource_type 'video'",
        v["resource"]["resource_type"] == "video" and not v.get("entries"),
        v["resource"]["resource_type"],
    )

    # 2. …and a playlist analyses as a collection with its entries.
    p = _req("POST", "/analyses", {"sources": [_src(Ctx.playlist)]})["sources"][0]
    entries = p.get("entries", [])
    check(
        "playlist → collection + entries",
        p["resource"]["resource_type"] == "collection" and len(entries) > 0,
        f"{len(entries)} entries · {p['resource']['title'][:30]}",
    )

    # 3. Per-item execution (scope each_item), bounded to the first item.
    request = {
        "schema_version": "1.0",
        "sources": [_src(Ctx.playlist)],
        "outputs": [
            {
                "id": "audio_main",
                "type": "audio",
                "scope": "each_item",
                "delivery": {"folder": FOLDER},
            }
        ],
    }
    created = _req("POST", "/jobs", request)
    job_id = created["job_id"]
    steps = len(_req("GET", f"/jobs/{job_id}").get("steps", []))
    check(
        "each_item plan → one step per entry",
        steps == len(entries) and steps > 1,
        f"{steps} steps",
    )

    delivered = []
    deadline = time.time() + 150
    while time.time() < deadline:
        if OUTPUT_DIR.exists():
            delivered = sorted(OUTPUT_DIR.glob("audio_main-*"))
            if delivered:
                break
        status = _req("GET", f"/jobs/{job_id}")["status"]
        if status in ("failed", "cancelled"):
            break
        time.sleep(3)
    check(
        "first playlist item downloaded & delivered",
        bool(delivered),
        delivered[0].name if delivered else "none within 150s",
    )

    # bounded: cancel so we don't download the whole playlist
    with contextlib.suppress(Exception):
        _req("POST", f"/jobs/{job_id}/cancel")
    print(f"  {DIM}cancelled job {job_id} (bounded run){RESET}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--api", default=Ctx.api)
    ap.add_argument("--cred", default=Ctx.cred)
    ap.add_argument("--video", default=Ctx.video)
    ap.add_argument("--playlist", default=Ctx.playlist)
    args = ap.parse_args()
    Ctx.api, Ctx.cred, Ctx.video, Ctx.playlist = (
        args.api,
        args.cred,
        args.video,
        args.playlist,
    )

    try:
        _req("GET", "/health")
    except Exception as exc:  # noqa: BLE001
        print(f"{RED}back-end not reachable at {Ctx.api}: {exc}{RESET}")
        return 2
    print(f"{BOLD}HomeTube playlist verification{RESET}  {DIM}api={Ctx.api}{RESET}\n")

    run()
    shutil.rmtree(OUTPUT_DIR, ignore_errors=True)
    passed = sum(1 for r in RESULTS if r)
    print(f"\n{BOLD}{passed}/{len(RESULTS)} passed{RESET}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
