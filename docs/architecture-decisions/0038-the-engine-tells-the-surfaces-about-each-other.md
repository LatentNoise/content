# ADR 0038 — The engine tells the surfaces about each other

Status: accepted (2026-09-15) · Builds on 0033 (the sign-in door), 0034
(server-side surfaces) · Related: 0011 (monorepo), 0015 (one client)

## Context

Four surfaces, four hostnames, and no way from one to another. A person on
Studio who wanted the Console typed its address or did not go. The sign-in page
said "Sign in to Content" whichever surface had sent someone there, and the
email said the same, because nothing knew where anyone was going back to.

The chart had half an answer already: it gave each UI an environment variable
per sibling, built from the ingress host. Nothing read those variables, and
they named the LAN host rather than the public one, which the chart cannot
tell apart. Three surfaces each configured about the other two is six facts
that must agree, and they were already wrong.

## Decision

### Declared once, on the engine, read from `/config`

`CONTENT_SURFACES` lists the deployed surfaces as `<kind>=<public url>`. The
engine publishes them in `GET /api/v1/config` as `{kind, title, url}`, in a
canonical order. A surface knows exactly one address — the engine's — and
learns everything else from it, the same way it learns what the engine can
produce.

That is already how the surfaces work (ADR 0015, 0034): clients of one
contract, never configured about each other. The chart folds each surface's
`publicUrl` into the one variable; compose builds it from the host ports;
`make dev` from its own ports.

### The kinds are a closed set

`studio`, `console`, `hometube`. A surface has to recognise which entry is
*itself* to leave it out of a switcher, and a name it does not know is not a
surface it can reason about. An unknown kind is refused at startup, where the
person who typed it is reading the log, not skipped where nobody would notice.

The titles live with the kinds, in the engine. They are the same three strings
the applications carry, and the reason they are also here is what follows.

### The door names where it leads

The sign-in page and the email match `next` against the surfaces, by origin —
the same unit the redirect allowlist uses — and say "Sign in to Content Studio"
when they know, and the product's name when they do not. Never the bare target:
a page that repeats whatever URL it was handed is a page that will one day
repeat a URL an attacker chose.

### The redirect allowlist follows from the surfaces

The surfaces are, by definition, the places a sign-in comes back to. Listing
them a second time in `CONTENT_ALLOWED_REDIRECT_ORIGINS` is how one goes
missing and a sign-in lands on the default target instead of where the person
was. So the allowlist defaults to the surfaces' origins; an explicit list
replaces it entirely, for the installation that needs to allow something else.

### Chips in the sidebar, in the same tab

Streamlit offers two native ways to link out and both open a new tab: a page
link marks external targets `_blank`, and a link button is the wrong weight for
navigation. Moving between rooms of one product should not multiply tabs, so
the switcher is a row of small plain anchors rendered as HTML, which the
sanitiser leaves without a target — and the browser navigates in place. Two
chips at most, under the surface's own heading, styled to inherit whatever
theme the visitor chose.

### And where the engine itself is (added 2026-09-16)

The same mistake existed one level down, and it reached production. Each
surface built its sign-in, sign-out, documentation and download links from its
own `CONTENT_PUBLIC_API_URL`, while the engine wrote `CONTENT_PUBLIC_BASE_URL`
into its emails. Nothing compared them. The production surfaces had
`http://api.content.k3s.lab` — a LAN name — so the public HomeTube's Sign in
button sent visitors to an address nobody outside the house could reach.

So the engine declares that address too, as `public_api_url` in `/config`, and
the surfaces build every link a visitor follows on it. Their own setting stays
as the fallback for an engine that declares none: a self-hosted install, where
nobody follows an email.

### Addresses that cannot sign anyone in are refused at startup

In `token` mode the engine now checks its addresses against each other before
it serves anything: a public address must exist; every surface and the public
address must sit under the session cookie's domain, or share one host when the
cookie has none; and a `Secure` cookie needs `https` everywhere. Each of those,
wrong, is a sign-in that completes and carries no session — a failure nobody
sees until a visitor reports it. Refusing where configuration is read puts the
message in front of the person who typed the value.

`none` mode is never checked: nobody signs in, and a self-hosted install must
not be refused for addresses it does not use.

The redirect allowlist is the other lesson of the same day. It was removed from
the production values in anticipation of the derivation above, deployed on an
engine that did not derive it yet, and every sign-in quietly returned to Studio
instead of where it began. A value that depends on a code change ships with
that change, not before it.

## Consequences

- `/config` grows a `surfaces` field; a client that does not know it ignores
  it. An engine that declares none renders no switcher and no empty row.
- One fact per surface, in one place, and the sign-in page, the email, the
  redirect allowlist and every switcher read it. Adding a fourth surface is one
  kind in the engine and one `publicUrl` in the chart.
- The dead sibling variables leave the chart.
- `APIError.message` joins the SDK in the same change, because the first thing
  a person meets after signing in and asking for too much is a quota refusal,
  and Studio was printing the raw body of it.
