// Where the engine is, and what to preselect. `chrome.storage.sync` so the
// settings follow a signed-in profile between machines.

export const DEFAULTS = {
  backendUrl: "http://localhost:8010",
  defaultPreset: "video",
  defaultFolder: "",
  maxHeight: 1080,
  container: "mkv",
  credentialId: "",
};

export async function readSettings() {
  const stored = await chrome.storage.sync.get(DEFAULTS);
  return { ...DEFAULTS, ...stored, backendUrl: trimUrl(stored.backendUrl) };
}

export async function writeSettings(patch) {
  const next = { ...patch };
  if (typeof next.backendUrl === "string") {
    next.backendUrl = trimUrl(next.backendUrl);
  }
  await chrome.storage.sync.set(next);
  return readSettings();
}

export function trimUrl(url) {
  return String(url || DEFAULTS.backendUrl).trim().replace(/\/+$/, "");
}

/**
 * The origin pattern this backend needs in `host_permissions`.
 *
 * The manifest grants only `http://localhost:8010/*` statically. Any other
 * address is an *optional* permission the user grants on demand from the
 * options page, so the extension never ships with blanket host access.
 */
export function originPattern(backendUrl) {
  try {
    return `${new URL(backendUrl).origin}/*`;
  } catch {
    return "";
  }
}

export async function hasBackendPermission(backendUrl) {
  const origins = [originPattern(backendUrl)].filter(Boolean);
  if (origins.length === 0) return false;
  return chrome.permissions.contains({ origins });
}
