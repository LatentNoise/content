<!-- markdownlint-disable MD033 -->

# HomeTube — the YouTube interface for Content

Paste a URL, choose what you want from it, watch the job land in your library.

A **client** of the Content API (`/api/v1`), not a backend. It takes up the
HomeTube use case (a URL in) with Content's versatile outputs (video, audio,
subtitles, thumbnail, metadata, transcript, summary): analyze → choose → launch
→ follow live → find the files in your library. No business logic lives here —
the engine validates, plans and executes, and the UI is **capability-driven**
(it renders what `/capabilities` resolves for your source, so a capability the
engine gains appears on its own). It speaks HTTP only through the SDK
(`content_sdk`).

## Configuration

**HomeTube has no settings of its own.** Everything it does is driven by the
engine's configuration, so all of the following goes in the `.env` beside your
`docker-compose.yml` — never here. The
[deployment guide](../../docs/operations/deployment.md#configuration-a-root-env-not-versioned)
lists every variable Content accepts; this table is the subset that changes
what HomeTube shows you.

| Variable | Default | Effect in HomeTube |
| --- | --- | --- |
| `CONTENT_DELIVERY_DIR_HOST` | `./playground/output` | The library finished files are copied into. Its **sub-folders become the destination choices** in the form, so a `Talks/`, `Music/`, `Kids/` layout on your NAS shows up as those options |
| `CONTENT_LANGUAGE_PRIMARY` | — | The language you speak (`fr`). First in the audio order after the original, and pre-selected |
| `CONTENT_LANGUAGES_SECONDARIES` | — | Comma-separated (`en,es`). Offered and pre-selected after the primary |
| `CONTENT_VO_FIRST` | `true` | Put the source's original voice ahead of your own languages |
| `CONTENT_LANGUAGE_PRIMARY_INCLUDED_IN_SUBTITLES` | `true` — but the shipped `.env.example` sets `false` | Whether your primary language is also pre-checked among subtitles |
| `CONTENT_CREDENTIALS` | — | `youtube=/config/cookies.txt`. Unlocks age-restricted, private or members-only videos. HomeTube only ever displays the **id**; the cookie file stays on the server and never enters a request |
| `COMPOSE_PROFILES` | `hometube,studio` | Which UIs start. Set to `hometube` to run this one alone |
| `HOMETUBE_PORT` | `8501` | The host port |
| `CONTENT_DELIVERY_DEFAULT` | `false` (compose: `true`) | Whether every artifact is copied into the library by default |

### Language preferences, by example

The rule is short — original voice (if enabled), then your primary, then your
secondaries, keeping only what the source actually offers — but the effect is
easier to read than the rule. With:

```bash
CONTENT_LANGUAGE_PRIMARY=fr
CONTENT_LANGUAGES_SECONDARIES=en,es
CONTENT_VO_FIRST=true
CONTENT_LANGUAGE_PRIMARY_INCLUDED_IN_SUBTITLES=false
```

on a Japanese talk offering `ja`/`en` audio and `ja`/`fr`/`en`/`de` subtitles:

| | What you get | Why |
| --- | --- | --- |
| **Audio order** | `ja, en` | The original first (`VO_FIRST=true`), then your languages; `fr` and `es` are dropped because this source has no such track |
| **Audio pre-selected** | `ja, en` | Everything wanted that exists |
| **Subtitles pre-checked** | `en` only | The original voice never applies to subtitles; `de` is not one of your languages; `fr` is excluded by `…_INCLUDED_IN_SUBTITLES=false` |

That last flag carries the original HomeTube semantics: someone fluent in
French does not need French subtitles, but still wants the English and Spanish
ones. It excludes **only** the primary — the secondaries keep pre-filling. Set
it to `true` and the subtitles become `fr, en`.

Two invariants worth relying on: nothing is ever pre-selected that the source
does not offer, and no default is final — everything stays editable in the form
before you launch.

### Cookies for restricted videos

Put the cookie file where the engine can read it and name it:

```bash
CONTENT_CREDENTIALS=youtube=/config/youtube_cookies.txt
```

The compose file already mounts `./config` into the container at `/config`, so
dropping `youtube_cookies.txt` there and restarting is enough. A credential
selector then appears in HomeTube, preselecting `youtube` when it exists —
because cookies all but decide whether a given YouTube download works.

## Running it

With the published images, HomeTube starts as part of the stack (see the
[Quick start](../../README.md#quick-start)) on <http://localhost:8501>.

<details>
<summary><b>Running from a clone, for development</b></summary>

The backend must be running (see the repository root). Then:

```bash
cd apps/web-hometube
pip install "streamlit>=1.40" -e ../../packages/python-sdk
CONTENT_API_URL=http://localhost:8010 streamlit run app.py
```

- `CONTENT_API_URL` — the backend as seen by **this process** (its HTTP calls).
- `CONTENT_PUBLIC_API_URL` — the backend as seen by the **browser** (the
  artifacts' download links). Defaults to `CONTENT_API_URL`. Under Docker the
  two differ: an internal service name (`http://content:8000`) versus a host
  port (`http://localhost:8010`).

From the repository root, `docker compose up --build` runs the whole stack
from source instead.

</details>
