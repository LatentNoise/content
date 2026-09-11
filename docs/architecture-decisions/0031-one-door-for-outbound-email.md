# ADR 0031 — One door for outbound email

Status: accepted (2026-09-12) · Related: 0030 (ownership and the two modes),
0015 (the SDK is the only API client)

## Context

The hosted offering needs email before it needs anything else in ADR 0030's
queue: the magic link decided there is delivered by mail, and without it no
one signs in. Nothing in the estate sends mail today.

The domain turned out to be ready, which changed the size of the problem.
`latentnoise.dev` already has MX records at AlwaysData and, more importantly,
the three records that decide whether a message is believed:

```
SPF    v=spf1 include:_spf.alwaysdata.com ~all
DKIM   alwaysdata._domainkey.latentnoise.dev
DMARC  v=DMARC1; p=none; rua=mailto:dmarc@latentnoise.dev
```

So no sending infrastructure had to be built. What was missing was an account,
an address, and code. The account exists now and sends: a probe to Gmail came
back `spf=pass`, `dkim=pass`, `dmarc=pass`, delivered to the main inbox.

`latentnoise.dev` is also meant to become the brand for several products, not
just Content. That is what makes this a decision rather than a chore.

## Decision

### A service, not a library

Outbound mail goes through one small HTTP service. A product posts a message
and gets an id; it never opens an SMTP connection and never holds the provider
password.

The alternative considered was a shared library embedded in each product, each
with its own credentials. It is simpler on the day it is written and worse on
every day after: the provider password ends up copied into every product that
sends mail, rotating it becomes a coordinated release, and the retry logic is
duplicated in as many places as there are consumers.

**The cost is real and is accepted**: the sign-in mail of every product now
depends on this service being up. That is the reason for the next decision.

### Accepting and delivering are separate

The API answers as soon as the message is durably queued, and a worker in the
same process delivers it. A provider outage therefore delays mail; it does not
fail a caller's request.

The queue is a SQLite file claimed with `BEGIN IMMEDIATE`, the same pattern the
engine uses for jobs — so moving the worker into its own deployment later is a
configuration change rather than a rewrite.

### Failures are classified, not counted

A 5xx refusal is permanent and fails the message at once; retrying it only
annoys the provider. Everything else is retried with exponential backoff.

Authentication failure is the deliberate exception. It is a 5xx, but the
operator can fix it by correcting a credential, so the message waits instead of
being discarded.

### Identity follows ADR 0030

**The client sends a secret, the server derives the identity.** A product never
names itself in a field. `MAILER_API_KEYS` holds `name:secret` pairs, so a log
line says which product sent a message and one product's key is revocable on
its own. A product reads back only its own messages, and another product's id
answers 404 rather than 403, because 403 would confirm the id exists.

### Templates live in code

A template is published behaviour. It belongs in review and in the diff, not in
a folder a deployment can replace quietly. A caller may still post a fully
written body.

### The provider host is treated as a secret

AlwaysData names the SMTP host after the account, and this project's account
name must not appear in public output (see AGENTS.md, *Public identity*). The
host is therefore never printed: `/v1/config` reports the security mode, the
default sender and the client names, and nothing else. There is no generic
provider hostname to use instead; three candidates were checked and none
resolve.

### It runs as a pod, in the cluster

Not as a container on a separate Linux host. The cluster already provides
restart, secrets, ingress and certificates, and the other four surfaces are
there. A second place to operate would have to earn itself, and one small
service does not.

## Consequences

- One place to rotate the provider password, read a delivery log, or change
  provider.
- One more service to keep alive, on the sign-in path of every product.
- Staging instances run with `MAILER_DRY_RUN` so they cannot mail a real
  person by accident.
- DMARC stays at `p=none` until real traffic has produced a few reports.
  Moving to `p=quarantine` is a later, separate decision.

## Where the code lives, and where it should live

The service sits in `services/mailer/` in this repository, with its chart in
`deploy/charts/mailer/`. It is **not** part of Content's public contract and
imports nothing from the engine — it is not an `apps/` client in the sense of
ADR 0011.

It lives here because that is where the work started. Before Content is
published as open source, it should move to its own repository. It was written
so that move is an extraction, not a rewrite: no shared code, no shared
configuration, its own chart and its own tests.
