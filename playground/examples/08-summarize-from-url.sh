#!/usr/bin/env bash
# URL -> summary (Markdown, via the local Ollama daemon) + transcript,
# downloaded into playground/output/<job_id>/.
# Needs captions on the video and Ollama running on the host.
# Usage: ./08-summarize-from-url.sh 'https://www.youtube.com/watch?v=...' [lang]
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
        {"id": "transcript", "type": "transcript", "required": False},
        {"id": "summary", "type": "summary", "from_outputs": ["transcript"],
         "options": {"language": lang, "length": "medium", "style": "structured"}},
    ],
}))
EOF
)"
