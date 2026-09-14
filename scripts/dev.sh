#!/usr/bin/env bash
# The whole product, from the working tree, on this machine.
#
# Four processes: the engine and the three surfaces. The engine reloads on
# save (uvicorn --reload), and Streamlit reloads on save by itself, so the
# loop is: edit a file, refresh the browser. No image, no registry, no
# cluster, and nothing pushed anywhere.
#
# It runs in `token` mode by default, because that is the mode where the
# interesting things live — signing in, sessions, per-user storage, quotas —
# and the one a self-hosted install never exercises. `AUTH=none` gives the
# self-hosted contract instead.
#
# Signing in works with no mail server: with CONTENT_MAILER_URL empty the
# engine writes the link to its own log (ADR 0031), and this script pulls it
# out and prints it on its own line. That is the whole local sign-in story.
#
# Ctrl-C stops all four. The data directory is separate from the compose
# stack's, so neither can surprise the other.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"

AUTH="${AUTH:-token}"
PORT_API="${PORT_API:-8000}"
PORT_HOMETUBE="${PORT_HOMETUBE:-8501}"
PORT_STUDIO="${PORT_STUDIO:-8502}"
PORT_CONSOLE="${PORT_CONSOLE:-8503}"
DATA="${DATA:-$ROOT/playground/dev-data}"
ADDRESS="${ADDRESS:-127.0.0.1}"
LOGS="$DATA/logs"

BACKEND_PY="$ROOT/apps/backend/.venv/bin/python"
UI_PY="$ROOT/.venv-ui/bin/python"
for interpreter in "$BACKEND_PY" "$UI_PY"; do
  [ -x "$interpreter" ] || {
    echo "missing $interpreter — run: make install && make ui-venv" >&2
    exit 1
  }
done

mkdir -p "$LOGS"

API="http://localhost:$PORT_API"
STUDIO="http://localhost:$PORT_STUDIO"
CONSOLE="http://localhost:$PORT_CONSOLE"
HOMETUBE="http://localhost:$PORT_HOMETUBE"

export CONTENT_AUTH_MODE="$AUTH"
export CONTENT_DATA_DIR="$DATA"
export CONTENT_DB_PATH="$DATA/content.db"
export CONTENT_DELIVERY_DIR="$DATA/delivery"
export CONTENT_PUBLIC_BASE_URL="$API"
# Localhost is not https, so a Secure cookie would be set and never sent back —
# the one setting that has to differ from production for sign-in to work here.
export CONTENT_SESSION_COOKIE_SECURE=false
# Empty domain means host-only: the browser files it under `localhost`, and
# cookies ignore the port, so one sign-in covers all four ports.
export CONTENT_SESSION_COOKIE_DOMAIN=""
# The surfaces, declared once on the engine (ADR 0038): every UI learns the
# others from /config, and the redirect allowlist follows from this list.
export CONTENT_SURFACES="studio=$STUDIO,console=$CONSOLE,hometube=$HOMETUBE"
export CONTENT_SIGN_IN_DEFAULT_TARGET="$STUDIO"
# No mailer: the sign-in link goes to the log, which is where this script
# reads it from.
export CONTENT_MAILER_URL=""
export CONTENT_OPERATOR_EMAILS="${OPERATOR_EMAILS:-you@example.com}"
export CONTENT_DELIVERY_DEFAULT=true
export CONTENT_API_URL="$API"
export CONTENT_PUBLIC_API_URL="$API"

pids=()
stop() {
  trap - INT TERM EXIT
  printf '\nstopping…\n'
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap stop INT TERM EXIT

start_surface() {
  local name="$1" app="$2" port="$3"
  # Bound to the loopback like the engine. Streamlit listens on every
  # interface by default, which on a laptop on someone's LAN means the whole
  # network can reach a development build. `ADDRESS=0.0.0.0` opts back in for
  # the case that wants it — looking at the UI from a phone.
  "$UI_PY" -m streamlit run "$app" \
    --server.port "$port" --server.headless true \
    --server.address "$ADDRESS" \
    --browser.gatherUsageStats false \
    >"$LOGS/$name.log" 2>&1 &
  pids+=("$!")
}

( cd "$ROOT/apps/backend" && exec "$BACKEND_PY" -m uvicorn content.api.app:app \
    --port "$PORT_API" --reload --reload-dir content ) >"$LOGS/engine.log" 2>&1 &
pids+=("$!")

start_surface studio "$ROOT/apps/web-studio/app.py" "$PORT_STUDIO"
start_surface console "$ROOT/apps/web-admin/app.py" "$PORT_CONSOLE"
start_surface hometube "$ROOT/apps/web-hometube/app.py" "$PORT_HOMETUBE"

printf '\n  engine    %s/docs\n  Studio    %s\n  Console   %s\n  HomeTube  %s\n' \
  "$API" "$STUDIO" "$CONSOLE" "$HOMETUBE"
printf '  mode      %s · data %s\n' "$AUTH" "$DATA"
if [ "$AUTH" = "token" ]; then
  printf '  sign in   ask for a link on any surface; it is printed here\n'
fi
printf '  logs      %s\n\n' "$LOGS"

# Follow the engine's log and lift the sign-in link out of it. Everything else
# stays in the files: four processes interleaved on one terminal is noise, and
# the one line anybody actually wants from a local run is the link.
tail -n 0 -F "$LOGS/engine.log" 2>/dev/null | while IFS= read -r line; do
  case "$line" in
    *"auth/callback?token="*)
      printf '\n🔑 sign-in link\n   %s\n\n' \
        "$(printf '%s' "$line" | sed -E 's|.*(https?://[^ ]*auth/callback\?token=[^ ]*).*|\1|')"
      ;;
    *ERROR*|*Traceback*) printf '⚠️  %s\n' "$line" ;;
  esac
done &
pids+=("$!")

wait "${pids[0]}"
