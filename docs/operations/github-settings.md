# GitHub settings the governance model depends on

The governance model ([GOVERNANCE.md](../../GOVERNANCE.md),
[CONTRIBUTING.md](../../CONTRIBUTING.md)) makes promises that live outside the
repository — in GitHub's web UI, where nothing is versioned and nobody can
review a change. This page is the audit list: what must be true on the hosting
platform, and what to check when something looks wrong.

Written during release preparation.

## The one the contribution policy rests on

CONTRIBUTING.md, the README and the issue templates all state that pull requests
cannot be opened. That is true only for as long as **pull requests are turned
off in the repository's settings**. Nothing in the repository enforces it: if
the setting is ever flipped back on, the project silently starts receiving pull
requests it has told everyone it will not read, and four documents become wrong
at once. It is row 1 of the checklist below for that reason — verify it, in the
web UI, before announcing the repository.

## Checklist

Tick these after creating the public repository. Everything here is invisible
from a clone, which is why it is written down.

| # | Setting | Required value | Why |
| --- | --- | --- | --- |
| 1 | **Pull requests** | Disabled | The whole contribution policy: CONTRIBUTING.md, the README and the issue templates all promise a PR cannot be opened. Nothing in the repository enforces this |
| 2 | **Actions → Allow all actions** (or allow the pinned ones) | Enabled for `actions/*`, `astral-sh/setup-uv`, `docker/*` | `ci.yml` pins these by SHA; a restrictive allowlist blocks the gate |
| 2b | **Actions → Workflow permissions** | Must permit `issues: write` | A workflow can only *narrow* the repository maximum, never exceed it. On the read-only default, `ytdlp-base-check.yml` fails to file its issue and the base image silently stops being tracked |
| 3 | **Branches → default branch** | `main` | Every documentation link uses `/blob/main/…`, including the issue-template contact links |
| 4 | **Branch protection on `main`** | Restrict pushes to the maintainer; do **not** require pull requests | Requiring PRs would contradict the model — the maintainer pushes directly |
| 5 | **Collaborators** | None | GOVERNANCE.md: one maintainer, no ladder, no committee |
| 6 | **Issues** | Enabled | Bug reports and feedback are explicitly welcome |
| 7 | **Blank issues** | Disabled (`.github/ISSUE_TEMPLATE/config.yml`) | Already in the repository; verify it took effect |
| 8 | **Discussions / Wiki / Projects** | Disabled | Unmoderated surfaces the single maintainer has not undertaken to read |
| 9 | **Private vulnerability reporting** | Enabled | SECURITY.md directs reporters to email; this gives them a private in-platform route too |
| 10 | **Forking** | Allowed | GOVERNANCE.md calls forking the intended path — never restrict it |
| 11 | **Repository visibility** | Public | AGPL §13: the source offer in the running UI must resolve (see below) |
| 12 | **Sponsor button / social preview** | Off | No marketing surface is claimed |

## The AGPL §13 chain

The running instances offer their source. That offer must resolve, or the
licence obligation is not met:

1. `GET /api/v1/system` returns `license` and `source_url`
   (`CONTENT_SOURCE_URL`, default `https://github.com/LatentNoise/content`).
2. Every UI renders it in the footer (`content_sdk.legal`).
3. The URL must return **200** for an anonymous visitor.

Check it from outside any session that is logged in to GitHub:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -L "$(curl -s localhost:8010/api/v1/system \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["source_url"])')"
```

A private repository answers 404 here, and so does a repository that was
renamed or moved to another account. Until the repository is public this
returns 404 — expected, and the last thing to re-check on publication day.

An operator who modifies Content and runs it for others must repoint
`CONTENT_SOURCE_URL` at *their* source. That is their §13 obligation, not this
project's; the variable exists precisely so they can meet it.

## Changing the repository's home

The home is `https://github.com/LatentNoise/content`, taken from the `origin`
remote. Nothing depends on that string being right *before* publication — the
engine, the tests and the images all work with a URL that resolves to nothing.
Exactly two things break if it is wrong *after*: the AGPL §13 source offer in
the UIs, and the issue-template contact links.

A fork that republishes should repoint it. It lives in **9 tracked files, 14
occurrences**:

| File | Occurrences | What it is |
| --- | --- | --- |
| `apps/backend/content/config.py` | 2 | the default `CONTENT_SOURCE_URL` — the §13 offer |
| `.github/ISSUE_TEMPLATE/config.yml` | 4 | contact links (`/blob/main/…`) |
| `apps/backend/Dockerfile` | 2 | OCI `source` + `url` labels |
| `apps/web-hometube/Dockerfile` | 1 | OCI label |
| `apps/web-studio/Dockerfile` | 1 | OCI label |
| `apps/web-admin/Dockerfile` | 1 | OCI label |
| `README.md` | 1 | the clone command |
| `NOTICE` | 1 | the redistribution notice |
| `.env.example` | 1 | the documented override |

To retarget it, in one pass:

```bash
OLD="LatentNoise/content"
NEW="your-org/your-repo"
git grep -l "$OLD" -- . ':!work/' | xargs sed -i '' "s|$OLD|$NEW|g"   # GNU sed: -i
git grep -n "$OLD" -- . ':!work/' || echo "no occurrence left"
make validate
```

Then re-check the §13 chain with the command in the section above — it must
answer 200 for an anonymous visitor, not for a browser already signed in to the
account that owns the repository.

`work/` is excluded on purpose: the archived prompts are a record of what was
asked at the time and are not rewritten.

## What is versioned instead

Deliberately in the repository, so it can be reviewed rather than remembered:

- `.github/CODEOWNERS` — records ownership (blocks nothing on its own).
- `.github/ISSUE_TEMPLATE/` — the bug and feature forms, blank issues off.
- `.github/workflows/ci.yml` — the gate, on pushes and tags.
- `.github/workflows/ytdlp-base-check.yml` — the weekly base-image check. It
  files an issue and nothing else; see
  [ytdlp-base-image.md](ytdlp-base-image.md).
- `.github/workflows/release-draft.yml` — a **draft** release skeleton on every
  version tag. Publishing it stays a manual, deliberate act.

The contribution policy itself is *not* versioned: it is the pull-request
setting in row 1, which is why that row exists.
