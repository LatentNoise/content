# content-sdk

## Install

```bash
pip install content-sdk        # https://pypi.org/project/content-sdk/
```

Python 3.11+. Requires only `httpx` and `pydantic`. The wheel is also attached
to each [GitHub release](https://github.com/LatentNoise/content/releases/latest);
from a clone: `pip install ./packages/python-sdk`.


The official Python SDK for the [Content](../../README.md) engine — the **one**
API client. It fully encapsulates the REST API (`/api/v1`); the CLI, the MCP
server and applications all speak through it, so the engine's rules are never
duplicated.

## Install

```bash
pip install content-sdk        # or: uv pip install -e packages/python-sdk
```

## Synchronous

```python
from content_sdk import ContentClient, outputs

with ContentClient("http://localhost:8010") as client:
    analysis = client.analyze(outputs.url_source("https://www.youtube.com/watch?v=…"))
    caps = client.get_capabilities(analysis.id)  # analysis_id is addressable
    job = client.generate(analysis.id, [outputs.audio_output()])
    job.wait()  # polls until terminal
    for artifact in job.artifacts:
        print(artifact.filename, artifact.media_type)
```

`analyze`, `get_capabilities` and `generate` accept **either** an
`analysis_id` / `Analysis` **or** inline sources (ADR 0014):

```python
client.get_capabilities([outputs.url_source("https://…")])  # stateless, by sources
```

## Asynchronous

```python
import asyncio
from content_sdk import AsyncContentClient, outputs


async def main():
    async with AsyncContentClient("http://localhost:8010") as client:
        analysis = await client.analyze(outputs.url_source("https://…"))
        job = await analysis.generate([outputs.audio_output()])
        await job.wait()
        print([a.filename for a in await job.artifacts()])


asyncio.run(main())
```

## Errors

Every non-2xx maps to a typed exception carrying the stable error codes:

```python
from content_sdk import NotFound, Gone, ValidationError

try:
    client.get_analysis("ana_stale")
except Gone as exc:  # 410 — the analysis or its facts expired
    print(exc.codes)  # ["analysis_expired"]
except NotFound:  # 404
    ...
except ValidationError as exc:  # 422 / 409 idempotency
    print(exc.codes)
```

## Design

- **`models.py`** — pure pydantic contract models (data only, no client).
- **`resources.py`** — behavioural objects (`Analysis`, `Job`) bound to a client.
- **`_transport.py`** — httpx sync/async layer; conservative retries (transport
  errors + 5xx on safe GETs only; creations retried only with an idempotency
  key).

The SDK never imports the engine and never depends on the CLI or MCP.

## Sending a local file

A `file` source names a path the **engine** can read. For a file on your own
machine — a laptop talking to a homelab engine — upload it first:

```python
source = client.upload_file("~/report.pdf")  # returns a ready-to-use source
analysis = client.analyze(source)
job = client.generate(analysis.id, [{"id": "s", "type": "summary"}])
```

`upload_file` streams from disk and hands back `{"type": "upload", …}` — the
upload id never has to be handled by hand. `upload_bytes(name, data, type)` does
the same for bytes that were never a file, which is what a browser upload is.
`upload()` returns the record itself (size, sha256) for callers who want it, and
`get_upload` / `delete_upload` complete the endpoint.

The async client mirrors all of it, with one deliberate difference: it buffers
rather than streams, because handing httpx a blocking file handle inside an
async send only relocates the stall. For a large upload, prefer the sync client.
