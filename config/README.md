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
cookies come from a file you export once:

1. **Export** a Netscape-format `cookies.txt` from a browser where you are
   signed in to YouTube (any "export cookies.txt" browser extension does
   this — export for `youtube.com`).
2. **Drop it here** as:

   ```text
   config/youtube_cookies.txt
   ```

3. **Declare it** in `.env` — the line ships commented, uncomment it:

   ```bash
   CONTENT_CREDENTIALS=youtube=/config/youtube_cookies.txt
   ```

4. **Refresh the stack**: `make docker-update`.

From then on, HomeTube's "🍪 Cookie Management" offers `youtube` in its
Authentication selector, and API clients pass it per source:

```json
{"id": "main", "type": "url", "uri": "https://…", "auth": {"credential_id": "youtube"}}
```

The mount stays read-only on purpose: yt-dlp rewrites cookie jars on exit, so
the engine copies the file to its own writable location before every use —
your export is never modified.

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
