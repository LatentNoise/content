# `content` — Content CLI

A thin command-line client for the Content engine, built on the official
[Python SDK](../../packages/python-sdk/README.md) (`content_sdk`) — it never
speaks HTTP itself and never runs the planner, yt-dlp or ffmpeg. Ergonomic
shortcuts (`video`, `audio`) are normalized to canonical `GenerationRequest`s;
there is no parallel contract.

## Install

```bash
pip install -e packages/python-sdk -e apps/cli    # exposes `content`
# (make install already does this in the project venv)
```

Point it at your instance with `--api-url` or `CONTENT_API_URL`
(default `http://localhost:8010`).

## Commands

```bash
content health
content config
content analyze https://youtu.be/… [--credential youtube]
content analysis <analysis_id>           # re-fetch a stored analysis (ADR 0014)
content video  https://youtu.be/… --height 1080 --container mkv --subs en,fr --watch
content audio  https://youtu.be/… --format opus --folder music --name track --watch
content video  https://…/playlist?list=… --playlist --watch      # each item
content submit request.json --watch      # raw GenerationRequest (or - for stdin)
content jobs
content job    <job_id>
content watch  <job_id>
content artifacts <job_id>
content download <artifact_id> -o out.mkv
content cancel <job_id> ; content retry <job_id>
```

Global flags: `--api-url URL` and `--json` (raw JSON output) go before the
subcommand. Exit code is non-zero on API errors or a failed watched job.
