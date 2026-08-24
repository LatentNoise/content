# The yt-dlp base image: pinning, noticing, bumping

The backend image is built on `jauderho/yt-dlp` — yt-dlp and ffmpeg
preinstalled, multi-arch, Alpine/musl. It is the one dependency that rots
without anyone touching this repository: YouTube changes its player, and a
yt-dlp that worked last month starts answering "No video formats found".

That creates a conflict. Reproducible builds want a frozen base; a working
downloader wants the newest one. This page is how Content resolves it: **pin
hard, notice automatically, upgrade deliberately.**

## The policy

| | |
| --- | --- |
| **Pinned** | An explicit version *and* an immutable digest — never `:latest` |
| **Detected** | A scheduled workflow compares the pin against upstream every week |
| **Upgraded** | The pin in the Dockerfile: only by the maintainer, only after local validation passes |
| **Refreshed** | The *moving image tags*: daily and unattended, gated on a boot check and a `yt-dlp --version` check |
| **Never** | No automatic edit to the pin, no auto-merge, and never a republished `X.Y.Z` |

The workflow files **an issue** rather than opening a pull request: a
notification, not a change. Unsolicited pull requests are closed unread
([CONTRIBUTING.md](../../CONTRIBUTING.md)) — the maintainer's own are the normal
route for everything else, and Dependabot opens them for pinned actions, but a
base-image bump wants the local validation below rather than a green tick.

### Why the tags are refreshed and the pin is not

A watcher only shortens the time to *notice*. Between noticing and a release,
every user's downloader stays broken — issue #28 spent six weeks in that gap,
and the failure mode is total: "No video formats found", not a degraded result.

What makes an unattended rebuild safe here is a distinction the tag scheme
already draws. `X.Y.Z` is what was released and validated, and nothing
republishes it. `latest`, `X.Y` and `X` **already move** — CI re-points them at
every release, and `deploy/docker-compose.yml` defaults to `latest`. So
`.github/workflows/ytdlp-refresh.yml` rebuilds the released tree on the newest
base and re-points only those, which is a promise none of them were making.

The source pin is untouched: the refresh passes the newer base as a
`--build-arg`, the escape hatch described below. The weekly issue still gets
filed, and the deliberate bump still happens — this only stops users waiting
for it.

It also catches something the yt-dlp framing misses. Upstream republishes the
same yt-dlp version when the distro underneath it is patched, and every
CRITICAL/HIGH finding in the image scan (ADR 0026) lives in exactly that layer.
So the trigger is a **digest** change, not a version change, and the run summary
says which of the two happened.

## How the pin is written

In [`apps/backend/Dockerfile`](../../apps/backend/Dockerfile):

```dockerfile
ARG YTDLP_BASE_VERSION=2026.07.04
ARG YTDLP_BASE_DIGEST=sha256:daef12c6ed97b6b2984d81142ddb0c56ee2f81e2d7372aba0ecd4fa7b5709889
FROM jauderho/yt-dlp:${YTDLP_BASE_VERSION}@${YTDLP_BASE_DIGEST}
```

Both, deliberately. The **digest** is what Docker resolves, and it is immutable:
the same commit builds the same image next year. The **version** is what a human
reads, and what the issue and the commit message quote. The digest is the
multi-arch OCI index, so `linux/amd64` and `linux/arm64` both resolve from it —
the tag build in CI needs both.

They are also `ARG`s so a candidate can be tried without editing the file:

```bash
docker compose build content \
  --build-arg YTDLP_BASE_VERSION=2026.08.01 \
  --build-arg YTDLP_BASE_DIGEST=sha256:…
```

Both values are recorded on the built image as
`org.opencontainers.image.base.name` / `.base.digest`, so `docker inspect`
answers "what was this built on" without consulting the source.

### yt-dlp does not self-update at build time

`YTDLP_SELF_UPDATE` defaults to `false`. It used to run unconditionally, which
made the pin decoration: the version shipped was whatever upstream published
that morning, two builds of the same commit could carry different engines, and a
regression could be neither reproduced nor attributed.

An operator who prefers freshness over reproducibility can opt in:

```bash
docker compose build content --build-arg YTDLP_SELF_UPDATE=true
```

A pinned build does age, and that is handled by noticing rather than drifting —
the weekly check below is the maintainer's loop. (`CONTENT_YTDLP_MAX_AGE_DAYS`
can additionally make a running instance warn in its UI about an ageing yt-dlp;
it is off by default, because age alone cannot distinguish "stale" from "the
newest release, which happens to be weeks old".)

## How an update is detected

[`.github/workflows/ytdlp-base-check.yml`](../../.github/workflows/ytdlp-base-check.yml)
runs **Mondays at 06:17 UTC**, and on demand from the Actions tab. It calls
[`.github/scripts/ytdlp_base_check.py`](../../.github/scripts/ytdlp_base_check.py),
which reads the pin out of the Dockerfile, asks the registry what `latest`
resolves to, and compares digests.

- **Digests match** → it prints "pinned base is current" and stops. No issue, no
  noise, nothing to close.
- **Digests differ** → it opens **one** issue labelled `ytdlp-base-update`,
  showing the pinned version and digest, the available version and digest, a
  link to the matching yt-dlp release, and the validation procedure below. If
  that issue is already open, it is **edited in place** — a pending bump is one
  thread, not a monthly pile.

It needs no secret (`GITHUB_TOKEN` with `issues: write`), touches no file, opens
no pull request, and builds nothing. Run it by hand any time:

```bash
python3 .github/scripts/ytdlp_base_check.py     # stdlib only
```

> Two GitHub behaviours worth knowing: scheduled workflows run only from the
> **default branch**, and GitHub disables them after ~60 days without repository
> activity. If the issues stop arriving, check that first.

## The maintainer update procedure

Accepting a bump is a validated act. Run every step; commit only if all pass.

**1. Update the pin** in `apps/backend/Dockerfile` — both lines, copied from the
issue:

```dockerfile
ARG YTDLP_BASE_VERSION=<version from the issue>
ARG YTDLP_BASE_DIGEST=<digest from the issue>
```

**2. Build the backend image** on the new base:

```bash
docker compose build content
```

**3. Run the hermetic gate:**

```bash
make validate
```

**4. Run the end-to-end release checks:**

```bash
make validate-release
```

**5. Confirm the real yt-dlp media slice actually ran.** A skip is not a pass.
The run prints its own coverage; the line must read:

```text
  [x] yt-dlp media source
```

If it reads `[ ]`, yt-dlp is missing or `CONTENT_RELEASE_YTDLP=off` — fix that
and rerun, because this is the one check the bump exists to satisfy.

**6. Inspect the yt-dlp version the new image really carries:**

```bash
docker compose run --rm --entrypoint yt-dlp content --version
```

`make validate-release` also prints the version the engine detected
(`-> yt-dlp version: …`). Both should name the release the issue did.

**7. Commit the bump** — only now, and only if steps 2–6 all succeeded. Quote
both the base version and the yt-dlp version step 6 reported, so the history
records what was actually validated:

```bash
git commit -m "Pin yt-dlp base image to 2026.08.01

Base: jauderho/yt-dlp:2026.08.01 (sha256:…)
yt-dlp reported by the built image: 2026.08.01
make validate + make validate-release green, media slice exercised."
```

Then close the tracking issue. If validation **fails**, do not commit: leave the
issue open, note what broke, and stay on the current pin — an image that builds
but cannot read YouTube is worse than one that is a fortnight old.

## What the release check actually covers

One fixture, on purpose:
[`test_release_a_real_media_source_is_analysed`](../../apps/backend/tests/test_release_validation.py)
analyses **"Me at the zoo"** (`jNQXAC9IVRw`) — the first video on YouTube: 19
seconds, public, never taken down, and already this project's stable fixture in
`test_youtube_external.py`. It asserts the resource is recognised as media, that
real metadata comes back, and that `video.download` is `available` or
`derivable` — **not** `unknown`, because a broken extractor still returns
metadata it could not interpret, and accepting `unknown` is how a rotted yt-dlp
passed this check unnoticed.

Analysis (`yt-dlp -J`) is where the documented breakage surfaces: a player
change empties the format list and shows up as "No video formats found", not as
a failed transfer. Deciphering format URLs is a later step, exercised by
`tests/test_youtube_external.py` when `CONTENT_TEST_COOKIES` points at a cookie
jar — deliberately **not** duplicated here, because an anonymous download is
precisely the flaky thing a release gate must not become.

Point `CONTENT_RELEASE_YTDLP` at another URL to check a different source, or set
it to `off` to skip the slice when you are offline or YouTube itself is down.
Adding fixtures is usually the wrong reflex: each one is another thing that can
break for reasons that have nothing to do with Content.
