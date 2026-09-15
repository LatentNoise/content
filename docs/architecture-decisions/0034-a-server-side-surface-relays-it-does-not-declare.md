# ADR 0034 — A server-side surface relays, it does not declare

Status: accepted (2026-09-12) · Completes ADR 0033 · Related: 0030 (ownership
and the two modes), 0015 (the SDK is the only API client), 0011 (monorepo
boundaries)

## Context

ADR 0033 gave the engine a sign-in door, and it works for anything that talks
to the engine directly: a browser gets a session cookie, a program gets a key.

The three UIs are neither. They are **Streamlit applications, and Streamlit
runs on a server**. The browser talks to Studio; Studio talks to the engine
from its own process. There are two hops, and the visitor's cookie only reaches
the first one.

So a signed-in visitor faced a Studio that was, to the engine, anonymous. Under
`CONTENT_AUTH_MODE=token`, every call it made answered 401. The sign-in door
was complete and the surfaces could not use it.

## The trap that shaped the decision

All three UIs cache their client for the whole process:

```python
@st.cache_resource
def get_client(base_url): return ContentClient(base_url)
```

`@st.cache_resource` is **shared by every visitor of that process**. The
obvious fix — attach the visitor's credential to that client — would make one
person's session the next visitor's. Not a crash: a silent, total
cross-contamination of identity, appearing only under concurrent use.

This is the single most dangerous line in the chantier, and it was already
written before any of this work started.

## Decision

### The surface forwards a secret; it never names a user

The UI reads the visitor's session cookie from `st.context.cookies` and
**replays it** to the engine. It never tells the engine who the visitor is.

That distinction is the whole ADR. The alternative — giving each UI a service
credential and letting it declare, per call, whom it is acting for — would work
on the first day and destroy the property ADR 0030 exists to protect: **the
client sends a secret, the server derives the identity.** A surface that can
name any user is a surface whose compromise is every account.

### The identity belongs to the request, never to the client

The SDK gained `headers_provider`, a callable invoked on **every** request:

```python
ContentClient(base_url, headers_provider=lambda: streamlit_visitor_headers(st.context))
```

The cached client is therefore still shared, and still correct, because it
carries no identity at all — it asks for one at the moment it is used. A
provider that raises, which is what happens outside a script run, yields no
headers rather than an error: absent identity is a valid state, and it is
exactly the self-hosted one.

`api_key` stays on the client, because a program's key genuinely belongs to the
process. Both can coexist: a key identifies the caller, a cookie identifies the
visitor it is acting for. Today nothing uses both at once, and the mechanism
does not forbid it.

### A refused visitor is sent to the door, not shown an error

A 401 makes the surface offer a sign-in link back to the engine, with a `next`
pointing at the surface. 403 is deliberately told apart: signed in but not
allowed is a different message, and returning someone to a form they already
filled would be wrong.

The `next` value is checked by the *engine* against its allowlist. The surface
does not validate it, because a surface cannot be trusted to guard a redirect
it also supplies.

### The challenge is asked, never awaited (added 2026-09-13)

The rule above said what to do with a 401. It did not say where the 401 comes
from, and the answer turned out to be "nowhere".

A surface boots on routes that carry no owner by design — `/health`,
`/config`, `/system` — because an installation must be able to say it is alive
before it knows who is asking. Everything owner-scoped then sits inside a
`try/except` that degrades politely, a dash instead of a job list, because a
surface must survive one endpoint being slow. Put together, those two
reasonable habits meant a visitor whose session had been deleted was shown a
complete, working product that silently did nothing, and never met the door.

Nothing leaked: every owner-scoped call still refused. But a UI that renders
for someone it will not serve is a broken promise, and no amount of care in
individual call sites fixes it, because each one is right on its own.

So every surface asks first, on every run: **`GET /api/v1/auth/me`**. It is the
one route whose answer *is* an identity, so it cannot be satisfied by anything
else, and in `none` mode it answers `local` — no surface branches on the
deployment mode. The answer is not cached: a session deleted between two clicks
must stop working on the next one, which is precisely the case that exposed
this.

**The interface still renders.** Replacing it with a door was the first
attempt and it was wrong: someone arriving at a public instance should see what
the product is before being asked for anything, and a surface that blanks
itself teaches nothing. So the page stays, with a banner above it saying
plainly that nothing will run until you sign in, and the same button in the
sidebar where it stays put after the banner scrolls away. Not a dismissible
dialog: a dialog is read once and then gone, while the reason the page is not
working lasts until it is fixed.

**And it is a button, not a redirect** — not by preference. Streamlit
components render inside an iframe sandboxed without `allow-top-navigation`, so
a script cannot move the browser out of the app at all.

One implementation, in the SDK (`content_sdk.signin`), because it is the only
place three single-file Streamlit apps can share code from — D-21 records what
happened the last time a helper was copy-pasted into three UIs.

### The forwarded cookie is a snapshot, and leaving has to account for it

Streamlit captures the request headers **once, when the websocket opens**, and
every rerun after that — a click, a fragment timer — reads that same snapshot.
So a surface forwards the cookie the browser presented at connection time, for
as long as the page stays open.

Measured against a real deployment, that gives four behaviours and only the
last one surprises:

| What happens | What the surface does |
| --- | --- |
| No cookie at connection | Refused, the door is drawn |
| Session revoked while the page is open | The next rerun is refused — the engine is the authority |
| Cookie deleted, page reloaded | New websocket, no cookie, the door is drawn |
| **Cookie deleted, page left open** | **Nothing changes** |

The last row is not a bug to fix in the surface: deleting a cookie in a browser
does not end a session, it forgets a credential, and the session on the engine
is still perfectly valid. There is also no mechanism by which a page can learn
that the browser's cookies changed.

What follows from it is that **signing out must be a place the browser goes,
not a call the page makes.** `POST /api/v1/auth/logout` revokes the session,
which is the half that matters, but it is called by the *surface* — so the
browser keeps its cookie and the page keeps its snapshot. `GET /auth/sign-out`
does both halves at once: revokes the session, clears the cookie in the browser
that actually holds it, and redirects back, which reconnects the surface with
nothing to present.

It requires no identity, deliberately. A link is what a person clicks, an
unknown cookie is nothing to revoke, and the worst a forged link achieves is
signing somebody out.

## Consequences

- **Self-hosted is untouched.** No cookie is sent, none is needed, and
  `streamlit_visitor_headers` returns nothing outside a browser session.
- **One process serves many people correctly.** Proven by an integration test
  where two real sign-ins go through one cached client and never mix.
- **A fourth surface cannot silently skip it**: a UI test asserts all three
  build their client with a provider.
- **Uploads still cross the network twice.** The browser sends a file to
  Streamlit, which forwards it to the engine, because `st.file_uploader` is a
  server-side component — the bytes are already in the UI's memory when the
  script runs. Fixing that needs the browser to call the engine directly, which
  means CORS and a custom component. It is a separate decision, deliberately
  not taken here.

## What this does not solve

The Console shows installation-level facts (`/config`, `/storage`, `/cache`)
that have no owner and should become operator-only. Forwarding a session does
not make those routes safe; it only makes the Console usable by the person who
signed in. That remains open.
