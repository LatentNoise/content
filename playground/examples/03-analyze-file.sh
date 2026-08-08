#!/usr/bin/env bash
# Analyze a media file placed in playground/input/.
# Usage: ./03-analyze-file.sh ma-video.mp4
source "$(dirname "$0")/lib.sh"

name="${1:?usage: $0 FILENAME (relative to playground/input/)}"
api POST /api/v1/analyses "$(python3 - "$INPUT_PREFIX/$name" <<'EOF'
import json, sys
print(json.dumps({"sources": [{"id": "vid", "type": "file", "path": sys.argv[1]}]}))
EOF
)" | pretty
