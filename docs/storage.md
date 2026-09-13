# File storage

Content separates four families of files, with four independent lifecycles.
The founding rule (ADR 0009):

```text
tmp != work != artifact != cache
```

The **database remains the source of truth**; files are addressed relative to
`CONTENT_DATA_DIR` so that the tree stays relocatable (a future S3/MinIO
boundary). Every path construction goes through
[`content/storage/paths.py`](../apps/backend/content/storage/paths.py)
(`StoragePaths`, `safe_segment`, `publish_file`) and
[`layout.py`](../apps/backend/content/storage/layout.py) (`JobStorage`) — no provider
or route invents its own tree.

## Tree

Two layouts, one code path (ADR 0037). `CONTENT_STORAGE_LAYOUT` is a value read
by one module, `content/storage/roots.py`; nothing else builds a path by hand.
It defaults to `flat` when nobody signs in and to `per_user` when people do.

**`flat` — one owner, the self-hosted majority:**

```text
$CONTENT_DATA_DIR/
├── content.db                       # source of truth
├── jobs/<job_id>/
│   ├── sources/                     # materialized inputs
│   ├── work/                        # the job's VALID intermediates
│   ├── artifacts/                   # persistent business results
│   ├── logs/                        # stdout/stderr per step
│   └── snapshots/                   # request / analysis / plan / result (immutable)
├── tmp/<job_id>/<step_id>/          # incomplete, technical, disposable
├── tmp/analysis/<resource_key>/     # analysis probe scratch — belongs to nobody
├── uploads/<upload_id>/             # bytes a client sent (ADR 0020)
├── resources/                       # raw downloads, per resource — reserved (ADR 0037)
├── cache/analysis/                  # public resource facts — belongs to nobody
└── delivery/                        # the user-facing library (CONTENT_DELIVERY_DIR)
```

**`per_user` — several owners, the hosted instance:**

```text
$CONTENT_DATA_DIR/
├── content.db
├── users/<owner_id>/                # ONE directory holds everything of one person
│   ├── jobs/<job_id>/…              # same five subdirectories as above
│   ├── tmp/<job_id>/…
│   ├── uploads/<upload_id>/
│   ├── resources/
│   └── output/                      # their private library (delivery scope per_owner)
├── tmp/analysis/                    # still nobody's
└── cache/analysis/                  # still nobody's
```

A flat tree with `CONTENT_AUTH_MODE=token` is refused at startup: it holds
exactly one owner by construction. `migrate_layout` files an existing disk into
the configured layout on every start — idempotent, interruptible, and
conservative about what it touches.

The **delivery library** is the user-facing copy of finished artifacts
(ADR 0018): files land under their `display_filename` (ADR 0017), the path is
recorded per artifact (`delivered_path`), and the job store remains the source
of truth. With `CONTENT_DELIVERY_DEFAULT` on (the docker-compose deployment),
every artifact is delivered unless the request says `delivery.mode: "none"`;
off (the code default), only explicit `delivery` intent is honored — the V1
behaviour. Mount the root wherever the library should appear
(`CONTENT_DELIVERY_DIR_HOST` in compose).

## The five families

| Family | Role | Lifecycle |
| --- | --- | --- |
| **`tmp`** | Incomplete/technical writes: download in progress, `.part`, staging before an atomic move, a copy of `cookies.txt`, probe scratch. | Disposable. Cleaned at the end of a job (`purge_tmp`) and by age. A file in `tmp` is **never** deemed valid or reusable. A crash may leave orphans behind. |
| **`work`** | **Valid** intermediates specific to one job (audio extracted for a transcript, media shared between outputs). | Shareable between steps of the **same** job, never across jobs. Purged according to `retention.working_files` (default `24h`). |
| **`artifacts`** | Business results (video, audio, subtitles, transcript, summary, thumbnail, metadata). | An `Artifact` row + provenance, exposed by the API. Its own retention. **Never** deleted by the `tmp`/`work` cleanup. |
| **`cache`** | Validated results reusable across jobs. | **Disabled in V1** (`CONTENT_CACHE_ENABLED=false`). The directory is not created while the cache is off. |
| **`uploads`** | Bytes a client supplied before any job exists (ADR 0020). Immutable, one directory per opaque id. | Swept `CONTENT_UPLOAD_TTL_HOURS` after the **last** job referenced it — not after creation, so a retry still finds its input. Untouched by `purge_work`/`purge_tmp`: an upload may outlive and feed several jobs. |

## Retention: expiring is not deleting

Two verbs act on the same files and they are not the same act.

**Deleting** is what a person asks for, with `DELETE /api/v1/jobs/{id}`. The job
goes: its row, its artifact rows, its whole directory, and — only when the call
says `?delivered=true` — its delivered copies.

**Expiring** is what the unattended sweep does, `CONTENT_RETENTION_DAYS` after a
job finished. Only the bytes go: `artifacts/`, `sources/` and the job's `tmp`.
The job row, its logs, its snapshots and its artifact rows stay, each marked
with the moment its content went. A job's history weighs a few kilobytes
against a video of hundreds of megabytes, so keeping it costs nothing and
answers "what did I ask for in June?" three months later.

The API then answers `410` with `artifact_content_expired` and the date, rather
than the `artifact_content_missing` it uses for a file it lost. Those are
different facts: expired means ask again and it will be produced anew.

The sweep runs on the existing housekeeping tick, beside the upload sweep, not
at a fixed hour. Retention removes what has passed the window, so the hour of
the pass changes *when* bytes go, never *which* — and a tick cannot miss its
day because the process restarted in that minute. A job whose artifacts are all
expired already is skipped, which is what stops the sweep walking the same
directory forever.

**The library is never swept.** `/output` is a library a human organises and a
media server reads (ADR 0018).

## What counts against a quota

`GET /api/v1/storage` reports every family; `total_bytes` counts three:

| Counted | Why |
| --- | --- |
| `artifacts` | The results the person asked for |
| `delivery` | The copies they asked to keep in their library |
| `uploads` | The bytes they put there themselves |

| Reported, not counted | Why |
| --- | --- |
| `resources` | Raw material the engine keeps so the *next* job need not fetch it. Charging for an optimisation would make the person who asks twice pay twice for one download |
| `history` | Logs and snapshots — kilobytes, and the thing retention deliberately keeps |
| `tmp` | Disposable by definition, and gone at the end of the job |

## Atomic publication

A result is never made partially visible (INV-STORAGE-007/008).
`publish_file(source, destination)`:

1. refuses a silent overwrite (unless `overwrite=True`);
2. `os.replace` — atomic when source and destination share a filesystem;
3. otherwise copies to a neighbouring temporary file then renames atomically, and
   removes the source.

`JobStorage.promote_artifact` publishes `work|tmp → artifacts/` that way
**before** the DB insert (write-then-register, INV-005).

### Claiming a name, not just finding one free

Publication is atomic; *choosing* the name has to be too. Two jobs run
concurrently with the shipped defaults (`CONTENT_MAX_CONCURRENT_JOBS=2`) and a
collection's members run concurrently inside a job, so several writers can
compute the same target in the same instant — and the delivery library is the
shared destination of all of them.

`if not target.exists(): write(target)` loses that race: between the look and
the write another writer takes the name, and one file overwrites the other. So
both the job store and the delivery library take a name in two steps:

1. **stage** the bytes beside the destination under a private hidden name
   (`stage_beside`) — the slow copy happens where nobody is looking;
2. **claim** the first candidate name (`name`, then `name-1`, `name-2`…) with
   `claim_with`, which hard-links the staged file into place. `os.link` fails
   if the name exists, so the name and its content appear in one atomic step
   and the loser of a race simply tries the next counter.

Where hard links are unavailable (some network and FAT-family mounts) the name
is claimed with an exclusive `O_CREAT | O_EXCL` create and filled by a rename:
still exclusive, with a visible window the length of a rename rather than the
length of a copy.

This is why a media server watching the delivery folder never sees a zero-byte
or half-written file appear. `DeliveryStore.deliver` keeps its dedup rule in
front of the claim: a candidate name holding byte-identical content is returned
as-is rather than cloned to `-1`.

## Cache (`CONTENT_CACHE_ENABLED`)

The cross-job cache is **disabled by default** in the code (ADR 0009) and
**enabled in the HomeTube deployment** (compose, ADR 0010). The
`CONTENT_CACHE_ENABLED` switch governs two caches.

**1. URL analysis JSON — 3 days, a source of truth.** A resource's analysis is
cached by `resource_key` (a hash of `url + yt-dlp version + credential`,
**without any network access**): within the TTL window
(`CONTENT_ANALYSIS_TTL_HOURS`, default **72h**), a new submission of the same URL
does not re-probe. The DB cache (the `analyses` table) is always active; when
`CONTENT_CACHE_ENABLED=true`, the payload is **also** persisted as a durable file
under `cache/analysis/<key>.json` (write-through, including on a DB hit) — the
source of truth that survives a database reset and serves as a network-free
fallback.

**2. Downloaded video/media — content-addressed reuse.** With
`reuse_existing=true` (the default) and the cache enabled, an acquisition step is
reused from an earlier job when its **signature** (operation + provider +
`resource_key` + params + dependencies) is identical, with the file being
checksum-verified. The HomeTube effect: a video already downloaded (same URL,
same options) is not downloaded again, and the **content generations**
(transcript, summary…) over that video reuse the acquisition. Changing a video
option (quality, format, embed, sponsorblock) produces a distinct variant → a new
download.

When `CONTENT_CACHE_ENABLED=false`:

- no cross-job reuse; two identical jobs redo their work;
- no `cache/analysis/` file; `cache/` does not need to exist;
- `execution.reuse_existing=true` is **accepted but inert** → a deterministic
  `reuse_unavailable` warning at submission.

The analysis probe scratch stays distinct from the cache: it lives under
`tmp/analysis/` (disposable), never under `cache/`.

## Invariants (INV-STORAGE-\*)

1. A file in `tmp` is never an artifact.
2. An artifact is never stored only in `tmp`.
3. The `tmp` cleanup never deletes an artifact, a snapshot, a retained log or a
   working file of an active job.
4. The `work` cleanup is limited to the directory of the job concerned.
5. Two jobs do not share a mutable file in `work`.
6. Paths are generated and controlled by the backend (`safe_segment` rejects
   `..` and any untrusted id); no name coming from a URL/title/provider/client
   is used without normalization.
7. Every final file is published through an atomic move.
8. A partially written file never shows up as a valid artifact.
9. `cache` is not a synonym for `tmp`.
10. Disabling the cache is a normal and fully supported mode.

## Configuration

| Variable | Default | Role |
| --- | --- | --- |
| `CONTENT_DATA_DIR` | `./data` | The root of all data |
| `CONTENT_STORAGE_LAYOUT` | `flat` / `per_user` by auth mode | How owners are filed: `flat` (one owner, `<data>/jobs/…`) or `per_user` (`<data>/users/<owner>/…`). `flat` + sign-in is refused (ADR 0037) |
| `CONTENT_TMP_ROOT` | `<data>/tmp` | The root of the disposable |
| `CONTENT_CACHE_ROOT` | `<data>/cache` | The cache root (reserved) |
| `CONTENT_CACHE_ENABLED` | `false` | Enables the cross-job cache/reuse |
| `CONTENT_DELIVERY_DIR` | `<data>/delivery` | The user-facing library root |
| `CONTENT_DELIVERY_SCOPE` | `shared` | `shared`, `per_owner` (`<root>/<owner_id>/…`) or `off` (no library; delivery refused with `delivery_not_supported`) |
| `CONTENT_DELIVERY_DEFAULT` | `false` | Deliver every artifact by default (ADR 0018; `true` in compose) |
| `CONTENT_UPLOADS_ROOT` | `<data>/uploads` | Where client uploads are stored |
| `CONTENT_MAX_UPLOAD_BYTES` | 2 GiB | Per-upload ceiling, enforced while streaming — a `Content-Length` header is a claim, not a fact |
| `CONTENT_UPLOADS_TOTAL_BYTES` | 20 GiB | Quota for the whole upload store; new uploads are refused rather than filling the disk the engine runs on |
| `CONTENT_UPLOAD_TTL_HOURS` | `24` | Expiry, counted from the last reference |

## Adding the cache later

The future cache will store only **complete, validated, identifiable, reusable**
results, published atomically, structured as
`category → operation → cache key → immutable content` (and not as
`video/audio/text/youtube`). A sketch:

```text
data/cache/
├── entries/{analysis,acquisition,transform}/
└── blobs/sha256/
```

It will plug in behind the already-reserved `cache/` boundary and the
`CONTENT_CACHE_ENABLED` switch, without modifying the callers. No LRU/TTL/CAS/Redis
is being built today.
