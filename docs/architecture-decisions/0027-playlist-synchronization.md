# ADR 0027 — Playlist synchronization: a tracked collection is a binding, not a schedule

Status: proposed (2026-08-23) · Follows ADR 0019, which made a collection pure
orchestration · Amends ADR 0023 §1 and its eligibility rule, and ADR 0017's
non-retroactivity clause · Reopens the question ADR 0024 settled · Constrained
by ADR 0025 (a member is not addressable in the request) and ADR 0009/0010 (the
cross-job cache is not the mechanism) · Full analysis:
docs/playlist-synchronization-review.md

## Context

Content can fan a playlist out today: `each_item` discovers members, and each
one is analyzed, planned and executed through the canonical single-resource
pipeline. What it cannot do is come back tomorrow.

The capability that is missing is the one standalone HomeTube had. A playlist
gains episodes, loses episodes, reorders, and retitles. The local library moves
too — a file renamed by hand, an episode deleted, a folder reorganized. The
user wants to know what changed and to converge on it *without re-downloading
what they already have*, and without Content quietly rearranging files it has
no business touching.

Two things make this harder than it looks in this codebase specifically, and
both were verified rather than assumed.

**Content cannot currently say which remote video an artifact is.** An artifact
row carries `resource_key`, and `content/domain/analysis.py` is explicit about
what that is worth: *"PUBLIC BUT UNSTABLE (D-12) … it changes whenever [the
provider or its version] does … do not persist it as an identifier."* That is
not theoretical — yt-dlp moved 2026.07.04 → 2026.08.19 and every URL-source key
changed with it. For a collection member the column is worse than unstable: it
is empty, because `_plan_each_item` passes `resource_key=""` and the executor
copies it verbatim. In the live database, 26 member artifact rows carry `''`.
The member's URL exists only inside `provenance` JSON and the plan snapshot on
disk. There is no column, no index, and no query that answers *"which artifact
is this playlist entry?"*

**The obvious fallback is thinner than anyone assumed.** HomeTube identified a
file by one container tag, so the natural plan was to do the same. Probing this
engine's own output: a plain Matroska video carries `PURL` and `COMMENT`; an
MP4 carries `comment`; an **audio-only artifact carries no tags at all**,
because `embedding_args` has exactly one call site and it is inside
`_acquire_video`; and a **SponsorBlock-cut video carries only `ENCODER`**,
because `_run_segment_cut` runs the concat demuxer without `-map_metadata` and
writes the result back over the artifact. SponsorBlock removal is the HomeTube
preset. The population most likely to want playlist synchronization is exactly
the population whose files are anonymous on disk.

So the honest starting position is that both tiers of the intended identity
ladder are missing, and the feature is a prerequisite problem before it is a
design problem.

## What decides the shape

**A synchronizer needs three states, and one of them is not observable.**
Previous (A), remote (B), local (C). Without A, *"the user deleted this
episode"* and *"this episode was never synchronized here"* are the same
observation and demand opposite actions — and since Content may not assume a
folder's contents are its business, the safe reading of an unrecorded file is
always "not mine". A stateless sync is therefore not a weaker V1; it is a sync
that cannot answer its own central question. A is not reconstructible from
existing rows either: nothing records that a playlist lives in a folder, and
`delivered_path` demonstrably drifts — one member of one playlist has three
distinct delivered paths and a `-1` clone in this repository's own database.

**Renumbering is not free, and the current ordinal is positional.**
`_normalize_collection` drops unusable entries before enumeration, so a
member's ordinal is its index among *survivors*. Remove one video from a
200-entry playlist and every subsequent ordinal shifts: 199 renames of the
user's files, 199 chances to fail, and a full media-server rescan, for one
remote edit. That is the dominant shape of the target workload, not an edge
case.

**The library is not Content's.** ADR 0023 §1 says so in the strongest terms
available, and the whole feature consists of renaming, moving and archiving
files inside it. That tension is the ADR's real subject.

## Decision

### 1. A durable remote identity is a prerequisite, and it is not `resource_key`

`artifacts` gains **`source_ref`**, indexed, filled for every artifact:

```
"<provider namespace>:<provider resource id>"     e.g. "youtube:kfQnyqoea2A"
""                                                when the provider has no stable id
```

The namespace is the **site**, taken from yt-dlp's `extractor_key` (and
`ie_key` for flat-playlist entries), captured into a new
`provider_namespace` on `NormalizedResource` and `CollectionEntry`. It is not
`detected_provider`, which is the implementation name (`"ytdlp"`) and would
name a tool in a stable public field, collide across extractors that reuse id
shapes, and put the two identity tiers in namespaces that could never agree.

One domain function builds the value and one recovers it from a canonical
watch URL, so the database tier and the metadata tier are the same namespace by
construction. Population rides the propagation chain the planner already has:
processor steps inherit `resource_key` from their dependency, and `source_ref`
travels the same way, so transcripts, summaries and subtitles inherit their
resource's identity for free. Collection members get theirs from the member
step's params, stamped on every produced file where `member_uri` is stamped
today. Historical member rows are backfilled once from
`provenance.attributes.member_uri`.

`source_ref` is documented as **stable and durable** — the deliberate opposite
of D-12. It answers *"has this remote resource already been produced?"*, which
`resource_key` never could across a tool upgrade, and it is worth adding even
if synchronization is never built.

An empty `source_ref` is **untrackable**, not a wildcard: a collection whose
members have no provider id is refused at tracking time rather than
half-tracked and mis-reconciled later.

### 2. A `TrackedCollection` is a binding the user creates, never a side effect

```
TrackedCollection : source URI + request template (immutable per version)
                    + one realized destination per output
                    + removal policy + renumber policy
tracked_members       : one row per (source_ref, occurrence) — state A, member level
tracked_member_files  : one row per delivered file — state A, file level
```

The snapshot's unit is a **file**, not a member, because one output legitimately
produces several artifacts (subtitles per language, keyframes per index). A
member-grained snapshot leaves every non-primary file permanently unmanaged.

Running a playlist job installs nothing. Implicit creation would leave a
standing association between a remote URL and a folder on the operator's disk
as a side effect of a download, which is precisely what §8 is written to
prevent. Destinations are prefix-disjoint across tracked collections, so two
collections can never fight over one file, and the delivery root itself can
never be a destination.

The template is immutable per version — INV-013 makes a job's request
immutable, and the thing that *generates* jobs inherits the reasoning. A stored
`naming_version` sits beside `template_version` so that a change to the naming
engine is not mistaken for a change the user made.

### 3. The ordinal is assigned once, not observed every run

`renumber: never | on_change | on_demand`, default **`never`**. A new member
takes the next ordinal; removals leave gaps; the remote position is evidence
displayed in the plan, not a rename target. Renumbering is an operation the
user asks for by name.

This is the difference between one action and two hundred for the routine case,
it removes the padding-width cliff for the default policy, and it is more
faithful to ADR 0019's *"the ordinal is orchestration data"* than recomputing it
from a listing on every pass.

### 4. Sync plans; the canonical pipeline generates

Reconciliation lives in its own planner. For `ADD` and `REACQUIRE` it submits an
ordinary `GenerationRequest` — one job per member, carrying the **whole**
template, source rewritten to the member URI, `scope` dropped.

Two details are load-bearing and both were found by testing the naive version.
Naming comes from **one** `NamingPlan`, resolved over the whole template with
`scope=each_item` intact, and reaches the derived request through
`delivery.filename` on **every** output, fed from that plan's item bases. A
single-output derivation would rename the member relative to what `each_item`
produced, because the primary-output rule is computed over the outputs present
in the request: in a video+audio template the audio member is
`007 - T - audio.opus` from the collection and `007 - T.opus` submitted alone.
And `execution.reuse_existing` is set to `false` explicitly, because the model
default is `true`, `derive_member_request` drops `execution` entirely, and the
HomeTube compose enables the cache — so a sync ADD would silently run with
cross-job reuse on, and unlike a member step its signature would actually hit.

INV-018 holds: the orchestration decides which members and in what order, and
delegates each to `build_plan` through the public request model. No sync-shaped
planning rule, no second media path.

### 5. Plan and apply are separate; the apply is a job

There is no dry-run flag — the plan is the only way to see what would happen,
because a flag that can be forgotten is HomeTube's dead `dry_run` waiting to
happen. The plan writes one row and touches nothing.

The apply is a **job** (`jobs.kind = 'sync'`), one step per action. That is not
a naming preference. A separate run engine would need its own status
vocabulary, its own transitions outside `content/domain/job.py`, an in-place
mutated table standing in for a journal, its own startup sweep, and it would
have no event stream — reinventing `jobs`, `job_steps` and `job_events` while
bypassing INV-004 and INV-006 and justifying it with *"a sync run is not a
job"*, which is dodging an invariant by renaming the object. As a job it
inherits append-only sequenced events, SSE with resume, cancellation, the
concurrency bound, and `requeue_running` on restart.

Per action: publish the intent, re-check that action's own preconditions
(`size`, `mtime_ns`, `(dev, ino)`, target absence), perform the operation,
**commit that one file's snapshot row**, publish the outcome. Committing per
file is the direct fix for HomeTube's worst behaviour, where the state file was
rewritten unconditionally and a rename that threw was recorded as the name it
was supposed to get.

Staleness is per action, not per plan: an unrelated download landing in the same
folder mid-apply skips one action, it does not abort two hundred.

Recovery is not rollback — reversing a completed rename is itself a risky
mutation. A crashed apply leaves steps `running`; the next plan reports
`recovering_from`, re-observes each affected file, resolves its own
plan-scoped scratch and leaves anything else alone.

### 6. Identity: five tiers, and only one of them confers authority

```
T0  the snapshot row              → attributes AND authorizes
T1  an artifact row's delivered_path → proposes an adoption
T2  content checksum                 → attributes (the only tier that reaches
                                       audio, cut video, .srt, .json, .md)
T3  container tag (PURL, then COMMENT, nothing else) → proposes an adoption
T4  duration within 5%               → evidence, never a gate
    filename                         → never
```

T3 reads `PURL`/`COMMENT` and no other field — never `DESCRIPTION`, which
routinely contains other videos' URLs, and never HomeTube's final
`else: video_id = comment`, which turns arbitrary free text into an identity.
It parses to a `source_ref` or to nothing.

T4 is evidence because a 20.9-second SponsorBlock cut on a four-minute video is
a 9 % drift and is legitimate. HomeTube's advertised 5 % guard was in any case
dead code: nothing ever wrote the duration it compared against.

Disagreement resolves conservatively. Snapshot attributes and the file has no
tag — proceed; absence of a tag is not evidence against a record. Snapshot
attributes and the tag names a *different* member — refuse, report both, mutate
nothing. The recorded file's bytes changed but its identity did not, or is
unreadable — that is a transcoder or a metadata writer, not a replacement: keep
it and re-baseline the record, because blocking here would make the feature
permanently unusable for anyone running Tdarr, unmanic or Plex optimization.
Two candidates for one file — prefer the one at a recorded delivered path, then
the one already at the canonical name, then refuse.

**An unresolved identity blocks one action and never the plan.** There is
nothing to acknowledge and no override flag: a single checkbox for N blocks is
the mechanism by which the one block that mattered eventually gets applied by a
user who has learned that the box is always ticked.

### 7. Convergence is claimed, never overwritten, and never counter-suffixed

Renames go through a dedicated `rename_claimed` primitive rather than a
repurposed `claim_with`. Reusing the delivery primitive looks free and is not:
its no-hard-link branch claims the destination as an **empty file** and then
lets a cross-device `os.replace` propagate, leaving a zero-byte file carrying
the member's canonical name in the user's media library — the exact broken
library entry the primitive exists to prevent, and one this ADR would then
forbid anyone from removing. Its `FileExistsError` branch also makes a
case-only retitle unresolvable, since the occupant is the file being renamed.
`rename_claimed` refuses anything that is not a regular file, short-circuits the
same-inode case, checks `st_dev` before acting rather than discovering it at
`os.replace`, cleans its placeholder on any failure, and fails in the safe
direction: two names for one inode, never zero.

A target held by a file Content has no record for is **skipped**, never resolved
with the delivery counter. Delivery uses the counter because it does not know
what it wants; synchronization does. The same rule applies before an ADD is
submitted, which is what actually stops `-1` accumulation.

Removal is archive-by-default, into `.archive/` inside the destination (built
with an explicit segment, because the folder sanitizer strips a leading dot),
keeping the file's full display name and its record — HomeTube dropped the
ordinal and severed archived files from the members they were. And a member
absent from **one** listing is not removed: it becomes `pending_removal` and
produces no filesystem action until it has been absent from several consecutive
successful listings. A transient region block, a truncated response and a
deletion look identical, and only one of them should move two hundred files.

Sync never removes a directory.

### 8. Scope: recorded files inside a tracked destination, and nothing else

Naming a destination grants Content nothing over the other files in it. A file
Content holds no record for is not an orphan, is not a candidate, and is never
named in an API response — only counted.

Two limits follow and are stated as decisions rather than discovered as bugs.
A file moved **within** the tracked destination is found by checksum and renamed
back. A file moved **out** of it is indistinguishable from a deletion and is
answered the same way — re-deliver from the job store, or re-acquire. **Content
never scans the wider library.**

An unmounted destination presents as an empty directory and passes a write
probe, so presence is proved by a sentinel file written at creation, not by
`os.access`. And a plan in which an implausible fraction of tracked files are
simultaneously missing is refused, for the same reason an empty remote listing
is.

### 9. ADR 0023 §1, replaced

The existing §1 reads:

> **The delivery library is never eligible. Ever.** … No retention rule, no
> sweep, no "orphan" heuristic may remove anything from it. This is an
> invariant a test enforces, not a guideline.

That last sentence is currently untrue: there is no `reclaimed_at` column, no
retention module, and no test among the sixty-five in the suite that asserts the
library's immunity. **This ADR does not weaken an enforcement; it supplies the
first one.** §1 becomes:

> ### 1. The delivery library is never eligible for retention. Ever.
>
> It is the user's media collection, it is mounted from their filesystem, and
> nothing Content does on its own initiative may remove or alter a file in it.
> No retention rule, no age policy, no size cap, no sweep, no "orphan"
> heuristic, no cleanup of any kind may remove anything from it. This holds
> whether the file was delivered by Content or put there by the user, and it
> holds regardless of disk pressure. Reclaiming frees the duplicate under
> `jobs/<id>/artifacts/`; the library copy is the one that survives, and it is
> the reason reclaiming is safe at all.
>
> **The single exception, and its exact bounds.** Playlist synchronization
> (ADR 0027) may rename, move, archive or — only behind an operator setting and
> an explicit per-file confirmation — delete a file in the library, under all
> seven of the following conditions, every one of which is necessary:
>
> 1. **A user act, not a policy.** The mutation happens only inside an apply the
>    user invoked on a plan they were shown. No timer, no background policy, no
>    side effect of any other operation may reach the library.
> 2. **An enabled capability.** Synchronization is off unless the operator turned
>    it on. A caller cannot enable it from a request.
> 3. **A tracked binding.** The file lies inside a destination folder recorded on
>    a tracked collection the user created explicitly. Naming a folder in a
>    one-off request grants nothing, and the delivery root itself can never be a
>    tracked destination.
> 4. **Recorded authority, not inferred attribution.** Content may mutate a file
>    only when it can name the snapshot row that says it delivered that file
>    there, or the moment the user personally adopted that exact file. Evidence
>    read from inside a file — a container tag, an embedded URL — may *propose*
>    an adoption to the user and may corroborate a record. It is never, by
>    itself, permission. A tag inside a file the user could have written is
>    evidence, not authority. A filename is neither.
> 5. **Containment.** Every source and every target path resolves inside that
>    destination folder, checked with the same containment check delivery uses,
>    on the resolved path, immediately before the act. Only regular files are
>    candidates. Sync never reads and never writes outside the folders its
>    tracked collections name — with one exception, named here so it is not a
>    loophole: sync's own scratch files, written under a reserved
>    `.content-sync-*` or `.staging-*` prefix inside a tracked destination, which
>    it may create and remove because it created them.
> 6. **Non-destructive by default.** A member that disappeared remotely is
>    archived inside its destination, keeping its full name and its record, and
>    only after it has been absent from several consecutive successful listings.
>    Deletion requires the operator setting, `removals: "delete"` on the tracked
>    collection, and an explicit per-file confirmation echoed back with the
>    apply. Nothing else in Content may unlink a library file.
> 7. **Recorded.** Every mutation is journalled as a step before it happens and
>    is readable afterwards as job history.
>
> Retention and synchronization are different powers with different owners.
> Retention is Content's policy over Content's own storage and stops at the
> library's edge. Synchronization is the user's authority over their own
> collection, exercised one approved plan at a time, over files Content can name
> a record for. Nothing may blend the two: a retention path that acquires a
> reason to touch the library, or a sync path that acquires a schedule and
> reaches the library without an approved plan, is a violation of this ADR and
> not a feature.

ADR 0023's **eligibility** rule is amended in the same change: an artifact
referenced by a live tracked-collection snapshot row is not reclaimable by
default, because it is the cheap answer to a library file the user deleted.
Reclaiming it anyway is an operator opt-in that says what it costs — the bytes
go, and a future restoration becomes a re-download.

ADR 0017's *"Not retroactive"* is amended too, and bounded: a tracked
collection's members are renamed to converge with **that collection's own**
ordering and titles, never because the naming engine itself was tuned. Each
member records the naming version it converged under, and re-applying a newer
namer is a separate operation the user asks for.

The invariant this leaves behind is **INV-020 — the delivery library is mutated
only by delivery and by an approved sync apply** — verified by outcome tests: a
full-tree byte-identity assertion across every purge entry point, and an
attribution-scope test proving that a rename of three tracked members leaves an
unrelated JPEG, a tagless MKV and a foreign-tagged MKV untouched by inode,
mtime and checksum. A module-level lint over filesystem-mutating calls backs
them up as a tripwire; it constrains where such calls live, not who may make
them, so it is not what the invariant rests on.

### 10. No scheduler — and that is exactly why ADR 0024 must be reopened now

V1 is manual. Nothing fires on its own, and the architecture stays compatible
with a later scheduler calling the same plan/apply pair.

But ADR 0024's trigger 4 is *"an endpoint that executes, schedules, or reaches
outward on a caller's behalf in a way the current limits do not contain"* — and
an unauthenticated primitive that can archive (let alone delete) files a user
already had is uncontained today, scheduler or no scheduler. Every gate in §9
is an input the same caller supplies, so the containment cannot live in the
request body:

- `CONTENT_SYNC_ENABLED=false` by default; the endpoints do not exist when off.
- `CONTENT_SYNC_ALLOW_DELETE=false` by default.
- Adoption — which is what turns "files Content owns" into "files Content may
  move" — is behind the same flag, and the delivery root is never a destination.

ADR 0024 gains the tracked-collection endpoints under trigger 4, and this ADR
records that attaching a scheduler to a tracked collection is the moment the
authentication question is reopened in full.

## Alternatives rejected, in writing

- **A stateless sync over remote + local.** Cannot distinguish "the user deleted
  this" from "this was never synchronized here", which is the one question the
  feature exists to answer, and would have to resolve it by either re-acquiring
  files the user deliberately removed or doing nothing.
- **Reconstructing the previous state from `artifacts.delivered_path`.** It is
  per-run, not unique, unindexed, never re-verified, and observably drifts —
  four rows share one delivered path in the live database.
- **`resource_key`, or `provenance.attributes.member_resource_key`, as the
  member identity.** The same unstable hash, in a column or in a JSON blob. It
  already changed for every URL source when yt-dlp was upgraded, and the domain
  says in writing not to persist it as an identifier.
- **`detected_provider` as the identity namespace.** Names the tool, not the
  site; not unique across extractors that reuse id shapes; and it would put the
  database tier and the metadata tier in namespaces that can never agree.
- **A container tag as sufficient authority to mutate a file.** It is an
  ordinary writable field. Anyone who can drop a file into a watched folder —
  a share, a download client, a second user on the NAS — could make Content
  adopt and then archive it. Tags propose; records authorize.
- **A dedicated sync-run engine with its own status tables.** Re-implements
  jobs, steps and events with two private vocabularies, an in-place-mutated
  "journal", no event stream and no startup sweep, and evades INV-004/INV-006 by
  calling the object something other than a job.
- **Reusing `claim_with` as the rename primitive.** Its degraded branch leaves a
  zero-byte file in the library and its `FileExistsError` branch makes a
  case-only retitle permanently unresolvable — both in exactly the NAS
  environments this feature targets.
- **A plan-wide "applicable" flag with an `acknowledge_blocked` boolean.** One
  ambiguity would hold a whole collection hostage, and one checkbox for N blocks
  is how a real misattribution eventually gets applied.
- **`reuse_existing` / the cross-job cache as the "don't redo it" mechanism.**
  A different question with a different lifetime (ADR 0025 §5), inert with the
  shipped default and enabled in the HomeTube compose — so it would be invisible
  in the default test configuration and active in the deployment that matters.
- **A `sync` field in `GenerationRequest`, or member addressing in it.** ADR
  0019 and ADR 0025 §3 keep members out of the contract; a synchronization-only
  addressing scheme would be the parallel contract this project does not build.
  Sync addresses members internally, through the same request rewrite the
  collection runner already uses.
- **Importing HomeTube's `status.json`.** Its `resolved_title` is pure filename
  trust — HomeTube fabricates a synthetic metadata record from it with no
  verification — and filename is never authoritative here. Container tags plus
  checksums cover every file HomeTube produced with embedding on, which is its
  default.
- **A trash directory sync sweeps.** That is a retention rule over the library,
  which §9 forbids and the carve-out does not cover.

## Consequences

**Gained.** A playlist stays a playlist. New episodes arrive, retitles land as
renames, removals archive rather than vanish, and a file the user deleted comes
back from the job store without touching the network. Content gains a durable
answer to *"which remote resource is this artifact?"* — useful well beyond
synchronization, and the first honest one it has ever had. And the delivery
library gets its first real enforcement: the invariant ADR 0023 asserted becomes
a test in the same change that carves the one exception to it.

**Paid.** Four new tables and two new columns on existing ones. Two identity
fixes and one naming fix must land in the provider and namer *before* the
feature, and they only help files produced afterwards — every SponsorBlock-cut
file already in a library is anonymous to the tag tier forever and is reachable
only through records Content kept. A tracked collection's template
over-describes what any single addition runs, the same cost ADR 0025 accepted,
at a longer lifetime. And Content acquires, for the first time, a code path that
renames and moves files in the user's media library — bounded, gated, journalled
and tested, but real.

**Follows.** ADR 0023 §1 and its eligibility rule are amended; ADR 0017's
non-retroactivity clause is amended and bounded by a stored naming version;
INV-020 is added with its tests; ADR 0019 gains the correction that a member's
durable identity is `source_ref` and that re-submitting a playlist is *not*
deduplicated by reuse under the shipped default; ADR 0024 gains these endpoints
under trigger 4; and `docs/contract.md` §9 records where a tracked collection, a
sync plan and a sync job sit relative to the four core concepts — and
disambiguates this feature's "sync" from the reserved `execution.mode: "sync"`,
which means *synchronous* and stays refused.

## What is needed before implementing

Nothing here is implemented. The decisions this ADR asks the maintainer to make,
in the order they bind:

1. **`source_ref` as the durable identity** — namespaced by the provider *site*,
   filled for every artifact rather than only for members, backfilled from
   `member_uri`, and documented as the deliberate opposite of D-12.
2. **A persistent tracked collection, never created implicitly**, whose snapshot
   is keyed per delivered **file**, not per member.
3. **`renumber: never` as the default** — ordinals assigned at first
   convergence, gaps preserved, renumbering an operation the user asks for. This
   is the most consequential user-visible choice in the design, and a different
   judgement is reasonable.
4. **§9's replacement wording, verbatim**, including clause 4 (attribution is not
   authority) and clause 5's named scratch exception — and the acknowledgement
   that its enforcement is being written for the first time rather than
   preserved.
5. **The apply is a job** (`jobs.kind`), with `job_events` as the journal — or an
   explicit decision to the contrary, with INV-004 and INV-006 satisfied some
   other way.
6. **Synchronization off by default and deletion off by default**, both by
   operator setting, and the acceptance that shipping this reopens ADR 0024
   rather than merely being noted in it.
7. **The scope limit**: a file moved out of its tracked destination is not
   findable, and Content will not scan the library to find it.

The prerequisite work in §1 and §6 — the identity column and the two metadata
fixes — is worth merging on its own merits whichever way the rest is decided.
