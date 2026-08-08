#!/usr/bin/env bash
# Local file -> mkv remux (stream copy, no re-encode), downloaded into
# playground/output/<job_id>/.
# Usage: ./06-remux-file.sh ma-video.mp4
source "$(dirname "$0")/lib.sh"

name="${1:?usage: $0 FILENAME (relative to playground/input/)}"
run_and_fetch "$(python3 - "$INPUT_PREFIX/$name" <<'EOF'
import json, sys
print(json.dumps({
    "schema_version": "1.0",
    "sources": [{"id": "vid", "type": "file", "path": sys.argv[1]}],
    "outputs": [
        {"id": "video_mkv", "type": "video", "options": {"container": "mkv"}},
    ],
}))
EOF
)"
