# input/

Drop the files (videos, audio…) to be used as `file` sources here.

This directory is mounted read-only inside the container under `/input` — in a
request, reference a file with `{"type": "file", "path": "/input/<name>"}`.
Media files are not versioned.
