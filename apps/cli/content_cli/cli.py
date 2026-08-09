"""``content`` — the command-line client for the Content engine.

A thin wrapper over the official SDK (``content_sdk``): it parses arguments,
calls the SDK, and prints results. It never speaks HTTP itself, never runs the
planner, yt-dlp or ffmpeg — the SDK is the only door to the engine.
"""

import argparse
import json
import sys
import time

from content_sdk import ContentClient, ContentError, TransportError
from content_sdk.resources import TERMINAL_STATUSES

from content_cli import __version__
from content_cli.builders import audio_request, video_request


def _out(obj, as_json: bool) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False) if as_json else obj)


def _print_job(job: dict) -> None:
    print(f"{job['status']}  {job['job_id']}")
    for step in job.get("steps", []):
        err = f"  ! {step['error']}" if step.get("error") else ""
        print(f"  [{step['status']:>9}] {step['step_id']}{err}")


def _watch(client: ContentClient, job_id: str) -> str:
    """Stream events until the job reaches a terminal state."""
    seen = 0
    while True:
        for event in client.events(job_id, after_sequence=seen):
            seen = event.sequence
            print(f"  {event.sequence:>3} {event.type} {event.data or ''}")
        status = client.get_job(job_id).status
        if status in TERMINAL_STATUSES:
            print(f"→ {status}")
            return status
        time.sleep(2.0)


def _submit_and_maybe_watch(client: ContentClient, request: dict, args) -> int:
    job = client.submit(request)
    for warning in job.data.warnings:
        print(f"warning: {warning['code']}: {warning['message']}", file=sys.stderr)
    print(job.id)
    if getattr(args, "watch", False):
        status = _watch(client, job.id)
        return 0 if status in ("succeeded", "partially_succeeded") else 1
    return 0


def _cmd_analyze(client: ContentClient, args) -> int:
    source: dict = {"id": "main", "type": args.type}
    if args.type == "url":
        source["uri"] = args.target
    elif args.type == "file":
        source["path"] = args.target
    else:
        source["content"] = args.target
    if args.credential:
        source["auth"] = {"credential_id": args.credential}
    analysis = client.analyze([source])
    entry = analysis.sources[0]
    if args.json:
        _out(entry.model_dump(), True)
        return 0
    print(f"{entry.resource_type} · {entry.title or '(untitled)'}")
    # An analysis is addressable (ADR 0014): resolve what can be produced from it.
    caps = client.get_capabilities(analysis.id)
    for cap in caps.sources[0].capabilities:
        print(f"  {cap.status:>11}  {cap.id}")
    if entry.entries:
        print(f"  collection: {len(entry.entries)} items")
    print(f"analysis_id: {analysis.id}")
    return 0


def _cmd_submit(client: ContentClient, args) -> int:
    if args.file == "-":
        raw = sys.stdin.read()
    else:
        with open(args.file) as handle:
            raw = handle.read()
    return _submit_and_maybe_watch(client, json.loads(raw), args)


def _cmd_video(client: ContentClient, args) -> int:
    request = video_request(
        args.url,
        height=args.height,
        codec=args.codec,
        container=args.container,
        subtitles=args.subs,
        audio_languages=args.audio_langs,
        sponsorblock=args.sponsorblock,
        playlist=args.playlist,
        credential=args.credential,
        folder=args.folder,
        name=args.name,
    )
    return _submit_and_maybe_watch(client, request, args)


def _cmd_audio(client: ContentClient, args) -> int:
    request = audio_request(
        args.url,
        fmt=args.format,
        languages=args.audio_langs,
        sponsorblock=args.sponsorblock,
        playlist=args.playlist,
        credential=args.credential,
        folder=args.folder,
        name=args.name,
    )
    return _submit_and_maybe_watch(client, request, args)


def _cmd_download(client: ContentClient, args) -> int:
    data = client.artifact_bytes(args.artifact_id)
    if args.output == "-":
        sys.stdout.buffer.write(data)
    else:
        with open(args.output, "wb") as handle:
            handle.write(data)
        print(f"wrote {len(data)} bytes to {args.output}")
    return 0


def _add_launch_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--credential", help="server-side cookie credential id")
    parser.add_argument("--folder", help="delivery sub-folder")
    parser.add_argument("--name", help="delivery file base name")
    parser.add_argument("--sponsorblock", default="disabled", help="SB preset")
    parser.add_argument("--audio-langs", dest="audio_langs", help="comma-separated")
    parser.add_argument(
        "--playlist", action="store_true", help="download each playlist item"
    )
    parser.add_argument("--watch", action="store_true", help="follow until done")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="content", description=__doc__)
    parser.add_argument("--api-url", dest="api_url", help="Content API base URL")
    parser.add_argument("--json", action="store_true", help="raw JSON output")
    # The installed release, from the package's own metadata rather than a
    # second literal: `content --version` must answer for the wheel a user
    # actually has, which is the first thing to check in a bug report.
    parser.add_argument(
        "--version",
        action="version",
        version=f"content {__version__}",
        help="show the installed Content CLI version",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health")
    sub.add_parser("config")

    p = sub.add_parser("analyze", help="analyze a source")
    p.add_argument("target")
    p.add_argument("--type", default="url", choices=["url", "file", "text"])
    p.add_argument("--credential")

    p = sub.add_parser("analysis", help="fetch a stored analysis by id")
    p.add_argument("analysis_id")

    p = sub.add_parser("video", help="download a video (shortcut)")
    p.add_argument("url")
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--codec", default="auto", choices=["auto", "av1", "vp9", "h264"])
    p.add_argument("--container", default="source", choices=["source", "mkv", "mp4"])
    p.add_argument("--subs", help="subtitle languages to embed (comma-separated)")
    _add_launch_flags(p)

    p = sub.add_parser("audio", help="download audio (shortcut)")
    p.add_argument("url")
    p.add_argument(
        "--format", default="source", choices=["source", "opus", "mp3", "m4a"]
    )
    _add_launch_flags(p)

    p = sub.add_parser("submit", help="submit a GenerationRequest JSON (file or -)")
    p.add_argument("file")
    p.add_argument("--watch", action="store_true")

    p = sub.add_parser("jobs", help="list recent jobs")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("job", help="show a job")
    p.add_argument("job_id")

    p = sub.add_parser("watch", help="follow a job until it ends")
    p.add_argument("job_id")

    p = sub.add_parser("artifacts", help="list a job's artifacts")
    p.add_argument("job_id")

    p = sub.add_parser("download", help="download an artifact")
    p.add_argument("artifact_id")
    p.add_argument("-o", "--output", default="-", help="output file, or - for stdout")

    p = sub.add_parser("cancel")
    p.add_argument("job_id")
    p = sub.add_parser("retry")
    p.add_argument("job_id")
    return parser


def run(argv: list[str], client: ContentClient) -> int:
    args = build_parser().parse_args(argv)
    cmd = args.command
    if cmd == "health":
        _out(client.health(), True)
    elif cmd == "config":
        _out(client.config(), True)
    elif cmd == "analyze":
        return _cmd_analyze(client, args)
    elif cmd == "analysis":
        _out(client.get_analysis(args.analysis_id).data.model_dump(), True)
    elif cmd == "video":
        return _cmd_video(client, args)
    elif cmd == "audio":
        return _cmd_audio(client, args)
    elif cmd == "submit":
        return _cmd_submit(client, args)
    elif cmd == "jobs":
        rows = client.list_jobs(limit=args.limit)
        if args.json:
            _out([r.model_dump() for r in rows], True)
        else:
            for row in rows:
                print(f"{row.status:>11}  {row.job_id}")
    elif cmd == "job":
        job = client.get_job(args.job_id).data.model_dump()
        _out(job, True) if args.json else _print_job(job)
    elif cmd == "watch":
        status = _watch(client, args.job_id)
        return 0 if status in ("succeeded", "partially_succeeded") else 1
    elif cmd == "artifacts":
        arts = client.artifacts(args.job_id)
        if args.json:
            _out([a.model_dump() for a in arts], True)
        else:
            for a in arts:
                print(f"{a.id}  {a.filename}  {a.size_bytes}B")
    elif cmd == "download":
        return _cmd_download(client, args)
    elif cmd == "cancel":
        _out(client.cancel(args.job_id), True)
    elif cmd == "retry":
        _out(client.retry(args.job_id).data.model_dump(), True)
    return 0


def _describe(exc: ContentError) -> str:
    """One readable line per problem, from the contract's own error shape.

    Every rejection now comes back as `{detail: {errors: [{code, path,
    message}]}}` — one shape for schema violations and engine refusals alike —
    so there is no reason to print a raw Python dict at somebody.
    """
    if isinstance(exc, TransportError):
        return f"cannot reach the engine — {exc}"
    body = getattr(exc, "body", None)
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict) and isinstance(detail.get("errors"), list):
        lines = []
        for error in detail["errors"]:
            where = f" at {error['path']}" if error.get("path") else ""
            code = f" [{error['code']}]" if error.get("code") else ""
            lines.append(f"{error.get('message', 'rejected')}{where}{code}")
        return "\n       ".join(lines)
    if isinstance(detail, str):
        return detail
    return str(body if body is not None else exc)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Peel the global --api-url before argparse sees it, so it can be given
    # before the subcommand. A missing value used to raise IndexError here —
    # a traceback for a typo.
    api_url = None
    if "--api-url" in argv:
        i = argv.index("--api-url")
        if i + 1 >= len(argv):
            print("error: --api-url needs a value", file=sys.stderr)
            return 2
        api_url = argv[i + 1]
        del argv[i : i + 2]
    client = ContentClient(api_url)
    try:
        return run(argv, client)
    # ContentError, not APIError: a refused connection raises TransportError,
    # and "the engine is not running" is the most common failure of all — it
    # used to print a sixty-line traceback.
    except ContentError as exc:
        print(f"error: {_describe(exc)}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
