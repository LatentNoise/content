# ADR 0024 — No authentication, and what would change that

Status: proposed (2026-08-15) · Records a position inherited rather than decided
· Related: ADR 0020 (uploads made the API a write primitive)

## Context

`authentication` sits in AGENTS.md under *initial exclusions*, alongside Redis,
Kafka and Kubernetes. That grouping is telling: it was a list of things not to
build yet, written when Content was one person's tool on one machine. It has
never been examined since, and two things have moved.

**The repository is public.** The threat model is no longer "software only I
run". People are installing it, and the README tells them to keep it on a
trusted network — which is honest, and which some of them will not do.

**Uploads made the API a write primitive.** Until 0.5.0 the worst an
unauthenticated caller on the network could do was make the engine *fetch*
something. Now they can make it *store* bytes. `deployment.md` says it plainly:
the size limit, the quota and the TTL bound the damage, and an opaque id resists
enumeration, but an id is identity, not authorization. Anyone who can reach the
API can upload.

An inherited assumption and a decision look identical from outside. This makes
it a decision.

## Decision

**No authentication in the engine. This stays true, and it is now a choice.**

The reasoning, so it can be argued with rather than guessed at:

**The deployment model already has an answer, and it is better than ours would
be.** Content is self-hosted software on a single host. Exposing it beyond a
trusted network means putting a reverse proxy in front, and that proxy —
Caddy, Traefik, nginx, a tunnel — does authentication properly: real identity
providers, TLS, rate limiting, audit, revocation. A static token checked by
middleware here would be strictly worse than any of them, while looking like
security to someone who then skips the proxy.

**A half-measure is the dangerous outcome.** The realistic small version — one
shared secret in an environment variable — has no revocation, no rotation, no
per-caller identity, and no story for the browser extension, which would have to
store it. It would let someone expose the engine believing they were protected.
Doing nothing keeps the honest message: *this API is open; put something in
front of it.*

**Every hour spent here is an hour not spent on the engine**, and the engine is
what people install Content for.

**What this does not excuse.** The bounds that make an open API survivable must
be real and stay real: upload size and quota, SSRF guarding on source URLs,
allowed input roots for `file` sources, no secret ever entering a request
(INV-009), and CORS off by default so a random website cannot drive a localhost
engine from a victim's browser. Those are the load-bearing parts of this
decision. Weakening any of them without revisiting this ADR would be
incoherent.

**The allowed-input-roots bound does not reach the MCP server.** The MCP
server never constructs a `file` source: it reads a local path itself and
uploads the bytes to the engine (ADR 0020), which is correct for an engine
running on another host but means `CONTENT_ALLOWED_INPUT_ROOTS` never sees
that read at all. The bound this ADR declares load-bearing is real on the
`file`-source path and absent on the MCP path — not weakened, routed around by
two designs that never checked each other. Its MCP-side equivalent is
`CONTENT_MCP_ALLOWED_READ_DIRS` (`apps/mcp/content_mcp/service.py`,
`_check_read_allowed`), which is its own bound rather than an inheritance of
this one, refuses every read by default the same way an empty
`CONTENT_ALLOWED_INPUT_ROOTS` does, and must be kept real independently of it.

## What would force this open again

Named now, so the question is not re-litigated from scratch each time:

1. **More than one user on one instance.** The moment two people share an engine
   and should not see each other's jobs, "no auth" stops being a deployment
   choice and becomes a missing feature.
2. **A hosted offering.** Anything Content runs on someone else's behalf needs
   real identity on day one; this ADR would not apply at all.
3. **A reported incident** in which an exposed instance was abused. That is
   evidence the documentation is not doing the job the design leans on.
4. **A write primitive that outgrows its bounds** — an endpoint that executes,
   schedules, or reaches outward on a caller's behalf in a way the current
   limits do not contain.

Items 1 and 2 are product decisions and would be seen coming. Items 3 and 4 are
the ones worth watching, because they arrive without notice.

## Consequences

**Gained.** A settled position, and the documentation that follows from it:
`deployment.md` and the README already say the API is open by design and that a
reverse proxy is the answer, which is now backed by a decision rather than by
omission.

**Paid.** Content cannot be safely exposed to the internet without help from
something else. That is a real limitation, stated plainly rather than softened,
and anyone for whom it is disqualifying should know before installing rather
than after.

**Not decided here.** Whether the *UIs* need a login is a different question with
a different answer — they are separate applications, and Streamlit has its own
options. Nothing above prevents putting a password on Studio while the engine
stays open behind it.
