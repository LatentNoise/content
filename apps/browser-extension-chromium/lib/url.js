// Turning the tab's address into the source URI the engine is given.
//
// The rule that matters: this normalises, it does not *decide*. yt-dlp supports
// a thousand sites, so anything not recognised is passed through verbatim
// rather than refused — the extension must never claim to know better than the
// engine about what a URL is.
//
// Pure and dependency-free on purpose: `tests/test_extension_contract.py`
// mirrors the table below in Python, so the two cannot disagree in silence.

/** Tracking and player noise that must never reach the engine or the cache key. */
const DROPPED_PARAMS = new Set([
  "si",
  "pp",
  "feature",
  "ab_channel",
  "utm_source",
  "utm_medium",
  "utm_campaign",
  "utm_term",
  "utm_content",
  "gclid",
  "fbclid",
]);

/** Only these schemes can be a source; `chrome://` and `file://` cannot. */
export function isSubmittable(rawUrl) {
  try {
    const { protocol } = new URL(rawUrl);
    return protocol === "http:" || protocol === "https:";
  } catch {
    return false;
  }
}

function youtubeWatchUrl(id, start) {
  const url = new URL("https://www.youtube.com/watch");
  url.searchParams.set("v", id);
  if (start) url.searchParams.set("t", start);
  return url.toString();
}

/**
 * `{ uri, kind }` for a tab address.
 *
 * `kind` is a *hint for the popup's wording only* — "collection" lets it say
 * "this playlist" instead of "this video". What the source can actually produce
 * still comes from the server, never from here.
 */
export function normalizeSourceUrl(rawUrl) {
  if (!isSubmittable(rawUrl)) return { uri: "", kind: "unsupported" };

  let url;
  try {
    url = new URL(rawUrl);
  } catch {
    return { uri: "", kind: "unsupported" };
  }

  const host = url.hostname.replace(/^www\./, "").replace(/^m\./, "");
  const start = url.searchParams.get("t") || "";

  // youtu.be/<id> — the id is the path.
  if (host === "youtu.be") {
    const id = url.pathname.slice(1).split("/")[0];
    return id
      ? { uri: youtubeWatchUrl(id, start), kind: "video" }
      : { uri: rawUrl, kind: "unknown" };
  }

  if (host === "youtube.com" || host === "music.youtube.com") {
    const path = url.pathname;

    // A Short is a video; the canonical watch URL is what yt-dlp prefers.
    const short = path.match(/^\/shorts\/([^/?#]+)/);
    if (short) return { uri: youtubeWatchUrl(short[1], ""), kind: "video" };

    const embed = path.match(/^\/embed\/([^/?#]+)/);
    if (embed) return { uri: youtubeWatchUrl(embed[1], start), kind: "video" };

    // The trap: `/watch?v=…&list=…` is *this video*, playing inside a playlist.
    // Sending the list would download the whole playlist instead of the video
    // the user is actually watching.
    if (path === "/watch") {
      const id = url.searchParams.get("v");
      if (id) return { uri: youtubeWatchUrl(id, start), kind: "video" };
    }

    // A playlist *page* really is the collection.
    if (path === "/playlist") {
      const list = url.searchParams.get("list");
      if (list) {
        return {
          uri: `https://www.youtube.com/playlist?list=${list}`,
          kind: "collection",
        };
      }
    }
  }

  // Everything else: strip the known noise and hand it over untouched.
  for (const param of [...url.searchParams.keys()]) {
    if (DROPPED_PARAMS.has(param)) url.searchParams.delete(param);
  }
  return { uri: url.toString(), kind: "unknown" };
}
