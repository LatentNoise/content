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
