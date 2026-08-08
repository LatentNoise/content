#!/usr/bin/env bash
# URL -> video (best <=1080p, h264 preferred, mkv) + metadata, downloaded into
# playground/output/<job_id>/.
# Usage: ./05-video-from-url.sh 'https://www.youtube.com/watch?v=...'
source "$(dirname "$0")/lib.sh"

url="${1:?usage: $0 URL}"
run_and_fetch "$(python3 - "$url" <<'EOF'
import json, sys
print(json.dumps({
    "schema_version": "1.0",
    "sources": [{"id": "main", "type": "url", "uri": sys.argv[1]}],
    "outputs": [
        {
            "id": "video_main",
            "type": "video",
            "options": {
                "selection": {
                    "max_height": 1080,
                    "video_codec": {"mode": "prefer", "value": "h264"},
                },
                "container": "mkv",
                "processing": {"embed_metadata": True, "embed_thumbnail": True},
            },
        },
        {"id": "metadata_main", "type": "metadata", "required": False},
    ],
}))
EOF
)"
