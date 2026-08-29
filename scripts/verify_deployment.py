#!/usr/bin/env python3
"""Prove a *deployed* engine works, from outside it.

`make validate` proves the code is right on a laptop. This proves the thing
people actually run is right on the machine it runs on — a different question,
and the one that has gone wrong more often. 0.5.0 shipped an image that could
not start; 0.6.7's image could not be rebuilt at all; a delivery share could
refuse a finished copy. None of those are visible to a test suite.

So this talks to a running engine over HTTP and nothing else. No repository, no
imports, no assumptions about where it runs: give it a URL and it tells you
whether that engine is one you would hand to somebody.

    make verify-deployment ENGINE=http://192.168.21.30:8010
    python3 scripts/verify_deployment.py --engine http://... --expect-version 0.6.8

Standard library only, so it runs from a laptop, a runner, or the box itself.

Every check is named for the claim it makes, and a failure prints what was
expected against what came back. Exit status is the answer: 0 means the
deployment is good, non-zero names what is not.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

TIMEOUT = 30
# A job is planned, queued and run by a worker; on a busy homelab that is
# seconds, not milliseconds. Generous, because a slow engine is not a broken
# one and a flaky check is worse than no check.
JOB_TIMEOUT = 180


class Failure(Exception):
    """A check that did not hold. The message is the report."""


def _call(engine: str, path: str, payload: dict | None = None, method: str = ""):
    url = f"{engine.rstrip('/')}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method or ("POST" if data else "GET"),
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:400]
        raise Failure(f"{method or 'GET'} {path} answered HTTP {exc.code}: {detail}")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise Failure(f"{url} is unreachable: {exc}")
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


# --- the checks ------------------------------------------------------------------


def check_health(engine: str, expect_version: str) -> str:
    """The engine is up, and it is the version we think we shipped."""
    health = _call(engine, "/api/v1/health")
    if health.get("status") != "ok":
        raise Failure(f"health is {health.get('status')!r}: {health.get('checks')}")
    for name, state in (health.get("checks") or {}).items():
        if state != "ok":
            raise Failure(f"health check {name!r} is {state!r}")
    version = health.get("version", "")
    if expect_version and version != expect_version:
        raise Failure(f"expected version {expect_version}, engine serves {version}")
    return f"healthy, version {version}"


def check_runners(engine: str, _: str) -> str:
    """The tools the engine claims are the tools it has.

    An image that starts is not an image that works: 0.6.7 answered /health
    perfectly while carrying a yt-dlp six weeks stale, and the version that
    matters is the one *inside the container*, which only the engine can report.
    """
    system = _call(engine, "/api/v1/system")
    runners = {r["name"]: r for r in system.get("runners", [])}
    if not runners:
        raise Failure("the engine reports no runners at all")

    unavailable = sorted(n for n, r in runners.items() if not r.get("available"))
    versions = {
        n: r.get("tool_version", "")
        for n, r in runners.items()
        if n in ("ytdlp", "ffmpeg", "content.pdf.typst")
    }
    ytdlp = versions.get("ytdlp", "")
    if not ytdlp:
        raise Failure("no yt-dlp runner is installed — downloads cannot work")
    if not runners.get("ytdlp", {}).get("available"):
        raise Failure(f"yt-dlp is installed ({ytdlp}) but reports itself unavailable")

    detail = ", ".join(f"{n}={v}" for n, v in sorted(versions.items()) if v)
    if unavailable:
        detail += f"  (unavailable: {', '.join(unavailable)})"
    return detail


def check_capabilities(engine: str, _: str) -> str:
    """Analysis and capability resolution work on a real source.

    Text rather than a URL on purpose: this must not depend on YouTube being
    reachable, or on cookies, to tell you the engine is sound.
    """
    source = {"id": "s", "type": "text", "content": _SAMPLE}
    resolved = _call(engine, "/api/v1/capabilities", {"sources": [source]})
    entries = resolved.get("sources") or []
    if not entries:
        raise Failure(f"capabilities returned nothing usable: {str(resolved)[:200]}")
    caps = {c["id"]: c["status"] for c in entries[0].get("capabilities", [])}
    if not caps:
        raise Failure("the source resolved to no capabilities at all")
    usable = sorted(k for k, v in caps.items() if v in ("available", "derivable"))
    if "markdown.export" not in usable:
        raise Failure(f"markdown.export is not offered on text; got {usable}")
    return f"{len(usable)} usable on a text source"


def check_a_real_job(engine: str, _: str) -> str:
    """The whole pipeline, end to end, on the deployment itself.

    Submit, wait, read the artifact back. `delivery: none` deliberately — this
    must not leave anything in the operator's media library.
    """
    payload = {
        "schema_version": "1.0",
        "sources": [{"id": "s", "type": "text", "content": _SAMPLE}],
        "outputs": [
            {"id": "md", "type": "markdown", "delivery": {"mode": "none"}}
        ],
    }
    submitted = _call(engine, "/api/v1/jobs", payload)
    job_id = submitted.get("job_id")
    if not job_id:
        raise Failure(f"the engine refused the job: {json.dumps(submitted)[:400]}")

    deadline = time.monotonic() + JOB_TIMEOUT
    status = ""
    while time.monotonic() < deadline:
        job = _call(engine, f"/api/v1/jobs/{job_id}")
        status = job.get("status", "")
        if status in ("succeeded", "failed", "partially_succeeded", "cancelled"):
            break
        time.sleep(2)
    if status != "succeeded":
        events = _call(engine, f"/api/v1/jobs/{job_id}/events") or []
        failures = [
            e.get("data") for e in events if e.get("type") in ("step.failed", "job.failed")
        ]
        raise Failure(f"job {job_id} ended {status!r}: {json.dumps(failures)[:300]}")

    artifacts = _call(engine, f"/api/v1/jobs/{job_id}/artifacts") or []
    if len(artifacts) != 1:
        raise Failure(f"expected one artifact, got {len(artifacts)}")
    artifact = artifacts[0]

    # Read the bytes back. An artifact row that cannot be fetched is a row, not
    # a file — and the delivery path is exactly where that has gone wrong.
    content = _call(engine, f"/api/v1/artifacts/{artifact['id']}/content")
    text = content.decode(errors="replace") if isinstance(content, bytes) else str(content)
    if _CANARY not in text:
        raise Failure(
            f"the artifact does not contain what was sent "
            f"(looked for {_CANARY!r} in {len(text)} chars)"
        )
    return f"{artifact['display_filename']} — {artifact['size_bytes']} bytes, readable"


def check_warnings_channel(engine: str, _: str) -> str:
    """Artifacts carry a `warnings` list, even when empty.

    A caller has to be able to ask "can I trust this file" without special-casing
    a missing field. This is the shape that answer arrives in.
    """
    jobs = _call(engine, "/api/v1/jobs?limit=5") or {}
    items = jobs if isinstance(jobs, list) else jobs.get("jobs", [])
    terminal = [j for j in items if j.get("status") == "succeeded"]
    if not terminal:
        return "no succeeded job to inspect (skipped)"
    job_id = terminal[0].get("job_id") or terminal[0].get("id")
    artifacts = _call(engine, f"/api/v1/jobs/{job_id}/artifacts") or []
    if not artifacts:
        return "the latest succeeded job produced no artifact (skipped)"
    provenance = artifacts[0].get("provenance") or {}
    if "warnings" not in provenance:
        raise Failure(
            "artifact provenance has no `warnings` key — an engine older than "
            "0.6.8, or the field regressed"
        )
    return "provenance carries `warnings`"


_CANARY = "PIERRE-DE-TOUCHE-9314"
_SAMPLE = (
    "# Vérification du déploiement\n\n"
    f"Ce texte porte une marque, {_CANARY}, pour prouver que l'artefact "
    "produit contient bien ce qui a été envoyé et non un fichier voisin.\n"
)

CHECKS = (
    ("health and version", check_health),
    ("runners and tool versions", check_runners),
    ("analysis and capabilities", check_capabilities),
    ("a real job, end to end", check_a_real_job),
    ("the warnings channel", check_warnings_channel),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, help="base URL of a running engine")
    parser.add_argument(
        "--expect-version", default="", help="fail unless the engine serves this"
    )
    args = parser.parse_args()

    print(f"Verifying {args.engine}")
    if args.expect_version:
        print(f"Expecting version {args.expect_version}")
    print()

    failures = []
    for name, check in CHECKS:
        try:
            detail = check(args.engine, args.expect_version)
            print(f"  [ok]   {name}: {detail}")
        except Failure as exc:
            failures.append((name, str(exc)))
            print(f"  [FAIL] {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 — a check must not take the run down
            failures.append((name, f"{type(exc).__name__}: {exc}"))
            print(f"  [FAIL] {name}: unexpected {type(exc).__name__}: {exc}")

    print()
    if failures:
        print(f"{len(failures)} of {len(CHECKS)} checks failed:")
        for name, message in failures:
            print(f"  - {name}: {message}")
        return 1
    print(f"All {len(CHECKS)} checks passed — this deployment is good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
