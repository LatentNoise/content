# ADR 0037 — One directory per person, and none when there is one

Status: accepted (2026-09-13) · Revises the filesystem half of ADR 0030 ·
Related: 0009 (storage families), 0018 (delivery library), 0020 (uploads),
0036 (quotas)

## Context

ADR 0030 gave every file an owner by inserting an owner level inside each
storage family: `jobs/<owner>/<job>`, `tmp/<owner>/<job>`. It was the smallest
change that made isolation real, and it left two things wrong.

**The families came first, the person second.** Everything one person owns was
spread across three trees. Deleting an account, measuring a quota or moving
someone meant touching each family in turn, and the delivery library and the
uploads had not even received the level — uploads were still filed by id
alone, with the database as the only witness of whose they were.

**Every self-hosted instance paid for a case it does not have.** A single-user
installation, which is the overwhelming majority, got `jobs/local/<job>`: a
directory level for distinguishing owners on a machine that will only ever
have one. Structure for a situation that never arrives.

The maintainer's objection was exactly that, and it was right.

## Decision

### Two layouts, one code path

```
flat        <data>/jobs/<job>          <data>/tmp/<job>        <data>/uploads/<id>
per_user    <data>/users/<owner>/{jobs,tmp,uploads,resources,output}/…
```

`CONTENT_STORAGE_LAYOUT` is a **policy value**, read by one module
(`content/storage/roots.py`) and by nothing else. Every path in the engine is
asked of an owner's roots; no route, executor or sweeper assembles one by hand,
and none of them knows which layout is in force. This is the same discipline
ADR 0030 imposed for identity and ADR 0018 for delivery: a value, not a branch.

### The default follows the mode, in exactly one place

Nobody signs in → `flat`. People sign in → `per_user`. That derivation happens
where configuration is read, and only there; the storage code never sees the
authentication mode. It is the one line where the two concepts meet, and it
only chooses a default.

### A flat tree refuses to host sign-in

A flat tree holds exactly one owner by construction. Combining it with
`CONTENT_AUTH_MODE=token` is refused at startup, where an operator reads the
message, rather than at the first second account, where nobody does. The
remedy is one variable; the engine files the existing data under
`users/local/` on the next start.

### The library goes with the person — except when it is everybody's

In `per_user`, a private library is `users/<owner>/output`, next to the rest
of what the person owns. A mounted volume can still be placed there: a mount
targets a path, and nothing about that path being inside another volume
prevents it. That was the argument against this layout, and it was wrong.

`delivery_scope` keeps its three answers on top: `shared` gives every account
the one configured library (a family instance), `off` gives none. Under `flat`,
`per_owner` has nobody to separate and collapses to the plain library rather
than inventing a `<root>/local/` level that would break every path a media
server already reads.

### Two things belong to nobody, in either layout

The analysis cache (`cache/analysis`) holds facts about public resources and
is shared on purpose (ADR 0030). The analysis probe scratch (`tmp/analysis`) is
keyed by resource, not by person. Both stay at the root of the data directory,
outside any owner, because filing them under one would claim an ownership that
does not exist.

### The upload path is derived, never trusted from the row

Upload rows record an absolute path as written. A layout migration moves
directories; it does not rewrite rows. So the location of an upload's bytes is
now computed from the owner's roots at read time, and the recorded path is
honoured only while nothing exists at the derived one — the window during a
migration, and nowhere else. This is what makes the migration a plain move.

## The migration

`migrate_layout` runs on every start, before the first request and before the
worker claims anything, and brings whatever it finds to the configured layout:
the pre-ownership tree, the 0.8.0 owner level, or the other layout. It is
idempotent, interruptible and conservative — directories that do not look like
what they claim to be are left where they are and mentioned once.

Leftovers are tolerated by design. An orphan directory is a wasted megabyte and
a log line; a lost one is a support ticket.

## What this deliberately does not do

**`resources/` exists in the layout and is empty.** It is where raw downloads
will live, indexed by resource rather than by job, so that asking for the last
minute of a video does not re-fetch what asking for the first minute already
brought. Reserving the place is cheap; deciding its retention and whether it
counts toward a quota is a separate decision.

**`work/` stays inside the job.** The suggestion to make every intermediate
reusable across jobs is the content-addressed store that git and Nix are, and
it is the right long-term direction. It is also a rewrite of the execution
model, not a directory move: intermediates have no identity in the database
today, and giving them one erases the line between `work` and `artifacts`.
The existing per-step reuse already covers artifacts; this ADR records the
direction and leaves the rewrite for when it is needed.

## Consequences

- A self-hosted install sees `/data/jobs/<job>` again, with no owner level,
  and an existing 0.8.x install is filed back on its next start.
- A hosted install has one directory per person. Deleting an account, measuring
  a quota, backing up or moving one person is one path.
- The uploads finally follow the rule ADR 0030 wrote for everything else.
- `owner_storage_report` and the quotas read one tree per owner, `resources`
  included, so a future raw-download cache is counted from the day it exists.
