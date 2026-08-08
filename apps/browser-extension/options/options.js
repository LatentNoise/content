// Settings, plus the permission request for a non-default backend.
//
// Saving is where the extension asks for host access: the manifest grants only
// http://localhost:8010 statically, so pointing at a NAS is an explicit,
// revocable grant rather than a blanket one shipped to everybody.

const el = (id) => document.getElementById(id);

function send(type, payload = {}) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage({ type, payload }, (reply) => {
      if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
      else if (!reply || !reply.ok)
        reject(new Error((reply && reply.error && reply.error.message) || "Failed."));
      else resolve(reply.data);
    });
  });
}

async function load() {
  const { settings } = await send("context");
  el("backend-url").value = settings.backendUrl;
  el("default-preset").value = settings.defaultPreset;
  el("max-height").value = String(settings.maxHeight || "");
  el("default-folder").value = settings.defaultFolder;
}

async function test() {
  el("test-result").textContent = "Testing…";
  try {
    const { health } = await send("testConnection", {
      backendUrl: el("backend-url").value.trim(),
    });
    const checks = health.checks || {};
    const failing = Object.entries(checks).filter(([, v]) => v !== "ok");
    el("test-result").textContent = failing.length
      ? `Reachable, but degraded: ${failing.map(([k, v]) => `${k} ${v}`).join(", ")}`
      : `Reachable — Content ${health.version}.`;
  } catch (error) {
    el("test-result").textContent = error.message;
  }
}

async function save() {
  const backendUrl = el("backend-url").value.trim();

  // Ask for host access before storing, so a saved-but-unusable address cannot
  // happen: if the grant is refused there is nothing to fix later.
  try {
    const origin = `${new URL(backendUrl).origin}/*`;
    const granted = await chrome.permissions.request({ origins: [origin] });
    if (!granted) {
      el("saved").textContent =
        "Not saved: without permission for that address the extension cannot reach it.";
      return;
    }
  } catch {
    el("saved").textContent = "That does not look like a URL.";
    return;
  }

  const maxHeight = el("max-height").value;
  await send("saveSettings", {
    patch: {
      backendUrl,
      defaultPreset: el("default-preset").value,
      maxHeight: maxHeight ? Number(maxHeight) : null,
      defaultFolder: el("default-folder").value.trim(),
    },
  });
  el("saved").textContent = "Saved.";
}

el("test").addEventListener("click", test);
el("save").addEventListener("click", save);
load();
