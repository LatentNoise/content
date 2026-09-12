# ADR 0035 — Operating is a privilege, not more data

Status: accepted (2026-09-13) · Completes ADR 0030 (ownership and the two
modes) · Related: 0024 (no authentication), 0033 (the sign-in door)

## Context

ADR 0030 gave every row and every file an owner, and left four routes outside
that rule with a note saying they would be dealt with later. They describe the
**installation** rather than anyone's work: disk paths and occupancy, the
shared fact cache, the client-facing configuration.

Filtering them by owner is meaningless — they have no owner. The question they
raise is not "whose is this" but "who may know".

Later arrived abruptly. The public deployment sat behind a temporary password
while the hosted mode was built; the day it came off, `/api/v1/storage`
answered anybody and printed the server's own paths:

```json
{"jobs":{"path":"/data/jobs"},"delivery":{"path":"/output"},"tmp":{"path":"/data/tmp"}}
```

A stopgap closed it at the reverse proxy within minutes. That fix lives at the
wrong layer — it only protects one route *in*, and says nothing to a caller
reaching the engine another way. This ADR is the right layer.

## Decision

### A flag on the account, not a second kind of credential

An operator signs in like everyone else, owns their own rows like everyone
else, and may additionally read facts about the machine. One sign-in, two
capabilities.

The alternative was a separate operator credential, held in the environment.
It is tempting because it needs no account — and it produces a secret with no
owner, no expiry, no audit trail, and no way to tell which of two people used
it. A privilege attached to a person is revocable, attributable, and visible in
the same place as everything else about them.

### The first operator exists by configuration, and only the first

`CONTENT_OPERATOR_EMAILS` grants the flag at sign-in. This solves the
chicken-and-egg problem honestly: there is nobody to promote the first
operator.

**Unlisting an address does not revoke the flag.** Withdrawing a privilege is a
deliberate act on the account, never a side effect of editing a file — an
operator who fat-fingers a variable should not silently demote themselves, and
a config file should not be a hidden authorization list.

### `local` is always the operator, and that is not a branch on the mode

A self-hosted instance has exactly one user, who is by definition the person
running the machine. `is_operator("local")` is therefore true by construction.

Nothing anywhere asks "am I hosted". This is a fact about *that owner*, not
about the deployment, which is what keeps ADR 0030's rule intact.

### `/api/v1/storage` was split rather than closed

The plain name now reports **what you hold**: your jobs, your uploads, your
delivered files when the library is per-owner, and one total. It carries no
paths — an owner needs to know how much they hold, never where the machine
keeps it.

The installation-wide view moved to `/api/v1/operator/storage`.

This follows the maintainer's framing, and it is better than what this ADR
first proposed. Closing the route would have been an administrator's answer:
correct, and useless to the person who wants to know how much space they are
using. Splitting it serves both, and it is the foundation the per-user quotas
read from.

`/api/v1/cache` and `/cache/purge` are operator-only without a per-owner half.
The cache holds facts about *public* resources, deliberately shared so two
people analysing the same URL pay for it once (ADR 0030); there is no "your
share" of it to report. Purging is operator-only for a blunter reason: it takes
a decision on behalf of everyone.

`/api/v1/config` stays open. It is a client-facing view — languages, delivery
policy, upload limits — and a client needs it to know what to ask for.

### 403, not 404

The caller is authenticated and the route plainly exists. Pretending otherwise
would only make a real operator think the engine is broken.

404 is the right answer for a *resource* that may not be theirs, which is a
different question: there, the id's existence is itself the secret.

## Consequences

- The ownership guard-rail now accepts either door, a plain identity or an
  identity plus the privilege, and still fails on a route that resolves
  neither.
- `/auth/me` reports `is_operator`, so a surface can show or hide operator
  views without probing a route to see whether it gets a 403.
- The Console reads the installation view through the new route and says so
  plainly when the person looking is not the operator.
- The proxy-level stopgap can be removed once this ships, and should be: a rule
  in one reverse proxy is a rule that does not travel.
