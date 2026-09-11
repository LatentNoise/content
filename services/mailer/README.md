# LatentNoise Mailer

One door for outbound email across LatentNoise products. A product never
speaks SMTP: it posts a message here and gets an id back. This service owns
the credentials, the retries and the record of what was sent.

## Why a service rather than a library

A library would have put the provider credentials inside every product that
sends mail, and duplicated the retry logic with it. One service means one
place to rotate a password, one place to read a delivery log, and one place
to change provider the day AlwaysData stops being the right answer.

The cost is honest and worth stating: the sign-in mail of any product now
depends on this service being up. That is why acceptance and delivery are
separated — a caller is answered as soon as the message is durably queued,
and an SMTP outage delays mail instead of failing a sign-in request.

## The contract

```
POST /v1/messages          Bearer <product key>   → 202 {"id": …, "status": "queued"}
GET  /v1/messages/{id}     Bearer <product key>   → the status of one's own message
GET  /v1/config            Bearer <product key>   → what this instance is, minus every secret
GET  /healthz              open                   → liveness and queue counts
```

A message carries either a written body or a template name:

```json
{"to": ["someone@example.com"], "subject": "Hello", "text": "…", "html": "…"}
{"to": ["someone@example.com"], "template": "magic-link",
 "variables": {"link": "https://…", "product": "Content", "minutes": "15"}}
```

Templates live in `mailer/templates.py` on purpose. A template is published
behaviour: it belongs in review and in the diff, not in a folder a deployment
can quietly replace.

## Identity

Same rule as the engine (ADR 0030): **the client sends a secret, the server
derives the identity.** A product never names itself in a field. `MAILER_API_KEYS`
holds `name:secret` pairs, so a log line says which product sent a message and
one product's key can be revoked without touching the others.

A product can only read back its own messages. Another product's id answers
404 rather than 403, because 403 would confirm the id exists.

## Configuration

| Variable | Meaning |
| --- | --- |
| `MAILER_SMTP_HOST` | provider host. **Treated as a secret** — see below |
| `MAILER_SMTP_PORT` | 465 for `ssl`, 587 for `starttls` |
| `MAILER_SMTP_USER` / `MAILER_SMTP_PASSWORD` | provider credentials |
| `MAILER_SMTP_SECURITY` | `ssl` (default) or `starttls` |
| `MAILER_DEFAULT_FROM` | sender used when a message names none |
| `MAILER_API_KEYS` | `content:secret,studio:secret` |
| `MAILER_DB_PATH` | queue and log, defaults to `/data/mailer.db` |
| `MAILER_MAX_ATTEMPTS` | give-up threshold, default 5 |
| `MAILER_RETRY_BASE_SECONDS` | backoff base, doubling up to one hour |
| `MAILER_DRY_RUN` | records instead of sending; what staging runs |

⚠️ **The SMTP host is never printed.** AlwaysData names the host after the
account, and that account name must not appear in public output. `/v1/config`
reports the security mode, the default sender and the client names, and
nothing else.

## Delivery semantics

A message is accepted, then delivered by a worker in the same process. The
queue is claimed with `BEGIN IMMEDIATE`, so splitting the worker into its own
deployment later is a configuration change rather than a rewrite.

Failures are classified, because they deserve opposite treatments. A 5xx
refusal is permanent and the message fails at once. Anything else is retried
with exponential backoff until `MAILER_MAX_ATTEMPTS`. Authentication failure
is the deliberate exception: it is a 5xx, but the operator can fix it, so the
message waits instead of being thrown away.

A crash mid-send leaves a row in `sending` that nothing would ever claim
again; a restart puts those back in the queue.

## Running it

```bash
python -m venv .venv && ./.venv/bin/pip install -e ".[test,dev]"
./.venv/bin/python -m pytest tests/ -q
./.venv/bin/python -m uvicorn mailer.app:create_app --factory --port 8025
```

The chart is `deploy/charts/mailer/`. Credentials come from a Kubernetes
secret that the chart references and never contains.

## Where this code should eventually live

This service is not part of Content's public contract and imports nothing
from the engine. It sits in this repository because that is where the work
started, and it should move to its own repository before Content is published
as open source. It was written to make that move a `git filter-repo` rather
than a rewrite.
