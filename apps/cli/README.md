# `content` — Content CLI

A thin command-line client for the Content engine, built on the official
[Python SDK](../../packages/python-sdk/README.md) (`content_sdk`) — it never
speaks HTTP itself and never runs the planner, yt-dlp or ffmpeg. Ergonomic
shortcuts (`video`, `audio`) are normalized to canonical `GenerationRequest`s;
there is no parallel contract.

## Install

Once published, the CLI is an ordinary Python application — nothing to clone:

```bash
uv tool install content-cli     # isolated, on your PATH — recommended
content --help

# or
pipx install content-cli
```

`content-cli` pulls `content-sdk` from PyPI as an ordinary dependency, pinned
to the matching release.

> **Not published yet.** Until the first publication the packages are attached
> to each GitHub release as wheels; see *From a release* below. The commands
> above are what will work afterwards.

### From a release (today)

Download `content_sdk-<version>-py3-none-any.whl` and
`content_cli-<version>-py3-none-any.whl` from the
[latest release](https://github.com/LatentNoise/content/releases/latest), then:

```bash
uv tool install ./content_cli-<version>-py3-none-any.whl \
    --find-links .          # --find-links lets it resolve the SDK beside it
```

### From a clone

```bash
pip install ./packages/python-sdk ./apps/cli     # exposes `content`
```

### For development

```bash
make install    # editable installs of the engine, SDK, CLI and MCP in one venv
```

Build the distributions yourself with `make wheels` (they land in `dist/`).

### Point it at your engine

`--api-url URL` or `CONTENT_API_URL` (default `http://localhost:8010`):

```bash
export CONTENT_API_URL=http://nas.local:8010
content health
```

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
