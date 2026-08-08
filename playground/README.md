# Playground — trying out the Content API

A sandbox for playing with the API: drop media into `input/`, run the scripts in `examples/`, collect the artifacts in `output/`.

## Getting started

```bash
# From the repository root
docker compose up --build        # the API on http://localhost:8000 (Swagger: /docs)
```

`playground/input/` is mounted **read-only** inside the container under `/input`, the only root allowed for `file` sources. The contents of `input/` and `output/` are not versioned (`.gitignore` allowlist).

## Examples

```bash
cd playground/examples

./01-analyze-url.sh 'https://www.youtube.com/watch?v=...'   # analyze a URL
./02-audio-from-url.sh 'https://...'                        # URL → audio + metadata

# First drop a video into playground/input/ (a demo.mp4 is provided):
./03-analyze-file.sh my-video.mp4                           # analyze a local file
./04-extract-from-file.sh my-video.mp4                      # file → audio + thumbnail + metadata
./05-video-from-url.sh 'https://...'                        # URL → video (≤1080p, h264 preferred, mkv)
./06-remux-file.sh my-video.mp4                             # file → mkv remux (stream copy)
./07-transcript-from-url.sh 'https://...' [fr]              # URL → transcript JSON + text (existing subtitles)
./08-summarize-from-url.sh 'https://...' [fr]               # URL → Markdown summary (local Ollama) + transcript
```

Each job script follows the progress (status + events) then downloads the artifacts into `output/<job_id>/`.

## Variables

| Variable | Default | Role |
| --- | --- | --- |
| `API_URL` | `http://localhost:8000` | The API address |
| `INPUT_PREFIX` | `/input` | The prefix of the file paths **as seen by the server**. Docker: `/input`. An API run locally (uvicorn): the absolute path of `playground/input`, with `CONTENT_ALLOWED_INPUT_ROOTS` pointing at it. |

An example locally without Docker:

```bash
cd apps/backend
CONTENT_ALLOWED_INPUT_ROOTS="$(pwd)/../../playground/input" \
  .venv/bin/python -m uvicorn content.api.app:app --port 8000
# then: INPUT_PREFIX="$(pwd)/../../playground/input" ./04-extract-from-file.sh my-video.mp4
```

## Going further

A free-form request: `POST /api/v1/jobs` with a [GenerationRequest](../docs/contract.md) — the Swagger at `/docs` lists every endpoint (events with `?after_sequence=`, `cancel`, artifact content…).
