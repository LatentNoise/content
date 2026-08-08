#!/usr/bin/env bash
# Shared helpers for the example scripts. Requires: curl, python3.
set -euo pipefail

API_URL="${API_URL:-http://localhost:8010}"
INPUT_PREFIX="${INPUT_PREFIX:-/input}"
OUTPUT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/output"

json_get() { # json_get '<expr>'  — reads JSON on stdin, prints expr (d = parsed dict)
  python3 -c "import sys, json; d = json.load(sys.stdin); print($1)"
}

api() { # api METHOD PATH [JSON_BODY]
  local method="$1" path="$2" body="${3:-}"
  if [ -n "$body" ]; then
    curl -sS -X "$method" "$API_URL$path" -H 'Content-Type: application/json' -d "$body"
  else
    curl -sS -X "$method" "$API_URL$path"
  fi
}

pretty() { python3 -m json.tool; }

submit_job() { # submit_job JSON  -> prints job_id
  local response
  response=$(api POST /api/v1/jobs "$1")
  if ! echo "$response" | json_get "d['job_id']" 2>/dev/null; then
    echo "Submission rejected:" >&2
    echo "$response" | pretty >&2
    return 1
  fi
}

follow_job() { # follow_job JOB_ID  — poll status + stream new events until terminal
  local job_id="$1" last_seq=0 status="" line
  while true; do
    while IFS= read -r line; do
      [ -n "$line" ] && echo "  event: $line"
      last_seq=$(echo "$line" | cut -d' ' -f1)
    done < <(api GET "/api/v1/jobs/$job_id/events?after_sequence=$last_seq" |
      python3 -c "import sys, json
for e in json.load(sys.stdin):
    print(e['sequence'], e['type'], json.dumps(e['data']) if e['data'] else '')")
    status=$(api GET "/api/v1/jobs/$job_id" | json_get "d['status']")
    case "$status" in
      succeeded|partially_succeeded|failed|cancelled) echo "final status: $status"; break ;;
      *) sleep 1 ;;
    esac
  done
  [ "$status" = succeeded ] || [ "$status" = partially_succeeded ]
}

download_artifacts() { # download_artifacts JOB_ID  — into playground/output/<job_id>/
  local job_id="$1" dest="$OUTPUT_DIR/$job_id"
  mkdir -p "$dest"
  api GET "/api/v1/jobs/$job_id/artifacts" |
    python3 -c "import sys, json
for a in json.load(sys.stdin):
    print(a['id'], a['filename'])" |
    while read -r artifact_id filename; do
      curl -sS -o "$dest/$filename" "$API_URL/api/v1/artifacts/$artifact_id/content"
      echo "  downloaded: output/$job_id/$filename"
    done
}

run_and_fetch() { # run_and_fetch JSON — submit, follow, download
  local job_id
  job_id=$(submit_job "$1")
  echo "job: $job_id"
  follow_job "$job_id" || true
  download_artifacts "$job_id"
}
