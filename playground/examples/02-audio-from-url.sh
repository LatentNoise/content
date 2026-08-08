#!/usr/bin/env bash
# URL -> audio + metadata, then download the artifacts into playground/output/.
# Usage: ./02-audio-from-url.sh 'https://www.youtube.com/watch?v=...'
source "$(dirname "$0")/lib.sh"

url="${1:?usage: $0 URL}"
run_and_fetch "$(python3 - "$url" <<'EOF'
import json, sys
print(json.dumps({
    "schema_version": "1.0",
    "sources": [{"id": "main", "type": "url", "uri": sys.argv[1]}],
    "outputs": [
        {"id": "audio_main", "type": "audio"},
        {"id": "metadata_main", "type": "metadata", "required": False},
    ],
}))
EOF
)"
