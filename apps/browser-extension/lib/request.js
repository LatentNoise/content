// Form state -> a canonical GenerationRequest.
//
// The only place in the extension that knows the contract's shape. Kept pure so
// `fixtures/` can be generated from it and validated in Python against the real
// `GenerationRequest` model — contract drift then fails `make validate` instead
// of failing in someone's browser.
//
// Deliberately absent: any opinion about what a source *can* produce. The popup
// only offers output types the server resolved as producible, so this function
// never has to guess (the D-37 lesson: a client that keeps its own catalogue
// goes stale silently).

export const SCHEMA_VERSION = "1.0";

function outputId(type) {
  return `${type}_main`;
}

/**
 * @param {object} state
 *   uri            the normalised source URI
 *   outputs        output types to request (already filtered to producible)
 *   maxHeight      number|null — a ceiling, not a demand
 *   container      "" | "mkv" | "mp4" — "" leaves the source container alone
 *   subtitleLangs  string[] — required when `subtitles` is requested
 *   folder         string — delivery folder ("" = library root)
 *   filename       string — optional override, no extension ("" = the server
 *                  names the file after the video)
 *   credentialId   string — a server-side credential id, never a cookie
 */
export function buildRequest(state) {
  const source = { id: "s0", type: "url", uri: state.uri };
  if (state.credentialId) {
    source.auth = { credential_id: state.credentialId };
  }

  const outputs = [];
  for (const type of state.outputs) {
    const output = { id: outputId(type), type };

    // Delivery carries *intent only* (ADR 0018). With no folder and no
    // filename, nothing is sent: the server's delivery policy decides, and
    // the engine names the file after the video itself (ADR 0017) — the
    // client no longer fabricates either. What the user did set is passed
    // through raw; the server sanitizes names, never rejects them (D-51).
    const delivery = {};
    if (state.folder) delivery.folder = state.folder;
    if (state.filename) delivery.filename = state.filename;
    if (Object.keys(delivery).length > 0) output.delivery = delivery;

    const videoOptions = {};
    if (type === "video" && state.maxHeight) {
      // A ceiling only. The engine still picks the best format under it, and a
      // source whose height it cannot determine is attempted rather than
      // excluded (D-44).
      videoOptions.selection = { max_height: state.maxHeight };
    }
    if (type === "video" && state.container) {
      // Without this the container is whatever the best stream happens to be —
      // often `.webm`, which some players and TVs refuse.
      videoOptions.container = state.container;
    }
    if (type === "video" && Object.keys(videoOptions).length > 0) {
      output.options = videoOptions;
    }

    if (type === "subtitles") {
      // The contract requires a non-empty list; an empty one is a client bug,
      // so fail here rather than let the API reject it.
      if (!state.subtitleLangs || state.subtitleLangs.length === 0) {
        throw new Error("subtitles need at least one language");
      }
      output.options = { languages: [...state.subtitleLangs] };
    }

    outputs.push(output);
  }

  if (outputs.length === 0) {
    throw new Error("choose at least one output");
  }

  // Nothing reserved is ever sent: `execution`, `preferences` and `constraints`
  // are left out entirely so the engine applies its own defaults. Sending
  // `mode`, `priority`, `retention`, `language` or `execution_location` would be
  // refused with `option_not_supported` (docs/contract.md §9).
  return {
    schema_version: SCHEMA_VERSION,
    sources: [source],
    outputs,
  };
}
