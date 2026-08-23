# Threat model

What an attacker can do to a Content deployment, what bounds it, and the one
decision that matters more than all the others.

This page exists because ADR 0024 chose not to build authentication into the
engine, on the argument that a reverse proxy does it properly and a shared
secret in an environment variable does it badly. That argument is sound and it
transfers the entire duty onto saying so clearly. This is the saying-so.

## The one thing that matters

**Content's API has no authentication. Anyone who can reach it can use it.**

Not "can use some of it". A caller who can open a TCP connection to the engine
can submit jobs, upload files, read every artifact any job ever produced, and
delete things. There is no account, no token, no per-caller identity, and
nothing to revoke.

That is a supported configuration on a home network. It is not a configuration
to put on the open internet, and the failure mode is not subtle:

- **Do** run it on a LAN, a VPN, or a Tailscale/WireGuard network.
- **Do** put an authenticating reverse proxy in front of it — Caddy with
  `basic_auth`, Traefik with a forward-auth middleware, nginx, Authelia,
  Cloudflare Access — if it must be reachable from outside.
- **Do not** publish the port. `ports: ["8000:8000"]` in a compose file on a
  host with a public IP is the mistake this page exists to prevent. Bind to
  `127.0.0.1:8000:8000` and let the proxy reach it, or keep it on an internal
  Docker network.

If you are not sure whether your instance is exposed, it is: check from
somewhere off your network before assuming otherwise.

## What an attacker who reaches the API can do

| They can | Bounded by |
| --- | --- |
| Submit jobs, making the engine fetch arbitrary URLs | Concurrency limits, per-job runtime cap. It is a server-side request forgery surface: the engine will fetch what it is told to fetch. |
| Upload arbitrary bytes | Per-file size limit, a storage quota, and a TTL that reclaims them (ADR 0020, ADR 0023) |
| Read every artifact of every job | Nothing. Artifact ids are opaque, which resists enumeration, but an id is identity and not authorization (ADR 0024) |
| Write into the delivery library | The library path is operator-configured; delivery cannot escape it |
| Cancel or delete jobs and artifacts | Nothing |
| Fill the disk | The quota and the retention policy, both operator-configured |

Two things it is worth being precise about, because they are the ones people
assume:

- **An opaque artifact id is not access control.** It is unguessable, which
  makes bulk enumeration impractical. It does not stop anyone who has the id,
  and every id is listable through the jobs API by anyone who can reach it.
- **The engine fetches what it is asked to fetch.** On a network where the
  engine can reach things a caller cannot — a router's admin page, a metadata
  endpoint, another container — that reach is available to whoever can reach
  the API.

## What is out of reach by design

- **Credentials never enter a generation request** (INV-009). Cookies and
  provider credentials are operator-side configuration; a request references
  them, it does not carry them. A leaked request body leaks no secret.
- **Delivery is confined.** An output's `delivery.folder` cannot traverse out
  of the configured library root.
- **Uploads are not executable.** They are read as sources; nothing in the
  pipeline runs them.
- **The engine holds no user accounts**, so there is no credential store to
  breach — which is the one genuine upside of having no authentication.

## Where the real vulnerabilities will be

Not in Content's Python. The resolved production dependency set is eighteen
packages, and on 2026-08-23 none of them carried a known advisory. The
interesting surface is the image's other half: **ffmpeg**, **yt-dlp** and
**typst**, plus the Debian base — large native codebases that parse hostile
input for a living. Those are scanned weekly against the *published* images and
the findings land in this repository's Security tab (ADR 0026).

Keeping the image current is therefore the single most effective thing an
operator does, and the first scan says so with numbers: of 87 CRITICAL/HIGH
findings across the four published images, every one was in the Debian base
layer, and 56 of them already have a fix published upstream. Those 56 go away
on a rebuild. Content is pre-1.0 and only the latest release is supported
(SECURITY.md).

## Reporting something

See [SECURITY.md](../../SECURITY.md). Do not open a public issue.
