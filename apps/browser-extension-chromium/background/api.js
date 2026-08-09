// The /api/v1 client. The ONLY module that calls fetch().
//
// Why it lives in the service worker and not in the popup or a content script:
// the engine sends no CORS headers by default (`CONTENT_CORS_ORIGINS` is empty
// — "curl/SDK never need it"), and a preflight OPTIONS answers 405 because
// there is no CORS middleware to handle it. A page-context fetch is therefore
// blocked outright. An extension service worker holding `host_permissions` for
// the target origin is exempt from CORS, so the extension works against a
// stock engine with nothing for the operator to configure.
//
// There is no Python SDK here and there cannot be (ADR 0016): this speaks the
// published contract directly, which is what docs/contract.md §9 exists to make
// safe to depend on.

/** A failure the popup can render: never a raw Response, never an HTML page. */
export class ApiError extends Error {
  constructor(message, { status = 0, code = "", path = "" } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.path = path;
  }
}

/**
 * Read the contract's error body.
 *
 * Since prompt 18 there is exactly one 422 shape — `{detail: {valid, phase,
 * errors: [{code, path, message}]}}` — covering both schema violations and the
 * engine's own refusals, so one reader handles every rejection.
 */
function describe(status, body) {
  const detail = body && body.detail;
  if (detail && Array.isArray(detail.errors) && detail.errors.length > 0) {
    const first = detail.errors[0];
    const extra = detail.errors.length - 1;
    const suffix = extra > 0 ? ` (+${extra} more)` : "";
    return new ApiError(`${first.message}${suffix}`, {
      status,
      code: first.code || "",
      path: first.path || "",
    });
  }
  if (typeof detail === "string") {
    return new ApiError(detail, { status });
  }
  return new ApiError(`The engine answered HTTP ${status}.`, { status });
}

async function request(backendUrl, path, { method = "GET", body } = {}) {
  const url = `${backendUrl}/api/v1${path}`;
  let response;
  try {
    response = await fetch(url, {
      method,
      headers: body ? { "content-type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (cause) {
    // No status: DNS, refused connection, or a missing host permission. The
    // three are indistinguishable from here, so say what to check.
    throw new ApiError(
      `Cannot reach the engine at ${backendUrl}. Check it is running, that the ` +
        `address is right, and that the extension has permission for it.`,
      { code: "unreachable" },
    );
  }

  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null; // an HTML error page from a proxy, say
    }
  }
  if (!response.ok) throw describe(response.status, payload);
  return payload;
}

export const api = {
  health: (backend) => request(backend, "/health"),
  config: (backend) => request(backend, "/config"),
  folders: async (backend) => (await request(backend, "/folders")).folders || [],
  capabilities: (backend, sources) =>
    request(backend, "/capabilities", { method: "POST", body: { sources } }),
  submit: (backend, body) => request(backend, "/jobs", { method: "POST", body }),
  job: (backend, id) => request(backend, `/jobs/${id}`),
  artifacts: (backend, id) => request(backend, `/jobs/${id}/artifacts`),
  cancel: (backend, id) =>
    request(backend, `/jobs/${id}/cancel`, { method: "POST" }),
};

/** The browser-facing download URL for an artifact, as the UIs build it. */
export function artifactUrl(backendUrl, artifactId) {
  return `${backendUrl}/api/v1/artifacts/${artifactId}/content`;
}
