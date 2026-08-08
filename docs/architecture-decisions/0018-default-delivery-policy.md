# ADR 0018 — The delivery library is the default destination

Status: accepted (2026-08-07) · Builds on ADR 0017 · Follows the ADR 0010 pattern

## Context

Delivery exists (M1, prompt 18): `output.delivery {folder, filename}` copies an
output's artifacts into `CONTENT_DELIVERY_DIR`, which docker-compose mounts on
the host — the applicative, browsable library. But:

- Delivery is **opt-in per output**, keyed on field presence: the executor
  skips it when neither `folder` nor `filename` is set — and `{}` is the schema
  default. The natural request leaves files only in `jobs/<id>/artifacts/`, a
  tree no user browses.
- Encoding intent in field *presence* already caused a real bug: the extension
  choosing "library root, no rename" sent no fields, so nothing was delivered.
- The delivered path is computed, returned by `DeliveryStore.deliver` — and
  discarded. Nothing in the database or the API can tell a client where a file
  landed.

The goal: **users should never have to search inside `jobs/<id>/artifacts/`.**
The artifact store remains the source of truth; the library is the user-facing
copy.

## Decision

**1. A server-side delivery policy: `CONTENT_DELIVERY_DEFAULT`.**

When enabled, every produced artifact whose output expresses no contrary
intent is also copied into the delivery library, under its own
`display_filename` (ADR 0017). Off in the engine's code default, **on in the
HomeTube docker-compose deployment** — the exact pattern of ADR 0010 (cache
off by default, on in the deployment): the bare engine keeps the frozen V1
contract behaviour and never doubles disk silently; the packaged product
delivers by default. With the policy off, nothing changes except that
artifacts now carry good names.

**2. An explicit per-output mode: `delivery.mode ∈ {inherit, deliver, none}`.**

Introduced **now**, not later, for three concrete reasons:

- **Contract honesty (§9).** This ADR changes what "no delivery fields" means
  (from "no copy" to "the server policy decides"). A client that relied on
  no-copy must have an explicit way to keep it — `mode: "none"` is what makes
  the semantic change compatible instead of silent.
- **It removes the ambiguity class that already produced a bug.** Intent
  encoded in field presence cannot distinguish "library root, default name"
  from "no delivery". An explicit mode can.
- **It costs one enum.** No new abstraction, no workflow, trivial validation.

Semantics (resolved by the **planner** into the ExecutionPlan; the executor
follows the plan):

| `mode` | policy on | policy off |
| --- | --- | --- |
| `inherit` (default, = absent) | deliver | deliver only if `folder` or `filename` set (today's behaviour, preserved exactly) |
| `deliver` | deliver | deliver |
| `none` | no copy | no copy (`folder`/`filename` present alongside `none` is a `schema_violation`) |

**3. Naming and delivery stay separate; the filename override is naming
intent.**

Delivery never invents a name: it copies the artifact under its
`display_filename`. A client-provided `delivery.filename` is, conceptually,
*naming* intent that lives in the delivery block for historical reasons: it
overrides the NamingPlan **base** for that output (sanitized by the server's
display profile — D-51 settled) while qualifiers, language suffixes and
numbering still apply — it names the artifact *family*, and is the literal
final filename only for a single-artifact output. Relocating or renaming the
field (`output.naming`, or `base_name` — the more precise word for what it
is) is deliberately **not** done now — it would be contract churn with no
behaviour to gain; both are noted for a future contract major.

**4. The delivered path is recorded and exposed — relative only.**

`DeliveryStore.deliver`'s return value is stored per artifact
(`delivered_path`, relative to the delivery root) and exposed by the API. A
client can finally say "in your library: `talks/2026/My Conference.mkv`".
Absolute server paths never leave the backend: the client knows its own mount.

**5. Collisions are handled deterministically.**

The existing counter suffix (`name.ext`, `name-1.ext`, `name-2.ext`) stays:
deterministic given the library state, and title-based names *will* recur
(the same video downloaded twice). The recorded `delivered_path` reflects the
actual final name.

**6. Clients can see the policy.**

`GET /api/v1/config` exposes whether default delivery is on, so a UI can show
the effective destination before submitting instead of guessing.

## Consequences

- A bare `GenerationRequest` — SDK, CLI, MCP, curl, one-click extension —
  against the packaged deployment ends with `My Conference.mp4` in the mounted
  library, and the API says so. No client-side delivery crafting remains
  necessary.
- The extension's mandatory-filename rule and HomeTube's prefill workaround
  become deletable (they keep working against a policy-off engine).
- `{}` changes meaning from "no copy" to "no intent — policy decides";
  `mode: "none"` is the compatible escape hatch. Contract §3 and the two
  Delivery docstrings are updated in the same change.
- Disk usage doubles for delivered artifacts in the packaged deployment — the
  known, accepted cost of a browsable library (same trade ADR 0010 made for
  the cache); `/api/v1/storage` already reports both families.
