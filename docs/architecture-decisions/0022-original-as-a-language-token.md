# ADR 0022 — `original` as a language token

Status: **accepted** (2026-08-15) · implemented 2026-08-21 · Completes ADR 0019
for per-member audio · Constrained by INV-018 (a collection invents no rule of
its own)

## Context

`CONTENT_VO_FIRST` expresses "prefer the source's own voice over a dub". For a
single video the UIs resolve it at request time: analysis reports the original
audio language, the client puts it first in `selection.audio_languages`, and the
request that reaches the engine is concrete — `["ja", "fr"]`.

That resolution cannot happen for a collection. "The original language" is not a
property of a playlist; it is a per-member fact, and members are deliberately
not analyzed at submission (ADR 0019: no eager whole-collection probing). So
HomeTube omits VO for playlists — correct, and a hole: a caller cannot express
"give me each video in its own language" at all, in any client, including the
API.

The gap is now more visible because members *are* planned from their own
analyses. The fact needed to resolve VO exists at exactly the right moment; the
contract simply has no word for it.

## Decision

**`"original"` becomes a reserved value inside
`options.selection.audio_languages`, resolved per resource at plan time.**

```json
{"selection": {"audio_languages": ["original", "fr"]}}
```

reads: *the source's own audio if it has one, otherwise French*. For a
collection with `scope: "each_item"`, each member resolves the token against its
own analysis — one plan step per member, each already built from that member's
facts.

Three properties make this the right shape rather than a special case:

- **It composes with ordering.** `audio_languages` is already an ordered
  preference list intersected with what a source offers. `original` is one more
  entry in that list, so "original, then French, then English" needs no new
  grammar and no new field.
- **It is resolved in the single-resource path.** The token is expanded by the
  same planner code that handles a lone video, so a single video gains the
  ability identically and INV-018 is respected: the collection contributes
  nothing but fan-out.
- **It degrades honestly.** A source whose analysis reports no original audio
  language drops the token and falls through to the next preference, with the
  existing `partial_output` warning when nothing matches — the behaviour the
  list already has for an unavailable language.

`CONTENT_VO_FIRST` keeps its meaning and becomes the client-side default that
*emits* the token, instead of a rule each client resolves for itself.

### Alternatives rejected, in writing

- **A boolean `prefer_original`.** Cannot express position. "Original, then
  French" and "French, then original" are different requests, and a flag beside
  an ordered list leaves their interaction undefined — precisely the kind of
  implicit coupling the contract avoids.
- **Resolving VO client-side for collections too.** Requires the client to
  analyze every member before submitting, which is the probe storm ADR 0019
  exists to prevent, and makes a playlist's cost proportional to its length
  before a single byte is downloaded.
- **A collection-specific option** (`per_item_original: true`). Forbidden by
  INV-018, and it would leave the single-video path unable to say the same
  thing.
- **Overloading the empty list** to mean "whatever the source has". Silent, and
  it collides with the existing meaning of "no preference expressed".

## Consequences

**Gained.** A playlist of talks in five languages can be requested once and come
back each in its own voice. The API, CLI, SDK and MCP gain the ability at the
same moment as the UIs, because it lives in the contract rather than in a
client's resolution step.

**Paid.** A reserved word inside a list of otherwise free-form language codes.
`original` is not an ISO 639 code, so nothing collides today — but the contract
must say it is reserved, and validation must reject it where it is meaningless
(a `subtitles` output's language list, where "original" has no defined meaning
for a translated track).

**Follows.** HomeTube can stop omitting VO for playlists, which is the visible
symptom that prompted this. Its per-video behaviour is unchanged in effect: it
sends a token instead of a resolved code, and the engine reaches the same
answer.

## Implementation notes (2026-08-21)

Built as written. Three details the decision did not have to settle, recorded
here because the next reader will wonder:

- **Where the token is refused.** The ADR named a `subtitles` output's language
  list; the same reasoning covers `processing.embed_subtitles` and a
  `translation`'s `target_language`/`source_language`, so all four refuse it.
  The refusal is a schema-level `ValueError`, which the API reports as
  `schema_violation` — invalid, not unsupported.
- **HomeTube emits the token for a collection only.** For a single video the
  form shows the source's real tracks, and pre-selecting the concrete code the
  user can see is better than showing them a word. The client is not resolving
  a rule there — the user is ticking a track. The token exists for the case
  where no client can resolve it, and that is where it is sent.
- **`content_sdk.ORIGINAL`** carries the word, so no client hardcodes a string
  literal from the contract.
