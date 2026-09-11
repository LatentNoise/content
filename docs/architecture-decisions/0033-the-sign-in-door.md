# ADR 0033 — The sign-in door

Status: accepted (2026-09-12) · Implements decision 4 of ADR 0030 · Related:
0024 (no authentication), 0031 (one door for outbound email)

## Context

ADR 0030 built the lock and deliberately left the key unmade.
`Identity._from_credential()` returned `None`, so `token` mode refused every
request — the only safe direction for an unfinished check. Everything else was
already in place: every row has an owner, every path has one, and no code
downstream branches on the mode.

What was missing was the one thing that turns a stranger into an owner.

## Decision

### The door lives in the engine

Not in the Console, not in a service of its own. Three reasons, and the first
is the one that settles it.

**ADR 0030 makes `content/api/auth.py` the only place that turns a request
into an identity.** Issuing credentials somewhere else would create a second
such place, which is exactly what that ADR forbids.

**A Streamlit surface cannot set the cookie.** It does not control response
headers, so it cannot set an `HttpOnly` cookie on the parent domain — and the
parent domain is the whole reason the four surfaces share one.

**A dedicated service would need the same database.** It would either share it
quietly or expose another API, for no benefit while there is one engine.

The engine therefore serves two HTML pages. They are *protocol* pages, not
product pages: the engine still has no product UI.

### A link in a mail, not a password

The flow, and what each step defends against:

1. `POST /api/v1/auth/link` — **the answer never depends on whether the
   address is known.** A different reply for a known address would turn this
   into an account-enumeration oracle: ask it one address at a time and it
   tells you who has an account. The reply is identical when the address is
   unknown, when the rate limit fired, and when the mail service refused.
2. A random token, valid 15 minutes, **stored as a fingerprint**. A leaked
   backup must be a list of useless hashes, not a working keyring.
3. `GET /api/v1/auth/callback` — **the check and the burn are one statement
   in one transaction.** A read followed by an update leaves a window where
   two callers both see an unused token, which on a sign-in endpoint means two
   sessions from one link. A double click, a mail client prefetching the link,
   or a replay all lose the race by construction.
4. A session cookie: `HttpOnly`, `Secure`, `SameSite=Lax`, on the **parent**
   domain. `SameSite=Lax` is enough because the sign-in redirect is a
   top-level GET.
5. `next` is checked against an **allowlist of whole origins**, at the request
   *and* again at the callback. An open redirect on a sign-in endpoint is how
   a phishing page borrows your domain: the link is genuinely yours, the mail
   is genuinely yours, and the browser still lands on someone else's form.

### Sessions are rows, not signed tokens

A self-contained signed token would need no database read. It also could not
be revoked, and "sign me out everywhere" is the answer to a lost laptop. So a
session is a row, and its fingerprint is what the cookie hashes to.

The expiry slides, but at most once an hour. Sliding on every request would
make a page view a database write; never sliding would sign out someone who
uses the product daily.

### SHA-256, and not bcrypt

These secrets are 32 bytes from `secrets.token_urlsafe`. There is no
dictionary to attack and no human memory to compensate for, so a slow hash
would tax the server on every request while adding nothing against an attacker
who cannot guess 256 bits. The opposite is true for passwords, which is why
this is worth writing down.

### The address is checked for shape only

A full RFC validator costs a runtime dependency and a DNS library, to reject
addresses the mail service will reject anyway, while risking a valid exotic
address being turned away at the door. An address that does not exist simply
never receives its link.

Addresses are stored trimmed and case-folded. Nothing cleverer: stripping dots
or `+tags` would merge addresses their owner considers distinct — and the
Polar payment address has to match exactly (ADR 0030, decision 5).

## A hole this closed on the way

The ownership guard-rail walked `app.routes`, which does **not** include the
routes of a router mounted with `include_router` — those appear as one opaque
entry. Every route added that way would have been exempt from the guard
without ever being listed as an exemption. The sign-in door was the first
router in the codebase, so it was also the first to find this. The guard now
walks nested routers.

## Consequences

- `token` mode serves real users. It is still off by default, and
  `CONTENT_AUTH_MODE=token` still requires a mailer to be reachable.
- Self-hosted behaviour is unchanged, byte for byte. `local` remains a real
  owner id with **no account row**: none is invented.
- With no mailer configured, the link is written to the log. That is not a
  degraded mode for a self-hosted instance, and it is how an operator recovers
  when the mail service is down.
- API keys (ADR 0030, decision 6) join at the same seam and change nothing
  else: `_from_credential` will accept a `Bearer` key exactly as it accepts a
  cookie. Two carriers, one outcome.
