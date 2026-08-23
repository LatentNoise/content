# ADR 0026 — What a security label can honestly mean here

Status: proposed (2026-08-23) · Extends ADR 0024, which made the absence of
authentication a decision rather than an omission

## Context

The question that started this was a good one: *security matters, people are
installing this, could Content carry some kind of security label?*

The instinct is right and the obvious execution is worthless. A badge we award
ourselves, or a page titled "Security" listing things we believe we do well,
costs nothing to produce and therefore says nothing. Every project that has
ever been compromised had one. A label carries information only in proportion
to what it would cost to be wrong about it — which means the only labels worth
putting on Content are the ones **a stranger can re-check without trusting
us**.

So the useful question is not "what badge", it is: *what can we publish that
someone sceptical could verify for themselves, and what would it take for that
publication to become false?*

An audit was run against the repository on 2026-08-23 to answer that from
facts rather than impressions. It came back lopsided in an instructive way.

## What is already true, and is not being said anywhere

The posture is stronger than the documentation suggests, in exactly the places
that required somebody to make a decision:

- **Every GitHub Action is pinned to a commit SHA** with the version in a
  trailing comment. There is no `@v4` in the repository that an upstream
  maintainer — or whoever compromises them — could silently re-point.
- **PyPI publishing uses OIDC Trusted Publishing.** There is no long-lived
  API token to steal, because none exists. The same is true of the MCP
  registry publication.
- **Workflow permissions default to `contents: read`** and are widened per job,
  rather than the other way round.
- **Publishing runs through GitHub Environments**, so the credentials-less
  exchange is still gated.
- **Secrets never enter a generation request** (INV-009), which is why
  SECURITY.md can ask reporters not to paste credentials — the design does not
  need them.
- **ADR 0024 states the no-authentication position as a decision**, with the
  threat model it rests on, instead of leaving it as an unexamined default.

None of this is visible to somebody deciding whether to install Content. That
is a documentation failure, not a security one — but it is the cheapest thing
on this page to fix.

## What is missing, and it is all the same shape

Every gap the audit found is an instance of one thing: **nothing here produces
evidence.** The decisions are sound and no machine ever re-checks them.

1. **There is no dependency lockfile.** Dependencies are floating ranges
   (`fastapi>=0.115`, no ceiling), resolved fresh at every build. Two builds of
   the same tag can therefore contain different code, and the question "which
   version of X shipped in 0.6.7" has no answer short of pulling the image and
   looking. Reproducibility is not a purity concern here; it is the thing that
   makes an advisory answerable.

2. **Nothing scans dependencies for known vulnerabilities.** The first
   `pip-audit` run of this audit found one advisory — and the *triage* is the
   part worth recording. It was `cryptography 49.0.0`, PYSEC-2026-3552, a
   Bleichenbacher oracle in `pkcs7_decrypt_*`. Content does not import
   `cryptography` anywhere, the package is in no manifest, and it was sitting
   in a developer's virtualenv as debris from an unrelated install. It ships in
   nothing.

   That is the lesson, not a footnote to it. **A scanner pointed at the wrong
   target produces noise, and noise is worse than no scanner**, because it
   teaches the one person reading the output to skim it. Whatever gets
   automated has to look at what is actually published.

3. **Nothing scans the image**, which is where this project's vulnerabilities
   will actually live. The engine image carries ffmpeg, yt-dlp, typst and a
   Debian base — three of those are large C codebases parsing hostile input,
   and one of them exists to talk to the open internet.

   The measurement makes the point better than the argument does. Resolving
   exactly what the Dockerfile installs gives **eighteen Python packages**;
   queried against OSV on 2026-08-23, **none of them carries a known
   advisory**. Content's Python surface is small and, today, clean. A
   Python-only scanner would therefore report a reassuring nothing, every
   week, about the least dangerous layer in the deliverable — which is not a
   gap in coverage so much as a machine for producing false confidence.

4. **Secret scanning, push protection and Dependabot alerts are all disabled**
   on the repository (verified 2026-08-23 via the API). They are free on public
   repositories and are the only controls on this page that require no code.

5. **Private vulnerability reporting is off**, which SECURITY.md honestly
   admits while routing reporters to email. Email works, but it produces no
   advisory record, no CVE, and no way for a user to check afterwards whether
   the thing they read about was fixed.

6. **Nothing links a published artifact to the commit that produced it.** A
   wheel on PyPI called `content-mcp 0.6.7` is trusted because it is on PyPI.
   Build provenance would make that checkable rather than assumed.

## Decision

**Publish evidence, not assertions.** Three moves, in order of what they buy.

### 1. Adopt one label, and pick the one that can go down

Content will publish an **OpenSSF Scorecard**. It is not our claim about
ourselves: it is an automated third-party re-check of the repository, run
weekly, whose result anyone can query from a public API without asking us. It
grades the things this ADR is about — pinned dependencies, permissions, branch
protection, released artifacts, maintenance — and it will grade some of them
badly at first.

That is the point, and it is the reason to prefer it to the alternatives. A
score that can fall is a measurement; a seal that only gets awarded is
marketing. The honest version of "reassure the users" is *here is a number we
do not control, and here is what we are doing about the parts of it we are
losing.*

Explicitly **not** adopted: any self-certified questionnaire badge presented as
an audit, and the word "audited" anywhere, unless an auditor who is not the
maintainer has actually done it.

### 2. Scan what ships

Two scanners, aimed at the deliverables rather than the workspace:

- the **published image**, for OS packages and Python distributions alike,
  because ffmpeg and yt-dlp are the real surface;
- the **release artifacts** — the wheels and sdists that reach PyPI.

With one rule about failure, learned from finding number 2 above: a scanner
that fails the build on anything it finds will, within a month, be a scanner
whose failures are routinely overridden. It reports, it opens an issue, and a
finding is closed by a written triage — *does Content reach this code path* —
not by a version bump reflex.

### 3. Say what Content is, where the decision is made

The largest real risk to a Content user is not a CVE in a dependency. It is
putting an engine with no authentication on the open internet, which ADR 0024
argues is the right design *and which therefore places the whole duty on the
message*. That message currently lives in a README paragraph and a deployment
doc; it needs to be where somebody is choosing a port to publish.

A short, plain threat-model page — what an attacker who can reach the API can
do, what bounds the damage, what a reverse proxy is for — is worth more to a
user's actual safety than every other item on this page combined.

## Consequences

- The Scorecard's first score will be public and mediocre in places (no
  lockfile, no branch protection on a single-maintainer repository, no signed
  releases). Publishing it before fixing those is deliberate: a badge that only
  appears once it is green is the self-awarded kind again.
- A weekly scan on a project with ffmpeg and yt-dlp in the image will find
  things. Most will be unreachable from Content's code paths, and each one
  costs a triage. That recurring cost is the actual price of this ADR, and it
  is charged to a single maintainer — which is the strongest argument against
  going further than these three moves.

  The first run measured that price. At `CRITICAL..LOW` it produced **300
  alerts** across the four images; narrowed to `CRITICAL,HIGH`, **87**. Every
  one sits in the Debian base layer — `perl-base`, the `util-linux` family,
  `openssl` — and **31 have no fixed version published at all**, which is the
  clearest possible vindication of not gating a build on them. The remaining 56
  do have fixes, and they say one useful thing between them: *rebuild the
  image.* The severity floor was raised to `HIGH` on the strength of that first
  run, because 213 medium and low findings in base packages is precisely the
  noise this ADR argues against, arriving by the front door.
- A lockfile is *not* adopted here. It is the correct fix for finding 1 and it
  is a real change to how the project builds and updates; it belongs in its own
  decision, with automated update PRs, rather than being smuggled in under a
  security heading.

## What this does not claim

Content is not audited. It has no SLA, no bounty, and one maintainer, and
SECURITY.md already says so. Nothing here changes that, and any label that
implied otherwise would be the failure mode this ADR exists to avoid.
