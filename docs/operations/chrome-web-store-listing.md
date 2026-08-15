# Chrome Web Store — the listing, ready to paste

Everything the submission form asks for, written and reviewed here rather than
improvised into a web form at midnight. Nothing in this document publishes
anything: enrolment, the developer fee, the upload and the submit are the
maintainer's, in a browser, as YannOrieult.

Companion to [browser-extension-distribution.md](browser-extension-distribution.md),
which explains *whether* to publish. This one assumes the answer is yes and
prepares the material.

## Identity

| Field | Value |
| --- | --- |
| Item name | **HomeTube for Content** (see *Name risk* below) |
| Category | Productivity |
| Language | English (UK/US neutral) |
| Homepage | `https://github.com/LatentNoise/content` |
| Support URL | `https://github.com/LatentNoise/content/issues` |

## Short description — 132 characters maximum

> Send the video or page you are watching to your own self-hosted Content engine, and get it filed in your library in one click.

(129 characters.)

## Full description

> **HomeTube for Content sends the page you are watching to your own server.**
>
> Content is a self-hosted engine that turns a link into what you actually want
> from it — the video, the audio, subtitles, a transcript, a summary, a PDF —
> and files the result in your media library with a readable name. This
> extension is its browser front door: open a video, click the icon, choose what
> to produce, and the job runs on your machine, not on someone else's.
>
> **You need your own Content engine.** This extension does nothing on its own:
> it is a client for a server you run (`docker compose up -d`, one command, see
> the project page). Without an engine to talk to, it has nothing to send to.
>
> **What it does**
> • Reads the address of the current tab and normalises it — youtu.be links,
>   Shorts and embeds become the canonical watch URL.
> • Asks your engine what that source can actually produce, and offers only
>   that. Anything unavailable stays visible with the server's reason.
> • Proposes a filename from the video's own title, and offers your library's
>   existing folders — or a new one — as the destination.
> • Submits the job, follows it live, and tells you where each file landed.
>
> **What it does not do**
> • It does not download anything itself, and contains no yt-dlp or ffmpeg.
> • It sends nothing to the developer or to any third party. There is no
>   analytics, no account, and no telemetry.
> • It does not read page content. It uses the tab's URL, nothing else.
>
> Free and open source, AGPL-3.0-or-later. The whole extension is a handful of
> unminified files you can read in ten minutes.

## Single-purpose statement

Required by the store, and it must be one purpose:

> The extension has a single purpose: to submit the URL of the current tab to a
> Content engine that the user hosts themselves, and to display the progress and
> result of that submission.

## Permission justifications

Reviewers read these individually. Each answers "why is this the minimum".

**`activeTab`** — The extension needs the URL of the tab the user is looking at
when they click the toolbar icon. `activeTab` grants that only for that tab and
only in response to that click, which is narrower than any host permission over
browsing. No page content is read.

**`storage`** — Stores the user's own settings: the address of their engine, the
preselected output type, a default destination folder and a quality ceiling.
Stored with `chrome.storage.sync` so the settings follow their signed-in
profile. No browsing data of any kind is stored.

**`host_permissions: http://localhost:8010/*`** — The default address of a
Content engine running on the user's own machine. This is the only host granted
at install time, and it is a loopback address: it cannot reach anything outside
the user's computer.

**`optional_host_permissions: http://*/*, https://*/*`** — *The one a reviewer
will stop on, so it is answered at length.*

> Content is self-hosted software: the server belongs to the user, and its
> address is one they choose. It is commonly a machine on their home network
> (`http://192.168.1.20:8010`), a hostname on a private network, or a domain of
> their own behind a reverse proxy. There is no way to enumerate those addresses
> in advance, so no fixed host permission can express "the server this user
> runs".
>
> These permissions are **optional and never requested at install time**. They
> are requested only when the user has typed their own server address into the
> options page and pressed Save, and the request names that one origin — the
> pattern is broad, the grant is not. A user who runs the default
> `localhost:8010` is never asked for anything.
>
> The extension makes network requests to exactly one origin: the engine address
> the user configured. It never contacts the developer, an analytics service, or
> any other host.

## Privacy — data-use declaration

Answers to the store's data questions, all of them the same answer:

| Store question | Declared |
| --- | --- |
| Personally identifiable information | **Not collected** |
| Health information | Not collected |
| Financial and payment information | Not collected |
| Authentication information | Not collected |
| Personal communications | Not collected |
| Location | Not collected |
| Web history | **Not collected.** The active tab's URL is sent to the user's own server at the moment they click, and is not stored, logged, or transmitted anywhere else |
| User activity | Not collected |
| Website content | **Not collected.** The extension does not read page content |

Certifications required by the form, all truthful:

- data is not sold to third parties;
- data is not used or transferred for purposes unrelated to the single purpose;
- data is not used or transferred to determine creditworthiness or for lending.

**Privacy policy URL.** The store requires one even when nothing is collected.
Rather than invent a hosting arrangement, publish
`docs/operations/extension-privacy-policy.md` through the repository's own
GitHub Pages, or point at the file on GitHub — a stable, public URL under the
project's control. Proposed text is one paragraph: the extension stores settings
locally, transmits the current tab's URL only to the server the user configures,
and its author receives nothing. **Decide the URL before submitting**; a policy
link that 404s is a rejection.

## Name risk, and the fallback decided in advance

"HomeTube for Content" contains no Google or YouTube mark, and "HomeTube" is an
established name for this project's lineage — but brand-similarity review is
mechanical and "…Tube" is exactly the pattern it flags. The argument, if
challenged:

> HomeTube is the name of an existing open-source project by the same author,
> published since 2025 and distributed as a container image with over 300,000
> pulls. The extension carries no YouTube or Google branding, uses none of their
> marks in its icon or screenshots, and states on its listing that it requires a
> self-hosted server. It is not presented as affiliated with any video platform.

**Agreed fallback, to be used without improvising if the name is refused:**
`Content Companion` — no platform-adjacent morpheme, still says what it is
beside the engine's name. It requires changing `manifest.json`'s `name` and the
listing title only; the extension's internal branding and the repository stay as
they are.

## Technical pass

Checked against the current manifest (version 0.4.1):

| Requirement | State |
| --- | --- |
| Manifest V3 | ✅ `manifest_version: 3`, module service worker |
| `name`, `description`, `version` | ✅ present; description is 68 chars, within the 132 limit |
| `homepage_url` | ✅ the repository |
| Icons 16/32/48/128 | ✅ real PNGs, verified by `tests/test_browser_extension_chromium.py` |
| Version format | ✅ `0.4.1`, dot-separated integers — the store accepts it and it matches the release exactly |
| No remote code | ✅ every script is packaged; no CDN, no `eval` |
| Minimum Chrome version | ✅ 102 |
| Store-specific manifest differences | **None needed.** The zip loaded unpacked and the zip uploaded to the store are the same artifact — no build variant, so `make extension-zip` stays as it is |

The one thing to watch across releases: the store rejects an upload whose
version is not strictly greater than the published one. Since the extension's
version moves in lockstep with the monorepo (`make version`), any release bumps
it, and that is compatible.

## Assets

| Asset | Size | State |
| --- | --- | --- |
| Small promo tile | 440×280 | **Produced** — `media/store-promo-440x280.png`, from the tracked SVG source beside it |
| Screenshot | 1280×800 | **Must be captured by the maintainer** — the store requires a real screenshot of the working product, and a mock-up would be both a rejection risk and a lie. Load the extension unpacked, open a video, click the icon, and capture the popup over the page |
| Icon 128×128 | 128×128 | ✅ already in the extension, store-compliant as-is |

At least one screenshot is mandatory; up to five are allowed. Two more worth
capturing once the first exists: the options page with a real engine address,
and the popup following a running job.

## What remains manual

Everything that costs money or speaks publicly:

1. Enrol as a Chrome Web Store developer (one-off fee) — **maintainer**.
2. Capture the screenshot(s) — **maintainer**.
3. Decide the privacy-policy URL and publish the page — **maintainer**.
4. Upload the zip from the release, paste this document's texts, submit.
5. Review takes days to weeks and can come back with questions; the permission
   justification above is written to answer the likeliest one before it is asked.
