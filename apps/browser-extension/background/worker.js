// The message router. Everything the popup needs goes through here, because
// this is the context that is allowed to reach the engine (see api.js).
//
// One shape in, one shape out: `{type, payload}` -> `{ok: true, data}` or
// `{ok: false, error: {message, code, path}}`. The popup therefore never has to
// know what an HTTP status is.

import { api, ApiError, artifactUrl } from "./api.js";
import { hasBackendPermission, readSettings, writeSettings } from "./settings.js";

const TERMINAL = ["succeeded", "failed", "cancelled", "partially_succeeded"];

const HANDLERS = {
  /** Settings + whether we are allowed to talk to the configured engine. */
  async context() {
    const settings = await readSettings();
    return { settings, permitted: await hasBackendPermission(settings.backendUrl) };
  },

  async saveSettings({ patch }) {
    return { settings: await writeSettings(patch) };
  },

  async testConnection({ backendUrl }) {
    const settings = await readSettings();
    return { health: await api.health(backendUrl || settings.backendUrl) };
  },

  /**
   * Everything the popup needs to draw itself, in one round trip: what this
   * source can produce (the server decides), plus destinations and credentials.
   *
   * Folders and config are best-effort — losing them means fewer choices, not
   * a failed download, so they must not take the whole popup down.
   */
  async resolve({ uri }) {
    const { backendUrl } = await readSettings();
    const sources = [{ id: "s0", type: "url", uri }];
    const resolved = await api.capabilities(backendUrl, sources);
    const source = (resolved.sources || [])[0] || {};

    const [folders, config] = await Promise.all([
      api.folders(backendUrl).catch(() => []),
      api.config(backendUrl).catch(() => ({})),
    ]);

    return {
      analysisId: resolved.analysis_id || "",
      title: source.title || "",
      // The name the engine would give the artifacts (ADR 0017): the popup
      // offers it as an editable proposal — the client invents nothing.
      suggestedFilename: source.suggested_filename || "",
      resourceType: source.resource_type || "unknown",
      capabilities: source.capabilities || [],
      folders,
      credentials: config.credentials || [],
      // The server's own language preferences seed the subtitle choices, so the
      // extension agrees with HomeTube instead of inventing a default.
      languagePreferences: config.language || {},
    };
  },

  async submit({ request }) {
    const { backendUrl } = await readSettings();
    const created = await api.submit(backendUrl, request);
    return { jobId: created.job_id, warnings: created.warnings || [] };
  },

  /** One poll. The popup drives the interval; a service worker must not. */
  async follow({ jobId }) {
    const { backendUrl } = await readSettings();
    const job = await api.job(backendUrl, jobId);
    const done = TERMINAL.includes(job.status);
    let artifacts = [];
    if (done) {
      const produced = await api.artifacts(backendUrl, jobId).catch(() => []);
      artifacts = produced.map((artifact) => ({
        id: artifact.id,
        // The user-facing name (ADR 0017); pre-naming artifacts only have
        // the technical one.
        filename: artifact.display_filename || artifact.filename,
        // Where the delivered copy landed, relative to the library root
        // ("" = no delivered copy) — ADR 0018.
        deliveredPath: artifact.delivered_path || "",
        mediaType: artifact.media_type,
        sizeBytes: artifact.size_bytes,
        url: artifactUrl(backendUrl, artifact.id),
      }));
    }
    return {
      status: job.status,
      error: job.error || "",
      steps: (job.steps || []).map((step) => ({
        id: step.step_id,
        operation: step.operation,
        status: step.status,
        error: step.error || "",
      })),
      artifacts,
      done,
    };
  },

  async cancel({ jobId }) {
    const { backendUrl } = await readSettings();
    await api.cancel(backendUrl, jobId);
    return {};
  },
};

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const handler = HANDLERS[message && message.type];
  if (!handler) {
    sendResponse({
      ok: false,
      error: { message: `Unknown request: ${message && message.type}` },
    });
    return false;
  }
  handler(message.payload || {})
    .then((data) => sendResponse({ ok: true, data }))
    .catch((error) => {
      const isApi = error instanceof ApiError;
      sendResponse({
        ok: false,
        error: {
          message: (error && error.message) || "Something went wrong.",
          code: isApi ? error.code : "",
          path: isApi ? error.path : "",
        },
      });
    });
  return true; // keep the channel open for the async reply
});
