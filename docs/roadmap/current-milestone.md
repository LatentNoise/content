# Current milestone — M1: HomeTube "single URL" parity + UI

**State:** in progress — S1, S2, S3a, S3b and S5 delivered; several waves have
shipped since (playlists, transcript, summary, translation, chapters, precise
cut, web/document sources, PDF rendering, generated thumbnails and keyframes).
**Value goal:** a user downloads their web
videos (including authenticated ones) with the quality and options they want,
from the UI — Content replaces HomeTube for a single video.

## Expected outcome of the milestone

All of HomeTube's URL features available on a single source: authentication
(cookies), quality/codec profiles with fallback, SponsorBlock, cut,
chapters/description/comments, multi-audio, merged/separate, delivery (folder +
naming), exposed by a UI dedicated to URLs.

## Journeys concerned

- "paste a URL → analyze → configure → launch → follow → collect".
- "download a video that requires authentication".

## Vertical slices (in order)

| # | Demonstrable capability | State | Contract fields made real |
| --- | --- | --- | --- |
| S1 | Download an **authenticated** video (cookies) + the choice in the UI | **done** (2026-07-25) | `source.auth.credential_id` |
| S3a | SponsorBlock (remove/mark) — typed video/audio options + a UI preset | **done** (2026-07-26) | `sponsorblock` on video/audio |
| S2 | Ordered quality/codec profiles + a yt-dlp multi-client fallback | **done** (2026-07-26) | (internal — no contract field) |
| S3b | cut (trim) | **done** (2026-07-31) — `video.clip`, keyframes and frame-accurate `precise` modes | `options.cut` |
| S4 | chapters/description/comments, merged/separate, multi-audio, `delivery` | **done, minus two sidecars** (2026-08-01) — see below | `output.delivery`, options |
| S5 | **A Streamlit client** (URL → versatile outputs → job → tracking → download) + **multi-container Docker** | **done** (2026-07-26) | — (a client) |

**Frontend client (S5, 2026-07-26).** A new `frontend/` component: a Streamlit
app that is a **pure client** of the API (no business logic), taking up the
HomeTube use case (a URL as input) with Content's versatile outputs. Deployed as
a **dedicated container** (docker-compose: `content` + `frontend`). Cookies,
SponsorBlock and quality profiles are exposed. Validated end to end in a
container on a real authenticated YouTube video ("Me at the zoo":
video+subtitles+metadata). Associated robustness fixes: a writable copy of the
cookies (yt-dlp rewrites the jar); auto-updating yt-dlp at build time (D-20).

**Sequencing notes.** SponsorBlock (S3a) and profiles (S2) delivered on
2026-07-26; cut (S3b) was deferred by PO decision as little-used, then picked up
and delivered on 2026-07-31 with both a fast keyframe mode and a frame-accurate
`precise` mode. S2
(ordered codec profiles av1→vp9→h264 + default/ios/web player-client rotation,
adapted from `hometube/engine/profiles.py`) is **internal to the provider** — no
contract field, HomeTube's `format_id` selector is not re-exposed (ADR 0005).

Every slice follows the same protocol: an observable goal, scope + non-goals,
acceptance criteria, a test strategy, a validation procedure, stop rules, a
report.

## Requirements covered (reference)

- Make `source.auth` **honest** (INV-100): `credential_id` either has an effect
  or returns `credential_not_available`.
- Respect INV-009 (no secret in the contract) and INV-002 (the contract does not
  name the tool).
- Start decomposing the planner (decision Q3: as we go).

## Dependencies

- M0 done (harness + docs) — required for the Definition of Done.

## Milestone risks

- **R2** — planner growth: mitigated in S1 by a pilot extraction (a small
  auth-resolution module), without a dedicated refactoring block.
- **Security** — handling cookies: the secret stays **out of the request**, on
  the server side (a configured file), never logged (INV-009).

## Milestone completion criteria

- The 5 slices demonstrable end to end (a real test).
- No newly exposed field is inert.
- The UI allows the full URL → artifacts journey.

## Decisions already taken (locked)

- **Cookies/auth**: cookie file(s) configured on the server side, referenced by
  `source.auth.credential_id`; an unknown `credential_id` → a normalized error.
- **UI**: we **extend the existing single-page** UI (no new rich UI at this
  stage).
- **Planner**: decomposition as we go.

## Decisions still open (to be settled in the slices concerned)

- **S2** — Should HomeTube's yt-dlp multi-client fallback be reintroduced, or is
  the current selector enough on real YouTube? *(to be assessed on real data)*
- **S3** — SponsorBlock/cut: options carried by the `video`/`audio` output, or a
  dedicated processing block? *(a contract-shape decision, to be made in S3)*
- ~~**S4** — `delivery`: how far does the naming template go (path safety)?~~
  **Settled (2026-08-01):** no template language. `delivery` is a relative
  `folder` plus a `filename`; traversal (`.`, `..`, absolute paths) is rejected
  by the contract. Anything richer is a naming DSL, and nothing has asked for
  one.

### S4, answered (2026-08-01)

The table said "considered" long after most of it shipped. Checked against the
code rather than against memory, during the live-UI verification (prompt 15):

| S4 item | State |
| --- | --- |
| chapters | **shipped** — the `chapters` output, `chapters.derive` when a source declares none, and `embed_chapters` on video |
| multi-audio | **shipped** — `selection.audio_languages`, one `+ba[language^=…]` per language, merged in a single file |
| merged/separate | **shipped, differently** — HomeTube needed a helper to fetch tracks and merge them by hand; here merging is what the format profiles already do, and "separate" is simply two outputs (`video` + `audio`) rather than a flag |
| `delivery` | **shipped** — `folder` + `filename`, path-safe (see above) |
| description | **not shipped** — carried inside the analysis (`resource.description`) and therefore inside `metadata`, but there is no standalone sidecar artifact |
| comments | **not shipped** — nothing reads them |

**Decision: V1 ships without the description and comments sidecars.** HomeTube
writes them via yt-dlp's `--write-description` / `--write-comments`, both
**off by default in HomeTube's own UI**. Comments in particular are slow to
fetch, frequently rate-limited, and are a different kind of resource from the
media — closer to a new source type than to an output of this one. The
description is already reachable through `metadata`.

This is reversible and small: both are `metadata`-adjacent outputs, not an
architectural gap. If a user asks, they arrive as their own output types rather
than as flags on the video.

**So the parity claim is: the HomeTube *flow* is fully carried over, and the
outputs are a superset except for those two sidecars.** That is what the README
says, and it is the only parity claim this project makes.
