# config/ — external files the engine needs at runtime

This folder is mounted read-only into the backend container at `/config`. It
is the one place to drop files that come from *outside* the project and that
the engine must be able to read: authentication cookies today, another site's
cookies or a custom PDF font tomorrow.

Nothing in this folder is ever committed (the repository's `.gitignore` keeps
everything here local except this README) and nothing here ever enters a
Docker image (`.dockerignore`). The API reports each credential's id, path,
presence and last-modified time — so the UIs can show that your cookies are
wired and fresh — but the file *contents* never leave the server.

## YouTube cookies, step by step

Cookies are what unlock age-restricted and private videos, and they make
ordinary downloads more reliable (YouTube increasingly rate-limits and
challenges anonymous clients). Inside Docker there is no browser, so the
cookies come from a file you export once.

The `youtube` credential is **declared by default** — HomeTube in Docker all
but needs it — so until the file exists, HomeTube and the Console show a
clear "cookies file missing" flag with these exact instructions. Two steps
close it:

1. **Export** a Netscape-format `cookies.txt` from a browser where you are
   signed in to YouTube (any "export cookies.txt" browser extension does
   this — export for `youtube.com`), and save it here as:

   ```text
   config/youtube_cookies.txt
   ```

   The engine reads it at the container path `/config/youtube_cookies.txt` —
   same file, two addresses: `./config` is the folder on your machine, and
   the compose mount grafts it into the container at `/config`.

   From a clone, `make cookies FILE=~/Downloads/cookies.txt` does this step
   for you: it warns if the file is not a Netscape export or carries no
   YouTube cookie, keeps any previous export as `youtube_cookies.txt.previous`,
   and tells you what to run next. Getting the *filename* wrong is the classic
   failure — the default `CONTENT_CREDENTIALS` looks for exactly
   `youtube_cookies.txt` — and that is the mistake the target removes.

2. **Refresh the stack**: `make docker-update`.

The flag turns into "✅ ready · updated …". To turn the feature off entirely,
set `CONTENT_CREDENTIALS=` (empty) in `.env`.

HomeTube's "🍪 Cookie Management" offers `youtube` in its Authentication
selector, and API clients pass it per source:

```json
{"id": "main", "type": "url", "uri": "https://…", "auth": {"credential_id": "youtube"}}
```

The mount stays read-only on purpose: yt-dlp rewrites cookie jars on exit, so
the engine copies the file to its own writable location before every use —
your export is never modified.

## When they stop working

Cookies expire. Nothing warns you in advance, so the way you find out is a
download that fails, and the engine's failure message names the cause rather
than leaving you to read a yt-dlp log: it distinguishes "no credential
configured", "declared but the file is not there, so this ran anonymously"
and "the cookies were used and still refused — they have expired". Re-run
`make cookies FILE=…` with a fresh export and `make docker-update`.

Refreshing is also the answer to a subtler symptom: YouTube increasingly
challenges anonymous clients, so a video that used to download and now fails
with a "sign in to confirm you're not a bot" is usually stale cookies rather
than a broken engine.

## Why cookies are not uploaded through the UI

It would be the obvious convenience, and it is deliberately absent. The V1 API
has no authentication (ADR 0024), so an upload endpoint for credentials would
let anyone who can reach the port install a cookie jar — and the mount is
read-only precisely so the engine cannot write to this folder. Dropping a file
into a directory you own is a step the operator takes once, with the
permissions they already have.

## More than one credential

`CONTENT_CREDENTIALS` takes comma-separated `id=path` pairs, so several sites
(or several accounts) coexist:

```bash
CONTENT_CREDENTIALS=youtube=/config/youtube_cookies.txt,vimeo=/config/vimeo_cookies.txt
```

Each id becomes selectable independently; a source only uses the credential it
names.

## Other files that belong here

- **A custom PDF font** — point `CONTENT_PDF_FONT=/config/fonts/MyFont.ttf`
  at it when the built-in faces cannot draw your script (CJK, for example).
- Anything similar a future runner needs as a file: keep it in this folder,
  reference it by its `/config/...` path, and it survives image rebuilds.
