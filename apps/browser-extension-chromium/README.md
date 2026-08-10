# HomeTube for Content — Chromium browser extension

Send the page you are watching to your Content engine, without opening a UI.

A **client of `/api/v1`**, like every other application here: it builds a
`GenerationRequest`, posts it, and follows the job. It never downloads anything
itself and never talks to yt-dlp — the engine does that.

**Chromium only** (Chrome, Brave, Edge, Vivaldi, Opera, Arc…), Manifest V3,
Chrome 102 or later. Not Firefox, not Safari — see *Why Chromium only* below.

## Install

There is no build step: what the browser loads is exactly these files.

### From a release (recommended)

1. Download `content-browser-extension-chromium-v<version>.zip` from the
   [latest release](https://github.com/LatentNoise/content/releases/latest)
   and **unzip it** — Chromium loads a folder, not a zip. (To verify the
   download: `shasum -a 256 -c SHA256SUMS.txt`, with the manifest from the
   same release.)
2. Start the engine (`docker compose up -d`) — it publishes on
   <http://localhost:8010>.
3. Open `chrome://extensions` (`brave://extensions`, `edge://extensions`…),
   turn on **Developer mode**.
4. **Load unpacked** → select the unzipped folder.
5. Open a video, click the extension.

Keep the folder where it is: Chromium loads an unpacked extension from that
path on every start. To update, download the newer zip, unzip over the same
folder, and press ↻ on the extension card.

### From a clone (for development)

Same, with step 1 replaced by *clone the repository* and step 4 pointing at
`apps/browser-extension-chromium/`. Reloading the popup picks up HTML/CSS/JS edits with
no rebuild; the ↻ button is only needed for `manifest.json` and the icons.

The zip is produced by `make extension-zip` — from `git ls-files` restricted
to the runtime entries (`manifest.json` + the five directories it references),
so only tracked, needed files are packaged and no local stray file can ride
along — and CI attaches it to every version tag automatically, beside a
`SHA256SUMS.txt` covering every asset.

### Pointing it at another engine

The popup's footer always names the engine it talks to (`192.168.21.30:8010`,
say) — the answer to "where is this sending my video?" is on screen, and
clicking it opens the settings. For an engine somewhere else (a NAS, another
port), change the address there. Chrome will ask for permission for that host:
the manifest grants only `http://localhost:8010` up front, so any other origin
is an explicit, revocable grant rather than blanket access shipped to everybody.

## What it does

- Reads the tab's address and normalises it (`youtu.be`, Shorts and embeds
  become the canonical watch URL; a `list=` on a watch URL means *that video*,
  not the playlist).
- Asks the engine what that source can produce, and offers **only that**.
  Anything unavailable stays visible with the server's reason, so you learn why
  rather than wonder where it went. Nothing about output types is hardcoded
  here — a capability the engine gains shows up on its own.
- Prefills the file name with the **naming engine's own proposal**
  (`suggested_filename` from the capabilities call, ADR 0017) — edit it or
  leave it; untouched, nothing is sent and the server names the artifacts
  itself. The destination offers the library's existing folders **plus
  "new folder…"** to type one that does not exist yet (created path-safe,
  server-side).
- Submits, then polls the job, links the artifacts and shows where each file
  landed in your library (`delivered_path`).

## Design notes

**Every network call is in the service worker.** Not a style choice: the engine
sends no CORS headers by default and answers a preflight `OPTIONS` with 405, so
a page-context `fetch` is blocked outright. A service worker holding
`host_permissions` is exempt from CORS, which is what lets this work against a
stock engine with nothing to configure. See
[ADR 0016](../../docs/architecture-decisions/0016-first-non-python-client.md).

**Polling, not SSE.** A Manifest V3 service worker is evicted when idle, and
resuming an `EventSource` across eviction is real complexity for no gain on a
job you are watching.

**No reserved fields are ever sent.** `execution`, `preferences` and
`constraints` are omitted entirely, so the engine applies its own defaults;
sending `mode`, `priority` or `retention` would be refused
(`option_not_supported`, `docs/contract.md` §9).

## Verification status — read this

Honest about what has actually been run, because the rest of this project has
spent several rounds removing claims that had not been:

| Path | State |
| --- | --- |
| Request bodies validate against the real `GenerationRequest` | **Verified** — `tests/test_browser_extension_chromium.py`, in `make validate` |
| `lib/url.js` normalisation (11 cases: Shorts, `youtu.be`, embeds, `list=`, tracking params, non-YouTube, `chrome://`) | **Verified by executing the JavaScript** — node-backed, skips cleanly without node |
| `lib/request.js` emits exactly the reviewed fixtures, and refuses subtitles with no language | **Verified by executing the JavaScript** |
| Manifest shape, permission minimality, icons are real PNGs | **Verified** — same suite |
| Every file is visible to git despite the allowlist `.gitignore` | **Verified** — same suite |
| The engine sends no CORS headers; preflight answers 405 | **Verified** against a running instance |
| JavaScript parses (`node --check`, every module) | **Verified** |
| Loading unpacked in Chrome: popup renders, capabilities listed, submit creates a real job, artifact produced | **Verified by the maintainer**, 2026-08-02 — on a YouTube video, end to end |
| Delivery into the media library, with a real filename | **Verified** (2026-08-02, pre-ADR 0018) — a request built by `lib/request.js` delivered `Example_Domain_-_Test_Page.md` into the library root |
| The ADR 0017/0018 flow (prefilled name proposal from `suggested_filename`, untouched → nothing sent and the server names; "new folder…"; popup shows the library path) | **NOT verified in a browser yet** — covered by `tests/test_browser_extension_chromium.py` and the backend suites |
| Chrome's `host_permissions` exemption for a **non-default** backend (the grant flow) | **NOT verified** — only the default `localhost:8010` path has been exercised |
| Playlists, authenticated sources, `subtitles`-only runs | **NOT verified** in a browser |

The first real download exposed two bugs — choosing the library root sent no
`delivery` block, and a missing `filename` produced `video_main.webm`. Both
classes are now solved **server-side**: the engine names every artifact after
the video itself (ADR 0017) and delivers by default when the policy is on
(ADR 0018), so the extension sends *intent only*: the filename field is an
optional override, the client-side title sanitizer (`lib/filename.js`) is
gone (D-51 — the server sanitizes, never rejects), and the popup shows where
the file landed in the library.

## Why Chromium only

Not a preference — three concrete incompatibilities, none of them worth a
compatibility layer for a single-maintainer project:

- **The CORS exemption this depends on.** The engine sends no CORS headers, so
  the extension works only because a service worker holding `host_permissions`
  is exempt. Firefox grants the equivalent to background scripts, but its MV3
  uses **event pages**, not service workers, so the one load-bearing mechanism
  is spelled differently.
- **`browser.*` vs `chrome.*`.** Firefox exposes a promise-based `browser.*`
  namespace; this code calls `chrome.*` with callbacks. A polyfill exists, and
  it is another dependency to vendor and keep current.
- **Safari** requires a native macOS wrapper app, Xcode, and an Apple developer
  account to distribute. That is a different product, not a port.

A fork is welcome to do it — the whole client is 6 small files with no build
step, and everything it knows about the contract lives in `lib/request.js`.

## Limits

- No authentication, because the API has none in V1. Cookie credentials are
  selected by id and resolved server-side — no cookie ever passes through the
  extension.
- Not published on the Chrome Web Store (yet) — see
  [docs/operations/browser-extension-distribution.md](../../docs/operations/browser-extension-distribution.md)
  for what that would involve.
