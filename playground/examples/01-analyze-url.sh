#!/usr/bin/env bash
# Analyze a URL: detected resource + capabilities.
# Usage: ./01-analyze-url.sh 'https://www.youtube.com/watch?v=...'
source "$(dirname "$0")/lib.sh"

url="${1:?usage: $0 URL}"
api POST /api/v1/analyses "$(python3 - "$url" <<'EOF'
import json, sys
print(json.dumps({"sources": [{"id": "main", "type": "url", "uri": sys.argv[1]}]}))
EOF
)" | pretty
