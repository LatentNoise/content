# `content` — Content CLI

A thin command-line client for the Content engine, built on the official
[Python SDK](../../packages/python-sdk/README.md) (`content_sdk`) — it never
speaks HTTP itself and never runs the planner, yt-dlp or ffmpeg. Ergonomic
shortcuts (`video`, `audio`) are normalized to canonical `GenerationRequest`s;
there is no parallel contract.

## Install

The CLI is **not on PyPI**, and neither is the SDK it depends on — so
`pip install content-cli` resolves to nothing (or, worse, to somebody else's
package that happens to hold the name). Every release attaches the two wheels
instead; each path below installs the same two files.

### From a release (recommended)

Download `content_sdk-<version>-py3-none-any.whl` and
`content_cli-<version>-py3-none-any.whl` from the
[latest release](https://github.com/LatentNoise/content/releases/latest), then:

```bash
pipx install ./content_cli-<version>-py3-none-any.whl \
     --pip-args "--find-links ."          # isolated, on your PATH — recommended
```

or, into a virtualenv you manage:

```bash
pip install ./content_sdk-<version>-py3-none-any.whl \
            ./content_cli-<version>-py3-none-any.whl
```

Both wheels are named explicitly because that is what stands in for the missing
package index: pip takes `content-sdk` from the file you gave it, and fetches
only the ordinary third-party dependencies (`httpx`, `pydantic`) from PyPI.
Python 3.11 or later.

### From a clone

```bash
pip install ./packages/python-sdk ./apps/cli     # exposes `content`
```

### For development

```bash
make install    # editable installs of the engine, SDK, CLI and MCP in one venv
```

Build the wheels yourself with `make wheels` (they land in `dist/`).

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
