# ADR 0039 — A person's last request for a source

Status: accepted (2026-09-16) · Related: 0017 (naming), 0018 (delivery), 0027
(playlist synchronization, proposed), 0030 (ownership)

## Context

Someone pastes a playlist they downloaded last month. The form starts blank:
the root folder, the default quality, the default languages. If they do not
remember exactly where it went and how, they choose differently, and the
playlist lands a second time somewhere else.

The maintainer's standalone HomeTube solved it by remembering, per URL, where
the last download went and with which settings. His request for Content was the
same, stated as the purpose: *« garder une trace claire, par utilisateur, de où
la vidéo ou la playlist a voulu être téléchargée […] pré-remplir les paramètres
qui avaient été choisis la fois d'avant […] pour ne pas re-télécharger les
choses si tu te trompes. »*

Two things stood in the way. Nothing named a source durably: `resource_key` is
published as unstable (D-12) — it moves with the provider and its version, so a
yt-dlp upgrade would make every memory forget. And the only record of past
requests was the jobs table, where a request is buried in a job a person may
delete to free space.

## Decision

### A source has a stable name: `source_ref`

Every analysed source carries `source_ref`, built from what the **site** states
rather than from how the engine fetched it:

```
<site>:<item|collection>:<id>   youtube.com:item:pXRviuL6vMY
url:<address without fragment>  when the provider knows no id
""                              when there is nothing to paste again (an upload)
```

The site is normalised (`youtu.be`, `music.youtube.com`, `www.` and `m.` all
name `youtube.com`), so the same video written two ways is one source. It is
the durable identity the playlist study (2026-08-23) asked for, published as
**stable** — the deliberate opposite of `resource_key`. It is derived from the
resource, so analyses cached before it existed carry it too.

### The last request is its own record, per person and per source

`last_requests (owner_id, source_ref) → job_id, title, request, requested_at`.
It is written **at submission**, not at success: where someone wanted a
playlist is their intent whether or not that attempt finished. The newest
request replaces the previous one. It is independent of the jobs table, so
deleting a job does not erase where a playlist lives. It never crosses owners:
the key is the pair.

What is stored is the **normalized request** — the public contract's own words
(outputs, options, delivery folder and name, source authentication) — not a
surface's private form state. Any client can read it back; HomeTube is the
first.

### One route: `GET /api/v1/last-request?source_ref=…`

Owner-scoped. Returns the request, its title, when, and the state of that job if
it still exists; `404 last_request_not_found` when nothing was asked.

### The surface proposes; the source still decides

HomeTube says it plainly above the form — *"You asked for this on 2026-09-12:
Video into `Talks` — last run succeeded"* — and prefills every field from the
remembered request. Each prefilled value falls back to the form's usual default
when the source no longer offers it (a language that disappeared, a resolution
the new analysis does not list). A remembered folder that is no longer in the
library is proposed as a new folder rather than dropped, so a renamed folder or
a missing mount is visible instead of silently landing at the root.

## Consequences

- The folder a playlist went to is known per person, which is the first input
  ADR 0027's update needs when the folder given now is empty.
- A person's history of intents is data about them: it goes with the account,
  and deleting an account deletes it.
- Not done here: forgetting one source on request, and showing the memory in
  Studio. Both are one route and one panel away when they are wanted.
