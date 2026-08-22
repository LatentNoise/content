#!/usr/bin/env bash
# Release check for content-mcp — tests the PUBLISHED ARTIFACT, not the source tree.
#
# Why this exists: the repo's 39 unit tests all pass while the published package
# is unusable. They import from the working tree, where the installed `mcp` is
# whatever the dev environment happens to have. A user running `uvx content-mcp`
# resolves dependencies from scratch, and can land on a version that does not
# have the API the code imports. That gap is invisible to every test we own.
#
# Rule of thumb: unit tests prove the code is right; this proves the package is
# installable. Both are needed, and only this one catches a floor that lies.
#
# Usage:
#   ./release-check.sh                 # test the version on PyPI
#   ./release-check.sh --local         # test the working tree, built and installed
#   CONTENT_API_URL=... ./release-check.sh
set -Eeuo pipefail

PKG="content-mcp"
# Pin the interpreter. Without this, `uv venv` picks whatever Python leads the
# PATH in the current directory — miniconda 3.10 from one shell, 3.13 from
# another — and the same script reports "unsatisfiable" or "all green"
# depending on where it was launched. A check whose verdict moves with the cwd
# is worse than no check: it produces confident false alarms.
PYVER="${RELEASE_CHECK_PYTHON:-3.13}"
API="${CONTENT_API_URL:-http://localhost:8010}"
MODE="pypi"
[[ "${1:-}" == "--local" ]] && MODE="local"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
PASS=0; FAIL=0
# macOS ships no `timeout`. perl's alarm is present everywhere and needs no install.
tmo() { local s="$1"; shift; perl -e 'alarm shift; exec @ARGV' "$s" "$@"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; [[ -n "${2:-}" ]] && printf '      %s\n' "$2"; FAIL=$((FAIL+1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$1"; }

head_ "1. Clean install, LOWEST allowed dependency versions (Python $PYVER)"
# This is the test that fails today. `--resolution lowest-direct` installs the
# oldest version each declared floor permits — exactly what a user with an older
# resolver, an old lockfile, or a constrained environment will get. If the code
# needs a newer API than the floor admits, it breaks here and nowhere else.
if uv venv --python "$PYVER" "$WORK/low" >/dev/null 2>&1; then
  if [[ "$MODE" == local ]]; then TARGET="$(cd "$(dirname "$0")" && pwd)"; else TARGET="$PKG"; fi
  if uv pip install --python "$WORK/low/bin/python" --resolution lowest-direct "$TARGET" >"$WORK/low.log" 2>&1; then
    ok "installs with lowest-direct resolution"
    if "$WORK/low/bin/python" -c "import content_mcp.server" >"$WORK/low.import" 2>&1; then
      ok "imports with the lowest allowed dependencies"
    else
      bad "IMPORT FAILS on the lowest allowed dependencies — the declared floor is a lie" \
          "$(tail -2 "$WORK/low.import" | tr '\n' ' ')"
      echo "      resolved: $("$WORK/low/bin/python" -c 'import importlib.metadata as m; print("mcp", m.version("mcp"))' 2>/dev/null || echo '?')"
    fi
  else
    bad "install failed" "$(tail -2 "$WORK/low.log" | tr '\n' ' ')"
  fi
else
  bad "uv venv unavailable"
fi

head_ "2. Clean install, current resolution"
uv venv --python "$PYVER" "$WORK/cur" >/dev/null 2>&1
if [[ "$MODE" == local ]]; then TARGET="$(cd "$(dirname "$0")" && pwd)"; else TARGET="$PKG"; fi
if uv pip install --python "$WORK/cur/bin/python" "$TARGET" >"$WORK/cur.log" 2>&1; then
  ok "installs"
  PY="$WORK/cur/bin/python"
  "$PY" -c "import content_mcp.server" 2>/dev/null && ok "imports" || bad "import fails"
  V="$("$PY" -c 'import importlib.metadata as m; print(m.version("content-mcp"))' 2>/dev/null || echo '?')"
  ok "version installed: $V"
else
  bad "install failed" "$(tail -2 "$WORK/cur.log" | tr '\n' ' ')"
fi

head_ "3. Console entry point"
# The README tells people to run `uvx content-mcp`. If the entry point is
# misdeclared the package installs fine and the documented command still fails.
if [[ -x "$WORK/cur/bin/content-mcp" ]]; then ok "content-mcp executable present"
else bad "console script 'content-mcp' missing from the install"; fi

head_ "4. MCP handshake over stdio"
# Speak the protocol the way a client does: initialize, then list tools.
# A server that imports cleanly can still fail to negotiate or expose nothing.
cat > "$WORK/handshake.py" <<'PY'
import json, subprocess, sys, os
env = dict(os.environ, CONTENT_API_URL=os.environ.get("CONTENT_API_URL", "http://localhost:8010"))
p = subprocess.Popen([sys.argv[1]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, text=True, env=env, bufsize=1)
def send(o): p.stdin.write(json.dumps(o) + "\n"); p.stdin.flush()
send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{
      "protocolVersion":"2025-06-18","capabilities":{},
      "clientInfo":{"name":"release-check","version":"1"}}})
init = json.loads(p.stdout.readline())
send({"jsonrpc":"2.0","method":"notifications/initialized"})
send({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}})
tools = json.loads(p.stdout.readline())
p.terminate()
names = sorted(t["name"] for t in tools.get("result", {}).get("tools", []))
print(json.dumps({"server": init.get("result", {}).get("serverInfo", {}), "tools": names}))
PY
if OUT=$(CONTENT_API_URL="$API" tmo 60 "$WORK/cur/bin/python" "$WORK/handshake.py" "$WORK/cur/bin/content-mcp" 2>/dev/null); then
  ok "initialize + tools/list answered"
  echo "      $(echo "$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["server"], "→", len(d["tools"]), "tools:", ", ".join(d["tools"]))')"
  # The tool set is a public contract: a silently missing tool breaks callers.
  for t in analyze_source generate get_job get_artifact list_capabilities get_config; do
    echo "$OUT" | grep -q "\"$t\"" && ok "tool exposed: $t" || bad "TOOL MISSING: $t"
  done
else
  bad "no MCP handshake — the server did not answer initialize/tools/list"
fi

head_ "5. Live engine, read-only"
if curl -fsS --max-time 10 "$API/health" >/dev/null 2>&1 || curl -fsS --max-time 10 "$API/" >/dev/null 2>&1; then
  ok "engine reachable at $API"
else
  bad "engine unreachable at $API — steps 4-5 only proved the server starts, not that it works"
fi

head_ "6. uvx path (what the README tells users to run)"
if [[ "$MODE" == pypi ]]; then
  if tmo 180 uvx --from "$PKG" content-mcp --help >/dev/null 2>&1 \
     || echo '' | tmo 30 uvx "$PKG" >/dev/null 2>&1; then
    ok "uvx content-mcp starts"
  else
    bad "uvx content-mcp fails — this is the exact first command a reader of the post will run"
  fi
else
  echo "  – skipped in --local mode"
fi

printf '\n\033[1mResult: %d passed, %d failed\033[0m\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]] || { echo "Do not publish."; exit 1; }
echo "Safe to publish."
