# ADR 0020 — Client-uploaded sources

Status: proposed (2026-08-15) · Implements the `upload` source type reserved in
`docs/contract.md` · Introduces a fifth storage root

## Context

Content accepts URLs, files and text. `file` means *a path the engine can
already read*, validated against `CONTENT_ALLOWED_INPUT_ROOTS` by
`check_path_allowed` in every provider. That is a security boundary as much as a
convenience: a `file` source always points at operator-controlled territory.

It also means a file on the caller's machine cannot be submitted at all. Three
clients hit this from different directions:

- **MCP** — the driving case. `content-mcp` runs as a local process on the
  user's machine and *can* read `~/Documents/report.pdf`, but has no way to hand
  those bytes to a homelab engine. There is no workaround: the path string is
  meaningless on the other host.
- **Studio** — runs in a container with no volumes at all and talks to the
  engine over HTTP. `st.file_uploader` yields bytes it has nowhere to put.
- **CLI** — same problem whenever the engine is not the local machine.

The alternative of *not* doing this is real and was weighed: documenting "drop
the file in `./playground/input`" is free, honest, and sufficient when the files
and the engine share a host. It fails completely for a laptop talking to a
remote engine, which is the topology this project actually recommends.

The source type is **not** an open question. `UploadSource` (`type: "upload"`,
`upload_id`) has been in `content/domain/request.py` since the contract was
written, `docs/contract.md` publishes it as *"validated, refused at feasibility
(`source_type_not_supported`)"*, and `content_sdk.models.upload_source()`
already builds one. This ADR decides only what was left open: where the bytes
live, how long, and what the write primitive costs in safety.

## Decision

### 1. Uploads are a fifth storage root, not a variant of the four

`docs/storage.md` distinguishes `tmp/` (disposable scratch), `work/` (a job's
working files), `artifacts/` (produced results) and `cache/` (reserved). An
upload is none of them: it exists **before** any job, may feed **several** jobs,
is not produced by the engine, and must not vanish when a job's work is purged.

So: **`uploads/`**, beside the others, laid out as

```text
<data>/uploads/<upload_id>/<sanitized-filename>
```

`upload_id` is opaque and unguessable (`new_id("upl")`). The physical path is an
implementation detail: **it is never returned to a client**, which keeps clients
from constructing paths and keeps the layout free to change.

Uploads are treated as **immutable blobs**. Nothing rewrites one after it is
addressable. Deduplication by content hash is not implemented, but the hash is
recorded so it can be added later without a format change.

### 2. Lifetime: unreferenced uploads expire, referenced ones follow their job

An upload nobody uses must not accumulate forever; an upload a job used must
survive long enough for `POST /jobs/{id}/retry` to find its input again.

- An upload becomes GC-eligible `CONTENT_UPLOAD_TTL_HOURS` (default 24) after
  its **last reference**, not after creation. Submitting a job that references
  it refreshes that clock.
- An upload referenced by a retained job is retained with it. Retention is not
  implemented in V1 (nothing is deleted automatically), so in practice the
  sweeper only removes uploads that no job ever referenced — which is the
  dangerous accumulation, and the honest scope for now.
- Referencing an upload that is gone answers **`upload_expired`**, a distinct,
  documented code — never a generic filesystem error, and never the same answer
  as "this id never existed", which would tell a caller nothing about whether
  to re-upload.

### 3. Security: this is Content's first arbitrary-byte write primitive

Until now the worst an unauthenticated caller on the network could do was make
the engine *fetch* something. Afterwards they can make it *store* something.
That is a genuine escalation and is stated as such in the deployment guide.

Required, all of them:

- a hard per-upload size limit (`CONTENT_MAX_UPLOAD_BYTES`), **enforced while
  streaming** — a `Content-Length` header is a claim, not a fact;
- a total upload-storage quota, refusing new uploads rather than filling the
  disk the engine needs to run;
- bounded chunked streaming to disk; an upload is never held whole in memory;
- writes go to a temporary name and are **atomically renamed on completion**, so
  a half-received file is never addressable, and interrupted transfers are
  swept;
- server-side filename sanitization (D-51: the server sanitizes, never
  rejects); the client's filename is metadata, and storage paths are derived
  from the id, not from it;
- the client's declared MIME type is recorded and never trusted — what a file
  *is* comes from analysis, as for any other source;
- a SHA-256 computed during the stream and returned, so a caller can verify.

**Opaque ids are identity, not authorization.** They make enumeration
impractical; they do not make an upload private. With no authentication in V1,
anyone who can reach the API can upload, and anyone holding an id can reference
it. The deployment guide must say so where it already says the engine belongs
behind a reverse proxy.

### 4. Upload is acquisition, never processing

Once resolved, an uploaded PDF behaves **exactly** like the same PDF sitting in
`./playground/input`: same analysis, same capabilities, same planner, same
naming, delivery and provenance. No `if source.type == "upload"` outside
resolution and storage. A shared upload is read by several jobs without copying
the bytes per job, unless a tool genuinely needs a mutable working copy — in
which case it copies into that job's `work/`, as it already would.

This is what makes the feature small: it adds a way *in*, not a second pipeline.

## Consequences

**Gained.** The topology the project recommends — engine on a homelab, humans
and agents elsewhere — stops having a hole in it. "Summarize this PDF" works
from Claude Code against a remote engine, Studio gets a file picker without a
shared filesystem, and the README's "URLs, files and text" becomes true for a
file on *your* machine.

**Paid.** A write primitive on an unauthenticated API, a fifth storage root to
document and sweep, and a garbage collector — the first thing in Content that
deletes data on its own, which deserves the caution its `unreferenced-only`
scope reflects.

**Deliberately deferred.** Content-hash deduplication (recorded, not used).
Resumable or chunked-protocol uploads: multipart is enough for the sizes this
targets, and resumability is a protocol, not a parameter. Per-upload access
control, which is meaningless before the API has any notion of a caller.

**Rejected.** Folding uploads into `file`: it would make one source type mean
both "the operator's disk" and "bytes a stranger POSTed", and would require
conditionally bypassing the allowed-roots check — the shape path-traversal bugs
arrive in. Returning a filesystem path instead of an id: it leaks the layout and
invites clients to build paths. Having the engine *pull* from the client: it
requires the caller to run a reachable server, which fails behind NAT and is
worse in every security dimension.
