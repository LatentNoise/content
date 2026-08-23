# Playlist synchronization — architecture review

Answers `work/coming/02-playlist-synchronization.md`, deliverables A-M. The
decision it recommends is **ADR 0027**; what follows is the reasoning and the
evidence behind it, kept because the ADR states conclusions and a reader who
disagrees with one will want the measurement it rests on.

Every load-bearing claim was verified against the code and against a live
`data/content.db` (173 artifact rows) on 2026-08-23. No feature code was
written.

**Scope.** Deliverables A–M of `work/coming/02-playlist-synchronization.md`. Every load-bearing claim below was re-verified this session against the code, the live `data/content.db` (173 artifact rows) and the artifacts under `data/jobs/*/artifacts/`. No feature code was written.

**Verdict.** The capability is worth building and is buildable, but not in the shape either the spec or the first draft describes. Four things must be settled before any sync code exists: a *durable* member identity that today does not exist; a set of pre-existing primitive defects that sync converts from cosmetic to library-blocking; a decision that **attribution is not authority**; and a decision that the apply is a **job**, not a private engine. With those settled the rest is ordinary work.

**What changed relative to the draft under review** — the corrections that survived adversarial reading:

| Draft position | Status after verification |
|---|---|
| `source_ref = "<provider>:<id>"` from `detected_provider` | **Wrong.** `detected_provider` is `"ytdlp"` (`providers/ytdlp.py:442`, `:600`, `:673`) — the tool, not the site. The two identity tiers could never agree. Fixed in §B.1. |
| "Content already preserves ordinal gaps like HomeTube" (A.1 #6) | **Wrong.** `_normalize_collection` drops falsy entries *before* enumeration (`providers/ytdlp.py:582-583`), so `position` indexes survivors. One removal renumbers the tail. Fixed in §B.4. |
| `claim_with` reused as the rename primitive | **Unsafe.** Its degraded branch creates a 0-byte file and then propagates. Replaced by `rename_claimed` in §F.1. |
| One `tracked_members` row per (collection, output, member) | **Insufficient.** One output legitimately yields many artifacts (`executor.py:544`, subtitles per language). Split in §B.5. |
| `SyncPlan.applicable = not any(blocked)` | **Wrong shape.** One ambiguity kills the collection. Per-action blocking in §D. |
| `observation_hash` | **Not computable as specified.** Replaced by per-action preconditions in §D.3. |
| "No backfill is needed" | **Wrong.** `provenance.attributes.member_uri` is present on every member row and is exactly the backfill input. §B.2. |
| ADR 0023 §1 is enforced by a test | **Confirmed false.** No `reclaimed_at` anywhere, no retention module, no immunity test in 65 test files, ADR status `proposed`. The carve-out *creates* the first enforcement. |
| "Processor steps carry no resource at all" (critique 3, S4) | **Wrong for the single-resource path** — the planner propagates `resource_key` down processor chains via `builder.step_resource_key(dependency_id)` (21 call sites in `planning/planner.py`), and live transcript rows carry `ytdlp:url:…`. The gap is collection members only, where the outer step's key is `""`. This makes the fix *cheaper* than that critique assumed. |
| "45 rows have `resource_key=''` — every collection member" | **Overstated.** 45 rows, of which **26** are collection members (video 14, audio 6, subtitles 6); the other 19 are pre-migration and processor rows. Corrected throughout. |
| Invariant 11's reorder example collides | **Overstated but moot.** Content's base is ordinal *and* title, so a pure reorder is collision-free unless two members share a title. Moot because §B.4 stops renumbering on reorder at all. |

---

## A. What HomeTube solved that the prompt does not capture

The prompt's Finding 2 has the skeleton. What it misses are the *asymmetries* — the places where the symmetric model gives the wrong answer — and the bugs whose Content analogues are one careless line away.

### A.1 Behaviours to restore

1. **The scan root is not the destination root.** `sync_playlist` reconstructs the folder to *scan* from previous state (`custom_title or title` + stored `playlist_location`, `playlist_sync.py:411-435`) and plans into the *new* one. Without that asymmetry, moving a playlist folder reads as "everything missing, re-download all". Content has the identical exposure: `OutputDelivery.folder` is resolved per run from the request (`planner.py:2064-2066`), so editing a template silently repoints the destination. **The realized destination must be stored, and the reconciler must observe the old one while planning the new one.**

2. **The filesystem outranks the record.** `playlist_sync.py:545` — *"Check if file actually exists (ALWAYS check, regardless of status)"*. A member marked `failed` whose file is present is renamed, not re-downloaded.

3. **`skipped` counts as present** (`:530`). An adopted pre-existing file is as good as a downloaded one. Content needs the same first-class `adopted` state.

4. **The extension is inherited from the existing file** (`extension=old_path.suffix`, `:582`). A rename never changes container. A Content RENAME must carry the *observed* suffix, never the planner's guess.

5. **A third state between "have it" and "don't".** `playlist_utils.py:163-166`: *"A video in tmp is NOT 'already downloaded'… It's 'ready to move'."* Content's version of that state is strictly better and the prompt never mentions it: **the bytes are in `jobs/<id>/artifacts/`, durable and checksummed.** A hand-deleted library file can be *re-delivered* rather than re-downloaded. That is the single largest behavioural win available and it earns its own verb.

6. **The ordinal is the collection's own index, gaps included** — HomeTube enumerates over all entries including `None` (`playlist_utils.py:113`). Content **does not** match this (see §B.4); the draft claimed it did.

7. **Padding width is a global rename trigger.** HomeTube's `idx()` renumbers everything at 99→100; Content's `width = max(3, len(str(usable)))` (`naming/engine.py:304`) pushes the cliff to 1000 but does not remove it.

8. **Non-download changes gate downloads** (`main.py:2869-2905`). The intent is right — do not add files to a folder mid-reorganization — but it must be a warning with an override, not a hard disable (see A.2).

### A.2 Bugs not to port

| HomeTube bug | Where | Content's answer |
|---|---|---|
| `dry_run` declared, never read; `sync_playlist` runs on **every Streamlit rerun**, one `ffprobe` per file per rerun | `:376`, `main.py:2073` | The plan is an addressable resource computed by an explicit call; there is no flag that can be forgotten |
| `apply_sync_plan` **mutates** the plan it was handed (delete→archive, `:863-882`), and that object lives in session state | `:863-882` | The plan is immutable; `removals` is frozen into it at plan time |
| `status.json` rewritten **unconditionally**, so a rename that threw is recorded as `completed` under the name it was supposed to get | `:1123-1131`, `:1191` | Per-file commit, on that file's convergence, never wholesale |
| Rename collision displaces the *occupant* via `with_suffix(".backup.mkv")` — which replaces the extension, leaves `03 - Foo.backup.mkv` forever, and gets re-indexed under the same id | `:948-952` | An occupied target is a per-action block, never a displacement and never a counter |
| Swaps silently drop one side; iteration order is a **set** | `:528`, `:946` | Deterministic ordering, explicit cycle breaking, a journal |
| `move_from_tmp` — plain `shutil.copy2`, no collision check | `:1060-1062` | Every library write goes through a claim |
| Archives written into the **new** destination, name **drops the ordinal**, severing the link to the member | `:501-505` | Archive keeps the full display name; the record survives as a retired member |
| A removed member with no file gets no action but loses its record | `:498` vs `:1096-1098` | `FORGET` is an explicit, visible action |
| First-ever apply skips every "mark completed" loop; everything stays `pending` | `:1077-1088` | Adoption is the first-apply case and is not special-cased |
| Old-directory cleanup rmdirs `videos_to_relocate[0].old_path.parent` and trips on `.DS_Store` | `:1013-1033` | **Sync never removes a directory.** Ever. |
| Renaming the playlist folder without changing the subfolder: `location_changed` false, no `ensure_dir`, `FileNotFoundError` swallowed | `:585`, `:956-963` | Destination is one field; any change to it is a RELOCATE |
| Case-sensitive lowercase-only globs (`*.mkv`) | `:222`, `:786` | Observation is by recorded path first; any scan is case-insensitive on extension |
| "Sync must be less than 2 hours old" exists in the UI copy (`en.py:394`) and nowhere in the code | `:1199-1226` | Staleness is enforced by the engine per action, with a stable code |
| The download button is hard-disabled while `has_non_download_changes`; combined with a permanently failing action, downloads become unreachable | `main.py:2869-2905` | Warning with override |

### A.3 What HomeTube's identity mechanism actually proves

`get_video_metadata_from_file` (`:145-208`) reads **one tag**, `comment`/`COMMENT`, parsed three ways with a final `else: video_id = comment` ("might be a different platform"). The advertised 5 % duration guard (`:798-803`) is **dead** — nothing writes a `duration` key into `status.json`, so the falsy guard at `:799` skips it on every call. HomeTube's shipped identity mechanism is therefore *a single, unverified, user-writable container tag*. The prompt describes the design, not the code. Content must not inherit the tolerance as a requirement, and must not inherit the tag as an *authority* (§E, §G).

---

## B. Domain model

### B.1 Prerequisite: a durable remote identity, correctly namespaced

The identity exists in the system and never reaches the artifact row. `CollectionEntry.id` is `str(item.get("id") or "")` (`ytdlp.py:586`) — for YouTube the 11-character video id — and it is in `_plan_each_item`'s hand at `planner.py:463` and discarded. `NormalizedResource.provider_id` carries the same for a single resource.

**But the namespace the draft proposed does not exist.** `detected_provider = self.name` at every provider (`ytdlp.py:600`, `:673`, `webpage.py:308`, `documents.py:283`, `ffmpeg.py:343`) and `ytdlp.py:442` is `name = "ytdlp"`. The only value producible today is `ytdlp:<id>` — which names a tool (against INV-002's spirit), is not globally unique (yt-dlp ids are extractor-scoped: a Vimeo `123456` and a Bilibili `123456` collide), and can never agree with a metadata tier that parses a `youtube.com/watch?v=` URL.

**Decision.**

1. Capture yt-dlp's **`extractor_key`** (per resource) and **`ie_key`** (per flat-playlist entry — it is in the listing and `_normalize_collection` discards it) into a new field on both models:

```python
class NormalizedResource(BaseModel):
    provider_namespace: str = ""   # the site: "Youtube", "Vimeo" — never the tool

class CollectionEntry(BaseModel):
    provider_namespace: str = ""
```

2. **One** domain function, used by every tier, so the namespaces agree by construction:

```python
def source_ref(namespace: str, provider_id: str) -> str:
    """"youtube:kfQnyqoea2A". Empty when either half is missing — an empty ref
    is untrackable, never a wildcard."""

def source_ref_from_url(url: str) -> str:
    """The same value, recovered from a canonical watch URL. Accepts exactly
    youtube.com/watch?v=<11>, youtu.be/<11>, and the site-specific canonical
    forms. Anything else -> "". Never free text."""
```

3. `artifacts.source_ref`, indexed, filled for **every** artifact:

```sql
-- _SCHEMA (artifacts)
source_ref TEXT NOT NULL DEFAULT '',
-- _MIGRATIONS entry 6
ALTER TABLE artifacts ADD COLUMN source_ref TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_artifacts_source_ref ON artifacts(source_ref, created_at);
```

Population is cheaper than the draft or the critiques assumed, because the propagation chain already exists: the planner threads `resource_key` down processor chains with `builder.step_resource_key(dependency_id)` (21 sites). Add `source_ref` alongside it and every sidecar — transcript, summary, subtitles, PDF — inherits it for free. For a collection member, `_plan_each_item` puts `member_ref` in the step params (it holds `entry.id` already) and `CollectionMemberRunner` stamps it on every produced item exactly where it stamps `member_uri` today (`collections.py:163-171`), so member sidecars are covered too.

`source_ref` is documented as **stable and durable — the deliberate opposite of `resource_key`'s D-12 disclaimer.** It is worth shipping even if sync is cancelled: it is the first honest answer to *"has this remote resource already been produced?"*, a question ADR 0019's cost model and ADR 0025's reuse discussion both quietly assume is answerable.

The member **URL** stays where it is, in `provenance.attributes.member_uri`. A URL is a locator, not an identity.

### B.2 The backfill, which the draft wrongly dismissed

Every existing member row carries `provenance.attributes.member_uri`. `source_ref_from_url` turns it into a ref. One `UPDATE … SET source_ref = …` over `WHERE source_ref='' AND json_extract(provenance,'$.attributes.member_uri') IS NOT NULL`, run once in the migration, makes the maintainer's own 26 member rows identifiable. Single-resource rows are recoverable the same way from the cached analysis payload where it is still fresh, and stay `''` otherwise — which is correct, because §E's ladder does not need them.

Without this, the reference library is unadoptable: see §J.

### B.3 Persistent, not stateless — invariant 3 is unsatisfiable otherwise

The proof is short. A stateless reconciler sees only B (remote) and C (local):

```
previous A B C · remote A B C · local A B
  → is C missing because the user deleted it, or because it was never synchronized?
```

Those demand opposite actions — restore versus *do nothing, this folder is not mine* — and invariant 8 makes the second answer mandatory. So a stateless sync either re-downloads everything the remote has and the folder lacks (violating invariant 8, and re-acquiring files the user deliberately removed) or does nothing.

State A is not reconstructible from existing rows, and the live database says so:

- The binding "this playlist lives in this folder" exists nowhere. `OutputDelivery.folder` is per-run plan state in `plan.json`, not a row.
- `delivered_path` drifts. The same member (`kfQnyqoea2A`) appears in `data/content.db` under `Playlist-EgalitarianMonkey/001 - Trapped by plates in The Sims.mkv`, `…-1.mkv`, and bare `Trapped by plates in The Sims.mkv` from a separate single-video job into the same folder.
- `delivered_path` is not unique: `Trapped by plates in The Sims - en.srt` has 4 rows; nine other paths have 2 each. No index, no reverse lookup.

**Decision: a persistent `TrackedCollection`, created explicitly, never implicitly.** Running a playlist job installs nothing. Implicit creation would leave a standing association between a remote URL and a folder on the operator's disk as a side effect of a download, which is ADR 0024's trigger 4 arriving by accident.

The template is immutable per version: INV-013 makes a job's request immutable, and the same reasoning binds the thing that *generates* jobs. Editing writes a new `template_version`; a **`naming_version`** is stored beside it (§B.6).

### B.4 The ordinal is assigned, not observed — the design decision the draft missed

`_normalize_collection` drops falsy entries before enumeration (`ytdlp.py:582-583`), so `position` in `_plan_each_item` (`planner.py:458`) and in `resolve_naming_plan` (`engine.py:305-313`) is the index into *survivors*. The comment about gaps only covers entries that survive with an empty `url`. Consequence: **one member removed from a 200-entry playlist shifts every subsequent ordinal, i.e. ~199 RENAME actions** — 199 mutations of the user's files, 199 chances to hit a failure, and a full media-server rescan, for one remote edit. That is the dominant shape of the target workload (a series playlist that grows), not an edge case.

**Decision: `tracked_members.ordinal` is assigned at first convergence and never recomputed by default.**

```
renumber: never | on_change | on_demand      (default: never)
```

Under `never`, a new member takes `max(ordinal)+1`, removals leave gaps, and the remote position is *evidence displayed in the plan*, not a rename target. Renumbering becomes an explicit operation the user asks for. This also removes the padding cliff for the default policy — the width is fixed at first tracking and stored — and turns `ORDINAL_CHANGED` into something the user opted into. It is more faithful to ADR 0019's *"the ordinal is orchestration data"* than recomputing it from a listing every run.

### B.5 Schema

Four small tables. Nothing in them mirrors yt-dlp; the analysis cache already holds the listing.

```sql
CREATE TABLE IF NOT EXISTS tracked_collections (
    id                TEXT PRIMARY KEY,          -- "trk_<uuid4hex>"
    source_uri        TEXT NOT NULL,
    source_ref        TEXT NOT NULL DEFAULT '',  -- the collection's own ref
    label             TEXT NOT NULL DEFAULT '',
    request_template  TEXT NOT NULL,             -- normalized GenerationRequest, scope=each_item
    template_version  INTEGER NOT NULL DEFAULT 1,
    naming_version    INTEGER NOT NULL DEFAULT 1,
    ordinal_width     INTEGER NOT NULL DEFAULT 3,
    renumber          TEXT NOT NULL DEFAULT 'never',    -- never | on_change | on_demand
    removals          TEXT NOT NULL DEFAULT 'archive',  -- archive | keep | delete
    removal_grace     INTEGER NOT NULL DEFAULT 2,       -- consecutive absences before ARCHIVE
    status            TEXT NOT NULL DEFAULT 'active',   -- active | paused | archived
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    last_sync_at TEXT NOT NULL DEFAULT '', last_plan_id TEXT NOT NULL DEFAULT ''
);

-- One realized destination per output. Separate because prefix-disjointness
-- and the mount sentinel are properties of a *folder*, not of a collection.
CREATE TABLE IF NOT EXISTS tracked_destinations (
    tracked_id TEXT NOT NULL,
    output_id  TEXT NOT NULL,
    folder     TEXT NOT NULL,          -- already through safe_relative_folder
    sentinel   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (tracked_id, output_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tracked_folder ON tracked_destinations(folder);

-- State A, member level.
CREATE TABLE IF NOT EXISTS tracked_members (
    tracked_id  TEXT NOT NULL,
    source_ref  TEXT NOT NULL,
    occurrence  INTEGER NOT NULL DEFAULT 1,
    ordinal     INTEGER NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    member_uri  TEXT NOT NULL DEFAULT '',
    duration_seconds REAL,
    state       TEXT NOT NULL,          -- synchronized | adopted | pending_removal
                                        -- | retired | ignored
    missing_streak   INTEGER NOT NULL DEFAULT 0,
    template_version INTEGER NOT NULL DEFAULT 1,
    naming_version   INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL, synced_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (tracked_id, source_ref, occurrence)
);

-- State A, file level. THE snapshot: one row per delivered artifact.
CREATE TABLE IF NOT EXISTS tracked_member_files (
    tracked_id  TEXT NOT NULL,
    source_ref  TEXT NOT NULL,
    occurrence  INTEGER NOT NULL DEFAULT 1,
    output_id   TEXT NOT NULL,
    variant     TEXT NOT NULL DEFAULT '',   -- language, or "NN" for item_index; '' otherwise
    artifact_id TEXT NOT NULL DEFAULT '',   -- '' for an adopted file
    job_id      TEXT NOT NULL DEFAULT '',
    delivered_folder TEXT NOT NULL DEFAULT '',
    delivered_name   TEXT NOT NULL DEFAULT '',  -- the REALIZED name, counter included
    checksum    TEXT NOT NULL DEFAULT '',
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    mtime_ns    INTEGER NOT NULL DEFAULT 0,
    state       TEXT NOT NULL,               -- synchronized | adopted | retired
    retired_path TEXT NOT NULL DEFAULT '',
    synced_at   TEXT NOT NULL,
    PRIMARY KEY (tracked_id, source_ref, occurrence, output_id, variant)
);
CREATE INDEX IF NOT EXISTS idx_tracked_files_ref ON tracked_member_files(source_ref);
```

Three fields deserve defending. **`delivered_name` is realized, not recomputed** — delivery's counter suffix is real (`_numbered_names` in `layout.py:37-47`), and a reconciler that recomputes will otherwise try to rename `Title-1.mkv` to `Title.mkv` forever. **`checksum` + `size_bytes` + `mtime_ns`** are the cheap/expensive pair that makes observation affordable (§E). **`variant`** is what makes a subtitles output with `en` and `fr` two managed files instead of one managed file and one permanently unmanaged one.

**One tracked collection = one source + one template + its realized destinations.** A second destination for the same playlist is a second tracked collection. Destinations must be **prefix-disjoint** across all tracked collections (checked on the sanitized path at creation) — otherwise two collections sharing a folder and a video rename it back and forth on alternate runs, forever. The attribution scan is non-recursive except into subfolders the collection itself created.

### B.6 `naming_version` — the retroactive sweep nobody asked for

`curate_title` (`engine.py:205-252`) is roughly fifteen heuristic regexes plus a capitalization rule; `display_name`'s truncation is another. Both will be tuned in a patch release. If the desired name is recomputed from the current engine on every plan, a routine improvement produces a plan that renames **every file in every tracked collection**, labelled as a template change, which is a lie. `naming_version` is stored per member file; a rename whose only cause is a namer bump is not proposed by a routine sync — it is offered once, as an explicit "re-apply current naming".

---

## C. The synchronization state model

```
A  previous   tracked_members ⋈ tracked_member_files WHERE tracked_id = ?
              Authoritative for "what Content believes it put there, and where".
              Written per file, on that file's convergence. Never inferred.

B  remote     AnalysisService.analyze_sources([collection], max_age=…)
              Authoritative for identity, title and remote position.
              Failure is a FIRST-CLASS state, not an empty set.

C  local      For each row in A: lstat the recorded path; cheap gate on
              (size, mtime_ns); hash only on disagreement. Plus a bounded,
              non-recursive attribution sweep of the tracked folders.
              Never authoritative for membership; only for presence and bytes.
```

### C.1 B must be forceable, and must be able to say "unknown"

`AnalysisService.analyze_sources(sources)` (`analysis/service.py:80`) takes **only a source list**. Internally it consults `load_fresh_analysis(key, self._settings.analysis_ttl_hours)` with `analysis_ttl_hours: float = 72.0` (`config.py:57`). There is no bypass anywhere. So "sync my playlist" would answer "nothing changed" for up to three days, and a `remote_state: fresh|cached` field could not honestly be populated.

**Do not solve this with a sync-only knob.** ADR 0022 is the precedent and it is exactly on point: `per_item_original` was refused because it would leave the single-resource path unable to say the same thing. "Give me a re-read, not the cache" is a need `POST /analyses` has too. Add `max_age: timedelta | None` to `analyze_sources` and a matching `refresh` on the analyses endpoint; sync passes `max_age=0` by default and exposes `--cached` as the opt-out. The plan reports `remote_observed_at`, not a vague enum.

Note the perverse interaction that makes the parameter mandatory rather than nice: a yt-dlp version bump changes every `resource_key` (`domain/analysis.py:112-124`), so upgrade day *silently* forces a full re-probe and behaves differently from every other day.

**A failed probe is `remote_unavailable` and the plan is not applicable.** An empty `entries` from a *successful* probe is a different thing and must also refuse to archive everything without an explicit acknowledgement — "all members disappeared" is overwhelmingly a provider hiccup.

**And a partial listing is the same class of hazard.** A member that vanishes from one listing — a transient region block, a truncated response, a video private for an hour — is indistinguishable from a removal. Hence `missing_streak` and `removal_grace`: a member absent from one successful listing becomes `pending_removal` and produces **no filesystem action**; `ARCHIVE` fires only after N consecutive successful listings (default 2). This converts the most destructive action in the vocabulary from "one bad HTTP response" into "a sustained fact", for the price of one integer.

### C.2 C is bounded by invariant 8, and an unmounted destination is not an empty one

The local observation is:

1. **Recorded paths.** `lstat` each `tracked_member_files` row. Not a regular file → not a candidate, never mutated (see §F.4 on symlinks). Present, `(size, mtime_ns)` unchanged → converged. Present, cheap gate disagrees → hash.
2. **Attribution sweep** of the tracked folders, media extensions only, for files step 1 did not claim. Identity comes from §E's ladder.
3. **A file with no recognizable identity is invisible to sync** — not an orphan, not a candidate, and never named in an API response. Only a count.

**Unmounted destinations present as empty directories**, and a write probe succeeds on the mountpoint. The plan would then be 200 × REDELIVER/REACQUIRE, and apply would write the whole library onto the local disk under the mountpoint, shadowing the real files when the share returns. The only reliable answer is a **sentinel**: `.content-tracked-<tracked_id>` written into each destination at creation; its absence is `destination_unavailable` and every action is blocked. Belt and braces: a plan in which more than a configurable fraction of tracked files are simultaneously missing is refused, not applied — the same reasoning C.1 applies to an empty remote listing.

### C.3 The scope limit, stated as a decision

Invariant 14 asks for "moved" and invariant 8 forbids the search that would find it. Invariant 8 wins.

- **Moved within the tracked destination (including into a subfolder the user made)** is detected by the sweep, and the answer is RENAME back to canonical or ADOPT at the new location.
- **Moved out of the tracked destination** is indistinguishable from "deleted by hand" and is answered the same way: REDELIVER (free) or REACQUIRE. **Content never scans the wider library.** This goes in the ADR as an explicit non-capability, so it is a decision rather than a future bug report.

---

## D. `SyncPlan` and the action model

### D.1 Vocabulary — ten kinds, and "blocked" is a *field*, not a kind

Making `blocked` a kind was the draft's structural error: it forced `applicable` to be plan-wide, so one ambiguity anywhere killed the collection — which is exactly HomeTube bug #19 reproduced. Blocking is a property of one action.

| Kind | Fires when | Library |
|---|---|---|
| `KEEP` | In A and B, present at the recorded name with matching bytes | — |
| `ADD` | In B, not in A | write (via a generation job) |
| `ADOPT` | In B, not in A, but a file in the destination is attributed to it | — (binds only) |
| `REDELIVER` | In A and B, file absent, sweep found nothing, `artifact_id` bytes still present | write |
| `REACQUIRE` | Same, but the artifact bytes are gone or reclaimed | write (via a job) |
| `RENAME` | In A and B, present, canonical name ≠ recorded name | rename |
| `RELOCATE` | In A and B, present, canonical folder ≠ recorded folder | rename across dirs |
| `ARCHIVE` | In A, absent from B for `removal_grace` runs, present, `removals=archive` | move |
| `FORGET` | In A, gone from B, and either not present or `removals=keep` | — |
| `DELETE` | In A, gone from B, present, `removals=delete` **and** three gates (§F.5) | unlink |

Deliberately absent: **`move_from_tmp`** (REDELIVER is strictly better and idempotent), **`RENUMBER`** (a mode of RENAME, reachable only when the renumber policy allows it), and **`IGNORE`** as a plan action — ignoring a member is a user act on the tracked collection (`PATCH`), recorded as `state='ignored'`, and it must exist, because otherwise a member the user deliberately deleted is re-offered forever.

### D.2 Actions operate on **files**, not members

Every action's subject is a `tracked_member_files` row (or a would-be row for ADD). `KEEP` at member level means every file row converged. This is what makes a `video + subtitles[en,fr]` template work; the draft's member-grained model left the `fr` subtitle permanently unmanaged.

```python
class SyncAction(BaseModel):
    kind: Literal["keep","add","adopt","redeliver","reacquire",
                  "rename","relocate","archive","forget","delete"]
    source_ref: str            # "youtube:kfQnyqoea2A"; never "" for a planned action
    occurrence: int = 1
    output_id: str
    variant: str = ""          # language / index discriminator
    ordinal: int | None = None
    title: str = ""

    current_folder: str = ""   # relative to the delivery root; "" for add/reacquire
    current_name: str = ""
    target_folder: str = ""    # "" for archive/delete/forget
    target_name: str = ""      # already through DeliveryStore.expected_name()

    reason: SyncActionReason
    blocked: SyncBlockReason | None = None    # blocked actions are skipped, never gate others
    evidence: list[str] = []   # "snapshot", "artifact_row", "checksum", "tag:PURL", "duration:-20.9s"
    preconditions: ActionPreconditions        # see D.3
    size_bytes: int = 0
    cross_device: bool = False
    requires_confirmation: bool = False       # delete only
```

```python
class SyncActionReason(StrEnum):
    NEW_MEMBER, REMOVED_REMOTELY, MISSING_LOCALLY, MOVED_LOCALLY,
    TITLE_CHANGED, ORDINAL_CHANGED, TEMPLATE_CHANGED, NAMING_VERSION_CHANGED,
    DESTINATION_CHANGED, CONVERGED

class SyncBlockReason(StrEnum):
    IDENTITY_AMBIGUOUS      # two candidate files claim one member/variant
    IDENTITY_CONFLICT       # snapshot says X, the file's own tag says Y
    IDENTITY_UNKNOWN        # absent from A, no checksum match, no readable tag
    TARGET_OCCUPIED         # desired name held by content that is not this file
    DUPLICATE_OCCURRENCE    # the occurrence count changed remotely
    UNTRACKABLE_MEMBER      # empty source_ref (generic extractor)
    RENAMES_UNSUPPORTED     # no hard links and no safe rename on this mount
    DESTINATION_UNAVAILABLE
    ARTIFACT_RECLAIMED      # 410 — REDELIVER impossible, fall to REACQUIRE
```

```python
class SyncPlan(BaseModel):
    id: str                      # "syp_<uuid4hex>"
    tracked_id: str
    template_version: int
    naming_version: int
    created_at: str
    remote_observed_at: str
    remote_state: Literal["observed", "unavailable"]
    local_state: Literal["available", "unavailable"]
    removals: Literal["archive","keep","delete"]   # frozen at plan time
    renumber: Literal["never","on_change","on_demand"]
    summary: SyncPlanSummary     # counts per (kind, reason), bulk_reason when N share one
    actions: list[SyncAction]    # paginated on read
    unmanaged_file_count: int    # invariant 8, made visible without naming names
    recovering_from: str = ""    # a previous apply that did not finish
    warnings: list[ValidationIssue] = []

    @property
    def applicable(self) -> bool:
        return self.remote_state == "observed" and self.local_state == "available"
```

`removals` is frozen into the plan because HomeTube decided archive-vs-delete twice and mutated the plan in place with no reverse conversion (`:499`, `:858-882`). The plan shown is the plan applied.

### D.3 Staleness is per action, not per plan

The draft's `observation_hash` is not computable: it was defined over the checksums of C, while the cost note says checksums are computed only when the cheap gate disagrees — so two observations of an identical world hash differently, and a partial per-file commit changes the record the hash depends on. Worse, with `max_concurrent_jobs=2` an ordinary download landing in the same folder mid-apply would abort a 200-action run.

```python
class ActionPreconditions(BaseModel):
    source_size: int | None = None
    source_mtime_ns: int | None = None
    source_dev_ino: tuple[int, int] | None = None   # the object, not the path
    target_absent: bool = False
```

Apply re-checks these immediately before each atomic step and **skips only the actions whose world moved**, reporting them as `stale`. Atomicity comes from the claim; the hash was buying a guarantee the claim already gives.

### D.4 Where ADD delegates, and the two naming traps

`ADD` and `REACQUIRE` submit an ordinary `GenerationRequest`. Re-submitting the playlist would fan out over all N members, which invariant 6 forbids; and ADR 0025 §3 blocks the alternative, since members are not addressable.

The draft's mechanism — `derive_member_request(...)` plus `payload["outputs"][0]["delivery"]["filename"]` — is wrong in two ways that only appear in a multi-output template, i.e. the real one.

**Trap 1: it names only the first output.** `resolve_naming_plan` reads `output.delivery.filename` per output (`engine.py:283-286`), so a video + subtitles template yields `007 - Title.mkv` beside `Title - subtitles - en.srt`. The next plan proposes a rename that can never converge.

**Trap 2: a one-output derived request changes the qualifier.** `derive_member_request` keeps only the output being fanned out (`collections.py:68-77`), and `_primary_output_id` is computed over *the outputs present in the request* (`engine.py:157-165`) against `PRIMARY_PRECEDENCE = (video, audio, markdown, document_text, pdf)`. In a `video + audio` collection request, audio is qualified: `007 - T - audio.opus`. Submitted alone, audio becomes primary: `007 - T.opus`. Same member, two names, depending on which path produced it.

**Decision — one namer, one job per member, the whole template.**

- The sync planner calls `resolve_naming_plan(template, collection_analysis)` on the **whole template** with `scope=each_item` intact, exactly as a real collection run does. That single `NamingPlan` is the authority for both RENAME targets and ADD names. Nothing recomputes the ordinal formula; nothing holds a second copy of `f"{position:0{width}d} - {title}"`.
- ADD submits **one job per member carrying every output of the template**, source rewritten to the member URI, `scope` dropped. That reproduces the collection's qualifier resolution exactly and divides the job count by the number of outputs.
- The ordinal reaches the derived request through `delivery.filename` on **every** output, fed from `naming.item_bases[label]` — never recomputed.
- `execution.reuse_existing = False` is set explicitly on every derived request (§K.7 explains why this is not optional).

INV-010 is satisfied because the *engine* computes the name; the sync planner is backend code, not a client. ADR 0017 is satisfied for the same reason. INV-018 holds: the orchestration chooses which members and in what order, and hands each to `build_plan` through the public request model.

**And ADD must refuse rather than clone.** `DeliveryStore.deliver` falls to the counter when a name is held by different content (`layout.py:288-293`). An ADD whose canonical name is taken by a foreign file would land as `-1`, be recorded correctly as `…-1.mkv`, and generate a `TARGET_OCCUPIED` block on every subsequent plan. Check the canonical name before submitting; block instead. This — not the rename rule alone — is what actually stops `-1` accumulation.

---

## E. Identity and reconciliation

### E.1 The metadata tier is thinner than the spec assumes — measured, not argued

Probed on the engine's own output this session:

| Artifact class | Format tags |
|---|---|
| Plain video, Matroska | `ARTIST, COMMENT, DATE, DESCRIPTION, ENCODER, GENRE, PURL, SYNOPSIS, title` |
| Plain video, MP4 | `comment` (lowercase; MP4 has no `PURL` atom) |
| **Audio-only** (`audio_main.opus`) | **`[]`** — empty, not degraded |
| **SponsorBlock-cut video** (3 artifacts, 3 jobs) | **`['ENCODER']`** — title, PURL, COMMENT all gone |
| Subtitles / transcript / summary / metadata | no container at all |

Both causes are exact and both are bugs, not decisions:

- `embedding_args` has **one** call site, `providers/ytdlp.py:967`, inside `_acquire_video`. `_acquire_audio` never calls it, so `--embed-metadata` never reaches an audio acquisition regardless of `processing.embed_metadata` (default `True`).
- `_run_segment_cut` (`ytdlp.py:891-930`) runs the concat demuxer with `-map 0 -dn -ignore_unknown -c copy -map_chapters 1` and **no `-map_metadata`**, then `target.replace(produced)`. Chapters are deliberately remapped; global tags are not carried across the concat.

**SponsorBlock removal is the HomeTube preset.** The population most likely to want playlist sync is precisely the population whose files carry no identity. Invariant 10.2 is not "a fallback that mostly works" — for that configuration it never works. Both fixes belong in Phase 0, together with a test that asserts the tag survives *every* acquisition and post-processing path, `providers/ffmpeg.py` format conversion included, so the next post-processor cannot silently strip it again.

**Even so, Phase 0 only helps files produced after it.** Existing SponsorBlock-cut files are permanently anonymous to the container tier. That is what makes the checksum tier below mandatory rather than optional.

### E.2 The corrected ladder — five tiers, and attribution is not authority

```
T0  Snapshot.  tracked_member_files row → lstat(folder/name).
    Regular file, (size, mtime_ns) match → ATTRIBUTED, evidence ["snapshot"].
    Cheap gate disagrees → hash; checksum matches → ATTRIBUTED (re-baseline
    mtime); checksum differs → see E.3.
    THIS TIER, AND ONLY THIS TIER (plus a completed ADOPT), CONFERS AUTHORITY.

T1  Artifact rows.  artifacts WHERE source_ref = ? AND delivered_path = ?
    Bootstrap for a folder Content delivered into before it was tracked.
    Proposes an ADOPT; does not by itself authorize a mutation.

T2  Checksum.  Hash the unattributed candidates in the tracked folders and
    match against tracked_member_files.checksum and artifacts.checksum.
    This is the tier that finds a manually renamed file, and it is the ONLY
    tier that works for audio, SponsorBlock-cut video, .srt, .json and .md.
    The draft omitted it; that omission made "renamed by hand" resolve to
    REDELIVER, i.e. a second copy of the same video beside the user's.

T3  Container metadata.  ffprobe -show_format on remaining candidates.
    Read PURL/purl, then COMMENT/comment. NOTHING ELSE — never DESCRIPTION
    (a real artifact here carries another video's URL inside it), and never
    HomeTube's `else: video_id = comment` fallthrough, which turns free text
    into an identity. Parse with source_ref_from_url; unrecognized → invisible.
    T3 PROPOSES AN ADOPTION. It never authorizes a rename, move, archive or
    delete on its own (see §G).

T4  Duration.  |observed - B.duration| within 5% → evidence "duration:ok";
    outside → evidence "duration:-20.9s". NEVER a gate: a 20.9s SponsorBlock
    cut on a four-minute video is a 9% drift and is in this repository's data.

Filename: never attributes. Used for exactly one thing — deciding whether a
RENAME is needed once identity is settled.
```

Two properties of T3 that must be written down. **The tag attributes a source, not an artifact**: the video, the audio and every subtitle of one member carry the same `PURL`, so T3 can only ever adopt the primary media file, and sidecars are T0/T1/T2-only, permanently. And **the tag is writable by anyone who can write the file** — which is why it proposes and never authorizes.

### E.3 Disagreement, and the one case the draft got backwards

| Situation | Answer |
|---|---|
| T0 attributes, no tag (audio, cut video, remux) | Proceed. Absence of a tag is not evidence against the snapshot. |
| T0 attributes, tag agrees | Proceed, `evidence = ["snapshot","tag:PURL"]`. |
| T0 path present, **bytes changed, identity still this member or unreadable** | **KEEP with an advisory `content_changed`; re-baseline checksum/size/mtime.** The draft blocked here, which would make every member of a collection permanently blocked for anyone running Tdarr, unmanic, Plex "optimize", or mkvmerge — a large share of the target population. |
| T0 path present, **tag resolves to a different member** | `IDENTITY_CONFLICT`. The user replaced the file. Refuse, report both refs, mutate nothing. |
| T0 silent, T2 matches a checksum | Attributed; RENAME back to canonical (or ADOPT the new location). |
| T0 and T2 silent, T3 attributes | `ADOPT`, opt-in, visible, never automatic. |
| T0 and T2 silent, T3 silent | `IDENTITY_UNKNOWN`. **Never REDELIVER on absence alone** — redelivering before the folder has been swept is how a second copy appears next to the user's renamed one. |
| Two candidates claim one (ref, variant) | Disambiguate first: prefer a path equal to a recorded `artifacts.delivered_path`, then one already at the canonical name; otherwise `IDENTITY_AMBIGUOUS`. HomeTube let glob order pick (`:228-229`). |
| Same ref twice in the listing | Legal. Match by `(source_ref, occurrence)` in stable ordinal order. A **change in the occurrence count** blocks rather than renumbering silently. |
| `source_ref == ""` (generic extractor) | `UNTRACKABLE_MEMBER`, reported at plan time. Tracking refuses collections whose members have no namespace rather than half-tracking them. |

**The governing rule: an unresolved identity produces a blocked action for that file and nothing else. Blocked actions never gate the actions around them, and there is nothing for the caller to "acknowledge" — "I accept that you will not touch these files" is not a decision worth recording, and a single checkbox for N blocks is the mechanism by which a real misattribution eventually gets applied.**

---

## F. The safe apply algorithm

### F.1 A dedicated rename primitive — `claim_with` must not be repurposed

`claim_with` is nearly right and is wrong in three specific ways when the "staged" file is a live library file (`storage/paths.py:114-141`):

```python
    except OSError:
        if not claim_path(destination):     # creates a 0-BYTE FILE
            return False
        os.replace(staged, destination)     # raises EXDEV — uncaught
        return True
```

- **Cross-device leaves a 0-byte file** carrying the member's canonical display name in the user's library — the exact broken-media-server entry the docstring says the primitive exists to prevent — and under the amended ADR 0023, nothing in Content is then permitted to remove it. Sync would brick a collection and the invariant it wrote would block the repair.
- **A case-only rename is an unresolvable block.** `curate_title` capitalizes a lowercase first word (`engine.py:249-251`), and remote retitles change case routinely. On APFS/NTFS/SMB — the NAS environments this targets — `os.link(old, "…/001 - Foo.mkv")` while `001 - foo.mkv` exists raises `FileExistsError`, mapped to `TARGET_OCCUPIED`, where the occupant *is* the file being renamed.
- **The idiom is a footgun.** Every existing caller pairs `claim_with` with `finally: staged.unlink(missing_ok=True)` (`layout.py:104`, `:296`). Copy that idiom with a live library file and a lost race deletes the user's file.

```python
def rename_claimed(source: Path, destination: Path) -> bool:
    """Give an existing file a new name, without ever overwriting and without
    ever leaving the destination partial. True when the name is now ours.

    Failure direction is deliberate: a crash between the link and the unlink
    leaves TWO names for one inode, never zero.
    """
```

Contract: `lstat` the source and refuse anything that is not a regular file; short-circuit `source.samefile(destination)` (the same-inode / case-change path, handled by a two-step through a plan-scoped temp name in the *same* directory); compare `st_dev` up front and route a genuine cross-device move through `stage_beside(source, target_dir, move=True)` + `claim_with`, flagged `cross_device`; on any failure after a `claim_path`, unlink the placeholder and re-raise; never unlink the source unless the link succeeded.

At plan time, probe hard-link support once per destination. Where it is unavailable, every RENAME/RELOCATE is `RENAMES_UNSUPPORTED` rather than driving the library through a branch that can leave empty files.

### F.2 Symlinks and containment

Measured: `os.link` on a symlink produces a hard link to the *target*, outside the delivery root, and the symlink disappears. A symlink farm into a media pool — a normal NAS pattern — would be silently rewritten into hard links, and containment would become nominal (the path is inside the root; the inode is not). `DeliveryStore.deliver` already refuses a symlinked directory under the root (`layout.py:285-287`); sync must not be laxer than delivery.

**Rule: only regular files are candidates. Containment is re-verified on the resolved final path immediately before each act, and the `(st_dev, st_ino)` observed at plan time is confirmed at apply time.**

### F.3 Ordering and cycles

Under the default `renumber: never`, bulk renames largely disappear. The machinery is still needed, for three narrower sources: two members sharing a title (whose delivered names are `Title.mkv` and `Title-1.mkv`, and whose reorder *is* a swap), an explicit renumber operation, and a RELOCATE that lands in a directory a sibling collection is leaving.

```
1. Build the rename graph over (current -> target) for RENAME | RELOCATE.
2. Partition: targets free of any managed source, and the rest.
3. Execute the free ones first, descending ordinal, so an insertion shifts
   from the tail and never meets itself.
4. Break each remaining cycle by moving ONE file to a plan-scoped temp name in
   its own target directory:  .content-sync-<plan_id[:8]>-<ref_slug><suffix>
   — dot-prefixed, plan-scoped so a crashed apply is identifiable, and in the
   target directory so it is never a cross-device move.
5. Drain the acyclic remainder, then claim the broken file's real target.
```

Every move is `rename_claimed`. A target occupied by an unmanaged file is **never** resolved with the delivery counter: delivery uses the counter because it does not know what it wants; sync does. `TARGET_OCCUPIED`, surfaced, the user's call.

### F.4 Archive

- Target: `<destination>/.archive/`, built with an **explicit segment**, not through `safe_relative_folder` — measured: `display_name(".archive")` → `"archive"`, because `display_name` ends with `.strip(" .")` (`naming/sanitize.py:50`). Since that is the one place a containment bug would live, the archive path goes through an explicit `resolve()` + parent check.
- On creation, drop `.ignore` and `.plexignore` markers inside it. The dot alone is not a reliable hiding mechanism across Plex and Jellyfin, and this costs two empty files.
- **The archived file keeps its full display name, ordinal included.** HomeTube dropped the ordinal (`:502-505`) and severed the file from the member. `tracked_member_files.retired_path` records where it went; `state='retired'`.
- The attribution sweep skips `.archive/`. `DeliveryStore.list_folders()` uses `rglob` (`layout.py:300-309`) and must learn to skip it too, or `GET /config` will advertise every archive as a delivery folder.
- **The archive belongs to the destination and relocates with it**, as a single journalled directory move; otherwise a RELOCATE strands retired members behind a `retired_path` that no longer resolves.
- Collisions inside `.archive/` go through the same `_numbered_names` + claim loop as everything else. One collision policy in the codebase, not two.

### F.5 Delete

Four gates, all required, none of them supplied by the same request: `CONTENT_SYNC_ALLOW_DELETE=true` (operator, default off); `tracked_collections.removals == 'delete'` at plan time; `requires_confirmation` on the action; and the apply echoing the `(source_ref, output_id, variant)` of every delete it authorizes. A mismatch is `sync_delete_unconfirmed`, refused. No trash directory — a trash Content sweeps would be a retention rule over the library, which ADR 0023 forbids and the carve-out does not cover.

### F.6 The apply is a job — journal, partial apply, recovery

The draft invented `sync_runs` + `sync_run_actions` with two private status vocabularies, in-place `UPDATE`s on the thing it called a journal, no SSE, and no startup sweep. That re-implements `jobs` + `job_steps` + `job_events`, bypasses INV-004 (*"No free-form `UPDATE status`… `content/domain/job.py` is the only authority"*) and INV-006 (append-only sequenced journal), and justifies itself with *"a sync run is not a job"* — which is dodging an invariant by renaming the object.

**Decision: the apply is a job.**

- `jobs` gains `kind TEXT NOT NULL DEFAULT 'generation'` (migration 7); a sync apply is `kind='sync'`.
- One `job_steps` row per action, operation `sync.rename` / `sync.relocate` / `sync.archive` / `sync.redeliver` / `sync.delete` / `sync.submit`, provider `content.sync`. `job_steps` carries no params, exactly as for collection members — and the API recovers presentation the way it already does for members (`app.py:749-772`): by re-reading the snapshot, here `jobs/<id>/snapshots/sync_plan.json`.
- `job_events` is the journal: append-only, sequenced, and already streamed at `GET /jobs/{id}/events/stream` with `Last-Event-ID` resume. A 200-file apply becomes observable for free.
- `store.requeue_running()` at startup (`execution/worker.py:41-43`) already handles the crashed-apply case; nothing new latches forever.
- Cancellation, `max_concurrent_jobs`, failure policy and ADR 0021's partial-success semantics all come along.

The friction is real and must be named rather than hidden: `jobs.request` is typed as a `GenerationRequest` and a sync apply is not one. The answer proposed is that a sync job's `request` is a small synthetic descriptor `{kind: "sync", tracked_id, sync_plan_id}`, `GET /jobs` filters to `kind=generation` by default, and contract §9 records the placement (§H). **That is a decision for the maintainer** — it is listed in §M.5.

Per-action order, which is the whole recovery story:

```
1. job_events: step.started                    -- intent, before any bytes move
2. re-check the action's preconditions          -- skip-and-report if the world moved
3. perform the operation via rename_claimed / DeliveryStore.deliver
4. UPDATE tracked_member_files for THIS FILE    -- per-file commit
5. job_events: step.succeeded
```

Step 4 is the direct fix for HomeTube's worst bug. Recovery is **not rollback** — reversing a completed rename is itself a risky mutation. On the next plan, a step left `running` makes the plan report `recovering_from: <job_id>`, each affected file is re-observed from scratch, a `.content-sync-*` temp whose plan id matches that job is resolvable (finish the move) and one matching nothing is reported and left alone, and any `.staging-*.partial` left by a killed cross-device move is swept.

One property worth writing down as load-bearing rather than leaving to luck: a crashed RENAME self-heals, because T2's checksum tier finds the file under its new name. Without T2 it would have healed only by accident, through `DeliveryStore.deliver`'s `_same_content` short-circuit, and only for byte-identical content.

### F.7 Serialization and dry run

Two clients can plan and apply concurrently, interleave renames, and write conflicting snapshot rows. Use the shape the codebase already uses for jobs (`idx_jobs_idempotency_active`):

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_sync_active
  ON jobs(json_extract(request,'$.tracked_id'))
  WHERE kind = 'sync' AND status IN ('queued','running');
```

The plan itself writes one row and nothing else. The test that keeps that honest is not "no exception" — it is a full tree snapshot of `(relpath, size, mtime_ns, st_ino, sha256)` over the entire delivery root, before and after (§L, T4).

Plan computation does `ffprobe` per unattributed file and opportunistic hashing, so it does **not** run inline in a request handler. It is a background computation with a status, bounded, reporting `verification: cheap | full` — which is a third argument (with F.6 and D.4) for reusing the queue's lifecycle rather than inventing one.

---

## G. ADR 0023, ADR 0017, and the invariant

### G.1 There is no enforcement to preserve — confirmed

`grep -rn "reclaimed_at" --include=*.py apps/ packages/` → nothing. No retention module. No test among the 65 in `apps/backend/tests/` asserts library immunity. ADR 0023's status line reads `proposed`. Its closing sentence — *"This is an invariant a test enforces, not a guideline"* — describes an intention, not a fact. **The carve-out cannot weaken an enforcement; it must supply the first one.** That is a better position to be in, and it changes the work: the amendment ships with the tests or it is prose.

### G.2 Exact replacement wording for ADR 0023 §1

The draft's clause 3 was an `OR` whose second branch — a container tag — was sufficient on its own. That authorizes archiving or deleting a file Content never created, on the strength of a tag any writer of the file can set, and it contradicts the amendment's own opening sentence. Corrected:

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

### G.3 ADR 0023's *eligibility* section must be amended too

Reclaiming targets artifacts whose `delivered_path` is set (`0023:65-73`) — which is exactly the set REDELIVER depends on, and its confidence rests on the very `delivered_path` whose file the user has since deleted. Turn retention on and REDELIVER dies in the case it exists for.

> An artifact referenced by a live tracked-collection snapshot row is not
> eligible by default: it is the cheap answer to a library file the user
> deleted. An operator may opt in to reclaiming it anyway, and the ADR says
> plainly what that buys and costs — the bytes come back, and a future
> restoration becomes a re-download.

### G.4 ADR 0017 must be amended, bounded by `naming_version`

> **Not retroactive, with one bounded exception.** Files already in a library
> keep their names; a change to the naming rules never sweeps back over what is
> already delivered. The exception is a tracked collection (ADR 0027): its
> members are renamed to converge with **that collection's own** current
> ordering and titles, inside an apply the user approved, over files the engine
> holds a delivery record for. A change to the *naming engine itself* is not
> such a cause: each tracked member records the naming version it converged
> under, and re-applying a newer namer is a separate operation the user asks
> for by name. Naming stays the engine's job in both cases — what sync adds is a
> moment at which an already-delivered name is recomputed, not a second namer.

### G.5 INV-020 and the five tests

INV-019 is the last invariant in `docs/architecture/invariants.md`, so INV-020 is free.

> **INV-020 — The delivery library is mutated only by delivery and by an
> approved sync apply.** Two code paths may write into the delivery root:
> `DeliveryStore.deliver`, which only ever creates a name it has claimed, and a
> sync apply, which acts on files it holds a delivery record for, inside a
> tracked destination, from a plan the user approved, with synchronization
> enabled by the operator. No other module may create, rename, move or remove
> anything there, and no scheduled or policy-driven path may do so at all.
> *Verified by:* the purge sweep (T2) and the attribution-scope test (T3) —
> outcome tests over a full tree snapshot; the module lint (T1) is a tripwire,
> not the guarantee.

**T1 — module lint.** Walk the AST of every module under `apps/backend/content/`, collect `os.unlink/remove/rename/replace`, `shutil.move/rmtree`, `Path.unlink/rename`, and assert the containing modules are exactly `{storage/paths.py, storage/layout.py, sync/apply.py}`. It fails on the *next* module that learns to delete — but it constrains lexical location, not authority, which is why it does not carry INV-020.

**T2 — `test_every_purge_leaves_the_library_byte_identical`.** A delivery root with delivered artifacts, an unmanaged `family-photo.jpg` and an `.archive/`. Run every purge entry point — `purge_tmp`, `purge_work`, the upload sweep, `POST /cache/purge`, and the ADR 0023 reclaim once it exists — then assert the full tree of `(relpath, size, mtime_ns, sha256)` is unchanged. Parametrized, so a new purge must be added to the list, which is where its author reads the invariant.

**T3 — `test_sync_apply_touches_only_recorded_files`.** Three tracked members plus, in the same folder: an unrelated JPEG, a tagless MKV, and an MKV whose `PURL` names a video outside the playlist. Apply a plan renaming all three members. Assert the three foreign files' `st_ino`, `st_mtime_ns` and checksums are unchanged, `unmanaged_file_count == 3`, and no foreign filename appears anywhere in the response.

**T4 — `test_plan_writes_nothing`.** Full-tree snapshot including inodes, before and after, on a scenario containing every action kind. Byte-identical. The DB gained exactly one plan row.

**T5 — `test_delete_needs_four_gates`.** Parametrized; each variant missing one gate is refused with `sync_delete_unconfirmed` and mutates nothing.

### G.6 ADR 0024 is reopened, not watch-listed

The draft treated trigger 4 as being about scheduling. It is not: *"an endpoint that executes, schedules, or **reaches outward on a caller's behalf** in a way the current limits do not contain."* An unauthenticated `unlink()` — or an unauthenticated mass-`ARCHIVE`, which is sufficient damage since nothing may put it back — on the user's media library is uncontained today, with or without a scheduler. Every gate the design lists is an input the same caller supplies.

The containment is therefore not in the request body:

- `CONTENT_SYNC_ENABLED=false` by default. The endpoints 404 when off.
- `CONTENT_SYNC_ALLOW_DELETE=false` by default.
- Adoption — which is what converts "files Content owns" into "files Content may move" — is behind the same enable flag, and the delivery root can never be a tracked destination.

ADR 0027 states in writing that shipping this reopens ADR 0024's question, and ADR 0024 gains the tracked-collection endpoints under trigger 4.

---

## H. API — the smallest coherent surface

The word `sync` stays out of `GenerationRequest`: `execution.mode: "sync"` is a reserved, refused value meaning *synchronous* (`docs/contract.md:236`). The collision lives only in English, and one line in contract.md §9 disambiguates it.

```
POST   /api/v1/tracked-collections                    201 → TrackedCollection
GET    /api/v1/tracked-collections                    200 → [TrackedCollection]   (?status=)
GET    /api/v1/tracked-collections/{id}               200 → TrackedCollection + last plan summary
PATCH  /api/v1/tracked-collections/{id}               200 → TrackedCollection     (bumps template_version)
DELETE /api/v1/tracked-collections/{id}               204   stops tracking; touches no file

POST   /api/v1/tracked-collections/{id}/sync-plans    202 → SyncPlan (computing)
GET    /api/v1/sync-plans/{id}                        200 → SyncPlan (?kind=&offset=&limit=)
POST   /api/v1/sync-plans/{id}/apply                  202 → Job (kind="sync")
GET    /api/v1/tracked-collections/{id}/history       200 → [Job]  (kind="sync", newest first)
```

- **`POST /tracked-collections`** takes `{source, outputs, label?, removals?, renumber?, adopt?}` — the body of a `GenerationRequest` with `scope: "each_item"`, validated by the **same** model, *plus* one extra validation the draft omitted: derive and validate one member request at creation time, so a template that would produce refusals only at ADD time is rejected up front. Creation from an existing job is `{"from_job": "job_…"}`, lifting the normalized request out of `jobs.request` — the common path, since the user has just downloaded the playlist. It runs nothing.
- **`PATCH`** exists because the draft's DELETE-and-recreate is destructive: for every artifact class the container tier cannot read — audio, cut video, every sidecar — the snapshot *is* the identity, and destroying it turns the next sync into a full re-download. PATCH bumps `template_version`, preserves the snapshot, and refuses a destination change that would break prefix-disjointness.
- **`DELETE`** defaults to `status='archived'`: the binding goes inert, the snapshot is kept, no file is touched, and the response says so. Purging the snapshot needs an explicit flag and reports what will be lost.
- **`POST …/sync-plans` is the only way to obtain a plan.** No `?dry_run=` — a flag that can be forgotten is HomeTube's dead `dry_run` waiting to happen. It returns `202` with a computing plan because it probes a provider and may `ffprobe` and hash.
- **`GET /sync-plans/{id}`** returns the summary always and paginates actions, because a first plan on a 200-entry playlist is 200 actions and no client should have to iterate to render a headline.
- **`apply`** takes `{"confirm_delete": [{source_ref, output_id, variant}, …]}` and refuses with `sync_delete_unconfirmed` on a mismatch, `sync_destination_unavailable` when the sentinel is missing, `sync_remote_unavailable`, and `sync_apply_in_progress` on the unique index. There is no `acknowledge_blocked`: blocked actions are skipped and reported, in the plan and again in the job. Five new stable codes, added to the closed v1 list, never repurposed.
- **`apply` returns a Job.** Filesystem convergence is that job's steps; ADD/REACQUIRE are child generation jobs whose ids appear in the sync job's `step.succeeded` events. Snapshot write-back for a child job is idempotent and runs both on its terminal event and at the start of the next plan, so a crash between the two cannot leave an ADD firing forever.
- **No endpoint returns an absolute path** (ADR 0018) and **none returns an unmanaged filename** (invariant 8) — only the count.
- SDK gains `TrackedCollectionData`, `SyncPlanData`, `SyncActionData`; the sync job is an ordinary `JobData` with `kind`. Everything goes through the SDK (ADR 0015).

**Placement against contract §9's four concepts**, which the draft never addressed: `TrackedCollection` is a **binding**, not a fifth concept — it *holds* a request and *produces* jobs. `SyncPlan` is a reconciliation plan over the filesystem, deliberately not an `ExecutionPlan`, and it lives at `/sync-plans` so the reserved `POST /plans` (the generation dry-run) stays free. The apply is a **Job**, which is why no job-shaped non-job is introduced. Contract §9 records this explicitly.

---

## I. CLI and HomeTube UX

No business logic in a client. `SyncAction` carries `kind`, `reason`, `evidence`, `blocked` and both paths, so every renderer is a formatting loop — HomeTube needed `format_sync_plan_details` precisely because its plan object was not self-describing.

```
content track add <url> --folder "Series/Playlist" [--label …]
                        [--removals archive|keep|delete] [--renumber never|on-change]
                        [--adopt]
content track list · show <id> · set <id> --removals … · rm <id> [--purge-snapshot]

content sync <id>                    # plan, render, prompt
content sync <id> --plan-only        # render, exit 0
content sync <id> --json             # the SyncPlan verbatim
content sync <id> --yes              # apply; still refuses on delete without --confirm-delete
content sync <id> --confirm-delete <ref>…
content sync <id> --renumber         # the explicit renumbering operation
content sync history <id>
```

```
Playlist-EgalitarianMonkey · 24 remote · 22 tracked · observed 12s ago
  2 add · 3 rename · 1 relocate · 1 archive · 17 keep · 2 skipped
  14 files in this folder are not managed by Content and will not be touched.

  ADD       005  Nested for loops                       new member
  RENAME    002  Temporary Power                        title changed remotely
            "002 - Temporary power.mkv" → "002 - Temporary Power.mkv"
  RENAME    002  Temporary Power  (subtitles · en)       title changed remotely
  RELOCATE  001  Trapped by plates in The Sims           destination changed
  ARCHIVE   —    An old clip                             absent 2 runs → .archive/
  SKIPPED   007  Crafting your first item                identity_conflict
            recorded file holds youtube:aBcDeFgHiJk, expected youtube:kfQnyqoea2A
            nothing will be done for this file; the other 22 will proceed

  2 actions skipped. Apply will converge the rest.
```

Two words matter in that output. **"SKIPPED", not "BLOCKED"** — the collection is not held hostage. And **the per-variant rename line**, which is how a user learns that their subtitles moved with the video.

**HomeTube UI.** Reuse the panel's shape, drop the two mistakes: the plan is fetched on an explicit button, never on every rerun (HomeTube spawned one ffprobe per file per Streamlit rerun, `main.py:2073`); and the download gate becomes a warning with an override, because a hard disable plus an action that can never succeed made downloads permanently unreachable.

---

## J. Migrating existing HomeTube libraries

The good news: HomeTube relied on `--embed-metadata` (`app/core.py:48`), and Content's video path emits `PURL` + `COMMENT` in Matroska and `comment` in MP4 — verified. A library filled by standalone HomeTube's *video* path is readable by tier T3 with no new mechanism.

The bad news, and it is about the maintainer's own library rather than HomeTube's: **`Playlist-EgalitarianMonkey/` contains four delivered files per member** — two from the playlist run (one a `-1` clone) and two from separate single-video jobs delivered into the same folder — **all six members' videos are SponsorBlock-cut and carry only `ENCODER`**, and all 26 member rows have `resource_key = ''`. With the draft's ladder, T1 misses, T2 misses, every file is `IDENTITY_UNKNOWN`, `--adopt` adopts nothing, all six members read as new, and sync's first act is six re-downloads landing as `-2` clones beside eighteen files that ADR 0023 forbids removing.

The corrected ladder fixes exactly this, and it is the reason T0/T1/T2 exist:

- the `member_uri → source_ref` backfill (§B.2) gives all 26 member rows an identity;
- T1 matches `artifacts.delivered_path` for the twelve playlist-run files;
- T2 matches checksums for the rest, including the cut videos T3 can never read;
- the twelve single-video files match nothing and stay **unmanaged**, counted and untouched — which is correct, because Content did not put them there as members of anything.

**`ADOPT` is opt-in and never renames in the same action.** It binds the existing file with `state='adopted'`, `artifact_id=''` where there is no artifact row, `delivered_name` = the file's *current* name, checksum computed once. A subsequent plan proposes the RENAME separately and visibly — the user may well prefer their own names, and adoption must not be a trojan horse for a mass rename. `artifact_id=''` matters: an adopted file has no bytes in the job store, so REDELIVER is unavailable and a later local deletion falls to REACQUIRE. Record that honestly.

**Duplicates need their own answer, and the draft left it undesigned.** `RESOLVE_DUPLICATE` is not a filesystem action — Content may not remove the surplus — but the *choice* must persist, or every sync re-asks the same six questions forever. `tracked_member_files` marks the bound file; a small `tracked_ignored_paths(tracked_id, folder, name)` records "this one is not mine". One question, once.

**HomeTube's `status.json` is a hint, never evidence.** Its `resolved_title` is pure filename trust — HomeTube fabricates a synthetic metadata dict from it with no verification (`:550-562`) — and invariant 2 forbids filename authority. Recommendation: leave the importer out of V1. `--adopt` over T1/T2/T3 covers everything HomeTube produced with metadata embedding on, which is the default, and everything Content itself delivered.

Files that are genuinely unidentifiable must be reported as such: *"N files in this folder could not be identified and were left alone."* Not "new".

---

## K. Non-goals for V1

1. **No scheduler, no cron, no polling, no subscription.** Manual only. A scheduler would call the same plan/apply pair, and attaching one reopens ADR 0024.
2. **No notifications.**
3. **No library-wide search.** A file moved out of its tracked destination is not hunted (§C.3).
4. **No sync for non-collection sources.**
5. **No mutation of files Content holds no delivery record for.** Ever, under any flag.
6. **No cross-collection reconciliation.** The same video in two tracked collections is two members with two files — and destinations are prefix-disjoint, so they cannot fight over one file.
7. **No `reuse_existing`, no cross-job cache — and it must be *enforced*, not assumed.** `ExecutionPolicy.reuse_existing` defaults to `True` (`domain/request.py:758`); `derive_member_request` drops `execution` entirely (`collections.py:78-83`); `reuse_enabled = cache_enabled and request.execution.reuse_existing` (`executor.py:210-212`); and ADR 0010 sets `CONTENT_CACHE_ENABLED=true` in the HomeTube compose. So every sync ADD in the HomeTube deployment would run with reuse **on**. The draft's reason why that is harmless — that a member's signature folds in `member_index`/`member_total` — is wrong for this path: a sync ADD is an ordinary single-resource job whose acquisition signature contains no member index, so reuse would **hit**, and `find_reusable_artifact_group` (`store.py:470-486`) does not check that the source job succeeded. The sync planner sets `execution.reuse_existing = False` explicitly on every derived request, and a test asserts it.
8. **No retroactive naming beyond tracked members**, and none from a naming-engine bump (§B.6).
9. **No trash directory.**
10. **No member addressing in `GenerationRequest`** (ADR 0025 §3). Sync addresses members internally, through `derive_member_request`.
11. **No per-member delivery folders.** One folder per output, as `OutputDelivery` already models it.
12. **No `retention` interaction.** The block stays reserved and refused.

---

## L. Test matrix

The spec's seventeen, the draft's forty-six, and the twenty the adversarial pass forced. Grouped by what breaks without them.

**Diff correctness**
1. already synchronized → all `KEEP`
2. add an entry → one `ADD`, correct ordinal, nothing else
3. remove an entry → `pending_removal`, **zero filesystem actions**; second run → `ARCHIVE` to `.archive/`, name and ordinal preserved, `state='retired'`
4. remove with `removals: keep` → `FORGET`, zero filesystem actions
5. **prepend a member to a 20-entry collection under `renumber: never`** → exactly one `ADD`, zero renames
6. same, under `renumber: on_change` → renames, all carrying `ORDINAL_CHANGED`
7. a member goes private → the member is `pending_removal`; the survivors' stored ordinals do not move
8. crossing 1000 members under `never` → no width change, no renames
9. title changed remotely → one `RENAME` per file of that member, no re-download
10. changed template naming → `RENAME`, `TEMPLATE_CHANGED`, `template_version` bumped on convergence
11. **naming-engine version bump alone** → no renames proposed by a routine sync
12. changed destination → `RELOCATE`, source folder never rmdir'd, **the archive relocates with it and `retired_path` still resolves**

**Three-state discrimination**
13. remote removal vs. local deletion from an identical local observation → provably different plans
14. locally deleted, artifact bytes present → `REDELIVER`, zero network
15. locally deleted, artifact reclaimed → `REACQUIRE`
16. **manually renamed SponsorBlock-cut video (no tags)** → identified by checksum, one `RENAME`, **no second copy**
17. **a `.srt` and a `.md` renamed by hand** → identified by checksum, one `RENAME` each, zero regeneration
18. **moved into a subfolder the user created** → found, `MOVED_LOCALLY`, one `RENAME` back
19. **present at the recorded path with different bytes, identity intact (a transcode)** → `KEEP` + `content_changed`, snapshot re-baselined, **not blocked**

**Identity**
20. T0 hits → `evidence == ["snapshot"]`, zero `ffprobe` processes spawned
21. `source_ref` empty on a pre-migration row → backfilled from `member_uri`, attributed
22. **audio-only artifact, no container tags** → T0 attributes it (regression guard for the `_acquire_audio` fix)
23. **SponsorBlock-cut video retains `PURL`/`COMMENT`** after `_run_segment_cut` (Phase-0 test; fails today)
24. format conversion through `providers/ffmpeg.py` retains them too
25. snapshot says X, tag says Y → `IDENTITY_CONFLICT`, nothing mutated
26. **four files in the folder share one ref** (the `Playlist-EgalitarianMonkey` fixture) → that member is skipped, **the others converge**, `applicable` is `True`
27. two candidates, one at a recorded `delivered_path` → disambiguated, not blocked
28. the same video listed twice → two members, stable occurrence ordering
29. the occurrence count changes → `DUPLICATE_OCCURRENCE`
30. 9 % duration drift from a real cut → attributed, drift in `evidence`, not blocked
31. **`DESCRIPTION` containing another video's URL** → never read; the file stays unattributed
32. a file whose `COMMENT` is free text → unrecognized, invisible, counted
33. **a Vimeo and a YouTube resource with the same id string** → distinct `source_ref`
34. a generic-extractor member → `UNTRACKABLE_MEMBER` at plan time; tracking refuses the collection

**Safety**
35. plan writes nothing — full-tree snapshot including inodes (T4)
36. **destination present but sentinel absent** → `DESTINATION_UNAVAILABLE`, zero actions
37. more than the configured fraction of tracked files missing → refused
38. remote unavailable → not applicable
39. remote returns zero entries → refuses to archive everything without acknowledgement
40. no unrelated destination file touched (T3)
41. delete needs four gates (T5)
42. sync endpoints 404 when `CONTENT_SYNC_ENABLED=false`
43. a tracked destination equal to the delivery root is refused
44. a tracked destination that is a prefix of an existing one is refused
45. no module outside the two writers mutates the library (T1)
46. every purge leaves the library byte-identical (T2)

**Apply mechanics**
47. collision-heavy renumber: ten members, three sharing a title, a 3-cycle → converges, no `-1`, nothing lost
48. rename target held by an unmanaged file → skipped `TARGET_OCCUPIED`, never counter-suffixed
49. **case-only retitle on a case-insensitive tmpdir** → renames, not blocked
50. **cross-device relocate with `os.link` and `os.replace` both failing** → **no zero-byte file at the target**, source intact
51. **a lost claim during a rename** → the source file still exists
52. hard links unavailable (`os.link` monkeypatched to raise) → the degraded path renames correctly and exclusively, or the action is `RENAMES_UNSUPPORTED`
53. a symlink in the destination → never a candidate, never converted
54. apply killed mid-run → `requeue_running` recovers; the next plan reports `recovering_from`; `.content-sync-*` and `.staging-*` swept; nothing lost
55. an unrelated job delivers into the folder mid-apply → only the affected action is skipped as stale
56. two concurrent applies on one collection → the second is refused
57. apply an empty plan → no-op, job `succeeded`, zero mutations

**Pipeline integrity**
58. `ADD` goes through `build_plan` — the spawned job's plan has ordinary acquisition steps and **no** `collection.member` step
59. `ADD` carries `execution.reuse_existing = False`
60. **template `video + audio`: the member added by sync is named identically to one produced by `each_item`**
61. `ADD` produces `007 - Title.mkv`, not `Title.mkv`
62. `ADD` for subtitles produces `007 - Title - en.srt` **and** `007 - Title - fr.srt`, both recorded as file rows
63. **a base name long enough to truncate** → the language suffix survives; `en` and `fr` do not collide
64. **12 new members × 3 outputs → at most 12 jobs submitted**
65. `ADD` whose canonical name is held by foreign content → skipped, **no `-1` created**
66. **apply an `ADD`, wait for the job, sync again → empty plan, snapshot rows present, zero new files**
67. sync repeated after a successful apply → identical empty plan

**Adoption**
68. a HomeTube-produced file with `COMMENT` → `ADOPT`, zero download
69. adoption does not rename in the same action; the next plan proposes the rename
70. an untagged, unmatched file → not adopted, counted, never named in the response
71. a duplicate resolution is remembered across syncs

---

## M. Recommendation

### M.1 Architecture to adopt

1. **`artifacts.source_ref`**, indexed, namespaced by the provider **site** (yt-dlp's `extractor_key` / `ie_key`, captured into `NormalizedResource.provider_namespace` and `CollectionEntry.provider_namespace`), filled for every artifact through the planner's existing `step_resource_key` propagation chain and stamped on member artifacts by `CollectionMemberRunner`. One shared `source_ref` / `source_ref_from_url` pair serves both identity tiers. Backfilled once from `provenance.attributes.member_uri`. Worth shipping even if sync is cancelled.
2. **A persistent, explicitly created `TrackedCollection`**, with destinations, members and **files** in separate tables, because invariant 3 is unsatisfiable without state A, that state is not reconstructible from existing rows, and one output legitimately produces several files per member.
3. **Ordinals are assigned at first convergence, not observed.** `renumber: never` by default. This is the difference between one action and two hundred on the feature's primary workload, and it is a user-visible policy, not an implementation detail.
4. **One namer.** The sync planner resolves a single `NamingPlan` over the whole template with `scope=each_item` intact, and both RENAME targets and ADD names read from it. ADD submits one job per member carrying **every** output, with `delivery.filename` fed from `item_bases` on each output and `reuse_existing=False`.
5. **A five-tier identity ladder — snapshot, artifact row, checksum, container tag, duration** — in which only the snapshot (and a completed adoption) confers authority. Blocking is per action; the rest of the collection converges.
6. **A dedicated `rename_claimed` primitive**, not a repurposed `claim_with`: regular files only, same-inode short-circuit, `st_dev` checked up front, placeholder cleaned on failure, safe failure direction (two names for one inode, never zero).
7. **The apply is a job** (`jobs.kind='sync'`), so `job_steps` is the intent record, `job_events` is the append-only journal, SSE and `requeue_running` come for free, and INV-004/INV-006 are satisfied rather than renamed around.
8. **Archive by default, after a grace of consecutive absences, inside the destination; delete behind four gates and an operator flag.**
9. **A carved, bounded, tested exception in ADR 0023 §1** whose governing sentence is: *Content may mutate a file in the delivery library only when it can name the record that says it delivered that file there, or the moment the user personally adopted that exact file. A tag inside a file the user could have written is evidence, never permission.*

### M.2 ADRs to write and amend

**Numbering note:** `0026` is already taken by *What a security label can honestly mean here* (proposed, 2026-08-23). The new ADR is **0027**, filed as `docs/architecture-decisions/0027-playlist-synchronization.md`. Document 2 is written accordingly.

- **ADR 0027 — Tracked collections and synchronization** (new). Carries the durable-identity prerequisite, the persistent binding, the three-state model, assigned ordinals, the identity ladder with attribution-is-not-authority and the "moved out of the folder is not findable" non-capability, the apply-as-job decision, and the statement that shipping this reopens ADR 0024.
- **ADR 0023 §1** — replaced verbatim with §G.2. Header notes that the carve-out ships with the first tests that ever enforced the invariant. **Its eligibility section also amended** (§G.3): an artifact referenced by a live snapshot row is not reclaimable by default.
- **ADR 0017** — "Not retroactive" amended per §G.4, bounded by `naming_version`.
- **ADR 0019** — two additions: *"A member's durable identity is `artifacts.source_ref`; `resource_key` is not it and never was"*, and a correction to `:202-203`, which claims re-submitting a playlist is cheap because *"reuse already deduplicates the work per member"* — false with the shipped code default (`cache_enabled = False`, `config.py:48`) and, where the cache *is* enabled, still a miss for members because their signatures fold in `member_index`/`member_total`. Sync is the feature that turns that dormant inaccuracy into a cost model.
- **ADR 0024** — reopened, not watch-listed: an unauthenticated primitive that can archive or delete pre-existing user files is trigger 4 arriving today. The mitigation (`CONTENT_SYNC_ENABLED` default off, `CONTENT_SYNC_ALLOW_DELETE` default off, the delivery root never a destination) is recorded there.
- **`docs/architecture/invariants.md`** — INV-020 per §G.5.
- **`docs/contract.md`** — §9 records where `TrackedCollection` / `SyncPlan` / a sync `Job` sit against the four core concepts, and one line disambiguating the feature's "sync" from the reserved `execution.mode: "sync"`.
- **`work/discoveries.md`** — the `derive_member_request` docstring/behaviour mismatch, if it is not fixed in Phase 0.

### M.3 Phases

| Phase | Content | Gate |
|---|---|---|
| **0 — Make identity real and the primitives safe** | `provider_namespace` on both models from `extractor_key`/`ie_key`; the shared `source_ref` functions; `artifacts.source_ref` + index + propagation + member stamping + backfill (migration 6). `_acquire_audio` gets `embedding_args`; `_run_segment_cut` gets `-map_metadata`; a test asserts the tags survive every acquisition and post-processing path. `bind_filename` truncation budget so qualifier and language survive. `rename_claimed`, plus the `claim_with` degraded-branch cleanup. `derive_member_request` carries `preferences`, `constraints`, `execution` (fixed once, both paths). `analyze_sources(..., max_age=…)` and `refresh` on `POST /analyses`. | Tests 22, 23, 24, 33, 49, 50, 51, 63. **Ships independently; every item is a bug fix or a capability that stands alone.** |
| **1 — Enforce the invariant that does not exist yet** | ADR 0023 §1 + eligibility, ADR 0017 amendment, INV-020, tests T1–T5 written against **current** code (they all pass today, since nothing mutates the library). | Merged before any sync code. The invariant is enforced before the exception exists. |
| **2 — Observe** | The four tables, create/list/get/patch/delete, sentinels, prefix-disjointness, the three-state observer, the sync planner, `SyncPlan`, plan endpoints. **No apply.** | The whole diff and identity matrix (1–34, 68–71) passes with **zero** filesystem mutation. Test 35 is the gate. Shipping this alone is not a half-feature: *"tell me what changed in my playlist"* is useful on its own, and it puts the diff engine in front of real libraries before anything is allowed to move. |
| **3 — Converge** | Apply as a `kind='sync'` job, steps and events as the journal, `rename_claimed`, cycle breaking, archive, redeliver, adoption, write-back, recovery. Delete still absent. | Tests 36–40, 42–71 except the delete ones. |
| **4 — Clients** | SDK models, CLI verbs, HomeTube panel. | — |
| **5 — Delete** | Four gates behind `CONTENT_SYNC_ALLOW_DELETE`, default off. | Test 41 (T5). |

### M.4 Major risks

1. **The metadata tier is much thinner than the spec assumed, and Phase 0 only helps the future.** Every SponsorBlock-cut and audio-only file already in a library is permanently anonymous to the container tier. The checksum tier is what makes those files adoptable at all, and it works only for files Content delivered and recorded. Coverage on existing libraries will be lower than the HomeTube experience suggests, and the UI must say so honestly rather than reporting those files as new.
2. **Duplicate accumulation is already happening**, exactly as ADR 0025 predicted: eighteen delivered paths for six members in the maintainer's own library. Blocking `TARGET_OCCUPIED` and refusing occupied ADDs stops sync from creating more; the existing ones need `ADOPT` plus a remembered duplicate resolution, or the first thing every existing user sees is a plan full of skips.
3. **Hashing and `ffprobe` cost at scale.** A 500-member 4K collection is terabytes. The `(size, mtime_ns)` gate keeps the common case cheap, but a coarse-mtime filesystem or an `rsync -a` restore triggers a full rehash; the sweep is one `ffprobe` per unattributed file. Bound it, report `verification: cheap|full`, scan once per plan and index by ref, and never do any of it inside a request handler.
4. **The unauthenticated standing binding.** V1 fires nothing on its own, but the object exists and is what a scheduler would attach to. Mitigated by the enable flags and written into both ADRs; not solved.
5. **`jobs.kind` is a real contract change.** Additive and defaulted, but it touches the shape clients read. If the maintainer rejects apply-as-job, the fallback is a separate run table whose transitions are declared in `domain/` behind an `ensure_*_transition` and whose per-action log is append-only with a sequence — strictly more code for strictly less capability.
6. **A template over-describes what any single ADD runs**, and its reserved-field dispositions are evaluated at creation while the derived request's are evaluated at ADD time. Validating one derived member request at creation closes most of it; the residue is the same cost ADR 0025 §3 already accepted, at an unbounded lifetime.

### M.5 What must be approved before feature code begins

1. **`artifacts.source_ref` as the durable identity** — namespaced by the provider *site* (`extractor_key`), filled for every artifact, backfilled from `member_uri`, documented as stable and the deliberate opposite of `resource_key`.
2. **A persistent `TrackedCollection`, never created implicitly**, with the four-table shape of §B.5 — and specifically that the snapshot's unit is a **file**, not a member.
3. **`renumber: never` as the default** — the ordinal is assigned at first convergence, removals leave gaps, renumbering is an operation the user asks for. This is the single most consequential user-visible choice in the design.
4. **The ADR 0023 §1 replacement wording in §G.2, verbatim**, including clause 4 (attribution is not authority), clause 5's named scratch exception, and the acknowledgement that its enforcement is being written for the first time.
5. **The ADR 0023 eligibility amendment and the ADR 0017 amendment** bounded by `naming_version`.
6. **`ADD` as one job per member carrying the whole template**, named from a single `NamingPlan` via `delivery.filename` on every output — i.e. that the engine setting `delivery.filename` on an internally derived request is compatible with INV-010.
7. **The apply is a job** (`jobs.kind`), with `job_events` as the journal — or an explicit decision to the contrary, with INV-004/INV-006 satisfied some other way.
8. **Sync is off by default and delete is off by default**, both by operator setting; the delivery root can never be a tracked destination; and shipping this **reopens** ADR 0024 rather than being added to its watch list.
9. **The scope limit**: "moved out of the tracked destination" is not findable, and Content will not scan the library to find it.
10. **Skipped-means-skipped**: an unresolved identity blocks one action and never the plan, and ambiguity never resolves into a best guess.
