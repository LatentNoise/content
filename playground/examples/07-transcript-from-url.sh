#!/usr/bin/env bash
# URL -> transcript (from existing subtitles/captions, canonical JSON) +
# plain-text variant, downloaded into playground/output/<job_id>/.
# Works great on any YouTube video with captions.
# Usage: ./07-transcript-from-url.sh 'https://www.youtube.com/watch?v=...' [lang]
source "$(dirname "$0")/lib.sh"

url="${1:?usage: $0 URL [lang]}"
lang="${2:-auto}"
run_and_fetch "$(python3 - "$url" "$lang" <<'EOF'
import json, sys
url, lang = sys.argv[1], sys.argv[2]
print(json.dumps({
    "schema_version": "1.0",
    "sources": [{"id": "main", "type": "url", "uri": url}],
    "outputs": [
        {"id": "transcript", "type": "transcript",
         "options": {"language": lang, "format": "json"}},
        {"id": "transcript_txt", "type": "transcript", "required": False,
         "options": {"language": lang, "format": "text"}},
    ],
}))
EOF
)"
