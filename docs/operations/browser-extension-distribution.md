# Distributing the browser extension

How users get "HomeTube for Content" into their browser, what publishing on the
Chrome Web Store would actually involve, and whether CI can do it.

## Today: a zip on every release

`make extension-zip` packages the extension into
`dist/content-browser-extension-chromium-v<version>.zip`, and
[`release-draft.yml`](../../.github/workflows/release-draft.yml) attaches it to
the draft release created by every version tag. A user downloads one file,
unzips it, and loads it unpacked (`apps/browser-extension-chromium/README.md` has the
five steps).

The file list comes from `git ls-files` restricted to the runtime entries
(`manifest.json`, `background/`, `icons/`, `lib/`, `options/`, `popup/`), so
the archive contains exactly the tracked files the browser loads — no
`.DS_Store`, no editor backup, no local experiment, and also no README or test
fixtures. Because CI builds it from the tag after `make validate`, it can
never be a stale hand-made copy, and a `SHA256SUMS.txt` attached beside the
assets lets a download be verified with `shasum -a 256 -c SHA256SUMS.txt`.

### Version mapping

The manifest version is the Content version, verbatim. Chrome's format is
1–4 dot-separated integers (no suffixes, no leading zeros), and Content's
`x.y.z` fits it as long as no pre-release suffix ever reaches the manifest —
which `make version-update` enforces by refusing anything that is not plain
`x.y.z`. `make version` holds the manifest and every other declaration to the
same value, so the zip name, the manifest and the release tag cannot drift
apart silently.

**What this costs the user:** Developer mode stays on, and Chromium shows a
"disable developer mode extensions" nag on some startups. Updates are manual —
download, unzip, press ↻.

## The Chrome Web Store: what it would take

Publishing is not hard, but it is not free of process either.

| Step | Reality |
| --- | --- |
| Developer account | A Google account plus a **one-time registration fee** (US$5 when this was written — check the current figure). |
| Store listing | Name, summary, description, at least one screenshot at a required size, an icon, a category, and the languages you claim. The `media/` visuals cover most of it. |
| Privacy disclosures | A **single-purpose** statement, a justification **per permission**, and a privacy policy URL. Not optional, and the most common cause of rejection. |
| Review | Days, occasionally longer. Automated checks plus human review; each update is reviewed too. |
| Updates | Upload a new zip with a higher `manifest.json` version. `make version-update` already keeps that number in step with the rest of the project. |

**The specific risk for this extension.** Its whole purpose is to talk to a
server the *user* runs, at an address only they know. That means
`optional_host_permissions: ["http://*/*", "https://*/*"]`, which reviewers
scrutinise: broad host access is the classic exfiltration shape. The honest
answers are in our favour — the permission is **optional** (nothing is granted
until the user names their own backend and accepts), the extension holds
`http://localhost:8010` by default, and no data goes anywhere except the
server the user chose. That argument has to be written into the permission
justification, and it may still take a round trip with a reviewer.

A **plain HTTP** default (`http://localhost:8010`) is also unusual for a
listing; it is correct here (a LAN service, often without TLS) and worth
stating in the justification rather than leaving a reviewer to guess.

## Can CI publish it automatically?

**Yes** — the Chrome Web Store has a REST API, and the flow is well-trodden:

1. Enable the *Chrome Web Store API* in a Google Cloud project.
2. Create an OAuth client, then mint a **refresh token** once, by hand.
3. Store four values as GitHub **secrets**: `CWS_CLIENT_ID`,
   `CWS_CLIENT_SECRET`, `CWS_REFRESH_TOKEN`, `CWS_EXTENSION_ID`.
4. In a tag-triggered job: exchange the refresh token for an access token,
   `PUT` the zip to `/upload/chromewebstore/v1.1/items/<id>`, then `POST` to
   `/chromewebstore/v1.1/items/<id>/publish`.

It is roughly twenty lines of `curl`, or an off-the-shelf action.

**But it changes the project's release posture**, which is why nothing does it
today. Publishing to a *store* is a different act from publishing an image.
`ci.yml` does push container images to GHCR on a tag — with the repository's
own `GITHUB_TOKEN`, no stored credential — while `release-draft.yml` stops at
a **draft** so a human writes the notes. A store upload would need long-lived
Google credentials in the repository's secrets, and it would put a new
extension in real users' browsers with no human in between.

## Recommendation

**Stay with the release zip for now, and revisit the store when there are users
asking for it.** Reasons, in order:

- The audience already self-hosts a Docker backend; unzipping a folder is not
  the obstacle in that journey.
- The store adds a review queue to every extension change — a real tax on a
  single-maintainer project that ships the extension and the engine together.
- The permission justification is a genuine conversation to have with a
  reviewer, and it is better had once the extension is stable.
- Nothing is lost by waiting: the same zip is what a store submission uploads.

If reach becomes the goal, the intermediate step is worth knowing: a store
listing can be **unlisted** — installable by link, invisible to search. It
still goes through review, but it skips the discovery-page expectations
(screenshots for every feature, polished copy) while giving users automatic
updates.
