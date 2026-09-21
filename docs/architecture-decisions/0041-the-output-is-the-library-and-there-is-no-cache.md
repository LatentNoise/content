# ADR 0041 — The output is the library, and there is no cache

Status: proposed (2026-09-21) · Reverses the copy-on-delivery of ADR 0018 ·
Retires the artifact half of ADR 0010 · Amends ADR 0023 · Releases the
`resources/` root ADR 0037 reserved

> The substance was decided by the maintainer on 2026-09-16, after three
> rounds, and is recorded verbatim in the brief
> `37-the-cache-that-already-exists`. This ADR writes it down and states what
> it costs; accepting it is what unblocks the code, because nothing below can
> be built halfway.

## Context

Brief `37` asks one question — *how does Content know it already has a file?*
— and answers it by removing a concept rather than adding one. What follows is
what the engine does today, verified in the code on 2026-09-21.

**A delivered file exists twice.** A step produces into `jobs/<id>/artifacts/`
(`JobStorage.promote_artifact`, which *moves* the file out of the job's
scratch), and `DeliveryStore.deliver` then **copies** it into the output tree
(`storage/layout.py`). The first copy stays. ADR 0018 said so in as many
words — *"disk usage doubles for delivered artifacts […] the known, accepted
cost of a browsable library"* — and on a self-hosted install the two copies
usually sit on two different disks, `./data` and the mounted media folder,
which is why the doubling has gone unnoticed.

**Downloads never read the output.** `GET /api/v1/artifacts/{id}/content`
serves from `JobStorage.artifacts`. The output is a destination the engine
writes to and never reads back.

**"Already have it" is answered twice, both times too late.** `DeliveryStore.
deliver` compares size then sha256 against the name it is about to take, so a
re-submitted download does not litter the library with `…-1` clones of the same
bytes — but the bytes were fetched to be compared. Before that,
`_find_reusable` (`execution/executor.py`) looks up
`find_reusable_artifact_group(owner_id, step.signature, job_id)`: the complete
product set of the most recent **other** job of the **same owner** whose step
carried the same content-addressed signature, each file checksum-verified on
disk before it is reused. That is the "cache" of ADR 0010. It is `false` in the
code default and **`true` in every shipped deployment** — `deploy/charts/
content/values.yaml`, `deploy/docker-compose.yml` and the root
`docker-compose.yml` all set `CONTENT_CACHE_ENABLED=true` — so it is running in
production, including on the hosted engine.

Three things follow from where that index points, and they decide this ADR.

1. **The reuse index reads the job's artifacts directory**, not the output:
   `source_storage.artifacts / row["filename"]`. A file that moves into the
   library is, from the reuse path's point of view, a file that vanished.
2. **Retention already contradicts it.** `expire_job_content` removes
   `artifacts/` and `sources/` after the window and deliberately leaves the
   library alone. So the engine's own answer to "do I already have it?" expires
   while the file the person actually has does not. The cache is an index of
   the wrong tree.
3. **The registry mostly exists.** An artifact row carries `step_signature`,
   `resource_key` and `delivered_path` (`persistence/store.py`). What it lacks
   is the folder the person asked for and a durable identity of the source that
   survives a yt-dlp upgrade — the playlist study of 2026-08-23 shows
   `resource_key` does not.

Two storage families reserved for this problem were never written: the
`resources/` root (declared in `storage/roots.py`, counted by
`/api/v1/storage`, never written) and the job's `sources/` directory (created
by `JobStorage.ensure`, sized and removed by retention, written by nothing).

## Decision

**1. The output is the person's library, and the only home of a delivered
file.** Self-hosted, it is the mounted media folder a human organises into Tech
and Films. Hosted, it is that person's own space. There is no other copy, and
Content does not keep a hidden one.

**2. There is no cache.** Whether someone already has a file is answered by
comparing against the output, in both modes, through a registry of where
Content put things. `cache/` keeps exactly one tenant: the analysis facts —
a few kilobytes of JSON per pasted link, 72 hours — which is a memory of a
*fact about a public resource*, not of a file. In-job sharing of one step by
two outputs stays too; that is planning, not caching.

**3. Delivery moves; it does not copy.** A finished file moves from the job's
scratch into the output under its claimed name, and the row is registered only
once it is there — write-then-register, INV-005, unchanged. The name is still
*claimed with its content* rather than found free, so concurrent writers and a
watching media server behave as they do today.

**4. An unavailable destination makes the job wait, and writes nothing.** If
the output is not there when the job ends — an unmounted NAS, whose mount point
is an empty directory that a naive `mkdir -p` would happily fill — the file
stays in the job's scratch, the job says *destination unavailable*, and nothing
is written under the mount point. This is the one case where a file lives
outside the library, and it is a diagnosed failure, not a silent fallback.

**5. Content is served from where the file is.** The content route reads the
registered path: the output for a delivered file, the job's area for one
deliberately not delivered. A file the person moved or deleted answers *no
longer at its recorded location* — `410`, naming the folder it was last in —
which is a different fact from "expired" and from "never existed", and deserves
different words.

**6. The registry records, per delivered file:** the source's durable identity,
the output folder the request asked for, the exact path, the output type and
its options; per collection, the folder it was delivered to. Existing rows are
backfilled from `delivered_path` and `provenance.attributes.member_uri`.

**7. "Already in your library" is checked before the acquisition is planned.**
Look up the registry for this owner, this source and compatible options, then
verify the recorded path is still a file of the recorded size. Present →
nothing is downloaded, the job points at that file, and the surfaces say so.
Gone → it is downloaded again, silently correct. A cut, an audio extract or a
transcript of a video the person already has takes **that file** as its input
rather than fetching it again.

**8. The media-minute quota counts what the plan will actually fetch**, not
what the request mentions. Answering from the library costs nothing, and
charging for it would make the feature a tax.

**9. Retention follows the mode, and ADR 0023 is amended accordingly.** Hosted:
the output *is* the generated files and the sweep reaches it after the window;
ADR 0023's rule that a library is never swept protects a human-curated media
folder, which a hosted space is not. Self-hosted: the output is never swept.
Job retention keeps removing `work/`, `tmp/` and the job's own area; what it
must never remove is a file it moved into the library.

**10. The contract is deprecated honestly, not silently.**
`execution.reuse_existing` is accepted with a warning for one release, then
removed; the artifact half of `CONTENT_CACHE_ENABLED` goes with it. The unused
`resources/` root and the job's `sources/` directory are dropped rather than
left as furniture for a feature that will not come.

## Consequences

**One copy means one copy, including when the person deletes it.** If they
remove or rename a file in their output, Content no longer has it: nothing
hidden survives, and asking again downloads it again. That is the price, and it
is the right one — the alternative is an invisible second library that the
person cannot see, cannot organise and cannot reclaim, which is exactly what
ADR 0018 accepted and what this reverses.

**Decisions 3 and 10 are one change, not two.** The reuse index resolves
`source_storage.artifacts / row["filename"]`; the moment delivery moves instead
of copying, that path is gone and every reuse falls back to a normal run —
silently, because `_find_reusable` returns `None` on a missing file by design.
Shipping the move without removing the cache would leave a dead code path
pretending to be a feature. They land together.

**The comment in `executor.py` is already wrong and should go with it.** It
says `reuse_existing=true` *"is accepted but has no effect in V1"*, three lines
above the code that gives it effect whenever `cache_enabled` is set — which
every shipped deployment sets.

**Reuse gets better, not worse.** Today it hits only when the *whole recipe*
matches: the signature covers every option, so re-asking the same video in
another quality re-downloads. A registry keyed on the source's identity answers
"you already have this video" across different requests, which is the question
people actually ask, and it survives job retention because it points at the
library rather than at an expiring job.

**Per-owner, still.** `find_reusable_artifact_group` never crosses an owner,
and the registry lookup must not either: someone else's file is someone else's
file, however identical the recipe. The library check is a *filing* answer and
never an authorization one (ADR 0030, ADR 0036).

**`/api/v1/storage` gets smaller and more honest** — one `output` family
instead of an artifacts total that double-counts every delivered file, minus
two families that were always zero.

## What this ADR does not settle

**The durable source identity does not exist yet.** Decision 6 requires one —
site plus video id, surviving a yt-dlp upgrade — and ADR 0027 left exactly that
question open for playlist members (`docs/playlist-synchronization-review.md`).
`resource_key` is not it. Brief `40-update-a-playlist-fetch-only-what-is-missing`
waits on the same answer, so it is one decision serving two briefs, and it is
the next thing to settle.

**"Compatible options" is undefined here.** Decision 7 says a lookup matches on
"compatible" options, which is deliberately weaker than the signature equality
used today: a 1080p file probably answers a request for "best available", and
certainly does not answer a request for 4K. Where that line falls is a product
judgement, not a storage one, and it belongs in the brief that implements the
lookup.

## Alternatives considered

- **Keep the copy and index it better.** The honest version of the status quo:
  leave delivery copying and make the reuse index point at the library. It
  fixes the "already have it" question and leaves the doubling, which the
  maintainer named as the thing to end. Rejected on the stated ground that the
  output *is* the library — a second copy nobody can see is not a cache, it is
  clutter with a technical excuse.
- **A content-addressed blob store (`cache/blobs/sha256`) with the library as
  hardlinks or symlinks.** One copy on disk, reuse intact, deletion in the
  library harmless. Rejected: it only holds where hardlinks hold — one
  filesystem, not a bind-mounted NAS, not object storage, not the hosted
  space — so it would be a fast path that silently degrades to a copy on the
  deployments that matter most, and it puts the person's file back under a name
  they did not choose. ADR 0009 already parked this store; it stays parked.
- **Reference-counting delivered files so retention can reclaim them.** Solves
  nothing here — the library is the person's, and retention sweeping a
  self-hosted media folder is the outcome ADR 0023 exists to prevent — and adds
  a counter that must stay true across a human moving files with a file
  manager. It cannot.
- **Keeping `execution.reuse_existing` as a no-op for compatibility.** Rejected
  by the contract rule the repository already applies (ADR 0026, `reserved.py`):
  a field accepted with no effect misleads the client. One release of warning,
  then it goes.
